const REQUEST_HEADERS = new Map();
const MAX_CAPTURES = 120;
const IGNORE_AS_FINAL = [
  ".vtt", ".srt", ".ass", ".jpg", ".jpeg", ".png", ".gif", ".webp",
  ".css", ".js", ".woff", ".woff2"
];
const SEGMENT_EXTENSIONS = [".ts", ".m4s", ".cmfv", ".cmfa"];
const PLAYBACK_STATE = new Map();
const AUTO_SEND_TIMERS = new Map();
const AUTO_SEND_DEBOUNCE_MS = 1200;
const AUTO_SEND_RETRY_MS = 2500;
const AUTO_SEND_MAX_RETRIES = 3;
const PLAYBACK_CAPTURE_LOOKBACK_SECONDS = 15;
const OVERLAY_OPTION_CACHE = new Map();
const OVERLAY_CAPTURE_LOOKBACK_SECONDS = 180;
const OVERLAY_CACHE_TTL_MS = 2 * 60 * 1000;

function headerValue(headers, name) {
  const lower = name.toLowerCase();
  const found = (headers || []).find((item) => String(item.name || "").toLowerCase() === lower);
  return found ? String(found.value || "") : "";
}

function urlParts(url) {
  try {
    const parsed = new URL(url);
    return {
      pathname: parsed.pathname.toLowerCase(),
      full: (parsed.pathname + parsed.search).toLowerCase()
    };
  } catch (_) {
    return {pathname: "", full: ""};
  }
}

function hasAny(text, values) {
  return values.some((value) => text.includes(value));
}

function classify(url, contentType, requestType = "", responseHeaders = []) {
  const parts = urlParts(url);
  if (!parts.pathname) return null;
  if (IGNORE_AS_FINAL.some((ext) => parts.pathname.endsWith(ext))) return null;

  const content = String(contentType || "").toLowerCase().split(";", 1)[0].trim();
  const requestKind = String(requestType || "").toLowerCase();
  const contentRange = headerValue(responseHeaders, "content-range");
  const acceptRanges = headerValue(responseHeaders, "accept-ranges").toLowerCase();

  if (parts.full.includes(".m3u8") ||
      hasAny(content, ["application/vnd.apple.mpegurl", "application/x-mpegurl", "audio/mpegurl", "audio/x-mpegurl"])) {
    return "hls";
  }
  if (parts.full.includes(".mpd") || content === "application/dash+xml") return "dash";

  if (hasAny(parts.full, ["/hls/", "hls=", "playlist.m3u", "master.m3u", "manifest.m3u"])) return "hls";
  if (hasAny(parts.full, ["/dash/", "dash=", "manifest.mpd"])) return "dash";

  const directExtensions = [
    ".mp4", ".webm", ".mov", ".mkv", ".mp3", ".m4a", ".aac",
    ".ogg", ".opus", ".wav", ".flac"
  ];
  if (directExtensions.some((ext) => parts.full.includes(ext))) return "direct";
  if (content.startsWith("video/") || content.startsWith("audio/")) {
    if (SEGMENT_EXTENSIONS.some((ext) => parts.pathname.endsWith(ext))) return null;
    return "direct";
  }

  const rangeLike = Boolean(contentRange) || acceptRanges.includes("bytes");
  const binaryLike = ["application/octet-stream", "binary/octet-stream", "application/mp4"].includes(content);
  if (requestKind === "media" && (binaryLike || rangeLike || !content)) return "direct";

  if (binaryLike && rangeLike && hasAny(parts.full, ["video", "media", "stream", "file", "play"])) {
    return "direct";
  }

  return null;
}

function pickHeaders(headers) {
  const allowed = new Set(["referer", "origin", "user-agent", "cookie", "authorization", "accept-language"]);
  const result = {};
  for (const item of headers || []) {
    const name = String(item.name || "").toLowerCase();
    if (!allowed.has(name)) continue;
    const value = String(item.value || "").trim();
    if (!value) continue;
    const canonical = {
      "referer": "Referer",
      "origin": "Origin",
      "user-agent": "User-Agent",
      "cookie": "Cookie",
      "authorization": "Authorization",
      "accept-language": "Accept-Language"
    }[name];
    result[canonical] = value;
  }
  return result;
}

async function storeCapture(item) {
  const state = await chrome.storage.session.get({captures: []});
  const captures = Array.isArray(state.captures) ? state.captures : [];
  const existing = captures.find((entry) => entry.tab_id === item.tab_id && entry.url === item.url);

  // A DOM/performance probe can report the same URL after webRequest. Do not
  // overwrite richer network-captured headers with an empty probe capture.
  if (existing) {
    const newHeaders = item.headers && Object.keys(item.headers).length ? item.headers : null;
    item.headers = newHeaders || existing.headers || {};
    item.content_type = item.content_type || existing.content_type || "";
    item.kind = item.kind && item.kind !== "unknown" ? item.kind : (existing.kind || "unknown");
    item.page_url = item.page_url || existing.page_url || "";
    item.title = item.title || existing.title || "Captured media";
    for (const key of [
      "width", "height", "fps", "tbr", "duration_seconds",
      "total_bytes", "media_type", "itag", "quality_label",
      "quality_status"
    ]) {
      if (!item[key] && existing[key]) item[key] = existing[key];
    }
  }

  const filtered = captures.filter((entry) => !(entry.tab_id === item.tab_id && entry.url === item.url));
  filtered.unshift(item);
  await chrome.storage.session.set({captures: filtered.slice(0, MAX_CAPTURES)});
}

async function extensionSettings() {
  return chrome.storage.local.get({
    port: 38471,
    token: "",
    autoSendBestHls: true
  });
}

function captureFingerprint(items) {
  return items
    .map((item) => String(item.url || ""))
    .filter(Boolean)
    .sort()
    .join("\n");
}

async function postCaptureToDesktop(item, cfg, endpoint = "/capture") {
  const response = await fetch(`http://127.0.0.1:${cfg.port}${endpoint}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Media-Downloader-Token": cfg.token
    },
    body: JSON.stringify(item)
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok || !data.ok) {
    throw new Error(data.error || "Could not send capture to Media Downloader.");
  }
  return data;
}


function overlayCacheKey(tabId, frameId, pageUrl) {
  return `${tabId}:${frameId}:${String(pageUrl || "")}`;
}

const YOUTUBE_ITAG_HEIGHT = new Map([
  [18, 360], [22, 720], [37, 1080], [38, 2160],
  [43, 360], [44, 480], [45, 720], [46, 1080],
  [160, 144], [133, 240], [134, 360], [135, 480],
  [136, 720], [137, 1080], [264, 1440], [266, 2160],
  [271, 1440], [313, 2160], [298, 720], [299, 1080],
  [302, 720], [303, 1080], [308, 1440], [315, 2160]
]);

function parsePositiveNumber(value) {
  const number = Number(value || 0);
  return Number.isFinite(number) && number > 0 ? number : 0;
}

function parseContentRangeTotal(value) {
  const match = String(value || "").match(/\/(\d+)\s*$/);
  return match ? parsePositiveNumber(match[1]) : 0;
}

function decodeMime(value) {
  try { return decodeURIComponent(String(value || "")).toLowerCase(); }
  catch (_) { return String(value || "").toLowerCase(); }
}

function inferDirectMetadata(capture) {
  const result = {
    mediaType: String(capture.media_type || ""),
    height: parsePositiveNumber(capture.height),
    width: parsePositiveNumber(capture.width),
    fps: parsePositiveNumber(capture.fps),
    bitrate: parsePositiveNumber(capture.tbr),
    totalBytes: parsePositiveNumber(capture.total_bytes),
    durationSeconds: parsePositiveNumber(capture.duration_seconds),
    itag: String(capture.itag || "")
  };

  const contentType = String(capture.content_type || "").toLowerCase();
  if (!result.mediaType) {
    if (contentType.startsWith("video/")) result.mediaType = "video";
    else if (contentType.startsWith("audio/")) result.mediaType = "audio";
  }

  try {
    const parsed = new URL(String(capture.url || ""));
    const mime = decodeMime(parsed.searchParams.get("mime") || parsed.searchParams.get("type") || "");
    if (!result.mediaType) {
      if (mime.startsWith("video/")) result.mediaType = "video";
      else if (mime.startsWith("audio/")) result.mediaType = "audio";
    }

    const itag = Number(parsed.searchParams.get("itag") || 0);
    if (itag) {
      result.itag = String(itag);
      if (!result.height && YOUTUBE_ITAG_HEIGHT.has(itag)) {
        result.height = YOUTUBE_ITAG_HEIGHT.get(itag);
      }
    }

    if (!result.height) {
      result.height = inferResolutionFromUrl(capture.url);
    }

    if (!result.fps) result.fps = parsePositiveNumber(parsed.searchParams.get("fps"));
    if (!result.bitrate) {
      const rawBitrate = parsePositiveNumber(
        parsed.searchParams.get("bitrate") ||
        parsed.searchParams.get("br") ||
        parsed.searchParams.get("abr")
      );
      result.bitrate = rawBitrate > 10000 ? rawBitrate / 1000 : rawBitrate;
    }
    if (!result.totalBytes) {
      result.totalBytes = parsePositiveNumber(
        parsed.searchParams.get("clen") ||
        parsed.searchParams.get("contentlength") ||
        parsed.searchParams.get("size")
      );
    }
    if (!result.durationSeconds) {
      result.durationSeconds = parsePositiveNumber(
        parsed.searchParams.get("dur") ||
        parsed.searchParams.get("duration")
      );
    }

    const quality = String(
      parsed.searchParams.get("quality_label") ||
      parsed.searchParams.get("quality") ||
      parsed.searchParams.get("res") ||
      ""
    ).toLowerCase();
    if (!result.height && quality) {
      const match = quality.match(/(2160|1440|1080|900|720|540|480|360|240|144)/);
      if (match) result.height = Number(match[1]);
    }
  } catch (_) {}

  if (!result.bitrate && result.totalBytes && result.durationSeconds) {
    result.bitrate = (result.totalBytes * 8) / result.durationSeconds / 1000;
  }
  if (!result.mediaType && result.height) result.mediaType = "video";
  return result;
}

function canonicalDirectKey(capture) {
  const meta = inferDirectMetadata(capture);
  try {
    const parsed = new URL(String(capture.url || ""));
    const variant = [
      meta.mediaType || "media",
      meta.itag || "",
      meta.height || 0,
      String(parsed.searchParams.get("mime") || ""),
      String(parsed.searchParams.get("quality") || parsed.searchParams.get("quality_label") || ""),
      meta.itag || meta.height ? "" : Math.round(meta.totalBytes || 0)
    ].join("|");
    return `${parsed.origin}${parsed.pathname}|${variant}`;
  } catch (_) {
    return String(capture.url || "");
  }
}

function directCaptureScore(capture) {
  const meta = inferDirectMetadata(capture);
  const headerScore = capture.headers && Object.keys(capture.headers).length ? 5 : 0;
  return (
    (meta.height ? 1000000 + meta.height * 1000 : 0) +
    (meta.bitrate || 0) +
    (meta.totalBytes ? 10 : 0) +
    headerScore +
    Number(capture.captured_at || 0) / 1000000000
  );
}

function enrichDirectCapture(capture) {
  const meta = inferDirectMetadata(capture);
  return {
    ...capture,
    media_type: meta.mediaType,
    height: meta.height,
    width: meta.width,
    fps: meta.fps,
    tbr: meta.bitrate,
    total_bytes: meta.totalBytes,
    duration_seconds: meta.durationSeconds,
    itag: meta.itag,
    quality_status: meta.height ? "ready" : String(capture.quality_status || "unknown"),
    quality_label: meta.height
      ? `${meta.height}p${meta.fps >= 50 ? "60" : ""}`
      : (meta.mediaType === "audio" ? "Audio only" : String(capture.quality_label || "Video"))
  };
}

function inferResolutionFromUrl(url) {
  const value = String(url || "").toLowerCase();
  const p = value.match(/(?:^|[^0-9])(2160|1440|1080|900|720|540|480|360|240)p(?:[^0-9]|$)/);
  if (p) return Number(p[1]);
  const dimensions = value.match(/(?:^|[^0-9])(\d{3,4})x(\d{3,4})(?:[^0-9]|$)/);
  if (dimensions) return Number(dimensions[2]) || 0;
  try {
    const parsed = new URL(url);
    for (const key of ["height", "h", "quality", "res", "resolution"]) {
      const raw = String(parsed.searchParams.get(key) || "");
      const match = raw.match(/(2160|1440|1080|900|720|540|480|360|240)/);
      if (match) return Number(match[1]);
    }
  } catch (_) {}
  return 0;
}

function parseAttributeList(value) {
  const attrs = {};
  const regex = /([A-Z0-9-]+)=((?:"[^"]*")|[^,]*)/gi;
  let match;
  while ((match = regex.exec(String(value || "")))) {
    let raw = String(match[2] || "").trim();
    if (raw.startsWith('"') && raw.endsWith('"')) raw = raw.slice(1, -1);
    attrs[String(match[1] || "").toUpperCase()] = raw;
  }
  return attrs;
}

function safeManifestHeaders(capture) {
  const source = capture && capture.headers && typeof capture.headers === "object"
    ? capture.headers
    : {};
  const result = {};
  for (const name of ["Authorization", "Accept-Language"]) {
    if (source[name]) result[name] = String(source[name]);
  }
  return result;
}

async function fetchManifestText(capture) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 4500);
  try {
    const response = await fetch(String(capture.url || ""), {
      method: "GET",
      headers: safeManifestHeaders(capture),
      credentials: "include",
      cache: "no-store",
      redirect: "follow",
      signal: controller.signal
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const text = await response.text();
    if (!text.includes("#EXTM3U")) throw new Error("Not an HLS manifest");
    return text;
  } finally {
    clearTimeout(timer);
  }
}

function hlsVariantsFromText(text, capture) {
  const lines = String(text || "").split(/\r?\n/);
  const variants = [];
  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index].trim();
    if (!line.startsWith("#EXT-X-STREAM-INF:")) continue;
    const attrs = parseAttributeList(line.slice("#EXT-X-STREAM-INF:".length));
    let uri = "";
    for (let next = index + 1; next < lines.length; next += 1) {
      const candidate = lines[next].trim();
      if (!candidate) continue;
      if (candidate.startsWith("#")) continue;
      uri = candidate;
      break;
    }
    if (!uri) continue;

    let resolved = "";
    try { resolved = new URL(uri, capture.url).href; } catch (_) {}
    if (!resolved) continue;

    let width = 0;
    let height = 0;
    if (attrs.RESOLUTION) {
      const parts = String(attrs.RESOLUTION).toLowerCase().split("x");
      width = Number(parts[0] || 0) || 0;
      height = Number(parts[1] || 0) || 0;
    }
    const bandwidth = Number(attrs["AVERAGE-BANDWIDTH"] || attrs.BANDWIDTH || 0) || 0;
    const fps = Number(attrs["FRAME-RATE"] || 0) || 0;

    variants.push({
      url: resolved,
      width,
      height,
      fps,
      bitrate: Math.round(bandwidth / 1000),
      codecs: String(attrs.CODECS || ""),
      capture: {
        ...capture,
        id: crypto.randomUUID(),
        captured_at: Date.now() / 1000,
        url: resolved,
        kind: "hls",
        width,
        height,
        fps,
        tbr: bandwidth ? bandwidth / 1000 : 0,
        quality_status: height ? "ready" : "unknown",
        quality_label: height ? `${height}p` : "HLS"
      }
    });
  }
  return variants;
}

function optionFromCapture(capture) {
  const kind = String(capture.kind || "media").toLowerCase();
  const source = kind === "direct" ? enrichDirectCapture(capture) : capture;
  const height = Number(source.height || inferResolutionFromUrl(source.url) || 0) || 0;
  const width = Number(source.width || 0) || 0;
  const fps = Number(source.fps || 0) || 0;
  const bitrate = Math.round(Number(source.tbr || 0) || 0);
  const mediaType = String(source.media_type || (height ? "video" : ""));
  return {
    url: String(source.url || ""),
    width,
    height,
    fps,
    bitrate,
    mediaType,
    totalBytes: Number(source.total_bytes || 0) || 0,
    codecs: "",
    capture: {
      ...source,
      height,
      width,
      fps,
      tbr: bitrate,
      media_type: mediaType,
      quality_status: height ? "ready" : String(source.quality_status || "unknown"),
      quality_label: height
        ? `${height}p${fps >= 50 ? "60" : ""}`
        : (mediaType === "audio" ? "Audio only" : String(source.quality_label || "Video"))
    }
  };
}

function optionSortValue(option) {
  const kindScore = option.capture.kind === "hls" ? 3 : option.capture.kind === "dash" ? 2 : 1;
  return [
    Number(option.height || 0),
    Number(option.bitrate || 0),
    Number(option.fps || 0),
    kindScore
  ];
}

function compareOptions(a, b) {
  const left = optionSortValue(a);
  const right = optionSortValue(b);
  for (let i = 0; i < left.length; i += 1) {
    if (left[i] !== right[i]) return right[i] - left[i];
  }
  return 0;
}

async function capturedCandidatesForOverlay(tabId, frameId, pageUrl) {
  const state = await chrome.storage.session.get({captures: []});
  const captures = Array.isArray(state.captures) ? state.captures : [];
  const cutoff = (Date.now() / 1000) - OVERLAY_CAPTURE_LOOKBACK_SECONDS;

  let candidates = captures.filter((item) => {
    return item.tab_id === tabId &&
      ["hls", "dash", "direct"].includes(String(item.kind || "").toLowerCase()) &&
      Number(item.captured_at || 0) >= cutoff &&
      String(item.url || "").startsWith("http");
  });

  const frameMatches = candidates.filter((item) => Number(item.frame_id ?? -99) === Number(frameId));
  if (frameMatches.length) candidates = frameMatches;

  const pageMatches = candidates.filter((item) => String(item.page_url || "") === String(pageUrl || ""));
  if (pageMatches.length) candidates = pageMatches;

  return candidates.slice(0, 24);
}

async function buildOverlayOptions(tabId, frameId, pageUrl) {
  const candidates = await capturedCandidatesForOverlay(tabId, frameId, pageUrl);
  const resolved = [];
  const seen = new Set();
  const directGroups = new Map();

  for (const capture of candidates) {
    const kind = String(capture.kind || "").toLowerCase();
    if (kind === "hls") {
      let variants = [];
      try {
        const text = await fetchManifestText(capture);
        variants = hlsVariantsFromText(text, capture);
      } catch (_) {}
      if (variants.length) {
        for (const variant of variants) {
          if (seen.has(variant.url)) continue;
          seen.add(variant.url);
          resolved.push(variant);
        }
        continue;
      }
    }

    if (kind === "direct") {
      const enriched = enrichDirectCapture(capture);
      const key = canonicalDirectKey(enriched);
      const previous = directGroups.get(key);
      if (!previous || directCaptureScore(enriched) > directCaptureScore(previous)) {
        directGroups.set(key, enriched);
      }
      continue;
    }

    const option = optionFromCapture(capture);
    if (!option.url || seen.has(option.url)) continue;
    seen.add(option.url);
    resolved.push(option);
  }

  for (const capture of directGroups.values()) {
    const option = optionFromCapture(capture);
    if (!option.url || seen.has(option.url)) continue;
    seen.add(option.url);
    resolved.push(option);
  }

  const videoOptions = resolved.filter((item) => item.mediaType !== "audio");
  const displayResolved = videoOptions.length ? videoOptions : resolved;
  displayResolved.sort(compareOptions);
  const available = [...new Set(displayResolved.map((item) => Number(item.height || 0)).filter(Boolean))]
    .sort((a, b) => b - a)
    .map((height) => `${height}p`);

  const playback = PLAYBACK_STATE.get(tabId);
  const batchId = (
    playback &&
    playback.sessionId &&
    (Date.now() - Number(playback.lastPlayAt || playback.lastHlsAt || 0)) < 2 * 60 * 1000
  ) ? playback.sessionId : crypto.randomUUID();

  const items = displayResolved.slice(0, 18).map((option, index) => {
    const id = crypto.randomUUID();
    const height = Number(option.height || 0);
    const fps = Number(option.fps || 0);
    const bitrate = Number(option.bitrate || 0);
    const mediaType = String(option.mediaType || option.capture.media_type || "");
    const quality = height
      ? `${height}p${fps >= 50 ? "60" : ""}`
      : (mediaType === "audio" ? "Audio only" : "Video");
    const sizeMb = Number(option.totalBytes || option.capture.total_bytes || 0) > 0
      ? (Number(option.totalBytes || option.capture.total_bytes) / 1024 / 1024)
      : 0;
    const detail = [
      bitrate ? `${bitrate} kbps` : "",
      sizeMb ? `${sizeMb >= 100 ? sizeMb.toFixed(0) : sizeMb.toFixed(1)} MB` : "",
      option.capture.kind ? String(option.capture.kind).toUpperCase() : ""
    ].filter(Boolean).join(" • ");

    option.capture = {
      ...option.capture,
      capture_group_id: batchId,
      available_qualities: available,
      has_multiple_qualities: available.length > 1,
      quality_status: height ? "ready" : String(option.capture.quality_status || "unknown"),
      quality_label: height ? quality : String(option.capture.quality_label || quality)
    };

    return {
      id,
      label: quality,
      detail,
      best: index === 0,
      height,
      width: Number(option.width || 0),
      fps,
      bitrate,
      kind: String(option.capture.kind || "media"),
      capture: option.capture
    };
  });

  const key = overlayCacheKey(tabId, frameId, pageUrl);
  OVERLAY_OPTION_CACHE.set(key, {at: Date.now(), items});
  return items;
}

function publicOverlayOptions(items) {
  return items.map((item) => ({
    id: item.id,
    label: item.label,
    detail: item.detail,
    best: item.best,
    height: item.height,
    width: item.width,
    fps: item.fps,
    bitrate: item.bitrate,
    kind: item.kind
  }));
}

async function getOverlayOptions(message, sender) {
  const tabId = sender && sender.tab ? sender.tab.id : -1;
  const frameId = Number(sender && Number.isInteger(sender.frameId) ? sender.frameId : 0);
  if (typeof tabId !== "number" || tabId < 0) {
    return {ok: false, error: "This page is not attached to a browser tab."};
  }

  const pageUrl = String(message.pageUrl || "");
  const key = overlayCacheKey(tabId, frameId, pageUrl);
  const cached = OVERLAY_OPTION_CACHE.get(key);
  if (cached && (Date.now() - Number(cached.at || 0)) < OVERLAY_CACHE_TTL_MS) {
    return {ok: true, options: publicOverlayOptions(cached.items)};
  }

  const items = await buildOverlayOptions(tabId, frameId, pageUrl);
  return {
    ok: true,
    options: publicOverlayOptions(items),
    message: items.length ? "" : "Play the video for a moment so its media stream can be detected."
  };
}

async function downloadOverlayOption(message, sender) {
  const tabId = sender && sender.tab ? sender.tab.id : -1;
  const frameId = Number(sender && Number.isInteger(sender.frameId) ? sender.frameId : 0);
  const pageUrl = String(message.pageUrl || "");
  const optionId = String(message.optionId || "");
  if (typeof tabId !== "number" || tabId < 0 || !optionId) {
    return {ok: false, error: "Invalid download request."};
  }

  const key = overlayCacheKey(tabId, frameId, pageUrl);
  let cached = OVERLAY_OPTION_CACHE.get(key);
  if (!cached || (Date.now() - Number(cached.at || 0)) >= OVERLAY_CACHE_TTL_MS) {
    const items = await buildOverlayOptions(tabId, frameId, pageUrl);
    cached = {at: Date.now(), items};
    OVERLAY_OPTION_CACHE.set(key, cached);
  }

  const selected = cached.items.find((item) => item.id === optionId);
  if (!selected) return {ok: false, error: "That stream expired. Open the menu again to refresh qualities."};

  const cfg = await extensionSettings();
  if (!cfg.token) return {ok: false, error: "Pair the extension with Media Downloader first."};

  try {
    // Seed all detected qualities first so the desktop engine has lower-quality
    // fallbacks if the chosen signed stream expires during download.
    for (const option of cached.items) {
      await postCaptureToDesktop(option.capture, cfg, "/capture");
    }
    const result = await postCaptureToDesktop(selected.capture, cfg, "/capture-download");
    return {
      ok: true,
      captureId: result.capture_id || "",
      label: selected.label
    };
  } catch (error) {
    return {
      ok: false,
      error: String(error && error.message ? error.message : "Open Media Downloader and try again.")
    };
  }
}

async function hlsCandidatesForPlayback(tabId, playback) {
  const state = await chrome.storage.session.get({captures: []});
  const captures = Array.isArray(state.captures) ? state.captures : [];
  const started = playback.startedAt / 1000;
  const tightCutoff = started - 4;
  const wideCutoff = started - PLAYBACK_CAPTURE_LOOKBACK_SECONDS;

  const eligible = captures.filter((item) => {
    return item.tab_id === tabId &&
      item.kind === "hls" &&
      Number(item.captured_at || 0) >= wideCutoff &&
      String(item.url || "").startsWith("http");
  });

  let candidates = eligible.filter((item) => Number(item.captured_at || 0) >= tightCutoff);
  if (!candidates.length) candidates = eligible;

  const samePage = candidates.filter((item) => String(item.page_url || "") === playback.pageUrl);
  if (samePage.length) candidates = samePage;

  const seen = new Set();
  const unique = [];
  for (const item of candidates) {
    const url = String(item.url || "");
    if (!url || seen.has(url)) continue;
    seen.add(url);
    unique.push(item);
  }
  return unique.slice(0, 16);
}

function clearAutoSendTimer(tabId) {
  const timer = AUTO_SEND_TIMERS.get(tabId);
  if (timer) clearTimeout(timer);
  AUTO_SEND_TIMERS.delete(tabId);
}

async function ensurePassiveHlsPlayback(tabId, item) {
  if (typeof tabId !== "number" || tabId < 0) return null;

  const pageUrl = String(item && item.page_url ? item.page_url : "");
  const now = Date.now();
  const existing = PLAYBACK_STATE.get(tabId);

  // Some players (especially iframe/MSE players) never bubble an HTMLMediaElement
  // play event to our content script. Treat a fresh HLS manifest request itself
  // as sufficient evidence of active playback so auto-send still works.
  const recentHlsBurst = Boolean(
    existing &&
    existing.pageUrl === pageUrl &&
    (now - Number(existing.lastHlsAt || existing.lastPlayAt || 0)) < 8000
  );
  if (recentHlsBurst) {
    existing.lastPlayAt = now;
    existing.lastHlsAt = now;
    PLAYBACK_STATE.set(tabId, existing);
    return existing;
  }

  const playback = {
    sessionId: crypto.randomUUID(),
    pageUrl,
    mediaUrl: "",
    startedAt: now,
    lastPlayAt: now,
    lastHlsAt: now,
    lastSentFingerprint: "",
    lastSentAt: 0,
    retryCount: 0,
    passiveHls: true
  };
  PLAYBACK_STATE.set(tabId, playback);
  return playback;
}

function scheduleBestHlsAutoSend(tabId, delay = AUTO_SEND_DEBOUNCE_MS) {
  const playback = PLAYBACK_STATE.get(tabId);
  if (!playback) return;
  clearAutoSendTimer(tabId);
  const timer = setTimeout(() => {
    AUTO_SEND_TIMERS.delete(tabId);
    autoSendBestHls(tabId).catch(() => {});
  }, Math.max(250, Number(delay) || AUTO_SEND_DEBOUNCE_MS));
  AUTO_SEND_TIMERS.set(tabId, timer);
}

async function autoSendBestHls(tabId) {
  const playback = PLAYBACK_STATE.get(tabId);
  if (!playback) return;

  const cfg = await extensionSettings();
  if (!cfg.autoSendBestHls || !cfg.token) return;

  const candidates = await hlsCandidatesForPlayback(tabId, playback);
  if (!candidates.length) {
    if (playback.retryCount < AUTO_SEND_MAX_RETRIES) {
      playback.retryCount += 1;
      PLAYBACK_STATE.set(tabId, playback);
      scheduleBestHlsAutoSend(tabId, AUTO_SEND_RETRY_MS);
    }
    return;
  }

  const fingerprint = captureFingerprint(candidates);
  if (fingerprint && fingerprint === playback.lastSentFingerprint) return;

  try {
    for (const candidate of candidates) {
      await postCaptureToDesktop(
        {
          ...candidate,
          capture_group_id: playback.sessionId
        },
        cfg
      );
    }

    playback.lastSentFingerprint = fingerprint;
    playback.retryCount = 0;
    playback.lastSentAt = Date.now();
    PLAYBACK_STATE.set(tabId, playback);
    await chrome.storage.local.set({
      lastAutoSendStatus: {
        ok: true,
        tabId,
        count: candidates.length,
        at: Date.now()
      }
    });
  } catch (error) {
    await chrome.storage.local.set({
      lastAutoSendStatus: {
        ok: false,
        tabId,
        message: String(error && error.message ? error.message : "Auto-send failed"),
        at: Date.now()
      }
    });
    if (playback.retryCount < AUTO_SEND_MAX_RETRIES) {
      playback.retryCount += 1;
      PLAYBACK_STATE.set(tabId, playback);
      scheduleBestHlsAutoSend(tabId, AUTO_SEND_RETRY_MS);
    }
  }
}

async function markPlaybackStarted(message, sender) {
  const tabId = sender && sender.tab ? sender.tab.id : -1;
  if (typeof tabId !== "number" || tabId < 0) return;

  let tab = sender.tab || null;
  if (!tab || !tab.url) {
    try { tab = await chrome.tabs.get(tabId); } catch (_) {}
  }

  const pageUrl = String((tab && tab.url) || message.pageUrl || "");
  const mediaUrl = String(message.mediaUrl || "");
  const now = Date.now();
  const existing = PLAYBACK_STATE.get(tabId);
  const sameMedia = Boolean(
    !mediaUrl ||
    !existing ||
    !existing.mediaUrl ||
    existing.mediaUrl === mediaUrl
  );
  const samePlayback = Boolean(
    existing &&
    existing.pageUrl === pageUrl &&
    sameMedia &&
    (now - Number(existing.lastPlayAt || 0)) < 2 * 60 * 1000
  );

  const playback = samePlayback
    ? existing
    : {
        sessionId: crypto.randomUUID(),
        pageUrl,
        mediaUrl,
        startedAt: now,
        lastSentFingerprint: "",
        lastSentAt: 0,
        retryCount: 0
      };

  playback.mediaUrl = mediaUrl || playback.mediaUrl || "";
  playback.lastPlayAt = now;
  playback.passiveHls = false;
  playback.retryCount = 0;
  PLAYBACK_STATE.set(tabId, playback);

  // The HLS manifest is often requested before the actual play event.
  // Give webRequest/performance probes a short moment to settle, then send
  // all current HLS candidates under one batch ID. The desktop app resolves
  // the best available quality and keeps lower variants as fallbacks.
  scheduleBestHlsAutoSend(tabId, 700);
}

async function saveCapture(details, kind, contentType) {
  if (details.tabId < 0 || !String(details.url || "").startsWith("http")) return;

  // Read request headers before any await. onCompleted can otherwise remove them first.
  const capturedHeaders = REQUEST_HEADERS.get(details.requestId) || {};
  let tab = null;
  try {
    tab = await chrome.tabs.get(details.tabId);
  } catch (_) {}

  const contentLength = parsePositiveNumber(headerValue(details.responseHeaders, "content-length"));
  const contentRangeTotal = parseContentRangeTotal(headerValue(details.responseHeaders, "content-range"));
  const directMeta = kind === "direct"
    ? inferDirectMetadata({
        url: details.url,
        content_type: contentType || "",
        total_bytes: contentRangeTotal || contentLength
      })
    : null;

  const item = {
    id: crypto.randomUUID(),
    captured_at: Date.now() / 1000,
    url: details.url,
    page_url: tab && tab.url ? tab.url : "",
    title: tab && tab.title ? tab.title : new URL(details.url).hostname,
    tab_id: details.tabId,
    kind,
    content_type: contentType || "",
    headers: capturedHeaders,
    frame_id: Number(details.frameId ?? -1),
    total_bytes: contentRangeTotal || contentLength || 0,
    media_type: directMeta ? directMeta.mediaType : "",
    height: directMeta ? directMeta.height : 0,
    fps: directMeta ? directMeta.fps : 0,
    tbr: directMeta ? directMeta.bitrate : 0,
    duration_seconds: directMeta ? directMeta.durationSeconds : 0,
    itag: directMeta ? directMeta.itag : ""
  };
  await storeCapture(item);
  if (kind === "hls") {
    await ensurePassiveHlsPlayback(details.tabId, item);
    scheduleBestHlsAutoSend(details.tabId);
  }
}

async function saveProbeCandidate(message, sender) {
  const tabId = sender && sender.tab ? sender.tab.id : -1;
  if (typeof tabId !== "number" || tabId < 0) return;

  let tab = sender.tab || null;
  if (!tab || !tab.title) {
    try { tab = await chrome.tabs.get(tabId); } catch (_) {}
  }

  const rawUrl = String(message.url || "").trim();
  if (!rawUrl.startsWith("http://") && !rawUrl.startsWith("https://")) return;

  const kind = classify(rawUrl, message.contentType || "", message.initiatorType || "", []);
  if (!kind) return;

  const directMeta = kind === "direct"
    ? inferDirectMetadata({
        url: rawUrl,
        content_type: String(message.contentType || ""),
        width: message.width,
        height: message.height,
        duration_seconds: message.durationSeconds
      })
    : null;

  const item = {
    id: crypto.randomUUID(),
    captured_at: Date.now() / 1000,
    url: rawUrl,
    page_url: String(message.pageUrl || (tab && tab.url) || ""),
    title: tab && tab.title ? tab.title : "Captured media",
    tab_id: tabId,
    kind,
    content_type: String(message.contentType || ""),
    headers: {},
    frame_id: Number(sender && Number.isInteger(sender.frameId) ? sender.frameId : -1),
    width: parsePositiveNumber(message.width),
    height: directMeta ? directMeta.height : parsePositiveNumber(message.height),
    duration_seconds: directMeta ? directMeta.durationSeconds : parsePositiveNumber(message.durationSeconds),
    media_type: directMeta ? directMeta.mediaType : "",
    fps: directMeta ? directMeta.fps : 0,
    tbr: directMeta ? directMeta.bitrate : 0,
    total_bytes: directMeta ? directMeta.totalBytes : 0,
    itag: directMeta ? directMeta.itag : ""
  };
  await storeCapture(item);
  if (kind === "hls") {
    await ensurePassiveHlsPlayback(tabId, item);
    scheduleBestHlsAutoSend(tabId);
  }
}

async function savePageFallback(message, sender) {
  const tabId = sender && sender.tab ? sender.tab.id : -1;
  if (typeof tabId !== "number" || tabId < 0) return;

  let tab = sender.tab || null;
  if (!tab || !tab.title) {
    try { tab = await chrome.tabs.get(tabId); } catch (_) {}
  }

  const frameUrl = String(message.pageUrl || "").trim();
  const topUrl = String((tab && tab.url) || "").trim();
  const target = frameUrl.startsWith("http") ? frameUrl : topUrl;
  if (!target.startsWith("http://") && !target.startsWith("https://")) return;

  const item = {
    id: crypto.randomUUID(),
    captured_at: Date.now() / 1000,
    url: target,
    page_url: topUrl || target,
    title: tab && tab.title ? tab.title : "Video page",
    tab_id: tabId,
    kind: "page",
    content_type: "",
    headers: {}
  };
  await storeCapture(item);
}

chrome.webRequest.onBeforeSendHeaders.addListener(
  (details) => {
    if (details.tabId < 0) return;
    REQUEST_HEADERS.set(details.requestId, pickHeaders(details.requestHeaders));
  },
  {urls: ["<all_urls>"], types: ["media", "xmlhttprequest", "other", "sub_frame"]},
  ["requestHeaders", "extraHeaders"]
);

chrome.webRequest.onHeadersReceived.addListener(
  (details) => {
    const contentType = headerValue(details.responseHeaders, "content-type");
    const kind = classify(details.url, contentType, details.type, details.responseHeaders || []);
    if (kind) saveCapture(details, kind, contentType).catch(() => {});
  },
  {urls: ["<all_urls>"], types: ["media", "xmlhttprequest", "other", "sub_frame"]},
  ["responseHeaders", "extraHeaders"]
);

chrome.webRequest.onCompleted.addListener(
  (details) => REQUEST_HEADERS.delete(details.requestId),
  {urls: ["<all_urls>"]}
);

chrome.webRequest.onErrorOccurred.addListener(
  (details) => REQUEST_HEADERS.delete(details.requestId),
  {urls: ["<all_urls>"]}
);

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (!message || typeof message !== "object") return;
  if (message.type === "media-probe-candidate") {
    saveProbeCandidate(message, sender).catch(() => {});
  } else if (message.type === "media-page-fallback") {
    savePageFallback(message, sender).catch(() => {});
  } else if (message.type === "media-play-started") {
    markPlaybackStarted(message, sender).catch(() => {});
  } else if (message.type === "overlay-get-options") {
    getOverlayOptions(message, sender)
      .then(sendResponse)
      .catch((error) => sendResponse({ok: false, error: String(error && error.message ? error.message : error)}));
    return true;
  } else if (message.type === "overlay-download-option") {
    downloadOverlayOption(message, sender)
      .then(sendResponse)
      .catch((error) => sendResponse({ok: false, error: String(error && error.message ? error.message : error)}));
    return true;
  }
});

chrome.tabs.onUpdated.addListener((tabId, changeInfo) => {
  if (!changeInfo.url) return;
  PLAYBACK_STATE.delete(tabId);
  clearAutoSendTimer(tabId);
  for (const key of [...OVERLAY_OPTION_CACHE.keys()]) {
    if (key.startsWith(`${tabId}:`)) OVERLAY_OPTION_CACHE.delete(key);
  }
});

chrome.tabs.onRemoved.addListener(async (tabId) => {
  PLAYBACK_STATE.delete(tabId);
  clearAutoSendTimer(tabId);
  for (const key of [...OVERLAY_OPTION_CACHE.keys()]) {
    if (key.startsWith(`${tabId}:`)) OVERLAY_OPTION_CACHE.delete(key);
  }
  const state = await chrome.storage.session.get({captures: []});
  const captures = Array.isArray(state.captures) ? state.captures : [];
  const filtered = captures.filter((entry) => entry.tab_id !== tabId);
  if (filtered.length !== captures.length) {
    await chrome.storage.session.set({captures: filtered});
  }
});
