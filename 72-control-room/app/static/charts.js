// Performance charts (Chart.js, MIT). Data comes from a JSON script tag, never from eval.
(function () {
  'use strict';
  document.addEventListener('DOMContentLoaded', () => {
    const src = document.getElementById('perf-data');
    if (!src || !window.Chart) return;
    const data = JSON.parse(src.textContent || '{}');
    const dark = window.matchMedia('(prefers-color-scheme: dark)').matches;
    Chart.defaults.color = dark ? '#c9d1db' : '#394150';
    Chart.defaults.borderColor = dark ? '#2c343f' : '#dde1e6';
    const bar = (id, labels, values, label, colors) => {
      const el = document.getElementById(id);
      if (!el || !labels.length) return;
      new Chart(el, {
        type: 'bar',
        data: { labels, datasets: [{ label, data: values, backgroundColor: colors || '#2f6fd6' }] },
        options: { maintainAspectRatio: false, indexAxis: 'y', plugins: { legend: { display: false } },
          scales: { x: { beginAtZero: true } } },
      });
    };
    const ch = data.by_channel || [];
    bar('by-channel', ch.map(r => r.channel), ch.map(r => r.clicks), 'clicks');
    const hooks = data.hooks || [];
    bar('hooks', hooks.map(s => s.hook_style), hooks.map(s => s.clicks_per_post), 'clicks per post',
      hooks.map(s => s.recommended ? '#1f8a4c' : '#8a8f98'));
  });
})();
