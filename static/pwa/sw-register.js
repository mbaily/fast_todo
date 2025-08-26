if ('serviceWorker' in navigator) {
  navigator.serviceWorker.register('/service-worker.js')
    .then(reg => console.log('SW registered', reg))
    .catch(err => console.warn('SW register failed', err));
}

window.addEventListener('online', () => updateOnlineStatus());
window.addEventListener('offline', () => updateOnlineStatus());
function updateOnlineStatus() {
  const el = document.getElementById('online');
  if (!el) return;
  el.textContent = navigator.onLine ? 'online' : 'offline';
}
updateOnlineStatus();
