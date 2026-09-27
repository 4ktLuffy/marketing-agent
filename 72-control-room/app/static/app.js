// Control room: swipe, keyboard triage, reject sheet, toasts, and the edit counter.
// No inline scripts (CSP script-src 'self'); htmx does the requests.
(function () {
  'use strict';
  const $ = (sel, root) => (root || document).querySelector(sel);
  const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));
  const queue = () => $('#queue');
  const cards = () => $$('#queue .card');
  let current = 0;

  function mark() {
    const cs = cards();
    if (current >= cs.length) current = Math.max(0, cs.length - 1);
    cs.forEach((c, i) => c.classList.toggle('current', i === current));
    const empty = $('.empty');
    if (empty && queue()) empty.hidden = cs.length > 0;
  }
  function cur() { return cards()[current]; }
  function focusCard(i) {
    const cs = cards(); if (!cs.length) return;
    current = (i + cs.length) % cs.length; mark();
    cs[current].scrollIntoView({ behavior: 'smooth', block: 'start' });
  }

  function openReject(card) {
    const sheet = $('.reject-sheet', card); if (!sheet) return;
    sheet.hidden = false;
    const input = $('input[name=reason]', sheet); if (input) input.focus();
  }
  function approve(card) { const b = $('[data-act=approve]', card); if (b) b.click(); }
  function skip(card) {
    // Skip = no decision (as in the form); the card moves to the end of the queue.
    const q = queue(); if (!q || !card) return;
    q.appendChild(card); mark();
  }

  document.addEventListener('click', e => {
    const t = e.target.closest('[data-act], [data-reason]');
    if (!t) return;
    const card = t.closest('.card');
    if (t.dataset.reason) {
      const input = $('input[name=reason]', card);
      input.value = input.value ? input.value + '; ' + t.dataset.reason : t.dataset.reason;
      return;
    }
    if (t.dataset.act === 'reject') openReject(card);
    else if (t.dataset.act === 'skip') skip(card);
    else if (t.dataset.act === 'close-sheet') t.closest('.reject-sheet').hidden = true;
  });

  // "Reject – rewrite it" needs a reason (without one the form would only reject).
  document.addEventListener('htmx:confirm', e => {
    const ev = e.detail.triggeringEvent;
    const btn = e.detail.elt && e.detail.elt.tagName === 'FORM' ? ((ev && ev.submitter) || document.activeElement) : null;
    if (btn && btn.hasAttribute && btn.hasAttribute('data-needs-reason')) {
      const input = $('input[name=reason]', btn.closest('form'));
      if (!input.value.trim()) { e.preventDefault(); input.focus(); input.placeholder = 'a reason is needed to rewrite'; }
    }
  });

  // Keyboard triage
  document.addEventListener('keydown', e => {
    if (e.defaultPrevented || e.metaKey || e.ctrlKey || e.altKey) return;
    const tag = (e.target.tagName || '').toLowerCase();
    if (tag === 'input' || tag === 'textarea' || tag === 'select' || e.target.isContentEditable) {
      if (e.key === 'Escape') { const s = e.target.closest('.reject-sheet'); if (s) s.hidden = true; e.target.blur(); }
      return;
    }
    if (!queue()) return;
    const card = cur();
    const k = e.key;
    if (k === '?') { const h = $('#help'); if (h && h.showModal) h.showModal(); return; }
    if (k === 'j') return focusCard(current + 1);
    if (k === 'k') return focusCard(current - 1);
    if (!card) return;
    if (k === 'a') approve(card);
    else if (k === 'e') location.href = card.dataset.editUrl;
    else if (k === 'r') { e.preventDefault(); openReject(card); }
    else if (k === 's') skip(card);
  });

  // Swipe: right = approve, left = reject. Touch and pen only; buttons keep working.
  let drag = null;
  document.addEventListener('pointerdown', e => {
    if (e.pointerType === 'mouse') return;
    const zone = e.target.closest('.swipe-zone');
    if (!zone || e.target.closest('a, button, video, input, textarea')) return;
    drag = { card: zone.closest('.card'), x: e.clientX, y: e.clientY, dx: 0, locked: false };
  }, { passive: true });
  document.addEventListener('pointermove', e => {
    if (!drag) return;
    const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
    if (!drag.locked) {
      if (Math.abs(dy) > 12 && Math.abs(dy) > Math.abs(dx)) { drag = null; return; }  // scrolling
      if (Math.abs(dx) < 12) return;
      drag.locked = true; drag.card.classList.add('dragging');
    }
    drag.dx = dx;
    drag.card.style.transform = `translateX(${dx}px) rotate(${dx / 40}deg)`;
    drag.card.classList.toggle('swipe-right', dx > 90);
    drag.card.classList.toggle('swipe-left', dx < -90);
  }, { passive: true });
  function endDrag() {
    if (!drag) return;
    const { card, dx } = drag; drag = null;
    card.classList.remove('dragging', 'swipe-right', 'swipe-left');
    card.style.transform = '';
    if (dx > 90) approve(card);
    else if (dx < -90) openReject(card);
  }
  document.addEventListener('pointerup', endDrag);
  document.addEventListener('pointercancel', endDrag);

  // Toasts disappear when their time is up (the decision is then being sent).
  function armToasts(root) {
    $$('.toast[data-ttl]', root).forEach(t => {
      if (t.dataset.armed) return; t.dataset.armed = '1';
      const ms = Math.max(1, parseFloat(t.dataset.ttl || '5')) * 1000;
      setTimeout(() => {
        const txt = $('.toast-text', t); const b = $('button', t);
        if (b) b.remove();
        if (txt && t.querySelector('.bar')) txt.textContent = txt.textContent.replace(/Sending in .*$/, 'Sending…');
        setTimeout(() => t.remove(), 1500);
      }, ms);
    });
  }
  document.addEventListener('htmx:load', e => { armToasts(e.detail.elt.parentNode || document); mark(); });
  document.addEventListener('htmx:afterSwap', () => mark());
  document.addEventListener('htmx:responseError', e => {
    const box = $('#toasts'); if (!box) return;
    const t = document.createElement('div');
    t.className = 'toast'; t.dataset.ttl = '5';
    const s = document.createElement('span'); s.className = 'toast-text';
    s.textContent = `Not saved (HTTP ${e.detail.xhr.status}). Reload the page and try again.`;
    t.appendChild(s); box.prepend(t); armToasts(box);
  });

  // Edit page: a character counter per network (limits from platform rules, 14).
  function counter(ta) {
    const out = $('#counter'), fold = $('.fold-preview .fold-text');
    const limit = parseInt(ta.dataset.limit || '0', 10), urlLen = parseInt(ta.dataset.urlLength || '0', 10);
    const maxTags = parseInt(ta.dataset.maxHashtags || '0', 10), foldAt = parseInt(ta.dataset.fold || '0', 10);
    const update = () => {
      let text = ta.value;
      if (urlLen) text = text.replace(/https?:\/\/\S+/g, 'x'.repeat(urlLen));
      const n = Array.from(text).length;   // code points, like 14 counts
      const tags = (ta.value.match(/(^|[^\p{L}\p{N}#&])#[\p{L}\p{N}_]+/gu) || []).length;
      const parts = [limit ? `${n} / ${limit} characters` : `${n} characters`];
      if (maxTags) parts.push(`${tags} / ${maxTags} hashtags`);
      if (urlLen) parts.push(`links count as ${urlLen}`);
      out.textContent = parts.join(' · ');
      out.classList.toggle('over', (limit && n > limit) || (maxTags && tags > maxTags));
      if (fold && foldAt) fold.textContent = Array.from(ta.value).slice(0, foldAt).join('') + (Array.from(ta.value).length > foldAt ? ' …see more' : '');
    };
    ta.addEventListener('input', update); update();
  }

  document.addEventListener('DOMContentLoaded', () => {
    const tpl = $('#initial-toast');
    if (tpl && $('#toasts')) {
      $('#toasts').appendChild(tpl.content.cloneNode(true));
      if (window.htmx) window.htmx.process($('#toasts'));
      armToasts($('#toasts'));
    }
    const ta = $('textarea[data-counter]'); if (ta) counter(ta);
    mark();
  });
})();
