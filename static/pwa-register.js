if ('serviceWorker' in navigator) {
  window.addEventListener('load', function() {
    navigator.serviceWorker.register('/service-worker.js').then(function(reg) {
      // registration successful
      console.log('ServiceWorker registered: ', reg);
    }).catch(function(err) {
      console.warn('ServiceWorker registration failed: ', err);
    });
  });
}
