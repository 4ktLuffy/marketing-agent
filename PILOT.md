# Pilot runbook (2–4 weeks)

A plan for one person running the marketing agent for one brand. Goal of the pilot: find
out, with numbers, whether the agent saves you time and gets clicks, while nothing goes out
that you did not approve.

## Day 0: set up (about 1–2 hours)

1. **Install.** Follow [INSTALL.md](01-marketing-stack/INSTALL.md). It runs
   [`01-marketing-stack/scripts/install.sh`](01-marketing-stack/scripts/install.sh): checks
   the machine, picks a profile (start with **core**), builds, starts everything, imports the
   workflows and runs a smoke test. At the end it prints the addresses.
2. **Log in to the control room** (<http://localhost:8172>, user and password from
   `01-marketing-stack/.env`: `CONTROL_USER`, `CONTROL_PASSWORD`). On a phone: put it behind
   HTTPS first ([INSTALL.md, section 8](01-marketing-stack/INSTALL.md#8-phone-access-https)).
3. **Set up your brand, today** (the schedules start drafting the next morning): control room →
   More → **Brand setup** (on a computer: **Brand** in the top bar). Six short steps:
   1. Basics: name, one sentence about what you sell, website, who you sell to.
   2. Products: name, one line, price (optional).
   3. Facts: only true, checkable statements, one per line. The agent may only claim what is
      here; aim for 10–20.
   4. Rules: phrases it must never use, which emoji (and how many), other websites it may link
      to, disclaimers (e.g. `paid_social: #ad`).
   5. Voice: answer the 10 questions in plain words, check the profile the model writes, save.
   6. Review: the score should be 100 %. The box "What the agent reads" is exactly what every
      writer sees.

   The agent uses changes within 60 seconds. "Reset" in step 6 goes back to the shipped example.
4. **Choose ONE publishing channel** for the pilot. One channel keeps the review short and the
   numbers clean.
   - **core** profile: your own webhook (`PUBLISH_WEBHOOK_URL`: Zapier, Make, Buffer). It has
     no dry run, so point it at a step that only logs until the first posts look right.
   - **growth** profile: LinkedIn and other networks through Postiz (`54`), or your blog through
     WordPress/Ghost (`62`). Set it up in its README and leave its dry run on
     (`POSTIZ_DRY_RUN=true` or `CMS_DRY_RUN=true`, the default).

   Details: [INSTALL.md, section 6](01-marketing-stack/INSTALL.md#6-turn-on-publishing-only-after-checking-a-dry-run).
5. **Groq keys (optional).** Everything runs on your machine by default. A free Groq key makes
   some steps faster or stricter; the text of those calls is then sent to Groq. In
   `01-marketing-stack/.env`:
   - fact checking on a stronger model: `VERIFIER_PROVIDER=openai`,
     `VERIFIER_MODEL=openai/gpt-oss-20b`, `OPENAI_BASE_URL`, `OPENAI_API_KEY` (measured: catches
     as much, with far fewer false alarms than the local model);
   - a faster chat: `CHAT_PROVIDER=hosted`, `CHAT_API_KEY`.
   Re-run the installer after changing `.env`.

## Day 1–3: first posts, still in dry run

- Approve 2–3 posts in the queue.
- The publisher (every 15 minutes) sends approved, due posts to the bridge. In dry run the
  bridge answers `{"status": "dry_run", ...}` and posts nothing. Look at that answer in n8n
  (**Executions** → *39 · Schedule · Publisher* → the bridge node) or in the bridge log
  (`docker compose logs postiz-bridge` or `cms-bridge`, in `01-marketing-stack`): text, link,
  image, channel.
- **Turn the dry run off only when the first approved post looks right there.** In `.env`, set
  that one bridge's switch to false (`POSTIZ_DRY_RUN=false` or `CMS_DRY_RUN=false`), re-run
  `./scripts/install.sh` (a plain restart does not read `.env`), and watch the next post go out.

## What the agent does by itself

Times are in the n8n time zone (`TZ` in `.env`, UTC by default). Which ones run depends on the
install profile; the control room's **Health** page shows what is up.

| When | What (workflow) |
|---|---|
| Every 15 min | Publishes approved posts that are due (39); writes due lifecycle emails to the flow outbox, a dry run that sends nothing (86, growth) |
| Every 6 h | Checks competitor pages (and their ads, full profile) for changes (37) |
| 1st of the month 07:00 | Competitor positioning map (37, full profile) |
| 1st of the month 08:00 | Monthly report on last month for a client or your boss, waiting for your approval; never sent by itself (85) |
| Daily 05:30 | Pulls yesterday's visits from analytics (56, if Umami is set up) |
| Daily 06:00 | Drafts the content engine's next posts (65, growth); measures campaigns (52) |
| Daily 07:30 | Syncs ad spend and sends new pacing / CPL / ROAS alerts (84, growth profile, if Meta or Google Ads is set up) |
| Daily 08:00 | Trend digest with post ideas (36) |
| Daily 09:00 | Drafts replies to new reviews (60, growth) |
| Mon 07:00 | Plans next week (40); finds pages losing Google clicks (68, growth); proposes up to 2 experiments (74, growth) |
| Mon 08:00 | Turns last quarter's best posts into fresh drafts (59) |
| Mon 08:30 | Weekly look at running experiments (75, growth) |
| Mon 09:00 | Weekly report with next actions (41) |
| Tue 06:00 | Checks whether AI assistants mention the brand (83, full) |
| Wed 07:00 | SEO briefs from almost-ranking searches (69, growth) |
| Fri 10:00 | Newsletter draft from the week's posts (66; also a Listmonk draft on growth) |
| Fri 16:00 | Proposes writing rules learned from your edits (50) |

Nothing is published without your approval.

## Every day: 10 minutes

1. Open the **Queue** (control room home).
2. For each card: **Approve**, **Edit** (then approve), or **Reject** (say why; "rewrite it"
   sends it back for a new draft). Flagged cards first (tab *Flagged*).
3. Done. Write down how many minutes it took.

Why edits and reasons matter: every edit and rejection is recorded (46). On Fridays the agent
turns patterns in them into proposed writing rules for you to accept or refuse. Short reasons
("too salesy", "wrong price") teach it the most.

## Every week: 20 minutes (Monday)

- Read the **weekly report** (41): clicks by channel, top posts, next actions.
- **Experiments** (growth): approve or refuse the proposals (form 76, `<n8n>/form/mkt-experiments`); check the weekly look (75).
- **Rules**: accept or refuse Friday's proposed rules (form 51, `<n8n>/form/mkt-rules-review`).
- **Visibility** (full, if set up): wrong claims about you in AI answers become ideas in the calendar.
- **Brand setup**: add facts you noticed were missing; check the score is still 100 %.

## What to measure

| Measure | Where |
|---|---|
| Clicks per post | control room → **Stats** (tracked short links) |
| Approval rate: approved as written / edited / rejected | control room → More → **Content engine** (per pillar; growth). On core: count your decisions in the queue |
| Time spent | your own note: minutes per day in the queue, plus any writing you still did yourself |
| Posts published per week | calendar, status "published" |

Fill this in before the pilot (your last normal month) and at the end:

| | Before (per week) | Pilot week 1 | Week 2 | Week 3 | Week 4 |
|---|---|---|---|---|---|
| Posts published | | | | | |
| Clicks per post | | | | | |
| Hours on content | | | | | |
| Approved as written (%) | — | | | | |
| Rejected (%) | — | | | | |

## When to stop or pause

- **The agent pauses itself**: a content pillar stops drafting when, after 10 decisions, more
  than 25 % are rejected or fewer than 50 % are approved without edits. Fix the cause (usually a
  missing fact or rule in Brand setup), then **Resume** it on the Content engine page.
- **Pause the pilot** (set the bridge's dry run back to `true`, or empty `PUBLISH_WEBHOOK_URL`,
  and re-run the installer) if a post with a false claim reaches the
  queue unflagged twice in a week, more than half the drafts are rejected for two weeks, or the
  daily review takes more than 20 minutes for a week. Write down what happened; that is useful
  feedback.

## When something breaks

1. Control room → **Health**: which service is down.
2. Logs: in `01-marketing-stack`, `docker compose logs --tail 100 <service>`.
3. n8n → **Executions**: failed workflow runs, with the step that failed. Failures are also
   sent to your notification webhook (43), if you set one.
4. Brand looks wrong in drafts? Brand setup → step 6 shows exactly what the writers read.
5. Restart one service: `docker compose restart <service>`. Re-running the installer is safe;
   it keeps your `.env` and data.

## Sending feedback

Open an issue at <https://github.com/4ktLuffy/marketing-agent/issues> with: what you did, what
you expected, what happened, and a screenshot. At the end of the pilot, add the filled-in
before/after table. Never paste keys, passwords or `.env` contents.
