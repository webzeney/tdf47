// 外部リンク（公式サイト等）クリックを GA4 の outbound_click として計測。
// GA4 未導入でも安全（gtag が無ければ何もしない）。リダイレクタは使わず直リンク。
(function () {
  document.addEventListener('click', function (e) {
    var a = e.target.closest ? e.target.closest('a[data-outbound]') : null;
    if (!a || typeof window.gtag !== 'function') return;
    window.gtag('event', 'outbound_click', {
      link_kind: a.getAttribute('data-outbound'),
      link_url: a.href,
      entity_code: document.body.getAttribute('data-entity') || '',
      pref_code: document.body.getAttribute('data-pref') || ''
    });
  });
})();
