// 検索エリアのタブ切替（フリーワード／地図）。モックアップの挙動を踏襲。
(function(){
  var tabs = Array.prototype.slice.call(document.querySelectorAll('.tab'));
  if(!tabs.length) return;
  function select(t){
    tabs.forEach(function(o){
      var on = o === t;
      o.classList.toggle('on', on);
      o.setAttribute('aria-selected', String(on));
      o.tabIndex = on ? 0 : -1;
      var p = document.getElementById(o.getAttribute('aria-controls'));
      if(p) p.hidden = !on;
    });
  }
  tabs.forEach(function(t){
    t.addEventListener('click', function(){ select(t); });
    t.addEventListener('keydown', function(e){
      var i = tabs.indexOf(t), n = null;
      if(e.key === 'ArrowRight') n = tabs[(i + 1) % tabs.length];
      else if(e.key === 'ArrowLeft') n = tabs[(i - 1 + tabs.length) % tabs.length];
      if(n){ e.preventDefault(); select(n); n.focus(); }
    });
  });
})();
