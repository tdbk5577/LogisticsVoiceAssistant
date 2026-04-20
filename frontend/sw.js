const CACHE = 'elmeeda-v1';
const SHELL = ['/', '/app.js', '/styles.css'];

self.addEventListener('install', e =>
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(SHELL)))
);

self.addEventListener('fetch', e => {
  if (e.request.url.includes('/chat') || e.request.url.includes('/tts') ||
      e.request.url.includes('/alertness') || e.request.url.includes('/hos') ||
      e.request.url.includes('/profile')) return;
  e.respondWith(
    caches.match(e.request).then(r => r || fetch(e.request))
  );
});
