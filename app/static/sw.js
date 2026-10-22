const CACHE_NAME = 'doce-mel-v2';
const STATIC_ASSETS = ['/static/css/style.css', '/static/img/logo.png'];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE_NAME).then(c => c.addAll(STATIC_ASSETS)));
  self.skipWaiting();
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', e => {
  if (e.request.method !== 'GET') return;
  e.respondWith(
    fetch(e.request).catch(() =>
      caches.match(e.request).then(r => r || new Response('Sem conexao com o servidor.', {
        status: 503,
        headers: { 'Content-Type': 'text/plain; charset=utf-8' }
      }))
    )
  );
});
