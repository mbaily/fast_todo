const CACHE_NAME = 'fast-todo-v1';
const OFFLINE_URL = '/html_no_js/';
const APP_SHELL = [
  '/',
  '/html_no_js/',
  '/static/manifest.json'
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(APP_SHELL))
  );
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) => Promise.all(
      keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k))
    ))
  );
  self.clients.claim();
});

// Simple fetch handler: try network first for API requests, else cache-first for navigation and static assets
self.addEventListener('fetch', (event) => {
  const req = event.request;
  const url = new URL(req.url);
  // treat API calls as network-first so clients get fresh data and can sync; fall back to cache if offline
  if (url.pathname.startsWith('/todos') || url.pathname.startsWith('/lists') || url.pathname.startsWith('/server')) {
    // use same-origin credentials so browser cookies (session/auth) are sent
    const fetchReq = new Request(req, { credentials: 'same-origin' });
    event.respondWith(
      fetch(fetchReq).then((res) => {
        // Optionally: update cache for GET
        if (req.method === 'GET') {
          const copy = res.clone();
          caches.open(CACHE_NAME).then((cache) => cache.put(req, copy));
        }
        return res;
      }).catch(() => caches.match(req))
    );
    return;
  }

  // navigation / app shell: cache-first
  event.respondWith(
    caches.match(req).then((resp) => resp || fetch(req).catch(() => caches.match(OFFLINE_URL)))
  );
});
