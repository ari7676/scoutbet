const CACHE_NAME = 'matchiq-v1';
const STATIC_ASSETS = [
  '/',
  '/static/manifest.json',
  '/static/icon-192.png',
  '/static/icon-512.png',
  'https://fonts.googleapis.com/css2?family=Bebas+Neue&family=DM+Sans:wght@400;500;600;700&family=JetBrains+Mono:wght@400;600&display=swap'
];

// Install: cache static assets
self.addEventListener('install', e => {
  e.waitUntil(
    caches.open(CACHE_NAME)
      .then(cache => cache.addAll(STATIC_ASSETS))
      .then(() => self.skipWaiting())
  );
});

// Activate: clean old caches
self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

// Fetch: network-first for API, cache-first for static
self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);

  // API calls: always network
  if (url.pathname.startsWith('/partidos') ||
      url.pathname.startsWith('/analizar') ||
      url.pathname.startsWith('/estadisticas') ||
      url.pathname.startsWith('/diag') ||
      url.pathname.startsWith('/login') ||
      url.pathname.startsWith('/logout')) {
    e.respondWith(fetch(e.request));
    return;
  }

  // Static assets: cache first, fallback to network
  e.respondWith(
    caches.match(e.request).then(cached => {
      return cached || fetch(e.request).then(response => {
        // Cache new static resources
        if (response.ok && response.type === 'basic') {
          const clone = response.clone();
          caches.open(CACHE_NAME).then(cache => cache.put(e.request, clone));
        }
        return response;
      });
    }).catch(() => {
      // Offline fallback for navigation
      if (e.request.mode === 'navigate') {
        return caches.match('/');
      }
    })
  );
});
