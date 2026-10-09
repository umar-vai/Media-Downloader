(() => {
  const seen = new Set();
  let fallbackSent = false;
  const overlayByMedia = new WeakMap();
  const overlayHosts = new Set();
  const mediaState = new WeakMap();
  let overlayScanTimer = null;

  function stateForMedia(media) {
    let state = mediaState.get(media);
    if (!state) {
      const activeNow = media instanceof HTMLMediaElement && !media.paused && media.readyState >= 2;
      state = {
        key: crypto.randomUUID(),
        startedAt: activeNow ? Date.now() - 3000 : 0,
        lastPlayAt: activeNow ? Date.now() : 0
      };
      mediaState.set(media, state);
    }
    return state;
  }

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

  function mediaMetadata(node) {
    if (!(node instanceof HTMLMediaElement)) return {};
    const width = node instanceof HTMLVideoElement ? Number(node.videoWidth || 0) : 0;
    const height = node instanceof HTMLVideoElement ? Number(node.videoHeight || 0) : 0;
    const duration = Number.isFinite(node.duration) ? Number(node.duration || 0) : 0;
    return {
      width,
      height,
      durationSeconds: duration,
      readyState: Number(node.readyState || 0)
    };
  }

  function report(url, initiatorType = "", contentType = "", metadata = {}) {
    const value = String(url || "").trim();
    if (!mediaHint(value, initiatorType)) return;
    const width = Number(metadata.width || 0) || 0;
    const height = Number(metadata.height || 0) || 0;
    const durationSeconds = Number(metadata.durationSeconds || 0) || 0;
    const key = value + "|" + initiatorType + "|" + width + "x" + height;
    if (seen.has(key)) return;
    seen.add(key);
    chrome.runtime.sendMessage({
      type: "media-probe-candidate",
      url: value,
      pageUrl: location.href,
      initiatorType,
      contentType,
      width,
      height,
      durationSeconds,
      readyState: Number(metadata.readyState || 0) || 0
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
      if (isHttp(current)) report(current, node.tagName.toLowerCase(), "", mediaMetadata(node));
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

    const metadata = mediaMetadata(target);
    const playerState = stateForMedia(target);
    const now = Date.now();
    if (!playerState.startedAt || (now - playerState.lastPlayAt) > 15000) {
      playerState.startedAt = now;
    }
    playerState.lastPlayAt = now;

    chrome.runtime.sendMessage({
      type: "media-play-started",
      pageUrl: location.href,
      mediaUrl: current,
      mediaType: target.tagName.toLowerCase(),
      width: metadata.width || 0,
      height: metadata.height || 0,
      durationSeconds: metadata.durationSeconds || 0,
      mediaKey: playerState.key,
      playbackStartedAt: playerState.startedAt
    }).catch(() => {});

    if (isHttp(current)) {
      report(current, target.tagName.toLowerCase(), "", metadata);
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
      if (isHttp(current)) report(current, target.tagName.toLowerCase(), "", mediaMetadata(target));
    }
  }, true);


  function mediaPageUrl(media) {
    const selectors = [
      'a[href*="/reel/"]',
      'a[href*="/reels/"]',
      'a[href*="/p/"]',
      'a[href*="/tv/"]',
      'a[href*="/video/"]',
      'a[href*="/videos/"]',
      'a[href*="/watch/"]'
    ];
    let node = media;
    for (let depth = 0; depth < 8 && node; depth += 1) {
      if (node.querySelector) {
        for (const selector of selectors) {
          const anchor = node.querySelector(selector);
          if (anchor && anchor.href && isHttp(anchor.href)) return anchor.href;
        }
      }
      node = node.parentElement;
    }
    return "";
  }

  function isVisibleMedia(media) {
    if (!(media instanceof HTMLMediaElement)) return false;
    const rect = media.getBoundingClientRect();
    if (rect.width < 220 || rect.height < 120) return false;
    const style = getComputedStyle(media);
    return style.display !== "none" && style.visibility !== "hidden" && Number(style.opacity || 1) > 0;
  }

  function positionOverlay(media, host) {
    if (!host || !host.isConnected || !isVisibleMedia(media)) {
      if (host) host.style.display = "none";
      return;
    }
    const rect = media.getBoundingClientRect();
    const buttonWidth = Math.min(170, Math.max(132, rect.width * 0.28));
    host.style.display = "block";
    host.style.width = `${buttonWidth}px`;
    host.style.left = `${Math.max(8, rect.right - buttonWidth - 10)}px`;
    host.style.top = `${Math.max(8, rect.top + 10)}px`;
  }

  function makeOverlay(media) {
    if (overlayByMedia.has(media) || !document.documentElement) return;
    const host = document.createElement("div");
    host.setAttribute("data-media-downloader-overlay", "1");
    host.style.position = "fixed";
    host.style.zIndex = "2147483647";
    host.style.fontFamily = "Segoe UI, Arial, sans-serif";
    host.style.pointerEvents = "auto";
    host.style.display = "none";

    const root = host.attachShadow({mode: "open"});
    root.innerHTML = `
      <style>
        :host { all: initial; }
        .wrap { position: relative; width: 100%; font-family: Segoe UI, Arial, sans-serif; }
        button { font-family: inherit; }
        .trigger {
          width: 100%; height: 34px; border: 1px solid rgba(255,255,255,.18);
          border-radius: 9px; background: rgba(10,20,36,.94); color: #fff;
          box-shadow: 0 8px 24px rgba(0,0,0,.35); cursor: pointer;
          font-size: 12px; font-weight: 700; backdrop-filter: blur(10px);
        }
        .trigger:hover { background: rgba(30,45,72,.98); }
        .menu {
          display: none; position: absolute; right: 0; top: 39px; width: 260px;
          background: #091321; color: #f7faff; border: 1px solid #263852;
          border-radius: 12px; box-shadow: 0 18px 40px rgba(0,0,0,.5);
          overflow: hidden;
        }
        .menu.open { display: block; }
        .head { padding: 10px 12px; font-size: 11px; color: #9fb0c8; border-bottom: 1px solid #223456; }
        .option {
          display: flex; align-items: center; justify-content: space-between; gap: 8px;
          width: 100%; border: 0; border-bottom: 1px solid rgba(255,255,255,.06);
          padding: 10px 12px; background: transparent; color: #f7faff; cursor: pointer;
          text-align: left;
        }
        .option:hover { background: #122139; }
        .quality { font-weight: 800; font-size: 12px; }
        .detail { display: block; margin-top: 2px; font-size: 9px; color: #8fa2bf; font-weight: 500; }
        .best { font-size: 8px; padding: 3px 6px; border-radius: 999px; background: #7657ff; color: #fff; }
        .status { padding: 11px 12px; color: #a7b5c9; font-size: 10px; line-height: 1.35; }
        .status.error { color: #ff7b8d; }
        .status.ok { color: #27d796; }
      </style>
      <div class="wrap">
        <button class="trigger" type="button">Download video ▾</button>
        <div class="menu">
          <div class="head">Click a quality to download</div>
          <div class="body"><div class="status">Play the video, then choose a quality.</div></div>
        </div>
      </div>
    `;

    const trigger = root.querySelector(".trigger");
    const menu = root.querySelector(".menu");
    const body = root.querySelector(".body");

    function status(text, mode = "") {
      body.innerHTML = "";
      const node = document.createElement("div");
      node.className = `status ${mode}`.trim();
      node.textContent = text;
      body.appendChild(node);
    }

    async function loadOptions() {
      status("Detecting available streams…");
      let response;
      try {
        const metadata = mediaMetadata(media);
        const playerState = stateForMedia(media);
        response = await chrome.runtime.sendMessage({
          type: "overlay-get-options",
          pageUrl: location.href,
          mediaKey: playerState.key,
          mediaUrl: media.currentSrc || media.src || "",
          width: metadata.width || 0,
          height: metadata.height || 0,
          durationSeconds: metadata.durationSeconds || 0,
          playbackStartedAt: playerState.startedAt || 0,
          mediaPageUrl: mediaPageUrl(media)
        });
      } catch (_) {
        status("Extension service is unavailable. Reload the extension.", "error");
        return;
      }

      if (!response || !response.ok) {
        status((response && response.error) || "Could not detect media streams.", "error");
        return;
      }
      const options = Array.isArray(response.options) ? response.options : [];
      if (!options.length) {
        status(response.message || "Play the video for a moment, then try again.");
        return;
      }

      body.innerHTML = "";
      for (const option of options) {
        const button = document.createElement("button");
        button.className = "option";
        button.type = "button";

        const left = document.createElement("span");
        const quality = document.createElement("span");
        quality.className = "quality";
        quality.textContent = option.label || "Video";
        left.appendChild(quality);

        if (option.detail) {
          const detail = document.createElement("span");
          detail.className = "detail";
          detail.textContent = option.detail;
          left.appendChild(detail);
        }
        button.appendChild(left);

        if (option.best) {
          const best = document.createElement("span");
          best.className = "best";
          best.textContent = "BEST";
          button.appendChild(best);
        }

        button.addEventListener("click", async (event) => {
          event.stopPropagation();
          status(`Sending ${option.label || "video"} to Media Downloader…`);
          try {
            const metadata = mediaMetadata(media);
            const playerState = stateForMedia(media);
            const result = await chrome.runtime.sendMessage({
              type: "overlay-download-option",
              pageUrl: location.href,
              mediaKey: playerState.key,
              mediaUrl: media.currentSrc || media.src || "",
              width: metadata.width || 0,
              height: metadata.height || 0,
              durationSeconds: metadata.durationSeconds || 0,
              playbackStartedAt: playerState.startedAt || 0,
              mediaPageUrl: mediaPageUrl(media),
              optionId: option.id
            });
            if (result && result.ok) {
              status(`${result.label || option.label || "Video"} queued in Media Downloader.`, "ok");
              trigger.textContent = "Sent to app ✓";
              setTimeout(() => { trigger.textContent = "Download video ▾"; menu.classList.remove("open"); }, 1600);
            } else {
              status((result && result.error) || "Open Media Downloader and try again.", "error");
            }
          } catch (_) {
            status("Open Media Downloader and try again.", "error");
          }
        });
        body.appendChild(button);
      }
    }

    trigger.addEventListener("click", (event) => {
      event.stopPropagation();
      const opening = !menu.classList.contains("open");
      menu.classList.toggle("open");
      if (opening) loadOptions();
    });

    root.addEventListener("click", (event) => event.stopPropagation());
    document.addEventListener("click", () => menu.classList.remove("open"), true);

    document.documentElement.appendChild(host);
    overlayByMedia.set(media, host);
    overlayHosts.add(host);
    positionOverlay(media, host);
  }

  function scanVideoOverlays() {
    for (const media of document.querySelectorAll("video")) {
      if (!(media instanceof HTMLVideoElement)) continue;
      if (!overlayByMedia.has(media)) makeOverlay(media);
      const host = overlayByMedia.get(media);
      if (host) positionOverlay(media, host);
    }
  }

  function refreshOverlayPositions() {
    for (const media of document.querySelectorAll("video")) {
      const host = overlayByMedia.get(media);
      if (host) positionOverlay(media, host);
    }
  }

  window.addEventListener("scroll", refreshOverlayPositions, true);
  window.addEventListener("resize", refreshOverlayPositions, true);
  document.addEventListener("fullscreenchange", () => setTimeout(refreshOverlayPositions, 50), true);

  const start = () => {
    scanMediaElements();
    scanPerformance();
    scanVideoOverlays();
    const mutation = new MutationObserver(() => {
      scanMediaElements();
      scanVideoOverlays();
    });
    mutation.observe(document.documentElement || document, {
      subtree: true,
      childList: true,
      attributes: true,
      attributeFilter: ["src"]
    });
    overlayScanTimer = setInterval(() => {
      scanMediaElements();
      scanPerformance();
      scanVideoOverlays();
    }, 2000);
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start, {once: true});
  } else {
    start();
  }
})();
