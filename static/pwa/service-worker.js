// Simple service worker: cache app shell and attempt background sync via fetch from client
const CACHE_NAME = 'fast-todo-shell-v1';
const URLS = [
  '/html_pwa/index.html',
  '/static/pwa/app.js',
  '/static/pwa/sw-register.js'
];

self.addEventListener('install', (ev) => {
  ev.waitUntil(caches.open(CACHE_NAME).then(c => c.addAll(URLS)));
  self.skipWaiting();
});

self.addEventListener('activate', (ev) => {
  ev.waitUntil(self.clients.claim());
});

self.addEventListener('fetch', (ev) => {
  // Serve cached shell, else network
  if (ev.request.mode === 'navigate' || ev.request.destination === 'document') {
    ev.respondWith(caches.match('/html_pwa/index.html').then(r => r || fetch(ev.request)));
    return;
  }
  ev.respondWith(caches.match(ev.request).then(r => r || fetch(ev.request)));
});
