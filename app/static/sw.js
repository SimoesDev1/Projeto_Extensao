const CACHE_NAME = 'doce-mel-v1';
const STATIC_ASSETS = [
    '/',
    '/static/css/style.css',
    '/static/img/logo.png'
];

self.addEventListener('install', e => {
    e.waitUntil(caches.open(CACHE_NAME).then(c => c.addAll(STATIC_ASSETS)));
});

self.addEventListener('fetch', e => {
    if (e.request.method !== 'GET') return;
    e.respondWith(
        caches.match(e.request).then(response => {
            return response || fetch(e.request);
        })
    );
});
