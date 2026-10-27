// 全国一覧の絞り込み。?q= を初期値にし、入力でクライアント側フィルタ。
(function () {
  var input = document.getElementById('all-q');
  var list = document.getElementById('all-list');
  var count = document.getElementById('all-count');
  var empty = document.getElementById('all-empty');
  if (!input || !list) return;
  var items = Array.prototype.slice.call(list.children);
  function norm(s) { return (s || '').toLowerCase(); }
  function apply() {
    var q = norm(input.value.trim());
    var shown = 0;
    items.forEach(function (li) {
      var hit = !q
        || norm(li.dataset.name).indexOf(q) >= 0
        || norm(li.dataset.kana).indexOf(q) >= 0
        || norm(li.dataset.slug).indexOf(q) >= 0
        || norm(li.dataset.pref).indexOf(q) >= 0;
      li.hidden = !hit;
      if (hit) shown++;
    });
    if (count) count.textContent = shown + ' 件';
    if (empty) empty.hidden = shown !== 0;
  }
  var params = new URLSearchParams(location.search);
  if (params.get('q')) input.value = params.get('q');
  input.addEventListener('input', apply);
  apply();
})();
