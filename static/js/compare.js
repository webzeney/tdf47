/* #4 自治体をえらんで比べる。軽量インデックス＋県別データの遅延フェッチ。URLで共有。 */
(function () {
  var MAX = 5;
  var idx = [], idxByCode = {}, selected = [], rec = {}, prefCache = {};
  var q = document.getElementById('cxq'),
      sug = document.getElementById('cxsug'),
      chips = document.getElementById('cxchips'),
      wrap = document.getElementById('cxwrap'),
      tableBox = document.getElementById('cxtable'),
      empty = document.getElementById('cxempty'),
      actions = document.getElementById('cxactions');
  if (!q) return;

  function esc(s) {
    return String(s).replace(/[&<>"]/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c];
    });
  }
  function fmtInt(x) { return Number(x).toLocaleString('ja-JP'); }
  function fmtArea(x) { return Number(x).toLocaleString('ja-JP', { maximumFractionDigits: 2 }); }
  function fmtF(v) { return String(+(+v).toFixed(2)); }

  var MET = [
    { label: '都道府県', get: function (r) { return esc(r.p || '—'); } },
    { label: '区分', get: function (r) { return esc(r.t || '—'); } },
    { label: '類似団体区分', get: function (r) { return esc(r.r || '—'); } },
    { label: '人口', get: function (r) { return r.pop != null ? fmtInt(r.pop) + '人' : '—'; } },
    { label: '面積', get: function (r) { return r.area != null ? fmtArea(r.area) + 'km²' : '—'; } },
    { label: '財政力指数', fk: 'zaisei_ryoku' },
    { label: '経常収支比率', fk: 'keijo_shushi', u: '%' },
    { label: '実質公債費比率', fk: 'kosaihi', u: '%' },
    { label: '将来負担比率', fk: 'shorai_futan', u: '%' },
    { label: 'ラスパイレス指数', fk: 'laspeyres' }
  ];

  function loadPref(p2) {
    if (prefCache[p2]) return prefCache[p2];
    prefCache[p2] = fetch('/data/compare/' + p2 + '.json')
      .then(function (r) { return r.ok ? r.json() : {}; })
      .then(function (j) { Object.keys(j).forEach(function (c) { rec[c] = j[c]; }); return j; })
      .catch(function () { return {}; });
    return prefCache[p2];
  }
  function addCode(code) {
    if (selected.indexOf(code) >= 0 || selected.length >= MAX) return Promise.resolve();
    return loadPref(code.slice(0, 2)).then(function () {
      if (!rec[code] || selected.indexOf(code) >= 0 || selected.length >= MAX) return;
      selected.push(code); syncHash(); render();
    });
  }
  function removeCode(code) {
    var i = selected.indexOf(code);
    if (i >= 0) { selected.splice(i, 1); syncHash(); render(); }
  }
  function syncHash() {
    var h = selected.join(',');
    history.replaceState(null, '', location.pathname + (h ? '#' + h : ''));
  }

  function render() {
    chips.innerHTML = selected.map(function (c) {
      var r = rec[c] || {};
      return '<span class="cx-chip">' + esc(r.n || c) +
        '<button class="cx-cx" type="button" data-c="' + c + '" aria-label="削除">×</button></span>';
    }).join('');
    if (selected.length < MAX) {
      chips.innerHTML += '<span class="cx-hint">' + selected.length + ' / ' + MAX + '</span>';
    } else {
      chips.innerHTML += '<span class="cx-hint">上限の' + MAX + '団体です</span>';
    }
    var has = selected.length > 0;
    empty.hidden = has; wrap.hidden = !has; actions.hidden = !has;
    if (!has) { tableBox.innerHTML = ''; return; }

    var html = '<table class="cx-tbl"><thead><tr><th class="cx-corner">項目</th>';
    selected.forEach(function (c) {
      var r = rec[c] || {};
      html += '<th><span class="cx-tn">' + esc(r.n || c) +
        '</span><button class="cx-x" type="button" data-c="' + c + '" aria-label="削除">×</button></th>';
    });
    html += '</tr></thead><tbody>';
    MET.forEach(function (m) {
      html += '<tr><th>' + m.label + '</th>';
      selected.forEach(function (c) {
        var r = rec[c] || {}, v;
        if (m.fk) { var fv = r.f && r.f[m.fk]; v = (fv != null) ? (fmtF(fv) + (m.u || '')) : '—'; }
        else { v = m.get(r); }
        html += '<td>' + v + '</td>';
      });
      html += '</tr>';
    });
    html += '<tr class="cx-linkrow"><th>ページ</th>';
    selected.forEach(function (c) {
      var r = rec[c] || {};
      html += '<td>' + (r.u ? '<a href="' + r.u + '">開く›</a>' : '—') + '</td>';
    });
    html += '</tr></tbody></table>';
    tableBox.innerHTML = html;
  }

  function norm(s) { return (s || '').toLowerCase(); }
  function showSug() {
    var v = norm(q.value).trim();
    if (!v) { sug.hidden = true; sug.innerHTML = ''; return; }
    var out = [];
    for (var i = 0; i < idx.length && out.length < 8; i++) {
      var e = idx[i];
      if (selected.indexOf(e.c) >= 0) continue;
      if (norm(e.n).indexOf(v) >= 0 || norm(e.k).indexOf(v) >= 0) out.push(e);
    }
    if (!out.length) { sug.innerHTML = '<div class="cx-none">見つかりません</div>'; sug.hidden = false; return; }
    sug.innerHTML = out.map(function (e) {
      return '<button type="button" class="cx-sg" data-c="' + e.c + '"><b>' + esc(e.n) + '</b><span>' + esc(e.p) + '</span></button>';
    }).join('');
    sug.hidden = false;
  }

  q.addEventListener('input', showSug);
  q.addEventListener('focus', showSug);
  document.addEventListener('click', function (ev) {
    var t = ev.target;
    var sg = t.closest && t.closest('.cx-sg');
    if (sg) { addCode(sg.dataset.c).then(function () { q.value = ''; sug.hidden = true; q.focus(); }); return; }
    var x = t.closest && t.closest('.cx-x, .cx-cx');
    if (x) { removeCode(x.dataset.c); return; }
    if (!(t.closest && t.closest('.cx-inwrap'))) { sug.hidden = true; }
  });

  var copy = document.getElementById('cxcopy');
  if (copy) copy.addEventListener('click', function () {
    var tbl = tableBox.querySelector('.cx-tbl'); if (!tbl) return;
    var lines = [];
    tbl.querySelectorAll('tr').forEach(function (tr) {
      var cells = [].slice.call(tr.children).map(function (td) {
        var a = td.querySelector('a');
        return (a ? a.textContent.replace('›', '') : td.textContent).replace(/×/g, '').replace(/\s+/g, ' ').trim();
      });
      lines.push(cells.join('\t'));
    });
    var txt = lines.join('\n'), done = function (ok) {
      copy.textContent = ok ? 'コピーしました' : 'コピー失敗';
      setTimeout(function () { copy.textContent = '表をコピー'; }, 1600);
    };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(txt).then(function () { done(true); }, function () { done(false); });
    } else {
      var ta = document.createElement('textarea'); ta.value = txt; document.body.appendChild(ta); ta.select();
      var ok = false; try { ok = document.execCommand('copy'); } catch (e) {}
      document.body.removeChild(ta); done(ok);
    }
  });
  var clr = document.getElementById('cxclear');
  if (clr) clr.addEventListener('click', function () { selected = []; syncHash(); render(); });

  fetch('/data/compare-index.json').then(function (r) { return r.json(); }).then(function (j) {
    idx = j; j.forEach(function (e) { idxByCode[e.c] = e; });
    var hash = decodeURIComponent(location.hash.replace(/^#/, '')).split(',')
      .map(function (s) { return s.trim(); }).filter(Boolean);
    var valid = hash.filter(function (c) { return idxByCode[c]; }).slice(0, MAX);
    (function next(i) {
      if (i >= valid.length) { if (!selected.length) render(); return; }
      addCode(valid[i]).then(function () { next(i + 1); });
    })(0);
    if (!valid.length) render();
  }).catch(function () {
    empty.textContent = 'データの読み込みに失敗しました。時間をおいて再度お試しください。';
  });
})();
