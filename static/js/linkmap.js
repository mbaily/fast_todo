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
      .backgroundColor('#000000')
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
          // Derive current canvas scale (prefer actual transform; fallback to provided globalScale)
          const tr = (ctx && typeof ctx.getTransform === 'function') ? ctx.getTransform() : null;
          const scale = tr ? (typeof tr.a === 'number' ? tr.a : (globalScale || 1)) : (globalScale || 1);
          // Only render text when zoomed in enough to be legible
          if (scale < 1.0) return;

          // Choose a base font size that caps for readability
          const MAX_SCREEN_PX = 14; // hard on-screen cap
          const desiredScreenPx = 12; // slightly smaller baseline per feedback
          const screenPx = Math.min(MAX_SCREEN_PX, Math.max(8, desiredScreenPx));
          // Compute max allowed label width to fit inside the node circle (on-screen)
          const baseR = (graph && typeof graph.nodeRelSize === 'function') ? graph.nodeRelSize() : 6;
          const val = (typeof n.degree === 'number' && n.degree > 0) ? n.degree : 1;
          const radiusCanvas = baseR * Math.sqrt(val); // approximate canvas-space radius used by ForceGraph 2D
          const radiusScreen = radiusCanvas * (scale || 1); // convert to screen px via current zoom scale
          const maxPx = Math.max(12, (radiusScreen * 2) - 6); // diameter minus a little padding

          // Convert node position to screen coordinates for constant-pixel rendering
          const sc = (graph && typeof graph.graph2ScreenCoords === 'function') ? graph.graph2ScreenCoords(n.x, n.y) : { x: n.x, y: n.y };

          // Draw in screen space: reset transform so text is not scaled; then restore
          ctx.save();
          if (typeof ctx.resetTransform === 'function') ctx.resetTransform(); else ctx.setTransform(1,0,0,1,0,0);
          const dpr = (typeof window !== 'undefined' && window.devicePixelRatio) ? window.devicePixelRatio : 1;
          if (dpr && typeof ctx.scale === 'function') ctx.scale(dpr, dpr);
          const fontPx = screenPx; // final on-screen font size in px (hard-capped)
          ctx.font = `${fontPx}px sans-serif`;
          ctx.textAlign = 'center';
          ctx.textBaseline = 'middle';

          // Generate a truncated label that fits within maxPx using measureText
          const full = (n.label || '').trim();
          if (!full) return;

          let display = full;
          // Ensure font is set before measuring (already set above)
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

          // Fill text only (remove black stroke/border)
          ctx.fillStyle = '#ffffff';
          ctx.fillText(display, sc.x, sc.y);
          ctx.restore();
        } catch (e) { /* ignore draw errors */ }
      });

    // Middle-click support: open node in a new tab
    try {
      el.addEventListener('auxclick', function(evt){
        try {
          if (evt.button !== 1) return; // only middle button
          const px = evt.offsetX, py = evt.offsetY;
          const nodes = graph && graph.graphData && graph.graphData().nodes ? graph.graphData().nodes : [];
          if (!nodes || !nodes.length) return;
          // Estimate current zoom scale in screen px per graph unit
          const s0 = graph.graph2ScreenCoords(0, 0);
          const s1 = graph.graph2ScreenCoords(1, 0);
          const scale = Math.abs((s1 && s0) ? (s1.x - s0.x) : 1) || 1;
          const baseR = (typeof graph.nodeRelSize === 'function') ? graph.nodeRelSize() : 6;

          let best = null;
          for (let i = 0; i < nodes.length; i++) {
            const n = nodes[i];
            if (typeof n.x !== 'number' || typeof n.y !== 'number') continue;
            const sc = graph && typeof graph.graph2ScreenCoords === 'function' ? graph.graph2ScreenCoords(n.x, n.y) : null;
            if (!sc) continue;
            const dx = sc.x - px, dy = sc.y - py;
            const d2 = dx*dx + dy*dy;
            // Compute node circle radius on screen (match drawing roughly)
            const val = (typeof n.degree === 'number' && n.degree > 0) ? n.degree : 1;
            const radiusScreen = baseR * Math.sqrt(val) * scale;
            const radius2 = (radiusScreen + 2) * (radiusScreen + 2); // small padding
            const inside = d2 <= radius2;
            if (inside) {
              if (!best || d2 < best.d2) best = { n, d2 };
            }
          }
          const target = best && best.n;
          if (!target) return; // only trigger when clicking inside a circle
          let url = null;
          if (target.kind === 'list') url = `/html_no_js/lists/${target.raw_id}`;
          else if (target.kind === 'todo') url = `/html_no_js/todos/${target.raw_id}`;
          if (url) {
            window.open(url, '_blank');
            evt.preventDefault();
          }
        } catch(e) {}
      });
    } catch(e) {}

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
