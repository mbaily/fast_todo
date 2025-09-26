(function(){
  function qs(id){ return document.getElementById(id); }
  var input = qs('bulk-create-input');
  var btn = qs('bulk-create-btn');
  var statusEl = qs('bulk-create-status');
  if(!input || !btn) return; // page missing elements

  function setStatus(msg, isErr){
    if(!statusEl) return;
    statusEl.textContent = msg || '';
    statusEl.style.color = isErr ? 'var(--danger, #b00)' : 'var(--muted, #666)';
  }

  async function createTags(){
    var raw = input.value.trim();
    if(!raw){ setStatus('Enter one or more hashtag tokens', true); return; }
    setStatus('Creating…');
    try {
      var resp = await fetch('/hashtags/bulk_create/json', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
        credentials: 'same-origin',
        body: JSON.stringify({ tags: raw })
      });
      if(!resp.ok){ setStatus('Server error: ' + resp.status, true); return; }
      var data = await resp.json();
      if(!data || data.ok !== true){ setStatus('Unexpected response', true); return; }
      var created = (data.created||[]).length;
      var existing = (data.existing||[]).length;
      var invalid = (data.invalid||[]).length;
      if(created === 0 && existing === 0){
        setStatus('No valid hashtags found' + (invalid? ('; invalid: ' + data.invalid.join(', ')) : ''), true);
        return;
      }
      // Simple strategy: reload page to show updated list
      setStatus('Created ' + created + (existing? (', existing ' + existing) : '') + (invalid? (', invalid ' + invalid) : '') + '. Reloading…');
      window.location.reload();
    } catch(e){
      console.error('bulk create hashtags failed', e);
      setStatus('Error: ' + (e && e.message? e.message : 'unknown'), true);
    }
  }

  btn.addEventListener('click', createTags);
  input.addEventListener('keydown', function(ev){
    if(ev.key === 'Enter' && (ev.ctrlKey || ev.metaKey)){
      ev.preventDefault();
      createTags();
    }
  });
})();
