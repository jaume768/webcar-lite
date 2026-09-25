// Service worker de RentFlow (version {{ version }}).
// - Estaticos (/static/): primero la cache; se guardan al descargarlos.
// - Paginas: siempre la red. Sin conexion se ensena la pagina "sin conexion".
// Nunca se guardan paginas con datos (reservas, clientes, caja) ni respuestas
// de formularios: en un mostrador compartido no puede quedar nada en el movil.
var CACHE = "rentflow-{{ version }}";
var OFFLINE_URL = {{ offline_url_json|safe }};
var PRECACHE = {{ urls_json|safe }};

self.addEventListener("install", function (event) {
  event.waitUntil(
    caches.open(CACHE)
      .then(function (cache) { return cache.addAll(PRECACHE); })
      .then(function () { return self.skipWaiting(); })
  );
});

self.addEventListener("activate", function (event) {
  event.waitUntil(
    caches.keys()
      .then(function (keys) {
        return Promise.all(
          keys.filter(function (k) { return k !== CACHE; }).map(function (k) { return caches.delete(k); })
        );
      })
      .then(function () { return self.clients.claim(); })
  );
});

self.addEventListener("fetch", function (event) {
  var request = event.request;
  if (request.method !== "GET") return;
  var url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  if (url.pathname.indexOf("/static/") === 0) {
    event.respondWith(
      caches.match(request).then(function (hit) {
        return hit || fetch(request).then(function (resp) {
          if (resp.ok) {
            var copia = resp.clone();
            caches.open(CACHE).then(function (cache) { cache.put(request, copia); });
          }
          return resp;
        });
      })
    );
    return;
  }

  if (request.mode === "navigate") {
    event.respondWith(
      fetch(request).catch(function () { return caches.match(OFFLINE_URL); })
    );
  }
});
