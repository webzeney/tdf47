// 区分ページの並べ替え（都道府県順／五十音順）。クライアント側のみ。
(function () {
  var list = document.getElementById('typelist');
  if (!list) return;
  var items = Array.prototype.slice.call(list.children);
  var buttons = document.querySelectorAll('.listtools__sort button');
  function sortBy(key) {
    items.slice().sort(function (a, b) {
      var av = a.dataset[key] || '', bv = b.dataset[key] || '';
      if (key === 'pref') {
        var c = av.localeCompare(bv);
        return c !== 0 ? c : (a.dataset.kana || '').localeCompare(b.dataset.kana || '', 'ja');
      }
      return av.localeCompare(bv, 'ja');
    }).forEach(function (li) { list.appendChild(li); });
  }
  buttons.forEach(function (btn) {
    btn.addEventListener('click', function () {
      buttons.forEach(function (b) { b.classList.remove('is-active'); });
      btn.classList.add('is-active');
      sortBy(btn.dataset.sort);
    });
  });
})();
