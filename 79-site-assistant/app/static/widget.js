/* Site assistant widget (79). Vanilla JS, no dependencies, no third-party code.
 *
 * Embed:  <script src="https://chat.example.com/widget.js" defer></script>
 * Optional: data-endpoint="https://chat.example.com" (default: where this file came from)
 *
 * Everything the visitor sees from the server is inserted as text (textContent), never as
 * HTML. The AI disclosure and the "Talk to a person" button are always visible.
 */
(function () {
  "use strict";
  if (window.__siteAssistantLoaded) return;
  window.__siteAssistantLoaded = true;

  var script = document.currentScript;
  var base = (script && script.getAttribute("data-endpoint")) || (script && script.src ? new URL(script.src).origin : "");
  base = base.replace(/\/+$/, "");
  var STORE_KEY = "site-assistant-session";
  var sessionId = null;
  var busy = false;

  function load() { try { return window.sessionStorage.getItem(STORE_KEY); } catch (e) { return null; } }
  function save(v) { try { window.sessionStorage.setItem(STORE_KEY, v); } catch (e) { /* private mode */ } }
  sessionId = load();

  function el(tag, attrs, text) {
    var n = document.createElement(tag);
    if (attrs) Object.keys(attrs).forEach(function (k) { n.setAttribute(k, attrs[k]); });
    if (text != null) n.textContent = text;
    return n;
  }

  var css = el("link", { rel: "stylesheet", href: base + "/widget.css" });
  document.head.appendChild(css);

  var root = el("div", { class: "sa-root" });
  var launcher = el("button", { type: "button", class: "sa-launcher", "aria-expanded": "false", "aria-controls": "sa-panel" }, "Questions? Ask our AI assistant");
  var panel = el("section", { id: "sa-panel", class: "sa-panel", role: "dialog", "aria-labelledby": "sa-title", hidden: "" });
  var header = el("div", { class: "sa-header" });
  var title = el("h2", { id: "sa-title", class: "sa-title" }, "Assistant");
  var close = el("button", { type: "button", class: "sa-close", "aria-label": "Close chat" }, "×");
  header.appendChild(title);
  header.appendChild(close);
  var disclosure = el("p", { class: "sa-disclosure" }, "AI assistant, not a person. It answers from our FAQ and product facts.");
  var logBox = el("div", { class: "sa-log", role: "log", "aria-live": "polite", "aria-relevant": "additions", tabindex: "0", "aria-label": "Conversation" });
  var status = el("p", { class: "sa-status", "aria-live": "assertive" });

  var consent = el("form", { class: "sa-consent", hidden: "", novalidate: "" });
  var consentIntro = el("p", { class: "sa-consent-intro" }, "Want a reply by email? Leave your address. A person will answer.");
  var emailLabel = el("label", { for: "sa-email" }, "Email");
  var email = el("input", { id: "sa-email", type: "email", autocomplete: "email", required: "", maxlength: "254" });
  var nameLabel = el("label", { for: "sa-name" }, "Name (optional)");
  var name = el("input", { id: "sa-name", type: "text", autocomplete: "name", maxlength: "120" });
  var agreeWrap = el("div", { class: "sa-agree" });
  var agree = el("input", { id: "sa-agree", type: "checkbox", required: "" });
  var agreeLabel = el("label", { for: "sa-agree" }, "I agree that you may use my email address and this chat to reply to me.");
  agreeWrap.appendChild(agree);
  agreeWrap.appendChild(agreeLabel);
  var consentSend = el("button", { type: "submit", class: "sa-button" }, "Send my email");
  [consentIntro, emailLabel, email, nameLabel, name, agreeWrap, consentSend].forEach(function (n) { consent.appendChild(n); });

  var form = el("form", { class: "sa-form" });
  var inputLabel = el("label", { for: "sa-input", class: "sa-visually-hidden" }, "Your message");
  var input = el("textarea", { id: "sa-input", rows: "2", maxlength: "800", placeholder: "Ask a question…" });
  var send = el("button", { type: "submit", class: "sa-button" }, "Send");
  form.appendChild(inputLabel);
  form.appendChild(input);
  form.appendChild(send);
  var person = el("button", { type: "button", class: "sa-person" }, "Talk to a person");

  [header, disclosure, logBox, status, consent, form, person].forEach(function (n) { panel.appendChild(n); });
  root.appendChild(panel);
  root.appendChild(launcher);
  (document.body || document.documentElement).appendChild(root);

  function addMessage(who, text, sources, bookingUrl) {
    var item = el("div", { class: "sa-msg sa-" + who });
    item.appendChild(el("span", { class: "sa-visually-hidden" }, who === "user" ? "You: " : "Assistant: "));
    item.appendChild(el("p", { class: "sa-text" }, text));
    if (sources && sources.length) item.appendChild(el("p", { class: "sa-sources" }, "Source: " + sources.join(", ")));
    if (bookingUrl && /^https:\/\//.test(bookingUrl)) {
      var a = el("a", { href: bookingUrl, target: "_blank", rel: "noopener noreferrer", class: "sa-book" }, "Book a call (opens a new tab)");
      item.appendChild(a);
    }
    logBox.appendChild(item);
    logBox.scrollTop = logBox.scrollHeight;
  }

  function setBusy(on) {
    busy = on;
    send.disabled = on;
    person.disabled = on;
    status.textContent = on ? "The assistant is writing…" : "";
  }

  function errorText(code) {
    if (code === 429) return "You're sending messages quickly. Please wait a moment and try again.";
    if (code === 403) return "This chat isn't available on this site.";
    if (code === 413) return "That message is too long. Please shorten it.";
    if (code === 503) return "The assistant is busy. Please try again in a minute.";
    return "Something went wrong. Please try again, or press “Talk to a person”.";
  }

  function post(path, body) {
    return fetch(base + path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      credentials: "omit"
    }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (data) { return { status: r.status, data: data }; });
    });
  }

  function handle(res) {
    if (res.status !== 200) { addMessage("bot", errorText(res.status)); return; }
    var d = res.data;
    if (d.session_id) { sessionId = d.session_id; save(sessionId); }
    var actions = d.actions || {};
    addMessage("bot", d.reply || "", d.sources, actions.booking_url);
    if (actions.show_consent) consent.hidden = false;
  }

  function chat(body) {
    if (busy) return;
    setBusy(true);
    body.session_id = sessionId;
    post("/chat", body).then(handle, function () { addMessage("bot", errorText(0)); })
      .then(function () { setBusy(false); input.focus(); });
  }

  form.addEventListener("submit", function (e) {
    e.preventDefault();
    var text = input.value.trim();
    if (!text) return;
    addMessage("user", text);
    input.value = "";
    chat({ message: text });
  });
  input.addEventListener("keydown", function (e) {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); form.requestSubmit ? form.requestSubmit() : send.click(); }
  });
  person.addEventListener("click", function () {
    addMessage("user", "Talk to a person");
    chat({ message: "", action: "handoff" });
  });
  consent.addEventListener("submit", function (e) {
    e.preventDefault();
    if (!agree.checked) { status.textContent = "Please tick the box to agree first."; agree.focus(); return; }
    if (!email.value || !email.checkValidity()) { status.textContent = "Please enter a valid email address."; email.focus(); return; }
    if (!sessionId || busy) return;
    setBusy(true);
    post("/consent", { session_id: sessionId, email: email.value.trim(), name: name.value.trim() || null, agree: true })
      .then(function (res) {
        if (res.status === 200) { consent.hidden = true; addMessage("bot", res.data.reply || "Thanks."); }
        else addMessage("bot", errorText(res.status));
      }, function () { addMessage("bot", errorText(0)); })
      .then(function () { setBusy(false); });
  });

  function open() {
    panel.hidden = false;
    launcher.setAttribute("aria-expanded", "true");
    launcher.hidden = true;
    input.focus();
  }
  function shut() {
    panel.hidden = true;
    launcher.hidden = false;
    launcher.setAttribute("aria-expanded", "false");
    launcher.focus();
  }
  launcher.addEventListener("click", open);
  close.addEventListener("click", shut);
  panel.addEventListener("keydown", function (e) { if (e.key === "Escape") shut(); });

  fetch(base + "/widget-config", { credentials: "omit" }).then(function (r) { return r.ok ? r.json() : null; })
    .then(function (cfg) {
      if (!cfg) return;
      if (cfg.assistant_name) title.textContent = cfg.assistant_name;
      if (cfg.disclosure) disclosure.textContent = cfg.disclosure;
      if (cfg.consent_text) agreeLabel.textContent = cfg.consent_text;
    }, function () { /* keep the built-in disclosure */ });
})();
