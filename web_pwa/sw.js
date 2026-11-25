const CACHE_PREFIX = "media-downloader-pwa-";
const CACHE = CACHE_PREFIX + "v11";
const SHELL = ["/", "/index.html", "/styles.css", "/app.js", "/manifest.webmanifest", "/icon.svg"];

self.addEventListener("install", event => {
  event.waitUntil(caches.open(CACHE).then(cache => cache.addAll(SHELL)));
});

self.addEventListener("activate", event => {
  event.waitUntil(caches.keys().then(keys => Promise.all(
    keys.filter(key => key.startsWith(CACHE_PREFIX) && key !== CACHE).map(key => caches.delete(key))
  )));
});

self.addEventListener("fetch", event => {
  const request = event.request;
  const url = new URL(request.url);
  if (request.method !== "GET" || url.origin !== self.location.origin || url.pathname.startsWith("/api/")) return;
  event.respondWith(fetch(request).catch(async () => {
    const cache = await caches.open(CACHE);
    const match = await cache.match(request);
    if (match) return match;
    if (request.mode === "navigate") {
      const shell = await cache.match("/index.html");
      if (shell) return shell;
    }
    return Response.error();
  }));
});
