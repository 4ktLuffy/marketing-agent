// Checks the notification formatter (n8nlib.NOTIFY_FORMAT_JS) as it ships: the code is read
// from the built workflow.json files, not copied here.
// Run: node tests/notify_format_test.js   (after build.py)
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const ROOT = path.resolve(__dirname, '../../..');
const nodeCode = (folder, name) => {
  const wf = JSON.parse(fs.readFileSync(path.join(ROOT, folder, 'workflow.json'), 'utf8'));
  const n = wf.nodes.find(x => x.name === name);
  assert.ok(n, `${folder}: no node "${name}"`);
  return n.parameters.jsCode;
};
// The whole Code node, as n8n runs it (all items mode).
const runNode = (code, items, env) =>
  new Function('$input', '$env', code)({ all: () => items.map(json => ({ json })) }, env);

const code43 = nodeCode('43-wf-error-handler', 'Notify format');
const notifyRequest = new Function(code43.slice(0, code43.indexOf('return $input.all()')) + '\nreturn notifyRequest;')();

const FORM = 'https://n8n.example.com/form/mkt-content-approval';
const base = { N8N_PUBLIC_URL: 'https://n8n.example.com', NOTIFY_WEBHOOK_URL: 'https://hooks.example/SECRET-HOOK' };
const TOKEN = '123456:FAKE-token_value';
const tg = { ...base, NOTIFY_WEBHOOK_URL: '', NOTIFY_FORMAT: 'telegram', TELEGRAM_BOT_TOKEN: TOKEN, TELEGRAM_CHAT_ID: ' -100200300 ' };
const sample = '*Content engine*: 2 drafted (1 in review) & <script>alert(1)</script>\n- #12 linkedin post → in_review\n' +
  'See [the report](https://example.com/r?a=1&b=2) or https://example.com/x @everyone';
let passed = 0;
const t = (name, fn) => { fn(); passed++; console.log(`ok - ${name}`); };
const noSecrets = obj => { const s = JSON.stringify(obj); assert.ok(!s.includes(TOKEN) && !s.includes('SECRET-HOOK'), `secret in ${s}`); };

t('generic is the payload from before NOTIFY_FORMAT', () => {
  for (const env of [base, { ...base, NOTIFY_FORMAT: '' }, { ...base, NOTIFY_FORMAT: 'teams' }]) {
    const r = notifyRequest(sample, env, false);
    assert.deepEqual(r.body, { text: sample, content: sample });
    assert.equal(r.url, '<NOTIFY_WEBHOOK_URL>');
  }
});

t('nothing configured: no request', () => {
  assert.equal(notifyRequest(sample, {}, true), null);
  assert.equal(notifyRequest(sample, { NOTIFY_FORMAT: 'slack' }, true), null);
  assert.equal(notifyRequest(sample, { ...base, NOTIFY_FORMAT: 'telegram' }, true), null);   // no token
  assert.equal(notifyRequest(sample, { ...tg, TELEGRAM_CHAT_ID: '' }, true), null);
  assert.deepEqual(runNode(code43, [{ text: 'x' }], {}), []);
});

t('slack: {text}, escaped, <url|label> links, approval link last', () => {
  const r = notifyRequest(sample, { ...base, NOTIFY_FORMAT: 'slack' }, true);
  assert.deepEqual(Object.keys(r.body), ['text']);
  const x = r.body.text;
  assert.ok(x.includes('&amp; &lt;script&gt;alert(1)&lt;/script&gt;'), x);
  assert.ok(!x.includes('<script'), x);
  assert.ok(x.includes('<https://example.com/r?a=1&amp;b=2|the report>'), x);
  assert.ok(x.endsWith(`<${FORM}|Open the approval form>`), x);
  noSecrets(r);
});

t('discord: {content} <= 2000 with the link kept, no mentions, bold', () => {
  const long = '*Weekly*\n' + 'word '.repeat(1500);
  const r = notifyRequest(long, { ...base, NOTIFY_FORMAT: 'discord' }, true);
  assert.ok(r.body.content.length <= 2000, r.body.content.length);
  assert.ok(r.body.content.length > 1900, 'cut too much');
  assert.ok(r.body.content.startsWith('**Weekly**'));
  assert.ok(r.body.content.includes('…'));
  assert.ok(r.body.content.endsWith(`Approval form: <${FORM}>`));
  assert.deepEqual(r.body.allowed_mentions, { parse: [] });
  const s = notifyRequest(sample, { ...base, NOTIFY_FORMAT: 'discord' }, false).body.content;
  assert.ok(s.includes('<https://example.com/x>') && !s.includes('mkt-content-approval'), s);
});

t('telegram: sendMessage on api.telegram.org, HTML-escaped, <= 4096, no token in output', () => {
  const r = notifyRequest(sample, tg, true);
  assert.equal(new URL(r.url.replace('<TELEGRAM_BOT_TOKEN>', 'T')).host, 'api.telegram.org');
  assert.equal(r.url, 'https://api.telegram.org/bot<TELEGRAM_BOT_TOKEN>/sendMessage');
  assert.deepEqual(Object.keys(r.body).sort(), ['chat_id', 'disable_web_page_preview', 'parse_mode', 'text']);
  assert.equal(r.body.chat_id, '-100200300');
  assert.equal(r.body.parse_mode, 'HTML');
  assert.equal(r.body.disable_web_page_preview, true);
  const x = r.body.text;
  assert.ok(x.startsWith('<b>Content engine</b>: 2 drafted (1 in review) &amp; &lt;script&gt;'), x);
  assert.ok(x.includes('<a href="https://example.com/r?a=1&amp;b=2">the report</a>'), x);
  assert.ok(x.endsWith(`<a href="${FORM}">Open the approval form</a>`), x);
  // Only our tags survive: every "<" starts <b>, </b>, <a href=, </a>.
  assert.ok(!/<(?!\/?b>|a href="|\/a>)/.test(x), x);
  noSecrets(r);
  const big = notifyRequest('<&>'.repeat(3000), tg, true).body.text;   // escaping grows the text 4x
  assert.ok(big.length <= 4096 && big.length > 4000, big.length);
  assert.ok(!/&[a-z]*…/.test(big), 'cut inside an entity');
  assert.ok(big.endsWith('Open the approval form</a>'));
  assert.equal(new URL(notifyRequest('x', { ...tg, TELEGRAM_API_URL: 'http://127.0.0.1:8199/' }, false)
    .url.replace('<TELEGRAM_BOT_TOKEN>', 'T')).host, '127.0.0.1:8199');
});

t('approval link: only when waiting, once, N8N_PUBLIC_URL with or without slash', () => {
  assert.ok(!notifyRequest('x', base, false).body.text.includes('mkt-content-approval'));
  assert.equal(notifyRequest('x', base, true).body.text, `x\n\nApproval form: ${FORM}`);
  assert.equal(notifyRequest('x', { ...base, N8N_PUBLIC_URL: 'https://n8n.example.com/' }, true).body.text, `x\n\nApproval form: ${FORM}`);
  assert.equal(notifyRequest(`Review: ${FORM}`, base, true).body.text, `Review: ${FORM}`);   // already ends with it
  const mid = `Review them: ${FORM}\n- #1 post`;   // link in the middle (65's summary): not added twice
  assert.equal(notifyRequest(mid, { ...base, NOTIFY_FORMAT: 'slack' }, true).body.text.split('mkt-content-approval').length - 1, 1);
  assert.ok(notifyRequest('x', { NOTIFY_WEBHOOK_URL: 'h' }, true).body.text.endsWith('http://localhost:5678/form/mkt-content-approval'));
});

t('node glue: one request per item; waits decided per item (65: drafted > 0)', () => {
  const code65 = nodeCode('65-wf-sched-engine-drafter', 'Notify format');
  const out = runNode(code65, [{ text: 'a', drafted: 2 }, { text: 'b', drafted: 0 }], { ...base, NOTIFY_FORMAT: 'slack' });
  assert.equal(out.length, 2);
  assert.ok(out[0].json.body.text.endsWith('|Open the approval form>'));
  assert.equal(out[1].json.body.text, 'b');
});

t('every notifying workflow uses the formatter', () => {
  for (const d of fs.readdirSync(ROOT).filter(d => /^\d\d-wf-/.test(d))) {
    const wf = JSON.parse(fs.readFileSync(path.join(ROOT, d, 'workflow.json'), 'utf8'));
    for (const n of wf.nodes) {
      const p = JSON.stringify(n.parameters);
      if (n.type === 'n8n-nodes-base.httpRequest' && p.includes('NOTIFY_WEBHOOK_URL')) {
        assert.ok(p.includes('<NOTIFY_WEBHOOK_URL>'), `${d}: "${n.name}" posts to NOTIFY_WEBHOOK_URL directly`);
        const src = Object.entries(wf.connections).find(([, o]) => (o.main || []).flat().some(c => c.node === n.name));
        assert.ok(src && src[0] === `${n.name} format`, `${d}: "${n.name}" is not fed by its formatter`);
      }
    }
  }
});

console.log(`${passed} passed`);
