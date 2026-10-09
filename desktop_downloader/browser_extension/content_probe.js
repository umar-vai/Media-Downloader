(() => {
  const seen = new Set();
  let fallbackSent = false;

  function isHttp(url) {
    return /^https?:\/\//i.test(String(url || ""));
  }

  function mediaHint(url, initiatorType) {
    const value = String(url || "").toLowerCase();
    if (!isHttp(value)) return false;

    const hints = [
      ".m3u8", ".mpd", ".mp4", ".webm", ".mov", ".mkv", ".mp3", ".m4a",
      ".aac", ".ogg", ".opus", ".wav", ".flac", "/hls/", "/dash/",
      "playlist", "manifest", "master", "stream", "video", "media"
    ];
    if (hints.some((hint) => value.includes(hint))) return true;

    const type = String(initiatorType || "").toLowerCase();
    return type === "video" || type === "audio";
  }

  function report(url, initiatorType = "", contentType = "") {
    const value = String(url || "").trim();
    if (!mediaHint(value, initiatorType)) return;
    const key = value + "|" + initiatorType;
    if (seen.has(key)) return;
    seen.add(key);
    chrome.runtime.sendMessage({
      type: "media-probe-candidate",
      url: value,
      pageUrl: location.href,
      initiatorType,
      contentType
    }).catch(() => {});
  }

  function reportPageFallback() {
    if (fallbackSent || !isHttp(location.href)) return;
    fallbackSent = true;
    chrome.runtime.sendMessage({
      type: "media-page-fallback",
      pageUrl: location.href
    }).catch(() => {});
  }

  function scanMediaElements() {
    const nodes = document.querySelectorAll("video, audio, source");
    for (const node of nodes) {
      const current = node.currentSrc || node.src || node.getAttribute("src") || "";
      if (isHttp(current)) report(current, node.tagName.toLowerCase());
      if (String(current).startsWith("blob:")) reportPageFallback();
    }
  }

  function scanPerformance() {
    try {
      for (const entry of performance.getEntriesByType("resource")) {
        report(entry.name, entry.initiatorType || "");
      }
    } catch (_) {}
  }

  try {
    const observer = new PerformanceObserver((list) => {
      for (const entry of list.getEntries()) {
        report(entry.name, entry.initiatorType || "");
      }
    });
    observer.observe({type: "resource", buffered: true});
  } catch (_) {}

  document.addEventListener("play", (event) => {
    const target = event.target;
    if (!(target instanceof HTMLMediaElement)) return;
    const current = target.currentSrc || target.src || "";

    chrome.runtime.sendMessage({
      type: "media-play-started",
      pageUrl: location.href,
      mediaUrl: current,
      mediaType: target.tagName.toLowerCase()
    }).catch(() => {});

    if (isHttp(current)) {
      report(current, target.tagName.toLowerCase());
    } else {
      // blob:/MediaSource and source-less players need a page/iframe fallback.
      reportPageFallback();
    }
    setTimeout(scanPerformance, 500);
    setTimeout(scanPerformance, 1800);
  }, true);

  document.addEventListener("loadedmetadata", (event) => {
    const target = event.target;
    if (target instanceof HTMLMediaElement) {
      const current = target.currentSrc || target.src || "";
      if (isHttp(current)) report(current, target.tagName.toLowerCase());
    }
  }, true);

  const start = () => {
    scanMediaElements();
    scanPerformance();
    const mutation = new MutationObserver(() => scanMediaElements());
    mutation.observe(document.documentElement || document, {
      subtree: true,
      childList: true,
      attributes: true,
      attributeFilter: ["src"]
    });
    setInterval(() => {
      scanMediaElements();
      scanPerformance();
    }, 2500);
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start, {once: true});
  } else {
    start();
  }
})();
