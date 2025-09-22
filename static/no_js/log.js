// Simple client-side helper for logging user events to the server (html_no_js only)
(function(){
  function safe(obj, k){ try { return obj && obj[k]; } catch(e){ return undefined; } }
  async function post(path, payload){
    try{
      var headers = { 'Content-Type': 'application/json', 'Accept': 'application/json' };
      return await fetch(path, { method: 'POST', credentials: 'same-origin', headers: headers, body: JSON.stringify(payload || {}) });
    }catch(e){ return null; }
  }
  window.ftLog = async function(message, opts){
    opts = opts || {};
    var payload = {
      message: String(message || ''),
      item_type: safe(opts, 'item_type') || null,
      item_id: safe(opts, 'item_id') || null,
      url: safe(opts, 'url') || null,
      label: safe(opts, 'label') || null,
      metadata: safe(opts, 'metadata') || null
    };
    try{ await post('/html_no_js/logs', payload); }catch(e){}
  };
})();
