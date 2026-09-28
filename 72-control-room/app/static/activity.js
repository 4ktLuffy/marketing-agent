// Activity page: polls /activity/data every 4 s (paused while the tab is hidden), ticks the
// running calls' seconds, filters the timeline, and draws sparklines. No inline script (CSP),
// no library. Text is always set with textContent. The helpers are pure and tested in node.
(function () {
  'use strict';
  const POLL_MS = 4000;

  // ---------------------------------------------------------------- pure helpers
  function seconds(ms) {
    if (ms === null || ms === undefined || isNaN(ms)) return '–';
    const s = ms / 1000;
    return s < 60 ? `${s.toFixed(1)} s` : `${Math.floor(s / 60)} min ${Math.floor(s % 60)} s`;
  }
  function ticking(ms) {   // a running call: whole seconds, calm to read
    const s = Math.max(0, Math.floor(ms / 1000));
    return s < 60 ? `${s} s` : `${Math.floor(s / 60)} min ${String(s % 60).padStart(2, '0')} s`;
  }
  function pad(n) { return String(n).padStart(2, '0'); }
  function hm(d) { return `${pad(d.getHours())}:${pad(d.getMinutes())}`; }
  function dayKey(d) { return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`; }
  function dayLabel(d, now) {
    const y = new Date(now.getTime()); y.setDate(y.getDate() - 1);
    if (dayKey(d) === dayKey(now)) return 'Today';
    if (dayKey(d) === dayKey(y)) return 'Yesterday';
    return d.toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', month: 'short' });
  }
  function groupByDay(events, now) {   // keeps order (newest first); local days
    const out = [];
    for (const e of events) {
      const d = new Date(e.at);
      const key = isNaN(d) ? '?' : dayKey(d);
      if (!out.length || out[out.length - 1].key !== key) {
        out.push({ key, label: isNaN(d) ? 'Unknown day' : dayLabel(d, now), events: [] });
      }
      out[out.length - 1].events.push(e);
    }
    return out;
  }
  function filterEvents(events, kind, ability) {
    let ev = ability === null || ability === undefined ? events : events.filter(e => e.ability === ability);
    if (kind === 'ai') ev = ev.filter(e => e.kind === 'ai');
    else if (kind === 'content') ev = ev.filter(e => e.kind === 'content');
    else if (kind === 'errors') ev = ev.filter(e => e.kind === 'ai' && !e.ok);
    return ev;
  }
  function sparkPoints(values, w, h) {
    w = w || 120; h = h || 28;
    const v = (values || []).filter(x => typeof x === 'number' && isFinite(x));
    if (v.length < 2) return '';
    const top = Math.max.apply(null, v) || 1, step = w / (v.length - 1);
    return v.map((x, i) => `${(i * step).toFixed(1)},${(h - 2 - (x / top) * (h - 4)).toFixed(1)}`).join(' ');
  }
  function ago(iso, now) {
    const d = new Date(iso);
    if (!iso || isNaN(d)) return '';
    const s = Math.round((now - d) / 1000);
    if (s < 60) return 'just now';
    if (s < 3600) return `${Math.floor(s / 60)} min ago`;
    if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
    return `${Math.floor(s / 86400)} d ago`;
  }

  const H = { seconds, ticking, dayLabel, groupByDay, filterEvents, sparkPoints, ago, hm };
  if (typeof module === 'object' && module.exports) { module.exports = H; return; }
  if (typeof document === 'undefined') return;

  // ---------------------------------------------------------------- page
  const $ = id => document.getElementById(id);
  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined && text !== null) e.textContent = text;
    return e;
  }
  function badge(local) { return el('span', `badge ${local ? 'local' : 'hosted'}`, local ? 'local' : 'hosted'); }

  let data = null, fetchedAt = Date.now(), timer = null, failed = 0;
  const params = new URLSearchParams(location.search);
  let filter = params.get('filter') || 'all';
  let ability = /^\d+$/.test(params.get('ability') || '') ? parseInt(params.get('ability'), 10) : null;
  let nowIds = null;

  function renderSources() {
    const box = $('act-sources'); box.replaceChildren();
    for (const s of data.sources) {
      const li = el('li', s.ok ? 'ok' : 'bad');
      const dot = el('span', 'dot'); dot.setAttribute('aria-hidden', 'true');
      li.append(dot, s.ok ? s.name : `${s.name}: ${s.detail}`);
      box.append(li);
    }
  }

  function renderNow() {
    const box = $('act-now');
    const ids = data.now.map(c => c.id).join('|');
    if (ids === nowIds) { tick(); return; }   // same calls: only the seconds change (no live-region noise)
    nowIds = ids;
    box.replaceChildren();
    if (!data.now.length) {
      const p = el('p', 'idle'); const dot = el('span', 'dot idle-dot'); dot.setAttribute('aria-hidden', 'true');
      p.append(dot, 'Idle — nothing running.'); box.append(p); return;
    }
    for (const c of data.now) {
      const card = el('article', 'now-card'); card.dataset.id = c.id;
      const top = el('div', 'row-between'); top.append(el('b', null, c.who), badge(c.local));
      const sec = el('div', 'elapsed'); sec.dataset.elapsed = c.elapsed_ms; sec.setAttribute('aria-hidden', 'true');
      card.append(top, el('div', 'small muted', `${c.prompt} · ${c.model}`), sec);
      box.append(card);
    }
    tick();
  }
  function tick() {
    const extra = Date.now() - fetchedAt;
    document.querySelectorAll('#act-now .elapsed').forEach(e => { e.textContent = ticking(+e.dataset.elapsed + extra); });
  }

  function renderTimeline() {
    const box = $('act-timeline'); box.replaceChildren();
    const now = new Date();
    const events = filterEvents(data.timeline, filter, ability);
    if (!events.length) {
      box.append(el('p', 'muted', filter === 'all' && ability === null ? 'Nothing yet. AI calls appear here as they finish.' : 'Nothing for this filter yet.'));
    }
    for (const g of groupByDay(events, now)) {
      box.append(el('h3', 'day', g.label));
      const ol = el('ol', 'tl');
      for (const e of g.events) {
        const li = el('li', `tl-row ${e.kind}${e.ok ? '' : ' bad'}`);
        const t = el('time', null, isNaN(new Date(e.at)) ? '' : hm(new Date(e.at))); t.dateTime = e.at || '';
        const icon = el('span', 'tl-icon', e.icon); icon.setAttribute('aria-hidden', 'true');
        const txt = el('span', 'tl-text');
        if (e.link && /^\/items\/\d+$/.test(e.link)) { const a = el('a', null, e.text); a.href = e.link; txt.append(a); } else txt.textContent = e.text;
        li.append(t, icon, txt); ol.append(li);
      }
      box.append(ol);
    }
    document.querySelectorAll('#act-filters a').forEach(a => {
      if (a.dataset.filter === filter) a.setAttribute('aria-current', 'true'); else a.removeAttribute('aria-current');
    });
    const tile = ability === null ? null : document.querySelector(`#act-abilities [data-ability="${ability}"]`);
    $('act-ability').hidden = ability === null;
    $('act-ability-name').textContent = tile ? tile.dataset.name : '';
    document.querySelectorAll('#act-abilities .tile').forEach(t => {
      if (t === tile) t.setAttribute('aria-current', 'true'); else t.removeAttribute('aria-current');
    });
  }

  function stat(label, value, cls) {
    const d = el('div'); const dd = el('dd', cls || null, String(value));
    d.append(el('dt', null, label), dd); return d;
  }
  function renderModels() {
    const box = $('act-models'); box.replaceChildren();
    if (!data.models.length) { box.append(el('p', 'muted', 'No model calls yet today.')); return; }
    for (const m of data.models) {
      const card = el('article', 'model-card');
      const top = el('div', 'row-between'); top.append(el('b', 'model-name', m.model), badge(m.local));
      const dl = el('dl', 'stats');
      dl.append(stat('Calls', m.calls_today), stat('Failed', m.failures_today, m.failures_today ? 'bad-text' : ''),
        stat('Average', seconds(m.avg_ms)), stat('p95', seconds(m.p95_ms)),
        stat('Tokens in', m.tokens_in), stat('Tokens out', m.tokens_out));
      const ns = 'http://www.w3.org/2000/svg';
      const svg = document.createElementNS(ns, 'svg');
      svg.setAttribute('class', 'spark'); svg.setAttribute('viewBox', '0 0 120 28');
      svg.setAttribute('preserveAspectRatio', 'none'); svg.setAttribute('role', 'img');
      svg.setAttribute('aria-label', `time per call, last ${m.spark.length} calls`);
      const pl = document.createElementNS(ns, 'polyline'); pl.setAttribute('points', sparkPoints(m.spark));
      svg.append(pl);
      card.append(top, el('div', 'small muted', m.provider), dl, svg);
      box.append(card);
    }
  }

  function renderAbilities() {   // tiles are server-rendered; only their "last" line changes
    const now = new Date();
    for (const a of data.abilities) {
      const span = document.querySelector(`#act-abilities [data-ability="${a.num}"] .tile-last`);
      if (span) span.textContent = a.last_at ? ` · last ${ago(a.last_at, now)}, ${a.last_text}` : '';
    }
  }

  function render() {
    fetchedAt = Date.now();
    renderSources(); renderNow(); renderTimeline(); renderModels(); renderAbilities();
    const u = $('act-updated'); u.dateTime = data.updated_at; u.textContent = hm(new Date(data.updated_at));
    $('act-poll').textContent = failed ? 'last update failed; retrying' : 'live, every 4 s';
  }

  async function poll() {
    clearTimeout(timer);
    try {
      const q = new URLSearchParams({ filter: 'all' });
      const r = await fetch(`/activity/data?${q}`, { credentials: 'same-origin', headers: { Accept: 'application/json' } });
      if (r.status === 401) { $('act-poll').textContent = 'logged out: reload the page'; return; }
      if (!r.ok) throw new Error(String(r.status));
      data = await r.json(); failed = 0; render();
    } catch (e) {
      failed += 1; $('act-poll').textContent = 'last update failed; retrying';
    }
    if (!document.hidden) timer = setTimeout(poll, POLL_MS);
  }

  function setUrl() {
    const q = new URLSearchParams();
    if (filter !== 'all') q.set('filter', filter);
    if (ability !== null) q.set('ability', String(ability));
    history.replaceState(null, '', `/activity${q.toString() ? '?' + q : ''}#timeline`);
  }

  document.addEventListener('click', e => {
    const f = e.target.closest('#act-filters a[data-filter]');
    const t = e.target.closest('#act-abilities .tile');
    const clear = e.target.closest('[data-ability-clear]');
    if (!data || !(f || t || clear) || e.metaKey || e.ctrlKey || e.shiftKey) return;
    e.preventDefault();
    if (f) filter = f.dataset.filter;
    if (t) { ability = parseInt(t.dataset.ability, 10); filter = 'all'; }
    if (clear) ability = null;
    setUrl(); renderTimeline();
    if (t) $('timeline').scrollIntoView({ behavior: 'smooth', block: 'start' });
  });

  document.addEventListener('visibilitychange', () => {
    if (document.hidden) { clearTimeout(timer); $('act-poll').textContent = 'paused while hidden'; }
    else poll();
  });

  document.addEventListener('DOMContentLoaded', () => {
    const src = $('act-data');
    if (!src) return;
    try { data = JSON.parse(src.textContent || 'null'); } catch (e) { data = null; }
    if (data) render();
    setInterval(tick, 1000);
    // A filtered first page (?filter=, ?ability=) holds only that part: fetch everything now.
    timer = setTimeout(poll, data && data.filter === 'all' && data.ability === null ? POLL_MS : 0);
  });
})();
