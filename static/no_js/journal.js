(function(){
  'use strict';
  function makeFormatter(tz){
    try{
      if(!tz){ return function(iso){ try{ return new Date(iso).toLocaleString(); }catch(_){ return iso||''; } }; }
      var fmt = new Intl.DateTimeFormat(undefined, { year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false, timeZone: tz, timeZoneName: 'short' });
      return function(iso){
        try{
          if(!iso) return '';
          var d = new Date(iso);
          if(isNaN(d.getTime())) return iso;
          return fmt.format(d);
        }catch(_){ return iso || ''; }
      };
    }catch(_){
      return function(iso){ try{ return new Date(iso).toLocaleString(); }catch(_){ return iso||''; } };
    }
  }
  function readCookie(name){
    try{
      var m = document.cookie.match(new RegExp('(?:^|; )'+name.replace(/([.$?*|{}()\[\]\\\/\+^])/g,'\\$1')+'=([^;]*)'));
      return m ? decodeURIComponent(m[1]) : undefined;
    }catch(_){ return undefined; }
  }
  function el(tag, attrs, children){
    var n = document.createElement(tag);
    if(attrs){
      Object.keys(attrs).forEach(function(k){
        if(k === 'class') n.className = attrs[k];
        else if(k === 'dataset'){
          var ds = attrs[k] || {};
          Object.keys(ds).forEach(function(dk){ n.dataset[dk] = String(ds[dk]); });
        } else if(k === 'text'){
          n.textContent = attrs[k];
        } else {
          n.setAttribute(k, attrs[k]);
        }
      });
    }
    if(children && children.length){ children.forEach(function(c){ if(c) n.appendChild(c); }); }
    return n;
  }
  function renderEntry(e, fmtDate){
    var li = el('li', { class: 'journal-item', dataset: { id: e.id } });
    var header = el('div', { class: 'journal-head', style: 'display:flex; align-items:center; gap:0.5rem; flex-wrap:wrap;' });
    header.appendChild(el('span', { class: 'meta', text: fmtDate(e.created_at) }));
    var editBtn = el('button', { type: 'button', class: 'journal-edit', title: 'Edit' }); editBtn.textContent = '✏️';
    var delBtn = el('button', { type: 'button', class: 'journal-delete', title: 'Delete' }); delBtn.textContent = '🗑';
    header.appendChild(editBtn); header.appendChild(delBtn);
    var body = el('div', { class: 'journal-body' });
    var text = el('pre', { class: 'journal-text', style: 'margin:0; padding:0.25rem 0; white-space:pre-wrap; overflow:hidden;' });
    text.textContent = e.content || '';
    // Defer height adjustment until in DOM
    setTimeout(function(){
      try{
        // Let browser layout first
        text.style.height = 'auto';
        // If CSS elsewhere sets a fixed height, override with scrollHeight
        var sh = text.scrollHeight;
        if(sh){ text.style.height = sh + 'px'; }
      }catch(_){ }
    },0);
    body.appendChild(text);
    li.appendChild(header); li.appendChild(body);
    return li;
  }
  function attach(container){
    if(!container) return;
    var todoId = parseInt(container.getAttribute('data-todo-id')||'0',10);
    var tzAttr = container.getAttribute('data-tz');
    var tz = (tzAttr && tzAttr.trim()) ? tzAttr.trim() : (readCookie('tz') || undefined);
    // Normalize known sentinel strings to undefined so we fall back to cookie/browser
    if(tz){
      var lower = tz.toLowerCase();
      if(lower === 'none' || lower === 'null' || lower === 'undefined'){ tz = undefined; }
    }
    var fmtDate = makeFormatter(tz);
    var ul = container.querySelector('.journal-list');
    var emptyP = container.querySelector('.journal-empty');
    var form = container.querySelector('form.journal-add');

    function setEmpty(state){ if(emptyP) emptyP.style.display = state ? '' : 'none'; }

    function load(){
      var url = '/client/json/todos/'+todoId+'/journal';
      if(tz){ url += (url.indexOf('?')===-1 ? '?' : '&') + 'tz=' + encodeURIComponent(tz); }
      fetch(url, { credentials: 'same-origin', headers: { 'Accept': 'application/json' } })
        .then(function(r){ if(!r.ok) throw new Error('load failed'); return r.json(); })
        .then(function(data){
          while(ul.firstChild) ul.removeChild(ul.firstChild);
          var entries = (data && data.entries) || [];
          if(entries.length === 0){ setEmpty(true); return; }
          setEmpty(false);
          entries.forEach(function(e){
            var createdStr = e.created_at_display || fmtDate(e.created_at);
            var li = renderEntry({ id: e.id, content: e.content, created_at: e.created_at }, function(){ return createdStr; });
            ul.appendChild(li);
          });
          // After all entries appended, ensure dynamic sizing (in case earlier timeout missed due to batching)
          try{
            requestAnimationFrame(function(){
              var pres = ul.querySelectorAll('pre.journal-text');
              for(var i=0;i<pres.length;i++){
                var p = pres[i];
                p.style.height = 'auto';
                var sh = p.scrollHeight; if(sh){ p.style.height = sh + 'px'; }
              }
            });
          }catch(_){ }
        })
        .catch(function(){ /* silent */ });
    }

    function autoResize(ta){
      try{
        if(!ta) return;
        ta.style.overflow = 'hidden';
        // Reset height to allow shrink
        ta.style.height = 'auto';
        // Add small buffer (2px) to avoid scrollbar flash
        ta.style.height = (ta.scrollHeight + 2) + 'px';
      }catch(_){ }
    }
    function wireAutoResize(ta){
      if(!ta || ta._autoResizeBound) return;
      ta._autoResizeBound = true;
      autoResize(ta);
      ta.addEventListener('input', function(){ autoResize(ta); });
    }

    // Locate the add-form textarea early and wire auto-resize
    try{
      var addTa = form && form.querySelector('textarea[name="content"]');
      if(addTa){ wireAutoResize(addTa); }
    }catch(_){ }

    function onAdd(ev){
      ev.preventDefault();
      try{
        var ta = form.querySelector('textarea[name="content"]');
        var val = (ta && ta.value || '').trim();
        if(!val) return;
        var body = JSON.stringify({ content: val });
        fetch('/client/json/todos/'+todoId+'/journal', { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' }, body: body })
          .then(function(r){ if(!r.ok) throw new Error('create failed'); return r.json(); })
          .then(function(_){ ta.value=''; autoResize(ta); load(); })
          .catch(function(){ /* silent */ });
      }catch(_){ }
    }

    function beginEdit(li){
      var textEl = li.querySelector('.journal-text');
      if(!textEl) return;
      var current = textEl.textContent || '';
      var ta = el('textarea', { rows: '3', style: 'width:100%;' });
      ta.value = current;
      var body = li.querySelector('.journal-body');
      body.innerHTML='';
      var saveBtn = el('button', { type: 'button', class: 'journal-save' }); saveBtn.textContent = 'Save';
      var cancelBtn = el('button', { type: 'button', class: 'journal-cancel', style: 'margin-left:0.4rem' }); cancelBtn.textContent = 'Cancel';
      body.appendChild(ta); body.appendChild(el('div', { style: 'margin-top:0.3rem' }, [saveBtn, cancelBtn]));
      wireAutoResize(ta);
    }

    function saveEdit(li){
      var id = parseInt(li.dataset.id||'0',10);
      var ta = li.querySelector('textarea');
      var val = (ta && ta.value || '').trim();
      fetch('/client/json/journal/'+id, { method: 'PATCH', credentials: 'same-origin', headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' }, body: JSON.stringify({ content: val }) })
        .then(function(r){ if(!r.ok) throw new Error('update failed'); return r.json(); })
        .then(function(_){ load(); })
        .catch(function(){ /* silent */ });
    }

    function del(li){
      var id = parseInt(li.dataset.id||'0',10);
      fetch('/client/json/journal/'+id, { method: 'DELETE', credentials: 'same-origin', headers: { 'Accept': 'application/json' } })
        .then(function(r){ if(!r.ok) throw new Error('delete failed'); return r.json(); })
        .then(function(_){ load(); })
        .catch(function(){ /* silent */ });
    }

    form.addEventListener('submit', onAdd);
    ul.addEventListener('click', function(ev){
      var t = ev.target;
      var li = t && t.closest('li.journal-item');
      if(!li) return;
      if(t.classList.contains('journal-edit')){ beginEdit(li); }
      else if(t.classList.contains('journal-save')){ saveEdit(li); }
      else if(t.classList.contains('journal-cancel')){ load(); }
      else if(t.classList.contains('journal-delete')){ if(confirm('Delete this entry?')) del(li); }
    });

    load();
  }

  document.addEventListener('DOMContentLoaded', function(){
    try{
      var containers = document.querySelectorAll('.journal[data-todo-id]');
      for(var i=0;i<containers.length;i++){ attach(containers[i]); }
    }catch(_){ }
  });
})();
