const CACHE_NAME = 'doce-mel-v3';

self.addEventListener('install', event => {
    event.waitUntil(
        caches.open(CACHE_NAME)
            .then(cache => cache.addAll(['/static/css/style.css', '/static/img/logo.png']))
            .then(() => self.skipWaiting())
    );
});

self.addEventListener('activate', event => {
    event.waitUntil(
        caches.keys()
            .then(cacheNames => Promise.all(
                cacheNames.filter(cacheName => cacheName !== CACHE_NAME)
                    .map(cacheName => caches.delete(cacheName))
            ))
            .then(() => clients.claim())
    );
});

self.addEventListener('fetch', event => {
    if (event.request.method !== 'GET') {
        event.respondWith(fetch(event.request));
        return;
    }

    if (event.request.url.includes('/static/')) {
        // Network-first: sempre tenta o servidor; cache so como reserva offline
        event.respondWith(
            fetch(event.request)
                .then(response => {
                    if (response.ok) {
                        const copia = response.clone();
                        caches.open(CACHE_NAME).then(cache => cache.put(event.request, copia));
                    }
                    return response;
                })
                .catch(() => caches.match(event.request))
        );
        return;
    }

    event.respondWith(fetch(event.request));
});