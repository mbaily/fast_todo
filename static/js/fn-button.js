// Simple fn-button wiring for Fast Todo (shared location)
(function(){
  'use strict';

  function readCookie(name){
    const v = document.cookie.match('(^|;)\\s*'+name+'\\s*=\\s*([^;]+)');
    return v ? decodeURIComponent(v.pop()) : null;
  }

  async function callFn(name, args){
    const payload = { name: name, args: args || {} };
    const csrf = readCookie('csrf_token');
    if (csrf) payload._csrf = csrf;
    const resp = await fetch('/api/exec-fn', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Accept': 'application/json'
      },
      body: JSON.stringify(payload),
      credentials: 'same-origin'
    });
    if (!resp.ok) {
      const txt = await resp.text().catch(()=>null);
      throw new Error('Server error: '+resp.status+(txt?(' - '+txt):''));
    }
    return resp.json();
  }

  function parseArgs(el){
    const raw = el.getAttribute('data-args');
    if (!raw) return {};
    try{ return JSON.parse(raw); }catch(e){
      const out = {}; raw.split(/\s+/).forEach(pair=>{ const i = pair.indexOf('='); if (i>0){ out[pair.slice(0,i)] = pair.slice(i+1); } }); return out;
    }
  }

  async function handleClick(e){
    const btn = e.currentTarget; const name = btn.getAttribute('data-fn'); if (!name) return;
    // By default skip confirm for search.multi; opt-in to confirm via data-force-confirm="true"
    const confirmText = btn.getAttribute('data-confirm');
    const forceConfirm = btn.getAttribute('data-force-confirm') === 'true';
    if (confirmText && (forceConfirm || name !== 'search.multi')){ if (!window.confirm(confirmText)) return; }
    btn.disabled = true; const orig = btn.innerHTML;
    try{
      const args = parseArgs(btn);
      btn.innerHTML = btn.getAttribute('data-loading') || '...';
      const res = await callFn(name, args);
      const ev = new CustomEvent('fn:result', { detail: { name, args, res } }); window.dispatchEvent(ev);
      if (res && res.results){ window.dispatchEvent(new CustomEvent('search.multi:result', { detail: res.results }));
        try{ if (name === 'search.multi' && btn.getAttribute('data-nav') !== 'false'){ const tags = (args && args.tags) || []; const q = encodeURIComponent(Array.isArray(tags) ? tags.join(',') : String(tags || '')); window.location.href = '/html_no_js/search?q=' + q; } }catch(e){ console.debug('auto-redirect failed', e); }
      }
    }catch(err){ console.error('fn-button error', err); window.dispatchEvent(new CustomEvent('fn:error', { detail: { name, error: (err && err.message) ? err.message : err } })); try{ alert('Action failed: '+err.message); }catch(e){} }
    finally{ btn.disabled = false; btn.innerHTML = orig; }
  }

  function wireAll(scope=document){ const els = scope.querySelectorAll('button[data-fn], a[data-fn]'); els.forEach(el=>{ if (el.__fn_wired) return; el.__fn_wired = true; el.addEventListener('click', function(ev){ ev.preventDefault(); handleClick(ev); }); }); }
  if (document.readyState === 'loading'){ document.addEventListener('DOMContentLoaded', ()=>wireAll(document)); } else { wireAll(document); }
  window.fnButton = { wireAll };
})();
