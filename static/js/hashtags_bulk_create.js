// JS for bulk hashtag creation on hashtags page
(function(){
  function $(sel){ return document.querySelector(sel); }
  var input = document.getElementById('bulk-hashtags-input');
  var btn = document.getElementById('bulk-hashtags-create-btn');
  var wrap = document.getElementById('hashtags-wrap');
  var status = document.getElementById('bulk-hashtags-status');
  if(!input || !btn) return;
  function showStatus(msg, cls){
    if(!status) return;
    status.textContent = msg;
    status.className = 'bulk-hashtags-status ' + (cls||'');
  }
  btn.addEventListener('click', function(){
    var raw = input.value.trim();
    if(!raw){
      showStatus('No hashtags entered', 'warn');
      return;
    }
    showStatus('Creating...', 'pending');
    fetch('/hashtags/bulk_create/json', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
      credentials: 'same-origin',
      body: JSON.stringify({ tags: raw })
    }).then(function(r){ return r.json().catch(function(){ return {}; }); })
    .then(function(data){
      if(!data || !data.ok){
        showStatus('Failed to create hashtags', 'error');
        return;
      }
      var created = data.created || [];
      if(created.length === 0){
        showStatus('No new hashtags created (all existed or invalid).', 'info');
      } else {
        showStatus('Created ' + created.length + ' hashtag' + (created.length>1?'s':'') + '.', 'ok');
      }
      // Update DOM inline to show newly created tags if they are not already listed
      if(wrap && created.length){
        created.forEach(function(tag){
          if(wrap.querySelector('[data-tag="'+ tag +'"]')) return;
          var a = document.createElement('a');
          a.className = 'tag-chip';
          a.href = '/html_no_js/search?q=' + encodeURIComponent(tag);
          a.textContent = tag;
          a.setAttribute('data-tag', tag);
          wrap.appendChild(a);
        });
      }
      input.value = '';
    }).catch(function(){
      showStatus('Network error creating hashtags', 'error');
    });
  });
})();
