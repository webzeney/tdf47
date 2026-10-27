// スライドナビの開閉（モックアップの挙動を踏襲）。
// PC: 第3階層パネル(panel3)は body 直下へ移して固定配置。SP/TB: 元位置でアコーディオン。
(function(){
  var btns  = Array.prototype.slice.call(document.querySelectorAll('.navbtn'));
  var subs  = Array.prototype.slice.call(document.querySelectorAll('.subbtn'));

  var MQ = window.matchMedia('(max-width:980px)');
  var slots = Array.prototype.slice.call(document.querySelectorAll('.panel3')).map(function(p){
    return { el:p, parent:p.parentNode, next:p.nextSibling };
  });
  function placePanels(){
    slots.forEach(function(o){
      if (MQ.matches){
        if (o.el.parentNode !== o.parent) o.parent.insertBefore(o.el, o.next);
      } else if (o.el.parentNode !== document.body){
        document.body.appendChild(o.el);
      }
    });
  }
  placePanels();
  if (MQ.addEventListener) MQ.addEventListener('change', placePanels);
  else if (MQ.addListener) MQ.addListener(placePanels);
  function panelOf(b){ return document.getElementById(b.getAttribute('aria-controls')); }
  function shut(b){
    b.setAttribute('aria-expanded','false');
    b.classList.remove('on');
    var p = panelOf(b); if (p) p.hidden = true;
  }
  function closeSubs(scope, except){
    subs.forEach(function(s){
      if (s === except) return;
      if (scope && !scope.contains(s)) return;
      shut(s);
    });
  }
  function closeAll(except){
    closeSubs(null, null);
    btns.forEach(function(b){ if (b !== except) shut(b); });
  }
  btns.forEach(function(b){
    b.addEventListener('click', function(e){
      e.stopPropagation();
      var p = panelOf(b);
      var isOpen = b.getAttribute('aria-expanded') === 'true';
      closeAll(b);
      b.setAttribute('aria-expanded', String(!isOpen));
      b.classList.toggle('on', !isOpen);
      if (p) p.hidden = isOpen;
    });
  });
  subs.forEach(function(s){
    s.addEventListener('click', function(e){
      e.stopPropagation();
      var p = panelOf(s);
      var isOpen = s.getAttribute('aria-expanded') === 'true';
      closeSubs(s.closest('.submenu'), s);
      s.setAttribute('aria-expanded', String(!isOpen));
      s.classList.toggle('on', !isOpen);
      if (p) p.hidden = isOpen;
    });
  });
  document.querySelectorAll('[data-close]').forEach(function(x){
    x.addEventListener('click', function(e){ e.stopPropagation(); closeAll(null); });
  });
  var side = document.querySelector('.side');
  var burger = document.querySelector('.burger');
  function closeNav(){
    if (!side) return;
    side.classList.remove('open');
    if (burger){ burger.classList.remove('on'); burger.setAttribute('aria-expanded','false');
      burger.setAttribute('aria-label','メニューを開く'); }
    closeAll(null);
  }
  if (burger){
    burger.addEventListener('click', function(e){
      e.stopPropagation();
      var open = side.classList.toggle('open');
      burger.classList.toggle('on', open);
      burger.setAttribute('aria-expanded', String(open));
      burger.setAttribute('aria-label', open ? 'メニューを閉じる' : 'メニューを開く');
      if (!open) closeAll(null);
    });
  }
  document.addEventListener('click', function(e){
    if (!e.target.closest) return;
    if (e.target.closest('.navitem') || e.target.closest('.panel3')) return;
    closeAll(null);
    if (!e.target.closest('.side')) closeNav();
  });
  document.addEventListener('keydown', function(e){
    if (e.key === 'Escape'){ closeAll(null); closeNav(); }
  });
})();
