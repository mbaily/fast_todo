// static/no_js/breakpoint_debug.js
// Show current active media query ranges used in html_no_js list template.
(function(){
  try{
    var el = document.getElementById('breakpoint-debug');
    if (!el) return;

    // Reflect the media queries present in list.html
    // We track common thresholds mentioned there: 420px, 480px, 560px
    var queries = [
      { label: 'MAX-420PX', test: '(max-width: 420px)' },
      { label: 'MAX-480PX', test: '(max-width: 480px)' },
      { label: 'MAX-560PX', test: '(max-width: 560px)' }
    ];

    function status(){
      var w = window.innerWidth || document.documentElement.clientWidth || 0;
      var dpr = (window.devicePixelRatio || 1).toFixed(2);
      var active = [];
      for (var i=0; i<queries.length; i++){
        if (window.matchMedia && window.matchMedia(queries[i].test).matches){
          active.push(queries[i].label);
        }
      }
      if (active.length === 0) active.push('NO MAX-WIDTH MATCH');
      el.textContent = ('MEDIA: ' + active.join(', ') + ' — WIDTH: ' + w + 'PX — DPR: ' + dpr).toUpperCase();
    }

    status();
    var t;
    window.addEventListener('resize', function(){ clearTimeout(t); t = setTimeout(status, 100); }, false);
  } catch(e){}
})();
