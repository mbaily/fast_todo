(function(){
  'use strict';
  function readCookie(name){ try{ var m=document.cookie.match(new RegExp('(?:^|; )'+name.replace(/([.$?*|{}()\[\]\\\/\+^])/g,'\\$1')+'=([^;]*)')); return m?decodeURIComponent(m[1]):undefined; }catch(_){ return undefined; } }
  function el(tag, attrs, kids){ var n=document.createElement(tag); if(attrs){ Object.keys(attrs).forEach(function(k){ if(k==='class'){ n.className=attrs[k]; } else if(k==='text'){ n.textContent=attrs[k]; } else if(k==='dataset'){ Object.keys(attrs[k]).forEach(function(dk){ n.dataset[dk]=String(attrs[k][dk]); }); } else { n.setAttribute(k, attrs[k]); } }); } (kids||[]).forEach(function(c){ if(c) n.appendChild(c); }); return n; }
  function fmtDate(iso){
    try{
      if(!iso) return '';
      var tz = readCookie('tz');
      // Normalize: backend likely emits naive UTC or explicit Z. If no timezone suffix, treat as UTC.
      var normalized = iso;
      if(/^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:.]+$/.test(iso)){ normalized = iso + 'Z'; }
      var d = new Date(normalized);
      if(isNaN(d.getTime())) return iso;
  var baseOpts = { year:'numeric', month:'short', day:'numeric', hour:'numeric', minute:'2-digit', hour:'numeric', minute:'2-digit', hour12:true };
      // Remove duplicate keys (hour/minute repeated) but keep final ones
  baseOpts = { year:'numeric', month:'short', day:'numeric', hour:'numeric', minute:'2-digit', hour12:true };
      if(tz){
        try{ return d.toLocaleString(undefined, Object.assign({}, baseOpts, { timeZone: tz, timeZoneName: 'short' })); }catch(_){ }
      }
      return d.toLocaleString(undefined, Object.assign({}, baseOpts, { timeZoneName: 'short' }));
    }catch(_){ return iso||''; }
  }
  function autoGrow(ta){ try{ ta.style.height='auto'; ta.style.height=(ta.scrollHeight)+'px'; }catch(_){ } }
  function attach(root){ if(!root) return; var listId=parseInt(root.getAttribute('data-list-id')||'0',10); var ul=root.querySelector('#notes-list'); var emptyEl=root.querySelector('#empty-notes'); var addBtn=root.querySelector('#btn-add-note'); var addTa=root.querySelector('#new-note-text'); var addStatus=root.querySelector('#add-note-status');
    function setEmpty(state){ if(emptyEl) emptyEl.style.display=state?'':'none'; }
    function renderNote(note){
      var li = el('li',{class:'list-note',dataset:{id:note.id}});
      var ta = el('textarea',{rows:'3', style:'width:100%;resize:none;overflow:hidden;'});
      ta.value = note.content||'';
      autoGrow(ta);
      ta.addEventListener('input',function(){ autoGrow(ta); markDirty(li); });
      var meta = el('div',{class:'note-meta'});
      meta.textContent = 'Created '+fmtDate(note.created_at)+(note.modified_at?' • Modified '+fmtDate(note.modified_at):'');
      var btns = el('div',{class:'note-buttons'});
      var saveBtn = el('button',{type:'button',class:'note-save'}); saveBtn.textContent='Save';
      var delBtn = el('button',{type:'button',class:'note-delete'}); delBtn.textContent='Delete';
      btns.appendChild(saveBtn); btns.appendChild(delBtn);
      li.appendChild(ta); li.appendChild(meta); li.appendChild(btns);
      return li;
    }
    function markDirty(li){ if(!li) return; li.classList.remove('saved'); }
    function markSaved(li){ if(!li) return; li.classList.add('saved'); }
  function load(){ fetch('/client/json/lists/'+listId+'/notes',{credentials:'same-origin',headers:{'Accept':'application/json'}})
    .then(function(r){ if(!r.ok){ return r.text().then(function(t){ throw new Error('load '+r.status+' '+t.slice(0,120)); }); } return r.json(); })
    .then(function(data){
      while(ul.firstChild) ul.removeChild(ul.firstChild);
      var notes=(data&&data.notes)||[];
      if(!notes.length){ setEmpty(true); return; }
      setEmpty(false);
      notes.forEach(function(n){ var li=renderNote(n); markSaved(li); ul.appendChild(li); });
      // second pass to ensure all textareas grow correctly (after layout)
      try { requestAnimationFrame(function(){ var tas=ul.querySelectorAll('li.list-note textarea'); for(var i=0;i<tas.length;i++){ autoGrow(tas[i]); } }); }catch(_){ }
    })
    .catch(function(err){ setEmpty(true); if(addStatus){ addStatus.textContent='Load failed'; setTimeout(function(){ if(addStatus.textContent==='Load failed') addStatus.textContent=''; },2500);} console.warn('[list-notes] load error', err); }); }
  function create(){ var val=(addTa.value||'').trim(); if(!val) return; addBtn.disabled=true; fetch('/client/json/lists/'+listId+'/notes',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','Accept':'application/json'},body:JSON.stringify({content:val})})
    .then(function(r){ if(!r.ok){ return r.text().then(function(t){ throw new Error('create '+r.status+' '+t.slice(0,120)); }); } return r.json(); })
    .then(function(){ addTa.value=''; autoGrow(addTa); load(); addStatus.textContent='Saved'; setTimeout(function(){ addStatus.textContent=''; },1500); })
    .catch(function(err){ addStatus.textContent='Error'; console.warn('[list-notes] create error', err); setTimeout(function(){ if(addStatus.textContent==='Error') addStatus.textContent=''; },2500); })
    .finally(function(){ addBtn.disabled=false; }); }
    function save(li){ var id=parseInt(li.dataset.id||'0',10); var ta=li.querySelector('textarea'); var val=(ta&&ta.value||'').trim(); fetch('/client/json/list_notes/'+id,{method:'PATCH',credentials:'same-origin',headers:{'Content-Type':'application/json','Accept':'application/json'},body:JSON.stringify({content:val})}).then(function(r){ if(!r.ok) throw new Error('save failed'); return r.json(); }).then(function(){ markSaved(li); load(); }).catch(function(){ /* silent */ }); }
    function del(li){ var id=parseInt(li.dataset.id||'0',10); if(!confirm('Delete this note?')) return; fetch('/client/json/list_notes/'+id,{method:'DELETE',credentials:'same-origin',headers:{'Accept':'application/json'}}).then(function(r){ if(!r.ok) throw new Error('delete failed'); return r.json(); }).then(function(){ load(); }).catch(function(){ /* silent */ }); }
    addBtn.addEventListener('click',create); addTa.addEventListener('input',function(){ autoGrow(addTa); }); ul.addEventListener('click',function(ev){ var t=ev.target; var li=t&&t.closest('li.list-note'); if(!li) return; if(t.classList.contains('note-save')){ save(li); } else if(t.classList.contains('note-delete')){ del(li); } }); load(); }
  document.addEventListener('DOMContentLoaded',function(){ try{ var root=document.getElementById('list-notes-root'); attach(root);}catch(_){ } });
})();
