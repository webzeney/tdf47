// フリーワード検索。#q の入力候補を /data/search-index.json から出し、
// 決定で該当ページへ。候補が無ければ /all/?q= に送って一覧側で絞り込む。
(function(){
  var input = document.getElementById('q');
  if(!input) return;
  var box = input.closest('.searchrow') || input.parentNode;
  var list = document.createElement('ul');
  list.className = 'suggest';
  list.hidden = true;
  box.parentNode.insertBefore(list, box.nextSibling);

  var INDEX=null, loading=null;
  function load(){
    if(INDEX) return Promise.resolve(INDEX);
    if(loading) return loading;
    loading = fetch('/data/search-index.json').then(function(r){return r.json();})
      .then(function(d){INDEX=d;return d;}).catch(function(){return [];});
    return loading;
  }
  function norm(s){return (s||'').toLowerCase();}
  function go(){ location.href = '/all/' + (input.value.trim()? '?q='+encodeURIComponent(input.value.trim()):''); }

  input.addEventListener('input', function(){
    var q = norm(input.value.trim());
    if(!q){ list.hidden=true; list.innerHTML=''; return; }
    load().then(function(data){
      var out=[];
      for(var i=0;i<data.length && out.length<12;i++){
        var e=data[i];
        if(norm(e.n).indexOf(q)>=0||norm(e.k).indexOf(q)>=0||norm(e.s).indexOf(q)>=0||norm(e.p).indexOf(q)>=0) out.push(e);
      }
      if(!out.length){ list.hidden=true; return; }
      list.innerHTML = out.map(function(e){
        return '<li><a href="'+e.u+'">'+e.n+'<span>'+e.p+'</span></a></li>';
      }).join('');
      list.hidden=false;
    });
  });
  input.addEventListener('keydown', function(e){ if(e.key==='Enter'){ e.preventDefault(); go(); } });
  input.addEventListener('focus', load);
  var btn = box.querySelector('button');
  if(btn) btn.addEventListener('click', go);
  document.addEventListener('click', function(e){
    if(!input.contains(e.target) && !list.contains(e.target)) list.hidden=true;
  });
})();
