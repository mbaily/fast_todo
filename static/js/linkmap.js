/* Link Map renderer for Fast Todo (no-js)
 * Uses force-graph (2D) via global ForceGraph()
 */
(function(){
  function $(sel){ return document.querySelector(sel); }
  function fetchJSON(url){ return fetch(url, { credentials: 'same-origin' }).then(r => { if(!r.ok) throw new Error('HTTP '+r.status); return r.json(); }); }

  function nodeColor(n){
    // Tailwind-like palette
    return n.kind === 'list' ? '#3b82f6' /* blue-500 */ : '#10b981' /* emerald-500 */;
  }
  function linkColor(){ return '#9ca3af'; /* gray-400 */ }

  function initGraph(data){
    var el = $('#graph');
    if(!el){ return; }
    var graph = ForceGraph()(el)
      .graphData(data)
      .nodeId('id')
      .nodeRelSize(6)
      .nodeVal(n => (n.degree || 1))
      .nodeColor(nodeColor)
      .nodeLabel(n => `${n.label || n.id}`)
      .linkColor(linkColor)
      .linkDirectionalArrowLength(3)
      .linkDirectionalArrowRelPos(0.5)
      .zoom(1.0)
      .onNodeClick(n => {
        try {
          // Navigate to item page on click
          if(n.kind === 'list') {
            window.location.assign(`/html_no_js/lists/${n.raw_id}`);
          } else if(n.kind === 'todo') {
            window.location.assign(`/html_no_js/todos/${n.raw_id}`);
          }
        } catch(e) {}
      })
      // Draw short labels inside node circles when zoomed in enough
      .nodeCanvasObjectMode(() => 'after')
      .nodeCanvasObject((n, ctx, globalScale) => {
        try {
          // Only render text when zoomed in enough to be legible
          if (globalScale < 1.0) return;

          // Choose a base font size that scales with zoom but caps for readability
          const base = 11; // px
          const fontSize = Math.max(6, Math.min(16, base / (globalScale * 0.9)));
          ctx.font = `${fontSize}px sans-serif`;

          // Compute maximum text width to attempt; approximate to circle diameter
          // We don't have exact radius here, so use a conservative width budget that scales with zoom
          const maxPx = Math.max(24, 64 / globalScale);

          // Generate a truncated label that fits within maxPx using measureText
          const full = (n.label || '').trim();
          if (!full) return;

          let display = full;
          let metrics = ctx.measureText(display);
          if (metrics.width > maxPx) {
            // Binary-like reduction: progressively shorten until it fits, append ellipsis
            const ell = '…';
            let lo = 1, hi = Math.max(1, full.length);
            let best = '';
            while (lo <= hi) {
              const mid = (lo + hi) >> 1;
              const candidate = full.slice(0, mid) + ell;
              const w = ctx.measureText(candidate).width;
              if (w <= maxPx) { best = candidate; lo = mid + 1; }
              else { hi = mid - 1; }
            }
            display = best || (full.slice(0, 1) + ell);
          }

          // Draw text centered on node
          ctx.textAlign = 'center';
          ctx.textBaseline = 'middle';

          // Text halo for contrast
          ctx.lineWidth = Math.max(2, fontSize / 4);
          ctx.strokeStyle = 'rgba(0,0,0,0.5)';
          ctx.strokeText(display, n.x, n.y);

          // Fill text
          ctx.fillStyle = '#ffffff';
          ctx.fillText(display, n.x, n.y);
        } catch (e) { /* ignore draw errors */ }
      });

    // Fit to graph after render
    setTimeout(function(){ try { graph.zoomToFit(400, 50); } catch(e){} }, 250);
  }

  function computeDegrees(data){
    try {
      var deg = Object.create(null);
      (data.links || []).forEach(l => {
        var s = typeof l.source === 'object' ? l.source.id : l.source;
        var t = typeof l.target === 'object' ? l.target.id : l.target;
        if(s){ deg[s] = (deg[s] || 0) + 1; }
        if(t){ deg[t] = (deg[t] || 0) + 1; }
      });
      (data.nodes || []).forEach(n => { n.degree = deg[n.id] || 0; });
    } catch(e) {}
    return data;
  }

  document.addEventListener('DOMContentLoaded', function(){
    fetchJSON('/html_no_js/linkmap/data')
      .then(d => computeDegrees(d))
      .then(d => initGraph(d))
      .catch(err => {
        console.error('Failed to load link map data', err);
        var el = $('#graph'); if(el){ el.innerHTML = '<div class="meta">Failed to load link map.</div>'; }
      });
  });
})();
