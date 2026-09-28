# Security review, 2026-09-28: the internet-facing parts

Scope, in priority order: 79 site assistant (public `/chat`, `/consent`, `/widget.js`), 80 lead
hub (public `/leads/webhook/{source}`, enrichment), 72 control room, the 01 install scripts
(review only, no edits: another change was in progress), and a quick pass over 73, 78, 82, 84, 62, 63.

Method: read the code and its tests. For each real issue, first a test that fails on the old
code (negative control), then the fix, then the whole suite. Nothing was deployed, and no
external calls were made.

| Service | Tests before | Tests after |
|---|---|---|
| 79 site-assistant | 69 | 75 (`tests/test_security.py`, 6 new) |
| 80 lead-hub | 39 | 47 (`tests/test_security.py`, 8 new); 59 after X-1 |
| 72 control-room | 80 | 80 (no code change) |

## Findings

Severity is for the stack as shipped: ports bound to 127.0.0.1, and only the documented
public paths behind an HTTPS proxy.

| ID | Component | Severity | Description | Exploit sketch | Fix status | Test |
|---|---|---|---|---|---|---|
| SA-1 | 79 `notify` | Medium | The visitor's message was pasted as-is into the Slack/Discord/Teams handoff notification | Chat `<!channel> urgent: <https://evil/login\|Reset your admin password>` + "Talk to a person". The whole team gets pinged, and the phishing link shows as trusted-looking link text inside the team's own channel. `@everyone` / `[x](url)` do the same on Discord | **Fixed.** `notify_safe`: `<` `>` become look-alike characters (‹ ›), `](` is broken up, and `@everyone/@here/@channel` get a zero-width space. The text itself still reaches the team | `test_notification_cannot_ping_everyone_or_hide_a_link`, `test_notification_keeps_plain_text` |
| SA-2 | 79 handoffs | Medium | Every new session could send one more notification (150 per IP per day; unlimited with more IPs). Each `/consent` call on a conversation with an open handoff notified again | A script loops `POST /chat {"action":"handoff"}` without a session id, or repeats `/consent` with the same session | **Fixed.** `NOTIFY_MAX_PER_HOUR` (default 20, 0 = off): past the cap, handoffs are still recorded, with `notify_error` saying why no notification was sent. `/consent` notifies only when the address changes | `test_notifications_are_capped_per_hour`, `test_repeated_consent_does_not_renotify_the_same_email` |
| SA-3 | 79 rate limits | Medium | Per-IP limits used the full IPv6 address, and one subscriber has a /64 (2^64 addresses) | Rotate the source address inside your own /64. The per-IP minute and day limits no longer apply, so the model and claim checker can be run at will (cost, and handoff spam) | **Fixed.** `rate_key`: IPv6 is limited per /64, and IPv4-mapped IPv6 counts as its IPv4 address | `test_ipv6_clients_share_their_64_prefix_limit`, `test_client_key_unit` |
| LH-1 | 80 webhook | Medium | The public webhook read the whole request body into memory before the 200 KB check. This happened before the secret check too, so it needed no secret | Send several chunked multi-GB bodies to `/leads/webhook/x`: memory runs out | **Fixed.** A `Content-Length` over the cap gets 413 at once. Otherwise the body is read as a stream and reading stops at 200 KB | `test_webhook_stops_reading_an_oversized_body` (direct ASGI call: TestClient buffers the body), `test_webhook_refuses_a_large_content_length_before_reading`, `test_webhook_normal_body_still_works` |
| LH-2 | 80 CRM notes | Medium (only with `DRY_RUN=false`) | HubSpot `hs_note_body` and Pipedrive `content` are HTML. The note held the lead's message and name unescaped | A lead types `<img src=https://tracker/p.gif><a href=https://evil/login>Log in to see the RFP</a>`. The sales team's CRM then shows a tracking pixel and a disguised link. The CRMs strip `<script>`, but not these | **Fixed.** `crm.note_html` escapes the note for both CRMs, and newlines become `<br>` | `test_crm_notes_escape_lead_html` |
| LH-3 | 80 enrichment | Medium (Low without DNS rebinding) | `same_site` checked the host name only. A robots.txt redirect, or 07's final URL, to `http://<company-domain>:<any port>` counted as the company's site. `_public_host` also missed IPv6 forms that embed an IPv4 address (NAT64, 6to4, IPv4-compatible), which 07 had already fixed | Attacker's domain + DNS rebinding: the robots redirect goes to `http://evil.example:8000/...`, which resolves to a stack service at connect time. 80 then fetched it, and 07's page text from that port was kept as "enrichment" (and could end up in a reply draft) | **Fixed.** `same_site(url, domain, base)` needs the same scheme and port as the base, so HTTPS certificate checks stop rebinding on :443. `_is_public` uses 07's rule. Residual: see X-1 | `test_same_site_needs_same_scheme_and_port`, `test_extractor_final_url_on_another_port_is_discarded`, `test_robots_redirect_to_another_port_is_not_followed`, `test_ipv6_forms_that_embed_a_private_ipv4_are_refused` |
| X-1 | 07 `net.py` (copied in 73; same pattern in 08, 09, 12, and 80's robots.txt fetch) | Medium | The SSRF guard resolves the name, checks it, and then httpx resolves it again to connect (time-of-check/time-of-use). DNS rebinding can pass the check and still connect to a private address. 07 `/extract` has no API key (internal network only) | A name with TTL 0 that answers public, then 10.x/127.x. 07 follows a redirect chain onto a plain-http internal port (a blind GET). After LH-3, the text no longer reaches 80's records | **Fixed.** New `app/safe_http.py`, one file kept identical in 07, 08, 09, 12, 73 and 80 (07 is the canonical copy; the header lists the others). `GuardedClient` parses the URL with httpx (the same parser that sends it), resolves the name once, refuses it if ANY A/AAAA answer is non-public (07's rule, incl. IPv6 forms that embed an IPv4), and pins the checked addresses. A `PinnedBackend` (httpcore network backend) dials only a pinned address and checks it again before dialling; an unchecked host is refused. The URL is never rewritten, so the Host header, SNI and certificate verification use the host name. The client never follows redirects: each hop goes through `send()` again (checked and pinned again). `ALLOW_PRIVATE_URLS=true` still switches the guard off. `net.py` in each service and 80's `robots()` use it. Residual: the backend is swapped into httpx's private `_pool` (a test fails if an upgrade stops honouring it; the constructor fails closed); the copies are kept identical by hand (same md5 today) | `tests/test_rebinding.py` in each of the 6 (real listener on 127.0.0.1, DNS answers public then 127.0.0.1: failed on the old code in all 6, the listener was hit), `tests/test_safe_http.py` (identical in the 6: pinning, every redirect hop re-checked, TLS SNI/verify on the host name while dialling the IP, IPv6 answers and literals, fallback only to checked addresses, unchecked host refused, `ALLOW_PRIVATE_URLS`) |
| IN-1 | 01 `uninstall.sh` | Medium (review only) | `--client X` removes every container, volume and network with compose project label `X`, even when `X` is not a marketing-agent client (no `.env.X`) | On a Docker context that also runs another compose project, `uninstall.sh --client <that project> --yes` deletes its data. Without `--yes` you must type the name, which is the only guard | **Open, not edited** (another change was in progress). Fix: refuse unless `.env.X` or `clients/X` exists, or the containers use `marketing-agent-*` images | none |
| CR-1 | 72 login limiter | Low | Failures are counted per `request.client.host`. Behind the HTTPS proxy in Docker, uvicorn doesn't trust `X-Forwarded-For` (FORWARDED_ALLOW_IPS defaults to 127.0.0.1), so every client has the proxy's address. The all-clients cap (4 × 5 in 15 min) then locks out the approver too | Anyone who can reach `/login` sends 20 wrong passwords every 15 minutes, and the approver can't log in | **Partly fixed**: with `TRUSTED_PROXIES` set, the visitor's own address (right-most untrusted `X-Forwarded-For` hop) is used, so one visitor's failures no longer lock everyone out; the all-clients cap stays by design (it stops distributed guessing — many addresses can still trigger it) | `72-control-room/tests/test_client_ip.py` |
| CR-2 | 72 cookies | Low | With `COOKIE_SECURE=auto` behind a TLS proxy, the scheme is `http` for the same reason, so the session cookie has no `Secure` flag and no HSTS is sent. The Dockerfile comment says `--proxy-headers` is enough, but it isn't | Someone on the network sees the cookie if the browser ever makes a plain-http request to that host (SameSite=Strict and HttpOnly still apply) | Documented (README: set `CONTROL_COOKIE_SECURE=true` behind HTTPS). Suggestion: the installer sets it when the public URL is https | none |
| SA-L1 | 79 `/consent` | Low | Any address can be left with "consent", and it isn't verified | Someone enters another person's email. The team gets a lead and replies to someone who never asked | Open. Double opt-in, or mark site-assistant leads "unverified" in 80 | none |
| SA-L2 | 79 origin | Low | Requests without `Origin` are allowed unless `REQUIRE_ORIGIN=true` (documented). `Origin: null` is refused | Scripts can call `/chat` directly. The rate limits are the protection | Documented | existing `test_origin_check_and_cors` |
| SA-L3 | 79 `Limiter` | Low | Over 50,000 keys, every hit scans the whole dict under the lock, and keys younger than a day are kept | A large botnet makes each request O(n) | Open. Prune at most every few seconds, and by each key's own window | none |
| LH-L1 | 80 webhook | Low | Signed webhooks carry no timestamp, so they can be replayed | Replaying a captured form post re-merges the same lead (deduplicated by email) | Open | none |
| X-L1 | 73 download | Low | The httpx timeout is per read, not for the whole download. The size of a decoded frame isn't capped (only the duration, `MAX_SOURCE_MINUTES`, and the ffmpeg timeouts) | A slow-drip server holds a job slot. A huge-resolution file uses CPU until `FFMPEG_TIMEOUT` | Open. `/jobs` needs the API key | none |
| X-L2 | 63 list allow-list | Low | With an empty `LISTMONK_LIST_IDS`, the caller may name any list id | A caller with the key sends a campaign to a list it shouldn't (still a dry run by default) | Open. Require the allow-list when `DRY_RUN=false` | none |
| IN-L1 | 01 `client.sh load_env` | Low | Exports any `KEY=` from the env file, including `PATH`, `BASH_ENV`, `LD_PRELOAD` | Only the owner's own file, so it can only hurt yourself | Open. Suggest a denylist | none |

## Checked and found sound

- **79.** The model sees only the approved facts, trusted knowledge-base excerpts and this
  visitor's own previous message. No other visitor's data, no admin data and no internal URL is
  in its context, and every sentence passes the guards and the claim checker. The widget
  inserts server text with `textContent` only, and the booking link must be `https://`. The
  admin key is compared in constant time, and the admin paths are never proxied (README
  Caddy block). CORS is exact-match with no credentials. The body is capped for chunked
  requests too. Session ids are random, only their hash is stored, and they expire. The
  guard regexes stay at or under 1.5 ms on adversarial 800-character inputs (repeated digits,
  dots, `@`), so there is no ReDoS. A lead needs `agree: true` (StrictBool) and buying intent.
- **80.** Webhook secrets use `hmac.compare_digest`. A source without a secret is refused (401).
  Logs record only exception type names. DELETE, export and every other endpoint require the key.
- **72.** CSRF runs on every POST, including htmx (`X-CSRF-Token` header), and login uses a
  double-submit cookie. The session id is rotated at login, and logout needs CSRF. `next=` is
  safe: tab, newline, NUL and full-width-space variants come out percent-encoded. Jinja
  autoescape is on and nothing is marked `|safe`. `hx-vals` uses `tojson`, and links are
  http(s)-only. CSP has `frame-ancestors 'none'`, and `X-Frame-Options: DENY` is set. The media
  proxy accepts hex names only. The n8n decision webhook compares the key in constant time and
  refuses keys shorter than 16 characters. The brand wizard caps field lengths.
- **01 install.sh.** Secrets come from `openssl rand` into a 0600 env file under `umask 077`.
  Values are passed through the environment or stdin, never argv (the owner password, and
  `kcurl -K -`). `change-me*` placeholders are regenerated. The URL and email are
  regex-checked before `env_set`. shellcheck is not installed on this machine, so it was not run.
- **84 / 78 / 82.** Tokens go in headers (84, 82). 78's Graph `access_token` is scrubbed from
  errors, and the httpx URL logging is turned down. 82 redacts provider keys before storing.
  **62:** a strict image-host allow-list, with no redirects followed.

## Files changed

- `79-site-assistant/app/main.py`: `rate_key`, `notify_safe`, the `notify` cap, and `/consent` re-notify only on a new address
- `79-site-assistant/tests/test_security.py` (new), `tests/test_main.py` (the fixture resets `notify_limiter`)
- `79-site-assistant/README.md`, `.env.example`: `NOTIFY_MAX_PER_HOUR`, IPv6 /64
- `80-lead-hub/app/main.py`: streamed webhook body with a cap
- `80-lead-hub/app/crm.py`: `note_html`
- `80-lead-hub/app/enrich.py`: `same_site(..., base)`, `_is_public`
- `80-lead-hub/tests/test_security.py` (new)
- X-1: `app/safe_http.py` (new, identical) in 07, 08, 09, 12, 73, 80; `app/net.py` in 07, 08, 09, 12, 73 (use `GuardedClient`); `80-lead-hub/app/enrich.py` (`robots()` uses `GuardedClient`, `_is_public` from `safe_http`)
- X-1 tests: `tests/test_safe_http.py` and `tests/test_rebinding.py` (new) in the 6 services; existing fixtures now patch `safe_http.resolve` (07 `test_main.py`, `test_youtube.py`; 08, 09, 12, 73 `test_main.py`; 80 `test_main.py`, `test_security.py`)
