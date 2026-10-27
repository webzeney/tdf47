// 都道府県ページの市区町村グリッド並べ替え。
// 団体コード順=初期DOM順（見出しつき）。五十音順=見出しを隠して一続きに。
(function(){
  var grid = document.getElementById('mgrid');
  if(!grid) return;
  var original = Array.prototype.slice.call(grid.children);
  var tiles = original.filter(function(el){ return el.classList.contains('mtile'); });
  var heads = original.filter(function(el){ return el.classList.contains('mhead'); });
  var btns = document.querySelectorAll('.sortbtn');

  function byCode(){
    heads.forEach(function(h){ h.style.display=''; });
    original.forEach(function(el){ grid.appendChild(el); });
  }
  function flat(cmp){
    heads.forEach(function(h){ h.style.display='none'; });
    tiles.slice().sort(cmp).forEach(function(t){ grid.appendChild(t); });
  }
  function byKana(){ flat(function(a,b){ return (a.dataset.kana||'').localeCompare(b.dataset.kana||'','ja'); }); }
  function byPop(){ flat(function(a,b){ return (+b.dataset.pop||0)-(+a.dataset.pop||0); }); }
  btns.forEach(function(btn){
    btn.addEventListener('click', function(){
      btns.forEach(function(b){ b.classList.remove('on'); });
      btn.classList.add('on');
      var s=btn.dataset.sort;
      if(s==='kana') byKana(); else if(s==='pop') byPop(); else byCode();
    });
  });
})();
