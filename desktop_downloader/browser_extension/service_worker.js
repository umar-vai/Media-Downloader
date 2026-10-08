const REQUEST_HEADERS = new Map();
const MAX_CAPTURES = 120;
const IGNORE_AS_FINAL = [
  ".vtt", ".srt", ".ass", ".jpg", ".jpeg", ".png", ".gif", ".webp",
  ".css", ".js", ".woff", ".woff2"
];
const SEGMENT_EXTENSIONS = [".ts", ".m4s", ".cmfv", ".cmfa"];

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
  }

  const filtered = captures.filter((entry) => !(entry.tab_id === item.tab_id && entry.url === item.url));
  filtered.unshift(item);
  await chrome.storage.session.set({captures: filtered.slice(0, MAX_CAPTURES)});
}

async function saveCapture(details, kind, contentType) {
  if (details.tabId < 0 || !String(details.url || "").startsWith("http")) return;

  // Read request headers before any await. onCompleted can otherwise remove them first.
  const capturedHeaders = REQUEST_HEADERS.get(details.requestId) || {};
  let tab = null;
  try {
    tab = await chrome.tabs.get(details.tabId);
  } catch (_) {}

  const item = {
    id: crypto.randomUUID(),
    captured_at: Date.now() / 1000,
    url: details.url,
    page_url: tab && tab.url ? tab.url : "",
    title: tab && tab.title ? tab.title : new URL(details.url).hostname,
    tab_id: details.tabId,
    kind,
    content_type: contentType || "",
    headers: capturedHeaders
  };
  await storeCapture(item);
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

  const item = {
    id: crypto.randomUUID(),
    captured_at: Date.now() / 1000,
    url: rawUrl,
    page_url: String(message.pageUrl || (tab && tab.url) || ""),
    title: tab && tab.title ? tab.title : "Captured media",
    tab_id: tabId,
    kind,
    content_type: String(message.contentType || ""),
    headers: {}
  };
  await storeCapture(item);
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

chrome.runtime.onMessage.addListener((message, sender) => {
  if (!message || typeof message !== "object") return;
  if (message.type === "media-probe-candidate") {
    saveProbeCandidate(message, sender).catch(() => {});
  } else if (message.type === "media-page-fallback") {
    savePageFallback(message, sender).catch(() => {});
  }
});

chrome.tabs.onRemoved.addListener(async (tabId) => {
  const state = await chrome.storage.session.get({captures: []});
  const captures = Array.isArray(state.captures) ? state.captures : [];
  const filtered = captures.filter((entry) => entry.tab_id !== tabId);
  if (filtered.length !== captures.length) {
    await chrome.storage.session.set({captures: filtered});
  }
});
