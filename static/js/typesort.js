// 区分ページの並べ替え（都道府県順／五十音順）。
(function(){
  var grid = document.getElementById('typemgrid');
  if(!grid) return;
  var tiles = Array.prototype.slice.call(grid.querySelectorAll('.mtile'));
  var btns = document.querySelectorAll('.sortbtn');
  function sort(key){
    tiles.slice().sort(function(a,b){
      if(key==='pref'){
        var c=(a.dataset.pref||'').localeCompare(b.dataset.pref||'');
        return c!==0?c:(a.dataset.kana||'').localeCompare(b.dataset.kana||'','ja');
      }
      return (a.dataset.kana||'').localeCompare(b.dataset.kana||'','ja');
    }).forEach(function(t){ grid.appendChild(t); });
  }
  btns.forEach(function(btn){
    btn.addEventListener('click', function(){
      btns.forEach(function(b){ b.classList.remove('on'); });
      btn.classList.add('on'); sort(btn.dataset.sort);
    });
  });
})();
