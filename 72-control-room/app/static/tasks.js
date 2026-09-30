// Tasks: the pack's Copy button (clipboard only). Without JavaScript the button stays hidden and
// the pack is a readonly text box to select and copy by hand. No inline scripts (CSP script-src 'self').
(function () {
  'use strict';
  document.querySelectorAll('[data-copy]').forEach(function (btn) {
    var box = document.getElementById(btn.getAttribute('data-copy'));
    var status = document.querySelector('[data-copy-status="' + btn.getAttribute('data-copy') + '"]');
    if (!box) return;
    btn.hidden = false;
    btn.addEventListener('click', function () {
      var done = function (ok) { if (status) status.textContent = ok ? 'Copied. Paste it into your AI chat.' : 'Select the text and copy it by hand.'; };
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(box.value).then(function () { done(true); }, function () { box.select(); done(false); });
      } else {
        box.select();
        var ok = false;
        try { ok = document.execCommand('copy'); } catch (e) { ok = false; }
        done(ok);
      }
    });
  });
}());
