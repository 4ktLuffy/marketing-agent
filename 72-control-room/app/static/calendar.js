// Calendar (FullCalendar standard bundle, MIT): month / week / list, status colours,
// drag to reschedule through the control room (it PATCHes 19; the browser holds no key).
(function () {
  'use strict';
  document.addEventListener('DOMContentLoaded', () => {
    const el = document.getElementById('calendar');
    if (!el || !window.FullCalendar) return;
    const csrf = JSON.parse(document.body.getAttribute('hx-headers') || '{}')['X-CSRF-Token'];
    const err = document.getElementById('cal-error');
    const show = msg => { err.textContent = msg; err.hidden = !msg; };
    const phone = window.matchMedia('(max-width: 700px)').matches;
    const cal = new FullCalendar.Calendar(el, {
      timeZone: 'UTC',
      initialView: phone ? 'listWeek' : 'dayGridMonth',
      headerToolbar: phone ? { left: 'prev,next', center: 'title', right: 'listWeek,dayGridMonth' }
        : { left: 'prev,next today', center: 'title', right: 'dayGridMonth,timeGridWeek,listWeek' },
      height: 'auto',
      firstDay: 1,
      nowIndicator: true,
      eventDisplay: 'block',
      editable: true,
      eventStartEditable: true,
      eventDurationEditable: false,
      events: (info, ok, fail) => {
        const q = new URLSearchParams({ start: info.startStr, end: info.endStr });
        fetch('/calendar/events?' + q, { credentials: 'same-origin' })
          .then(r => { if (r.status === 401) { location.href = '/login?next=/calendar'; return []; }
            if (!r.ok) throw new Error('calendar: HTTP ' + r.status); return r.json(); })
          .then(evs => { show(''); ok(evs); })
          .catch(e => { show('The calendar did not answer. ' + e.message); fail(e); });
      },
      eventDrop: info => {
        fetch('/calendar/reschedule', {
          method: 'POST', credentials: 'same-origin',
          headers: { 'content-type': 'application/json', 'X-CSRF-Token': csrf },
          body: JSON.stringify({ id: Number(info.event.id), start: info.event.start.toISOString().replace('.000Z', 'Z') }),
        }).then(r => r.ok ? r.json() : r.json().then(b => { throw new Error(b.detail || ('HTTP ' + r.status)); }))
          .then(() => show(''))
          .catch(e => { info.revert(); show('Not moved: ' + e.message); });
      },
    });
    cal.render();
  });
})();
