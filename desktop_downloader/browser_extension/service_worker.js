const REQUEST_HEADERS = new Map();
const MAX_CAPTURES = 100;
const IGNORE_EXTENSIONS = [
  ".ts", ".m4s", ".cmfv", ".cmfa", ".vtt", ".srt", ".ass", ".jpg", ".jpeg",
  ".png", ".gif", ".webp", ".css", ".js", ".woff", ".woff2"
];

function headerValue(headers, name) {
  const lower = name.toLowerCase();
  const found = (headers || []).find((item) => String(item.name || "").toLowerCase() === lower);
  return found ? String(found.value || "") : "";
}

function classify(url, contentType) {
  let pathname = "";
  try {
    pathname = new URL(url).pathname.toLowerCase();
  } catch (_) {
    return null;
  }
  if (IGNORE_EXTENSIONS.some((ext) => pathname.endsWith(ext))) return null;

  const content = String(contentType || "").toLowerCase().split(";", 1)[0].trim();
  if (pathname.endsWith(".m3u8") ||
      ["application/vnd.apple.mpegurl", "application/x-mpegurl", "audio/mpegurl", "audio/x-mpegurl"].includes(content)) {
    return "hls";
  }
  if (pathname.endsWith(".mpd") || content === "application/dash+xml") return "dash";
  if (pathname.endsWith(".mp4") || pathname.endsWith(".webm") || pathname.endsWith(".mov") ||
      pathname.endsWith(".mkv") || pathname.endsWith(".mp3") || pathname.endsWith(".m4a") ||
      pathname.endsWith(".aac") || pathname.endsWith(".ogg") || pathname.endsWith(".opus") ||
      pathname.endsWith(".wav")) {
    return "direct";
  }
  if (content.startsWith("video/") || content.startsWith("audio/")) return "direct";
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

async function saveCapture(details, kind, contentType) {
  if (details.tabId < 0 || !details.url.startsWith("http")) return;

  let tab = null;
  try {
    tab = await chrome.tabs.get(details.tabId);
  } catch (_) {}

  const capturedHeaders = REQUEST_HEADERS.get(details.requestId) || {};
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

  const state = await chrome.storage.session.get({captures: []});
  const captures = Array.isArray(state.captures) ? state.captures : [];
  const filtered = captures.filter((entry) => !(entry.tab_id === item.tab_id && entry.url === item.url));
  filtered.unshift(item);
  await chrome.storage.session.set({captures: filtered.slice(0, MAX_CAPTURES)});
}

chrome.webRequest.onBeforeSendHeaders.addListener(
  (details) => {
    if (details.tabId < 0) return;
    REQUEST_HEADERS.set(details.requestId, pickHeaders(details.requestHeaders));
  },
  {urls: ["<all_urls>"], types: ["media", "xmlhttprequest", "other"]},
  ["requestHeaders", "extraHeaders"]
);

chrome.webRequest.onHeadersReceived.addListener(
  (details) => {
    const contentType = headerValue(details.responseHeaders, "content-type");
    const kind = classify(details.url, contentType);
    if (kind) {
      saveCapture(details, kind, contentType).catch(() => {});
    }
  },
  {urls: ["<all_urls>"], types: ["media", "xmlhttprequest", "other"]},
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

chrome.tabs.onRemoved.addListener(async (tabId) => {
  const state = await chrome.storage.session.get({captures: []});
  const captures = Array.isArray(state.captures) ? state.captures : [];
  const filtered = captures.filter((entry) => entry.tab_id !== tabId);
  if (filtered.length !== captures.length) {
    await chrome.storage.session.set({captures: filtered});
  }
});
