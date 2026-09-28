"""Client report (85): a monthly report for a client, or for anyone who reports upward.

Last calendar month vs the month before, from the sources that are installed (20 analytics, with
the Umami and Search Console rows that 55 and 67 put there, 45 tracked posts, campaigns and
experiments, 84 paid ads). "What we did" and "What changed" are built in code. The LLM only
writes a short summary (prompt `client_report_summary`), and the summary is checked in code:
a sentence with a number that is not in the data, or one that claims a cause without hedging,
is dropped. The report goes to the calendar as `client_report` / `in_review`. Nothing is ever
sent to the client: the publisher (39) never sends `client_report` items, a person forwards it.
"""
from n8nlib import GATE_NOTIFY, Workflow, code, gateway, http, if_true, notify, schedule

# Pure: tests/client_report_test.js runs it. CLIENT_REPORT_MONTH=YYYY-MM reports that month.
MONTH = r"""
// Last calendar month (UTC) and the month before. CLIENT_REPORT_MONTH=YYYY-MM reports that month instead.
const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];
const want = String($env.CLIENT_REPORT_MONTH || '').trim();
const now = new Date();
let y = now.getUTCFullYear(), mo = now.getUTCMonth() - 1;
if (/^\d{4}-(0[1-9]|1[0-2])$/.test(want)) { y = Number(want.slice(0, 4)); mo = Number(want.slice(5, 7)) - 1; }
const iso = d => d.toISOString().slice(0, 10);
const first = new Date(Date.UTC(y, mo, 1)), last = new Date(Date.UTC(y, mo + 1, 0));
const pFirst = new Date(Date.UTC(y, mo - 1, 1)), pLast = new Date(Date.UTC(y, mo, 0));
const name = d => `${MONTHS[d.getUTCMonth()]} ${d.getUTCFullYear()}`;
return [{ json: { month: iso(first).slice(0, 7), label: name(first), from: iso(first), to: iso(last),
  prev_from: iso(pFirst), prev_to: iso(pLast), prev_label: name(pFirst), days: last.getUTCDate(),
  // 45 /insights/posts counts back from today: enough days to cover both months.
  since_days: Math.max(1, Math.ceil((now.getTime() - pFirst.getTime()) / 864e5) + 1) } }];
"""

# Pure: one GET per source whose service is installed. A source whose URL env is empty (not in
# this install profile) is skipped and named in the reviewer notes. Umami (55) and Search
# Console (67) rows live in 20 under source=umami / source=gsc.
SOURCES = r"""
const m = $('Month').first().json;
const url = v => String($env[v] || '').trim().replace(/\/+$/, '');
const q = o => Object.entries(o).map(([k, v]) => `${k}=${encodeURIComponent(v)}`).join('&');
const cur = { from: m.from, to: m.to, compare: 'false' }, prev = { from: m.prev_from, to: m.prev_to, compare: 'false' };
const SPEC = [
  ['brand', 'BRAND_URL', 'BRAND_URL', '/profile', 'brand profile (05)'],
  ['kpis', 'ANALYTICS_URL', 'ANALYTICS_URL', `/kpis?${q(cur)}`, 'analytics (20)'],
  ['kpis_prev', 'ANALYTICS_URL', 'ANALYTICS_URL', `/kpis?${q(prev)}`, 'analytics (20), previous month'],
  ['umami', 'UMAMI_SYNC_URL', 'ANALYTICS_URL', `/kpis?${q({ ...cur, source: 'umami' })}`, 'Umami visits (55)'],
  ['umami_prev', 'UMAMI_SYNC_URL', 'ANALYTICS_URL', `/kpis?${q({ ...prev, source: 'umami' })}`, 'Umami visits (55), previous month'],
  ['gsc', 'GSC_URL', 'ANALYTICS_URL', `/kpis?${q({ ...cur, source: 'gsc' })}`, 'Search Console (67)'],
  ['gsc_prev', 'GSC_URL', 'ANALYTICS_URL', `/kpis?${q({ ...prev, source: 'gsc' })}`, 'Search Console (67), previous month'],
  ['posts', 'CAMPAIGNS_URL', 'CAMPAIGNS_URL', `/insights/posts?days=${m.since_days}`, 'tracked posts (45)'],
  ['campaigns', 'CAMPAIGNS_URL', 'CAMPAIGNS_URL', '/campaigns', 'campaigns (45)'],
  ['experiments', 'CAMPAIGNS_URL', 'CAMPAIGNS_URL', '/experiments', 'experiments (45)'],
  ['ads', 'ADS_URL', 'ADS_URL', `/summary?${q({ days: m.days, end: m.to, top: 5 })}`, 'paid ads (84)'],
  ['items', 'CALENDAR_URL', 'CALENDAR_URL', `/items?${q({ status: 'published,approved' })}`, 'content calendar (19)'],
  ['existing', 'CALENDAR_URL', 'CALENDAR_URL', `/items?${q({ channel: 'client_report' })}`, 'earlier client reports (19)'],
];
const on = SPEC.filter(([, gate, base]) => url(gate) && url(base));
const skipped = SPEC.filter(s => !on.includes(s)).map(([, gate, base, , label]) => `${label}: ${url(gate) ? base : gate} is empty`);
if (!on.length) return [{ json: { key: 'none', label: 'nothing', url: 'http://127.0.0.1:1/health', skipped } }];
return on.map(([key, , base, path, label]) => ({ json: { key, label, url: url(base) + path, skipped } }));
"""

# Pure: tests/client_report_test.js runs it. Every number of the report is computed here; the
# LLM only gets finished sentences (`data`) and may quote them.
REPORT_DATA = r"""
const m = $('Month').first().json;
const src = $('Sources').all().map(i => i.json);
const res = $input.all().map(i => i.json || {});
const got = {}, failed = [];
const skipped = (src[0] || {}).skipped || [];
src.forEach((s, i) => {
  if (s.key === 'none') return;
  const r = res[i] || {};
  if (r.statusCode >= 200 && r.statusCode < 300 && r.body != null && typeof r.body === 'object') got[s.key] = r.body;
  else failed.push(`${s.label} (${r.statusCode || (r.error ? 'no response' : 'no answer')})`);
});
const listOf = b => Array.isArray(b) ? (b.length === 1 && Array.isArray(b[0]) ? b[0] : b).filter(x => x && typeof x === 'object') : [];
const num = v => typeof v === 'number' && isFinite(v) ? v : null;
const round = (n, d) => Math.round(n * 10 ** d) / 10 ** d;
const int = n => Math.round(n).toLocaleString('en-US');
const dec = n => Number.isInteger(n) ? n.toLocaleString('en-US') : round(n, 2).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const CUR = String($env.REPORT_CURRENCY || '').trim();
const F = { int, num: dec, money: n => `${CUR}${round(n, 2).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`,
  pct: n => `${round(n * 100, 2).toFixed(2)}%`, x: n => `${dec(n)}x` };
const inMonth = (d, from, to) => { const s = String(d || '').slice(0, 10); return s >= from && s <= to; };

// ---- What changed: one row per measure with a value in either month.
const rows = [];
const row = (group, label, kind, c, p, note) => {
  c = num(c); p = num(p);
  if ((c === null || c === 0) && (p === null || p === 0)) return;
  let change = 'n/a';
  if (c !== null && p !== null) {
    if (kind === 'pct') { const d = round((c - p) * 100, 2); change = `${d > 0 ? '+' : ''}${d.toFixed(2)} pts`; }
    else if (kind === 'x') { const d = round(c - p, 2); change = `${d > 0 ? '+' : ''}${dec(d)}`; }
    else {
      const d = c - p, rel = p ? round(d / p * 100, 1) : null;
      const abs = kind === 'money' ? F.money(Math.abs(d)) : kind === 'int' ? int(Math.abs(d)) : dec(round(Math.abs(d), 2));
      change = d === 0 ? 'no change' : `${d > 0 ? '+' : '-'}${abs}` + (rel !== null ? ` (${rel > 0 ? '+' : ''}${rel}%)` : ' (new)');
    }
  }
  rows.push({ group, label, value: c === null ? 'n/a' : F[kind](c), previous: p === null ? 'n/a' : F[kind](p), change, note: note || null });
};
const tot = k => (got[k] && got[k].totals) || null;
const k0 = tot('kpis'), k1 = tot('kpis_prev');
if (k0 || k1) {
  const a = k0 || {}, b = k1 || {};
  row('Website and channels', 'Sessions', 'int', a.sessions, b.sessions);
  row('Website and channels', 'Clicks', 'int', a.clicks, b.clicks);
  row('Website and channels', 'Impressions', 'int', a.impressions, b.impressions);
  row('Website and channels', 'Conversions', 'num', a.conversions, b.conversions);
  row('Website and channels', 'Conversion rate', 'pct', a.cvr, b.cvr);
  row('Website and channels', 'Spend', 'money', a.spend, b.spend);
  row('Website and channels', 'Cost per conversion', 'money', a.cpa, b.cpa);
}
const u0 = tot('umami'), u1 = tot('umami_prev');
if (u0 || u1) {
  row('Website visits (Umami)', 'Visits', 'int', (u0 || {}).sessions, (u1 || {}).sessions);
  row('Website visits (Umami)', 'Conversions', 'num', (u0 || {}).conversions, (u1 || {}).conversions);
}
const g0 = tot('gsc'), g1 = tot('gsc_prev');
if (g0 || g1) {
  row('Google search', 'Clicks from Google search', 'int', (g0 || {}).clicks, (g1 || {}).clicks);
  row('Google search', 'Impressions in Google search', 'int', (g0 || {}).impressions, (g1 || {}).impressions);
}
const posts = listOf((got.posts || {}).posts);
if (got.posts) {
  const cur = posts.filter(p => inMonth(p.posted_at, m.from, m.to)), prev = posts.filter(p => inMonth(p.posted_at, m.prev_from, m.prev_to));
  const clicks = l => l.reduce((n, p) => n + (num(p.clicks) || 0), 0);
  row('Tracked posts', 'Posts with a tracked link', 'int', cur.length, prev.length);
  row('Tracked posts', 'Clicks on those posts (to date)', 'int', clicks(cur), clicks(prev),
      'counted to today, so the earlier month had more time to collect clicks');
}
const ads = got.ads && got.ads.has_data ? got.ads : null;
if (ads) {
  const w = (ads.window || {}).previous || {};
  for (const t of listOf(ads.total)) {
    const c = t.current || {}, p = t.previous || {}, cu = t.currency || '';
    const g = `Paid ads (${cu})`, note = `previous = ${w.start} to ${w.end}, the same number of days before`;
    const money = (label, kc, kp) => { const c2 = num(kc), p2 = num(kp);
      if ((c2 || 0) === 0 && (p2 || 0) === 0) return;
      const d = c2 !== null && p2 !== null ? c2 - p2 : null, rel = d !== null && p2 ? round(d / p2 * 100, 1) : null;
      rows.push({ group: g, label, value: c2 === null ? 'n/a' : `${dec(round(c2, 2))} ${cu}`, previous: p2 === null ? 'n/a' : `${dec(round(p2, 2))} ${cu}`,
        change: d === null ? 'n/a' : d === 0 ? 'no change' : `${d > 0 ? '+' : '-'}${dec(round(Math.abs(d), 2))} ${cu}` + (rel !== null ? ` (${rel > 0 ? '+' : ''}${rel}%)` : ' (new)'), note }); };
    money('Ad spend', c.spend, p.spend);
    row(g, 'Ad conversions', 'num', c.conversions, p.conversions, note);
    money('Cost per lead (CPL)', c.cpl, p.cpl);
    if (num(c.roas) !== null || num(p.roas) !== null) row(g, 'Return on ad spend (ROAS)', 'x', c.roas, p.roas, note);
  }
}

// ---- What we did: calendar work shipped in the month, campaigns, experiments. No LLM.
const INTERNAL = ['client_report', 'blog_refresh', 'seo_brief', 'competitor_brief', 'visibility_gap', 'positioning', 'lead_reply'];
const done = listOf(got.items).filter(i => !INTERNAL.includes(String(i.channel).toLowerCase()))
  .filter(i => i.status === 'published' ? inMonth(i.published_at, m.from, m.to)
    : i.status === 'approved' && inMonth(i.scheduled_at || i.updated_at, m.from, m.to));
const byCh = {};
for (const i of done) (byCh[String(i.channel).toLowerCase()] ||= []).push(i);
const chans = Object.entries(byCh).sort((a, b) => b[1].length - a[1].length || a[0].localeCompare(b[0]));
const campaigns = listOf(got.campaigns).filter(c => ['active', 'paused', 'completed'].includes(c.status))
  .filter(c => (!c.start_date || String(c.start_date) <= m.to) && (!c.end_date || String(c.end_date) >= m.from));
const exps = listOf(got.experiments);
const decided = exps.filter(e => e.status === 'decided' && inMonth(e.decided_at, m.from, m.to));
const running = exps.filter(e => e.status === 'running');
const verdict = e => e.decision === 'winner' && e.winner && e.loser ? `${e.winner.value} beat ${e.loser.value}`
  : e.decision === 'no_practical_difference' ? 'no practical difference' : 'inconclusive';

const work = [];
if (got.items) work.push(done.length ? `Published or approved in ${m.label}: ${done.length} piece${done.length === 1 ? '' : 's'} (${chans.map(([c, l]) => `${c} ${l.length}`).join(', ')}).`
  : `Nothing was published or approved in ${m.label}.`);
for (const [c, l] of chans) work.push(`${c}: ${l.slice(0, 5).map(i => `"${String(i.title).slice(0, 90)}"`).join(', ')}${l.length > 5 ? ` and ${l.length - 5} more` : ''}.`);
const kpiText = c => (c.kpis || []).filter(k => num(k.actual_value) !== null)
  .map(k => `${k.metric} ${dec(k.actual_value)}` + (num(k.target_value) !== null ? ` of a ${dec(k.target_value)} target` : '')).join(', ');
for (const c of campaigns.slice(0, 6)) work.push(`Campaign "${c.name}" (${c.status}${c.start_date ? `, ${c.start_date} to ${c.end_date || 'open'}` : ''})${kpiText(c) ? `: ${kpiText(c)} so far` : ''}.`);
for (const e of decided.slice(0, 4)) work.push(`Experiment on ${e.variable} (${(e.channels || []).join(', ')}): ${verdict(e)}.`);
if (running.length) work.push(`${running.length} experiment${running.length === 1 ? '' : 's'} still running.`);

const facts = rows.map(r => `${r.group}, ${r.label}: ${r.value} in ${m.label}, ${r.previous} in ${m.prev_label}, change ${r.change}.`);
const brand = String((got.brand || {}).name || $env.CLIENT_NAME || '').trim();
const data = JSON.stringify({ client: brand || null, month: m.label, previous_month: m.prev_label, facts, work });

// ---- Markdown the client reads (the summary goes above it after the check).
const cell = s => String(s).replace(/\|/g, '\\|').replace(/\n/g, ' ');
const workMd = [`### What we did in ${m.label}`, '',
  ...(work.length ? work.map(w => `- ${w}`) : ['- No work log available for this month.'])].join('\n');
const notes = [...new Set(rows.filter(r => r.note).map(r => r.note))];
const changeMd = [`### What changed: ${m.label} vs ${m.prev_label}`, '',
  ...(rows.length ? [`| Measure | ${m.label} | ${m.prev_label} | Change |`, '|---|---:|---:|---:|',
    ...rows.map(r => `| ${cell(r.group + ': ' + r.label)} | ${cell(r.value)} | ${cell(r.previous)} | ${cell(r.change)} |`),
    ...(notes.length ? ['', ...notes.map(n => `_${n}._`)] : [])]
    : ['No numbers were available for this month.'])].join('\n');

// 21 wants 20's /kpis shape for the tiles: the month, the month before, and the change in %.
let kpis = null;
if (k0) {
  const delta = {};
  for (const [k, v] of Object.entries(k0)) {
    const p = (k1 || {})[k];
    delta[k] = num(v) !== null && num(p) ? round((v - p) / p * 100, 1) : null;
  }
  kpis = { period: { from: m.from, to: m.to }, totals: k0, by_channel: (got.kpis.by_channel || []),
    previous: k1 ? { period: { from: m.prev_from, to: m.prev_to }, ...k1 } : null, delta_pct: k1 ? delta : null };
}
// One report per month; a rejected one does not count, so rejecting it and running again rebuilds it.
const existing = listOf(got.existing).find(i => String(i.title) === `Client report ${m.label}` && i.status !== 'rejected') || null;
return [{ json: { month: m.month, label: m.label, prev_label: m.prev_label, from: m.from, to: m.to, brand,
  rows, work, facts, data, work_md: workMd, change_md: changeMd, kpis, existing_id: existing ? existing.id : null,
  sources_used: Object.keys(got), sources_skipped: skipped, sources_failed: failed } }];
"""

# Pure: tests/client_report_test.js runs it. The LLM's summary, checked in code, not trusted:
# a sentence with a number that is not in the data is dropped (41's check: thousands separators
# and the sign are ignored), and so is one that claims a cause without hedging. What was
# dropped goes to the reviewer notes, never to the client text. LLM failed: tables only.
CHECK_NARRATIVE = r"""
const d = $('Report data').first().json;
const g = $input.first().json || {};
const nums = s => (String(s).replace(/(\d),(?=\d{3}\b)/g, '$1').match(/\d+(?:\.\d+)?/g) || []).map(Number);
// ISO dates are checked whole, so the day and month parts of a date never excuse a count.
const DATE = /\b\d{4}-\d{2}-\d{2}\b/g;
const dates = new Set(String(d.data).match(DATE) || []);
const known = new Set(nums(String(d.data).replace(DATE, ' ')));
const dataText = String(d.data).toLowerCase();
// Spelled-out counts are numbers too; "doubled" or "twice" is a computed claim the data never states.
const WORDS = { two: 2, three: 3, four: 4, five: 5, six: 6, seven: 7, eight: 8, nine: 9, ten: 10, eleven: 11, twelve: 12, twenty: 20, hundred: 100, thousand: 1000 };
const MULT = /\b(doubl\w*|tripl\w*|quadrupl\w*|halv\w*|twice|thrice)\b/i;
const unknownNums = s => {
  const t = String(s).toLowerCase();
  const out = (t.match(DATE) || []).filter(x => !dates.has(x));
  out.push(...nums(t.replace(DATE, ' ')).filter(x => !known.has(x)));
  for (const [w, n] of Object.entries(WORDS)) if (new RegExp(`\\b${w}\\b`).test(t) && !known.has(n)) out.push(w);
  const mm = t.match(MULT);
  if (mm) out.push(mm[1]);
  // "2x" or "3 times" is a ratio the model worked out, unless the data states it (ROAS "2.40x").
  for (const x of t.matchAll(/(\d+(?:\.\d+)?)\s*(?:x|times)(?![\w-])/g)) if (!dataText.includes(`${x[1]}x`)) out.push(x[0]);
  return out;
};
// A cause stated as a fact. Hedged ("may have", "we can't tell yet") is fine.
const CAUSAL = /(?<![\w-])(because|due to|thanks to|owing to|as a result|therefore|hence|result(?:s|ed)? in|(?:the )?result of|led to|lead(?:s|ing)? to|caus(?:e|es|ed|ing)|dr(?:ove|ives?|iven|iving)|boost(?:s|ed|ing)?|help(?:s|ed)?|contribut(?:e|es|ed|ing) to|responsible for|attribut\w*|fuel(?:l?ed|s)?|explains?|which is why)(?![\w-])/i;
const HEDGE = /(?<![\w-])(may|might|could|possibly|perhaps|likely|probably|can'?t tell|cannot tell|can not tell|unclear|not (?:yet )?clear|too early|hard to say|not sure|don'?t know yet|do not know yet)(?![\w-])/i;
// The right number with the wrong direction ("clicks fell" when they rose). Conservative: only a
// sentence that names ONE measure, whose rows all move the same way, with direction words of one
// kind, and that is not about the future. Most specific names first; each maps to row labels.
const MEASURES = [
  [/cost per (?:lead|conversion)|\bcpl\b|\bcpa\b/, /^(Cost per lead|Cost per conversion)/],
  [/\broas\b|return on ad spend/, /^Return on ad spend/],
  [/ad conversions|conversions from ads/, /^Ad conversions$/],
  [/ad spend|spend on ads|\bspend\b|\bspent\b/, /^(Ad spend|Spend)$/],
  [/conversion rate/, /^Conversion rate$/],
  [/(?:google |search )(?:search )?clicks|clicks from (?:google|search)/, /^Clicks from Google search$/],
  [/(?:google |search )impressions|impressions in (?:google|search)/, /^Impressions in Google search$/],
  [/\bconversions?\b/, /^(Conversions|Ad conversions)$/],
  [/\bclicks?\b/, /^(Clicks|Clicks from Google search|Clicks on those posts.*)$/],
  [/\bimpressions?\b/, /^(Impressions|Impressions in Google search)$/],
  [/\bsessions?\b/, /^Sessions$/],
  [/\bvisits?\b|\bvisitors?\b/, /^Visits$/],
];
const UP = /(?<![\w-])(rose|risen|rising|grew|grown|growing|increas\w*|went up|go(?:es)? up|up by|higher|climb\w*|gain\w*)(?![\w-])/;
const DOWN = /(?<![\w-])(fell|fallen|falling|dropp?\w*|declin\w*|decreas\w*|went down|go(?:es)? down|down by|lower|fewer|less|reduc\w*|cut|dipped|shr[ai]nk\w*)(?![\w-])/;
const FLAT = /(?<![\w-])(stayed (?:about |roughly )?the same|unchanged|held steady|flat|no change)(?![\w-])/;
const FUTURE = /(?<![\w-])(will|next month|aim|plan\w*|goal|target|want|hope|should|try|expect)(?![\w-])/;
const rowsOf = d.rows || [];
const wrongDirection = s => {
  const t = s.toLowerCase();
  if (FUTURE.test(t)) return null;
  let rest = t; const hit = [];
  for (const [re, lab] of MEASURES) if (re.test(rest)) { hit.push(lab); rest = rest.replace(new RegExp(re.source, 'g'), ' '); }
  if (hit.length !== 1) return null;
  const said = [UP.test(t) && 'up', DOWN.test(t) && 'down', FLAT.test(t) && 'flat'].filter(Boolean);
  if (said.length !== 1) return null;
  const rs = rowsOf.filter(r => hit[0].test(r.label) && /^[+-]|^no change/.test(r.change));
  if (!rs.length) return null;
  const sign = r => r.change === 'no change' ? 0 : r.change[0] === '+' ? 1 : -1;
  if (new Set(rs.map(sign)).size !== 1) return null;
  const sg = sign(rs[0]), rel = (rs[0].change.match(/\(([+-]?\d+(?:\.\d+)?)%\)/) || [])[1];
  const bad = said[0] === 'up' ? sg <= 0 : said[0] === 'down' ? sg >= 0
    : sg !== 0 && rel !== undefined && Math.abs(Number(rel)) >= 5;
  return bad ? `says ${said[0]}; the data says ${rs[0].change} (${rs[0].label})` : null;
};
const o = g.output && typeof g.output === 'object' ? g.output : null;
const raw = o && Array.isArray(o.summary) ? o.summary : [];
const sentences = raw.flatMap(s => String(s || '').replace(/\s+/g, ' ').trim().split(/(?<=[.!?])\s+(?=["'(A-Z0-9])/)).map(s => s.trim()).filter(Boolean);
const kept = [], dropped = [];
for (const s0 of sentences) {
  const s = s0.replace(/[‘’]/g, "'");
  const bad = unknownNums(s);
  if (bad.length) { dropped.push({ sentence: s0, reason: 'number', detail: `not in the data: ${bad.slice(0, 5).join(', ')}` }); continue; }
  const w = wrongDirection(s);
  if (w) { dropped.push({ sentence: s0, reason: 'direction', detail: w }); continue; }
  const c = s.match(CAUSAL);
  if (c && !HEDGE.test(s)) { dropped.push({ sentence: s0, reason: 'cause', detail: `claims a cause ("${c[1]}") without hedging` }); continue; }
  kept.push(s0);
}
// A client reads 3 to 5 sentences; the rest is cut (and counted in the notes).
const MAX = 5, cut = Math.max(0, kept.length - MAX);
kept.splice(MAX);
const count = r => dropped.filter(x => x.reason === r).length;
const failed = !o;
const why = failed ? String((g.error && (g.error.message || g.error)) || 'no output').slice(0, 200) : '';
const summaryMd = kept.length ? kept.join(' ') : '';
const highlights = [summaryMd, d.work_md, d.change_md].filter(Boolean).join('\n\n');
const noteLines = [
  `client report ${d.month}`,
  failed ? `summary: the LLM failed (${why}); the report has the tables only`
    : `summary: ${sentences.length} sentence(s) written, ${kept.length} kept, ${count('number')} dropped for a number not in the data, ${count('direction')} for the wrong direction, ${count('cause')} for an unhedged cause`,
  ...(cut ? [`summary cut to ${MAX} sentences (${cut} more were fine but too many)`] : []),
  ...dropped.map(x => `dropped (${x.detail}): ${x.sentence.slice(0, 300)}`),
  `sources used: ${d.sources_used.join(', ') || 'none'}`,
  ...(d.sources_skipped.length ? [`sources skipped (not installed): ${d.sources_skipped.join('; ')}`] : []),
  ...(d.sources_failed.length ? [`sources failed: ${d.sources_failed.join('; ')}`] : []),
  'Never sent to the client automatically: after approval, forward it yourself.',
];
return [{ json: { kept, dropped, cut, produced: sentences.length, dropped_number: count('number'), dropped_direction: count('direction'), dropped_cause: count('cause'),
  llm_failed: failed, summary_md: summaryMd, highlights_md: highlights, notes: noteLines.join('\n') } }];
"""

# Pure: the calendar item (or why none is saved) and the owner's notification text.
CALENDAR_ITEM = r"""
const d = $('Report data').first().json, c = $('Check narrative').first().json;
const r = $input.first().json || {};
const rendered = r.statusCode >= 200 && r.statusCode < 300 && r.body && typeof r.body.markdown === 'string' ? r.body : null;
const title = `Client report ${d.label}`;
const heading = `# ${d.brand ? d.brand + ': ' : ''}monthly report, ${d.label}`;
const body = rendered ? rendered.markdown : [heading, '', c.highlights_md].join('\n');
const notes = c.notes + (rendered ? '' : `\nreport-builder (21) failed (${r.statusCode || 'no response'}): the body is the plain markdown`);
if (d.existing_id) return [{ json: { save: false, title, text: `*${title}*: already in the calendar (#${d.existing_id}); not made again. Reject it to rebuild it.` } }];
if (!$env.CALENDAR_URL) return [{ json: { save: false, title, text: `*${title}*: CALENDAR_URL is not set; nothing saved.` } }];
return [{ json: { save: true, title, item: { title, channel: 'client_report', status: 'in_review', body, notes } } }];
"""

SUMMARY = r"""
const d = $('Report data').first().json, c = $('Check narrative').first().json, it = $('Calendar item').first().json;
if (!it.save) return [{ json: { text: it.text, saved: 0 } }];
const s = $input.first().json || {};
if (!s.id) return [{ json: { text: `*${it.title}*: saving to the calendar failed (${String((s.error && (s.error.message || s.error)) || 'no id').slice(0, 200)}).`, saved: 0 } }];
const sum = c.llm_failed ? 'summary: the LLM failed, tables only'
  : `summary: ${c.kept.length} of ${c.produced} sentences kept` + (c.dropped.length ? ` (${c.dropped_number} dropped for a number not in the data, ${c.dropped_direction} for the wrong direction, ${c.dropped_cause} for an unhedged cause)` : '');
return [{ json: { saved: 1, text: [`*${it.title}*${d.brand ? ` for ${d.brand}` : ''} is waiting for review (#${s.id}): ${d.rows.length} numbers, ${d.work.length} work lines, ${sum}.`,
  d.sources_skipped.length ? `Skipped (not installed): ${d.sources_skipped.length} source(s).` : '',
  d.sources_failed.length ? `Failed: ${d.sources_failed.join('; ')}.` : '',
  'Nothing was sent to the client. Approve it, then forward it yourself.'].filter(Boolean).join('\n') } }];
"""


def wf85():
    wf = Workflow(85, "Schedule · Monthly client report")
    t = schedule(wf, "1st of the month 08:00", "0 8 1 * *")
    mo = code(wf, "Month", MONTH)
    so = code(wf, "Sources", SOURCES)
    # One GET per installed source; a failure stays an item so Report data can pair them by index.
    fe = wf.add("Fetch", "n8n-nodes-base.httpRequest", 4.2, {
        "method": "GET", "url": "={{ $json.url }}",
        "sendHeaders": True,
        "headerParameters": {"parameters": [{"name": "X-API-Key", "value": "={{ $env.INTERNAL_API_KEY }}"}]},
        "options": {"response": {"response": {"neverError": True, "fullResponse": True}}, "timeout": 120000},
    }, onError="continueRegularOutput")
    rd = code(wf, "Report data", REPORT_DATA)
    g = gateway(wf, "Summary draft", "client_report_summary",
                "{ data: $json.data, month: $json.label, previous_month: $json.prev_label }", continue_on_fail=True)
    ck = code(wf, "Check narrative", CHECK_NARRATIVE)
    rr = http(wf, "Render report", "POST", "={{ $env.REPORT_URL }}/render",
              "={{ JSON.stringify({ title: ($('Report data').first().json.brand ? $('Report data').first().json.brand + ': ' : '') + 'monthly report, ' + $('Report data').first().json.label, "
              "period: { from: $('Report data').first().json.from, to: $('Report data').first().json.to }, "
              "kpis: $('Report data').first().json.kpis || {}, highlights_markdown: $json.highlights_md, "
              "currency: $env.REPORT_CURRENCY || '' }) }}",
              never_error=True, continue_on_fail=True, full_response=True, timeout=60000)
    ci = code(wf, "Calendar item", CALENDAR_ITEM)
    sv_ = if_true(wf, "Save?", "={{ $json.save }}")
    sv = http(wf, "Save for review", "POST", "={{ $env.CALENDAR_URL }}/items", "={{ JSON.stringify($json.item) }}",
              key=True, continue_on_fail=True)
    sm = code(wf, "Summary", SUMMARY)
    gn = code(wf, "Webhook set?", GATE_NOTIFY)
    n = notify(wf, "Notify", "$json.text", waits="$json.saved > 0")
    wf.chain(t, mo, so, fe, rd, g, ck, rr, ci, sv_)
    wf.link(sv_, sv, src_index=0)
    wf.link(sv_, sm, src_index=1)
    wf.chain(sv, sm, gn, n)
    return wf, {
        "summary": "On the 1st of every month it builds a report on the month before, for a client (agencies) or for whoever the owner reports to. It compares the last calendar month with the month before, from every source that is installed: analytics (20 `/kpis`, all sources, plus the Umami rows of 55 and the Search Console rows of 67 when `UMAMI_SYNC_URL` / `GSC_URL` are set), tracked posts and their clicks (45 `/insights/posts`), campaigns and experiments (45), and paid ads (84 `/summary`, when `ADS_URL` is set). A source whose URL is empty (not installed on this profile) is skipped; one that fails is left out. Both are named in the reviewer notes.\n\n**What we did** (no LLM): the calendar items published or approved in that month, counted by channel with their titles, the campaigns that were active (with their KPI actuals against targets) and the experiments decided. **What changed** (no LLM): a table with the value, the previous value and the change for each measure, computed in code.\n\n**Summary**: the LLM (prompt `client_report_summary`) writes 3 to 5 short sentences for the client from those finished sentences. It is **checked in code, not trusted**: a sentence with a number that is not in the data is dropped (the same check as 41: thousands separators and the sign are ignored; spelled-out counts and \"doubled\" count as numbers); so is a sentence that names one measure and says it went the wrong way (\"clicks fell\" when they rose; checked only when that measure's rows all moved the same way, and not for plans), and a sentence that claims a cause (\"because\", \"led to\", \"drove\", \"thanks to\" ...) without hedging (\"may\", \"might\", \"likely\", \"we can't tell yet\" ...). The counts and the dropped sentences go to the reviewer notes, never to the client text. If the LLM fails, the report has the tables only.\n\nThe report is rendered by 21 with the brand name from 05 and saved to the calendar (19) as channel `client_report`, status `in_review`, title `Client report <Month YYYY>`, so it goes through the approval form and the control room like everything else. A month that already has a report is not made again. **Nothing is ever sent to the client**: the publisher (39) never sends `client_report` items, even approved. The owner gets a notification (`NOTIFY_WEBHOOK_URL`); a person downloads the approved report from the control room (**Download**, one HTML file) and forwards it.",
        "schedule": "1st of every month, 08:00",
        "env": {"CLIENT_REPORT_MONTH": "optional `YYYY-MM`: report that month instead of the last one (set it, run the workflow by hand, then clear it)",
                "REPORT_CURRENCY": "optional prefix for money from analytics (20), e.g. `EUR ` or `$`; ad money always carries its own currency",
                "CLIENT_NAME": "optional; used when the brand profile (05) has no name",
                "UMAMI_SYNC_URL, GSC_URL, ADS_URL": "optional sources; empty = skipped",
                "NOTIFY_WEBHOOK_URL": "optional; the owner's notification (never the client)"},
        "setup": "To rebuild a month, reject its `Client report <Month YYYY>` item and run the workflow again (with `CLIENT_REPORT_MONTH` set, for an older month).\n\nThe calendar body is the report as markdown (tiles table, summary, what we did, what changed). The HTML page from 21 is in the *Render report* node of the run.",
        "depends": ["03-llm-gateway", "05-brand-service", "19-content-calendar", "20-analytics-ingest", "21-report-builder",
                    "45-campaign-service", "55-umami-sync, 67-gsc-sync, 84-ads-sync (optional)"],
    }


ALL_CLIENT_REPORT = [wf85]
