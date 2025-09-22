// breakpoint_debug.js
// Purpose: Show a visible debug banner with the current Tailwind-like breakpoint.
// We avoid inline JS in HTML. This script updates an element with id "breakpoint-debug".

(function () {
  const el = document.getElementById('breakpoint-debug');
  if (!el) return;

  function getBreakpointName(w) {
    // Tailwind v3 default breakpoints (min-width):
    // sm: 640px, md: 768px, lg: 1024px, xl: 1280px, 2xl: 1536px
    if (w >= 1536) return '2XL (≥1536px)';
    if (w >= 1280) return 'XL (≥1280px)';
    if (w >= 1024) return 'LG (≥1024px)';
    if (w >= 768) return 'MD (≥768px)';
    if (w >= 640) return 'SM (≥640px)';
    return 'BASE (<640px)';
  }

  function update() {
    const w = window.innerWidth;
    const bp = getBreakpointName(w);
    const dpr = (window.devicePixelRatio || 1).toFixed(2);
    el.textContent = `MEDIA: ${bp} — WIDTH: ${w}px — DPR: ${dpr}`.toUpperCase();
  }

  // Initial update and on resize (debounced)
  update();
  let t;
  window.addEventListener('resize', () => {
    clearTimeout(t);
    t = setTimeout(update, 100);
  });
})();
