const CACHE = "media-downloader-pwa-v7";
const SHELL = ["/","/index.html","/styles.css","/app.js","/manifest.webmanifest","/icon.svg"];
self.addEventListener("install",(event)=>event.waitUntil(caches.open(CACHE).then((cache)=>cache.addAll(SHELL))));
self.addEventListener("activate",(event)=>event.waitUntil(caches.keys().then((keys)=>Promise.all(keys.filter((key)=>key!==CACHE).map((key)=>caches.delete(key))))));
self.addEventListener("fetch",(event)=>{
  const url=new URL(event.request.url);
  if(url.pathname.startsWith("/api/")) return;
  event.respondWith(fetch(event.request).catch(()=>caches.match(event.request).then((r)=>r||caches.match("/index.html"))));
});
