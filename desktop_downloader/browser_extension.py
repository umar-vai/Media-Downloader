"""Source of the Media Downloader browser extension (Chrome / Edge / Brave / Firefox, Manifest V3).

The files are embedded here so the installed app can export them to a folder, which the user
then loads once with "Load unpacked" (Chrome/Edge) or "Load Temporary Add-on" (Firefox).

What the extension does:
* watches the network traffic of each tab for real media streams (HLS .m3u8, DASH .mpd and
  plain video/audio files), the same way IDM does, so it also works on players that only expose
  a blob: URL. Switching a player to another source (for example Sub -> Dub) loads a new
  playlist, which becomes the new "Now playing" entry;
* reads HLS master playlists to list every quality (1080p, 720p, ...);
* shows a "Download" button on top of every video/audio player; its menu lists the captured
  streams, and a click sends the stream, with the browser's Referer/Origin/User-Agent and
  cookies, to the desktop app;
* never runs on the sites the user lists under "Blocked sites" in the extension options.
"""

from __future__ import annotations

import json
from pathlib import Path

EXTENSION_VERSION = "2.9.0"

MANIFEST = {
    "manifest_version": 3,
    "name": "Media Downloader Connector",
    "version": EXTENSION_VERSION,
    "description": "Downloads with Media Downloader: catches the browser's downloads, adds right-click download options and a Download button on every video.",
    "icons": {"128": "icon.png"},
    "permissions": [
        "activeTab", "contextMenus", "cookies", "downloads", "storage", "webRequest", "notifications",
        "declarativeNetRequestWithHostAccess", "webNavigation",
    ],
    "host_permissions": ["<all_urls>"],
    "background": {"service_worker": "background.js", "scripts": ["background.js"]},
    "content_scripts": [
        {
            "matches": ["<all_urls>"],
            "js": ["content.js"],
            "all_frames": True,
            "match_about_blank": True,
            "run_at": "document_start",
        }
    ],
    "action": {"default_title": "Media Downloader", "default_popup": "popup.html"},
    "options_ui": {"page": "options.html", "open_in_tab": True},
    "commands": {
        "send-page": {
            "suggested_key": {"default": "Alt+O"},
            "description": "Send this page to Media Downloader",
        }
    },
    "browser_specific_settings": {"gecko": {"id": "connector@media-downloader.local", "strict_min_version": "115.0"}},
}

BACKGROUND_JS = r"""
const api = typeof browser !== "undefined" ? browser : chrome;
const PORTS = Array.from({length: 10}, (_, i) => 47720 + i);
const URL_MEDIA_RE = /\.(m3u8|mpd|mp4|webm|mkv|mov|m4v|m4a|mp3|ogg|oga|opus|flac|wav)(\?|#|$)/i;
const SEGMENT_RE = /\.(ts|m4s|m4f|cmfv|cmfa|vtt|webvtt|srt|key|jpg|jpeg|png|webp|gif|js|css|json|html?)(\?|#|$)/i;
const SKIP_URL_RE = /[?&](range|bytestart|byteend)=|\/videoplayback\b|\/seg-\d+|\/segment[-_]?\d+/i;
const MIN_FILE_BYTES = 300 * 1024;
const MAX_STREAMS = 12;

// File types caught from the browser's own downloads when no list is saved in the options.
const DEFAULT_CAPTURE_TYPES = "zip rar 7z tar gz tgz bz2 xz iso img bin exe msi msix apk dmg deb rpm pdf doc docx " +
  "xls xlsx ppt pptx epub torrent mp4 mkv webm mov avi wmv flv m4v 3gp mp3 m4a aac flac wav ogg opus wma";
let settings = {blocked: "", overlay: true, capture: true, captureTypes: DEFAULT_CAPTURE_TYPES, minSizeKb: 0};
const tabs = new Map();      // tabId -> {gen, navGen, origin, streams: [], children: []}
const pending = new Map();   // requestId -> captured request headers
const ownFetches = new Set(); // playlist URLs we are reading ourselves (ignore their traffic)

// ------------------------------------------------------------------ settings / blocked sites
async function loadSettings() {
  const data = await api.storage.local.get(["blocked", "overlay", "capture", "captureTypes", "minSizeKb"]);
  settings = {
    blocked: String(data.blocked || ""),
    overlay: data.overlay !== false,
    capture: data.capture !== false,
    captureTypes: typeof data.captureTypes === "string" ? data.captureTypes : DEFAULT_CAPTURE_TYPES,
    minSizeKb: Math.max(0, Number(data.minSizeKb) || 0),
  };
}
loadSettings();
api.storage.onChanged.addListener((changes, area) => {
  if (area === "local" && (changes.blocked || changes.overlay || changes.capture || changes.captureTypes ||
      changes.minSizeKb)) loadSettings();
});

function blockedRules() {
  return settings.blocked.split(/[\r\n,]+/).map(line => line.trim().toLowerCase())
    .filter(line => line && !line.startsWith("#"))
    .map(line => line.replace(/^[a-z]+:\/\//, "").replace(/^\*\./, "").replace(/^www\./, "").split(/[\/:?#\s]/)[0])
    .filter(Boolean);
}

function hostOf(url) {
  try { return new URL(url).hostname.toLowerCase(); } catch (e) { return ""; }
}

function isBlocked(url) {
  const host = hostOf(url);
  if (!host) return false;
  return blockedRules().some(rule => host === rule || host.endsWith("." + rule));
}

// ------------------------------------------------------------------ desktop app connection
// The app writes its pairing token into pairing.json when it exports this extension, so it
// connects by itself: nothing to copy or paste. A token typed in the options still works.
async function syncBundledPairing() {
  try {
    const r = await fetch(api.runtime.getURL("pairing.json"), {cache: "no-store"});
    if (!r.ok) return;
    const j = await r.json();
    const token = String((j && j.token) || "").trim();
    if (!token) return;
    const current = String((await api.storage.local.get("token")).token || "").trim();
    if (current !== token) await api.storage.local.set({token, pairedAutomatically: true});
  } catch (e) {}
}
const pairingReady = syncBundledPairing();

async function getToken() {
  await pairingReady;
  const data = await api.storage.local.get("token");
  return (data.token || "").trim();
}

let appCapture = true; // the app's "catch browser downloads" option, read from /ping
async function findPort() {
  const cached = (await api.storage.local.get("port")).port;
  const order = cached ? [cached, ...PORTS.filter(p => p !== cached)] : PORTS;
  for (const port of order) {
    try {
      const r = await fetch(`http://127.0.0.1:${port}/ping`, {cache: "no-store"});
      const j = await r.json();
      if (j && j.app === "Media Downloader") {
        appCapture = j.capture !== false;
        if (port !== cached) await api.storage.local.set({port});
        return port;
      }
    } catch (e) {}
  }
  return 0;
}

// Self-pairing: the app hands its token to browser extensions only (web pages cannot fake
// the Origin header). So a reinstalled app, a new token or an extension loaded from another
// folder never ends up as "wrong token": it simply pairs again.
async function pairWithApp(port) {
  try {
    const r = await fetch(`http://127.0.0.1:${port}/pair`, {cache: "no-store"});
    const j = await r.json();
    const token = String((j && j.token) || "").trim();
    if (r.ok && token) { await api.storage.local.set({token, pairedAutomatically: true}); return token; }
  } catch (e) {}
  return "";
}

// POST to the app, finding it and (re)pairing as needed. Returns {...reply, ok, status}.
async function appRequest(path, body) {
  const port = await findPort();
  if (!port) return {ok: false, status: 0, error: "Media Downloader is not running."};
  let token = await getToken();
  if (!token) token = await pairWithApp(port);
  for (let attempt = 0; attempt < 2; attempt++) {
    try {
      const r = await fetch(`http://127.0.0.1:${port}${path}`, {
        method: "POST",
        headers: {"Content-Type": "application/json", "X-MD-Token": token},
        body: JSON.stringify(body || {}),
      });
      const j = await r.json().catch(() => ({}));
      if (r.status === 401 && attempt === 0) {
        const fresh = await pairWithApp(port);
        if (fresh) { token = fresh; continue; }
      }
      return {...j, ok: r.ok && !!j.ok, status: r.status};
    } catch (e) {
      try { await api.storage.local.remove("port"); } catch (err) {}
      return {ok: false, status: 0, error: "Could not reach Media Downloader."};
    }
  }
  return {ok: false, status: 401, error: "Media Downloader refused the pairing."};
}

async function cookiesFor(url) {
  if (!url || !/^https?:/i.test(url)) return [];
  try { return await api.cookies.getAll({url}); } catch (e) { return []; }
}

function cleanTitle(title) {
  let text = String(title || "").replace(/\s+/g, " ").trim();
  const parts = text.split(/\s+[-|–—]\s+/);
  if (parts.length > 1 && parts[parts.length - 1].length <= 30) text = parts.slice(0, -1).join(" - ");
  return text.replace(/^watch\s+/i, "").replace(/\s+online(\s+free)?$/i, "").replace(/\s+in\s+(full\s+)?hd$/i, "").slice(0, 180);
}

// payload: {url, referer, headers, title, quality, mode, pageUrl, kind, fileName, fileSize, mime}
async function sendToApp(payload) {
  const seen = new Set();
  let cookies = [];
  for (const source of [payload.url, payload.referer, payload.pageUrl]) {
    if (!source || seen.has(source)) continue;
    seen.add(source);
    cookies = cookies.concat(await cookiesFor(source));
  }
  const body = {
    url: payload.url,
    referer: payload.referer || "",
    headers: payload.headers || {},
    title: payload.title || "",
    quality: payload.quality || "",
    mode: payload.mode || "",
    pageUrl: payload.pageUrl || "",
    kind: payload.kind || "",
    fileName: payload.fileName || "",
    fileSize: payload.fileSize || 0,
    mime: payload.mime || "",
    cookies,
  };
  const result = await appRequest("/download", body);
  if (result.ok) return {ok: true};
  return {ok: false, error: result.error || "The app refused the link."};
}

function postToApp(path, body) {
  return appRequest(path, body);
}

// "Which qualities does this video have?" - the app reads the video's own link (a reel, post,
// watch page...) with yt-dlp, using this browser's cookies for logged-in sites.
const analyzeCache = new Map(); // url -> {time, promise}
let prefetching = 0;
// prefetch: the page started a video or the pointer rests on one, so the qualities are read
// in the background and the menu shows them at once (like IDM/NDM). At most two at a time.
function analyzeVideo(url, pageUrl, prefetch) {
  const hit = analyzeCache.get(url);
  if (hit && Date.now() - hit.time < 8 * 60 * 1000) return hit.promise;
  if (prefetch && prefetching >= 2) return Promise.resolve({ok: false, skipped: true});
  if (prefetch) prefetching++;
  const promise = (async () => {
    let cookies = [];
    for (const source of [...new Set([url, pageUrl].filter(Boolean))]) cookies = cookies.concat(await cookiesFor(source));
    const result = await postToApp("/analyze", {url, pageUrl: pageUrl || "", cookies});
    if (!result.ok) analyzeCache.delete(url);
    return result;
  })();
  if (prefetch) promise.finally(() => { prefetching--; });
  analyzeCache.set(url, {time: Date.now(), promise});
  if (analyzeCache.size > 60) analyzeCache.delete(analyzeCache.keys().next().value);
  return promise;
}

// Tell the app this browser has the extension, so a link pasted into the app can be opened
// here when only a real browser can play it.
let lastHello = 0;
function sayHello() {
  if (Date.now() - lastHello < 120000) return;
  lastHello = Date.now();
  postToApp("/hello", {browser: navigator.userAgent}).then(r => { if (!r.ok) lastHello = Date.now() - 90000; });
}
sayHello();

function notify(message) {
  try { api.notifications.create({type: "basic", iconUrl: "icon.png", title: "Media Downloader", message}); } catch (e) {}
}

async function sendAndNotify(payload) {
  const result = await sendToApp(payload);
  if (!result.ok) notify(result.error);   // success is silent: the app opens its own window
  return result;
}

// ------------------------------------------------------------------ per-tab stream store
async function tabState(tabId) {
  if (tabs.has(tabId)) return tabs.get(tabId);
  let state = null;
  try { state = (await api.storage.session.get("t" + tabId))["t" + tabId]; } catch (e) {}
  if (!tabs.has(tabId)) tabs.set(tabId, state || {gen: 0, navGen: 0, origin: "", streams: [], children: []});
  return tabs.get(tabId);
}

function persist(tabId) {
  const state = tabs.get(tabId);
  try { if (state) api.storage.session.set({["t" + tabId]: state}); } catch (e) {}
}

function forget(tabId) {
  tabs.delete(tabId);
  try { api.storage.session.remove("t" + tabId); } catch (e) {}
  if (resolving.has(tabId)) finishResolve(tabId, [], false);
}

// ------------------------------------------------------------------ links analyzed by the app
// When a link pasted into the app only plays in a real browser, the app opens it here with
// "#md-resolve=<job>" appended. This tab then starts the player, waits for its stream and
// reports it (best: the master playlist with every quality + audio) back, then closes.
const RESOLVE_RE = /[#&]md-resolve=([0-9a-f]{16,40})/;
const resolving = new Map(); // tabId -> {job, started, firstStream, plays, timer, done}

function startResolve(tabId, job) {
  const current = resolving.get(tabId);
  if (current && current.job === job) return;
  if (current) clearInterval(current.timer);
  const entry = {job, started: Date.now(), firstStream: 0, plays: 0, done: false, timer: 0};
  entry.timer = setInterval(() => resolveTick(tabId), 1500);
  resolving.set(tabId, entry);
}

function resolveTick(tabId) {
  const entry = resolving.get(tabId);
  if (!entry || entry.done) return;
  const now = Date.now();
  if (entry.plays < 14) {
    entry.plays++;
    try { Promise.resolve(api.tabs.sendMessage(tabId, {type: "md-autoplay"})).catch(() => {}); } catch (e) {}
  }
  const state = tabs.get(tabId);
  const streams = state ? currentStreams(state).filter(s => !s.drm) : [];
  if (streams.length && !entry.firstStream) entry.firstStream = now;
  const hasMaster = streams.some(s => s.master);
  const ready = streams.length && ((hasMaster && now - entry.firstStream > 2500) || now - entry.firstStream > 9000);
  if (ready) finishResolve(tabId, streams, true);
  else if (now - entry.started > 45000) finishResolve(tabId, streams, true);
}

async function finishResolve(tabId, streams, closeTab) {
  const entry = resolving.get(tabId);
  if (!entry || entry.done) return;
  entry.done = true;
  clearInterval(entry.timer);
  resolving.delete(tabId);
  let tab = null;
  try { tab = await api.tabs.get(tabId); } catch (e) {}
  const sorted = [...streams].sort((a, b) => (Number(!!b.master) - Number(!!a.master)) || (b.gen - a.gen));
  const out = [];
  for (const s of sorted.slice(0, 6)) {
    const headers = {...(s.headers || {})};
    const cookies = await cookiesFor(s.url);
    if (cookies.length) headers.Cookie = cookies.map(c => c.name + "=" + c.value).join("; ");
    out.push({
      url: s.url, kind: s.kind, master: !!s.master, referer: s.referer, headers,
      heights: (s.variants || []).map(v => v.height).filter(Boolean),
    });
  }
  await postToApp("/resolved", {
    id: entry.job,
    title: cleanTitle(tab && tab.title),
    pageUrl: tab && tab.url ? tab.url.replace(RESOLVE_RE, "") : "",
    streams: out,
  });
  if (closeTab && tab) { try { await api.tabs.remove(tabId); } catch (e) {} }
}

// Sites whose player streams come from signed CDN links (bound to the browser's session):
// the app cannot download those, so the video's own page link is used and the badge shows
// how many qualities that page offers instead of how many requests the player made.
const PAGE_ONLY_RE = /(^|\.)pornhub\.(com|org)$/;
function pageOnlyUrl(url) {
  try { return PAGE_ONLY_RE.test(new URL(url).hostname.toLowerCase()); } catch (e) { return false; }
}

function announce(tabId) {
  const state = tabs.get(tabId);
  let count = state ? currentStreams(state).filter(s => !s.drm).length : 0;
  if (state && pageOnlyUrl(state.page || "")) {
    count = state.q && state.q.page === state.page ? state.q.n : 0;
  }
  try {
    Promise.resolve(api.action.setBadgeBackgroundColor({tabId, color: "#7657ff"})).catch(() => {});
    Promise.resolve(api.action.setBadgeText({tabId, text: count ? String(count) : ""})).catch(() => {});
  } catch (e) {}
  try { api.tabs.sendMessage(tabId, {type: "md-streams-changed", count}).catch(() => {}); } catch (e) {}
}

function header(list, name) {
  const item = (list || []).find(h => h.name.toLowerCase() === name);
  return item ? String(item.value || "") : "";
}

function classify(url, contentType, length, status, contentRange) {
  if (!/^https?:/i.test(url) || SKIP_URL_RE.test(url)) return null;
  const ct = contentType.toLowerCase().split(";")[0].trim();
  const urlMatch = (url.match(URL_MEDIA_RE) || [])[1];
  const ext = urlMatch ? urlMatch.toLowerCase() : "";
  if (ct.includes("mpegurl") || ext === "m3u8") return {kind: "hls", ext: "m3u8"};
  if (ct.includes("dash+xml") || ext === "mpd") return {kind: "dash", ext: "mpd"};
  if (ct === "video/mp2t" || ct.includes("iso.segment") || SEGMENT_RE.test(url)) return null;
  const isMediaType = /^(video|audio)\//.test(ct);
  if (!isMediaType && !(ext && (!ct || ct === "application/octet-stream" || ct === "binary/octet-stream"))) return null;
  let total = length;
  const range = /\/(\d+)\s*$/.exec(contentRange || "");
  if (range) total = Number(range[1]);
  if (total && total < MIN_FILE_BYTES) return null;
  if (!total && status !== 206) return null;
  const fromType = (ct.split("/")[1] || "").replace("x-matroska", "mkv").replace("mpeg", "mp3").replace("quicktime", "mov");
  return {kind: "file", ext: ext || fromType || "media", size: total || 0, audio: ct.startsWith("audio/")};
}

function parseAttributes(line) {
  const out = {};
  const body = line.slice(line.indexOf(":") + 1);
  const re = /([A-Z0-9-]+)=("[^"]*"|[^,]*)/g;
  let m;
  while ((m = re.exec(body))) out[m[1]] = m[2].replace(/^"|"$/g, "");
  return out;
}

function parsePlaylist(text, base) {
  const lines = text.split(/\r?\n/).map(l => l.trim());
  const result = {master: false, variants: [], renditions: [], duration: 0, drm: false};
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    if (line.startsWith("#EXT-X-STREAM-INF:")) {
      result.master = true;
      const a = parseAttributes(line);
      let j = i + 1;
      while (j < lines.length && (!lines[j] || lines[j].startsWith("#"))) j++;
      if (j >= lines.length) continue;
      const [w, h] = String(a.RESOLUTION || "").split("x").map(Number);
      let url = "";
      try { url = new URL(lines[j], base).href; } catch (e) { continue; }
      result.variants.push({url, width: w || 0, height: h || 0, bandwidth: Number(a["AVERAGE-BANDWIDTH"] || a.BANDWIDTH || 0), separateAudio: !!a.AUDIO});
      i = j;
    } else if (line.startsWith("#EXT-X-MEDIA:") || line.startsWith("#EXT-X-I-FRAME-STREAM-INF:")) {
      const a = parseAttributes(line);
      if (a.URI) { try { result.renditions.push(new URL(a.URI, base).href); } catch (e) {} }
    } else if (line.startsWith("#EXTINF:")) {
      result.duration += parseFloat(line.slice(8)) || 0;
    } else if (line.startsWith("#EXT-X-KEY:") || line.startsWith("#EXT-X-SESSION-KEY:")) {
      const a = parseAttributes(line);
      const method = String(a.METHOD || "").toUpperCase();
      const format = String(a.KEYFORMAT || "").toLowerCase();
      if (method.startsWith("SAMPLE-AES") || /widevine|playready|streamingkeydelivery|fairplay/.test(format)) result.drm = true;
    }
  }
  return result;
}

// Player CDNs usually refuse requests without the player's Referer/Origin, and an extension's
// own fetch() sends neither (Firefox/Zen) or its own. A short-lived session rule adds the
// values the player itself sent, for requests made by the extension only (tabId -1).
let ruleSeq = 4000;
async function withPlayerHeaders(url, ctx, fn) {
  const dnr = api.declarativeNetRequest;
  const requestHeaders = [];
  if (ctx && ctx.referer) requestHeaders.push({header: "referer", operation: "set", value: ctx.referer});
  const origin = ctx && ctx.headers && ctx.headers.Origin;
  if (origin) requestHeaders.push({header: "origin", operation: "set", value: origin});
  if (!dnr || !dnr.updateSessionRules || !requestHeaders.length) return fn();
  ruleSeq = ruleSeq >= 4900 ? 4000 : ruleSeq + 1;
  const id = ruleSeq;
  let added = false;
  try {
    await dnr.updateSessionRules({removeRuleIds: [id], addRules: [{
      id, priority: 1,
      action: {type: "modifyHeaders", requestHeaders},
      condition: {urlFilter: "|" + url.split("#")[0], tabIds: [-1], resourceTypes: ["xmlhttprequest", "other"]},
    }]});
    added = true;
  } catch (e) {}
  try { return await fn(); }
  finally { if (added) { try { await dnr.updateSessionRules({removeRuleIds: [id]}); } catch (e) {} } }
}

async function playerFrames(tabId, ctx) {
  // Frames to ask, best first: the frame that requested the stream, then frames on the
  // player's origin, then the rest.
  const order = [];
  if (ctx && ctx.frameId >= 0) order.push(ctx.frameId);
  let frames = [];
  try { frames = (await api.webNavigation.getAllFrames({tabId})) || []; } catch (e) {}
  const want = (() => { try { return new URL((ctx && (ctx.headers && ctx.headers.Origin)) || (ctx && ctx.referer) || "").origin; } catch (e) { return ""; } })();
  const scored = frames.map(f => {
    let origin = "";
    try { origin = new URL(f.url).origin; } catch (e) {}
    return {id: f.frameId, score: (want && origin === want ? 2 : 0) + (f.frameId === 0 ? 0 : 1)};
  }).sort((a, b) => b.score - a.score);
  for (const f of scored) if (!order.includes(f.id)) order.push(f.id);
  if (!order.length) order.push(0);
  return order.slice(0, 6);
}

async function readPlaylist(url, tabId, ctx) {
  ownFetches.add(url);
  try {
    try {
      const text = await withPlayerHeaders(url, ctx, async () => {
        const r = await fetch(url, {credentials: "include", cache: "no-store"});
        return r.ok ? await r.text() : "";
      });
      if (text && text.includes("#EXTM3U")) return text;
    } catch (e) {}
    // Fallback: let the player's own frame read it (same origin, cookies and referer).
    for (const frameId of await playerFrames(tabId, ctx)) {
      try {
        const r = await api.tabs.sendMessage(tabId, {type: "md-fetch", url}, {frameId});
        if (r && typeof r.text === "string" && r.text.includes("#EXTM3U")) return r.text;
      } catch (e) {}
    }
    return "";
  } finally {
    setTimeout(() => ownFetches.delete(url), 4000);
  }
}

async function inspectHls(tabId, stream) {
  const text = await readPlaylist(stream.url, tabId, stream);
  const state = tabs.get(tabId);
  if (!text || !state) return;
  const parsed = parsePlaylist(text, stream.url);
  if (parsed.master) {
    const byHeight = new Map();
    for (const v of parsed.variants) {
      const key = v.height || ("bw" + v.bandwidth);
      const prev = byHeight.get(key);
      if (!prev || v.bandwidth > prev.bandwidth) byHeight.set(key, v);
    }
    stream.variants = [...byHeight.values()].sort((a, b) => (b.height - a.height) || (b.bandwidth - a.bandwidth));
    stream.master = true;
    stream.drm = parsed.drm;
    const children = parsed.variants.map(v => v.url).concat(parsed.renditions);
    state.children = state.children.concat(children).slice(-300);
    state.streams = state.streams.filter(s => s === stream || !children.includes(s.url));
    const top = stream.variants[0];
    if (top) {
      const media = await readPlaylist(top.url, tabId, stream);
      if (media) {
        const info = parsePlaylist(media, top.url);
        stream.duration = Math.round(info.duration);
        stream.drm = stream.drm || info.drm;
      }
    }
  } else {
    if (state.children.includes(stream.url)) {
      state.streams = state.streams.filter(s => s !== stream);
    } else {
      stream.duration = Math.round(parsed.duration);
      stream.drm = parsed.drm;
    }
  }
  persist(tabId);
  announce(tabId);
}

async function register(details, info, captured) {
  const tabId = details.tabId;
  let tab = null;
  try { tab = await api.tabs.get(tabId); } catch (e) {}
  const pageUrl = (tab && tab.url) || "";
  if (isBlocked(pageUrl)) return;
  const state = await tabState(tabId);
  const url = details.url;
  if (state.children.includes(url)) return;
  const existing = state.streams.find(s => s.url === url);
  if (existing) {
    // The player loaded this source again (e.g. switched back from Dub to Sub): make it current.
    if (existing.gen !== state.gen) { existing.gen = ++state.gen; persist(tabId); announce(tabId); }
    return;
  }
  const frameUrl = details.documentUrl || details.frameUrl || "";
  const referer = captured.referer || frameUrl || (details.initiator ? details.initiator + "/" : "") || pageUrl;
  const headers = {};
  if (captured.origin) headers.Origin = captured.origin;
  if (captured["user-agent"]) headers["User-Agent"] = captured["user-agent"];
  const stream = {
    id: Date.now().toString(36) + Math.random().toString(36).slice(2, 7),
    url, kind: info.kind, ext: info.ext, size: info.size || 0, audio: !!info.audio,
    frameId: details.frameId || 0, referer, headers, pageUrl,
    title: cleanTitle(tab && tab.title), time: Date.now(), gen: ++state.gen,
    master: false, variants: [], duration: 0, drm: false,
  };
  state.streams.push(stream);
  if (state.streams.length > MAX_STREAMS) state.streams.sort((a, b) => b.gen - a.gen).splice(MAX_STREAMS);
  persist(tabId);
  announce(tabId);
  if (info.kind === "hls") inspectHls(tabId, stream);
}

function foundInPage(tab, sender, url) {
  if (!/^https?:/i.test(url) || SKIP_URL_RE.test(url) || SEGMENT_RE.test(url)) return;
  const ext = ((url.match(URL_MEDIA_RE) || [])[1] || "").toLowerCase();
  if (!ext) return;
  const info = ext === "m3u8" ? {kind: "hls", ext} : ext === "mpd" ? {kind: "dash", ext}
    : {kind: "file", ext, size: 0, audio: /^(m4a|mp3|ogg|oga|opus|flac|wav)$/.test(ext)};
  const frameUrl = sender.url || tab.url || "";
  let origin = "";
  try { origin = new URL(frameUrl).origin; } catch (e) {}
  register({tabId: tab.id, url, frameId: sender.frameId || 0, documentUrl: frameUrl},
    info, {referer: frameUrl, origin});
}

const FILTER = {urls: ["<all_urls>"], types: ["media", "xmlhttprequest", "other", "object"]};

function onSendHeaders(details) {
  if (details.tabId < 0 || ownFetches.has(details.url)) return;
  const captured = {};
  for (const h of details.requestHeaders || []) {
    const name = h.name.toLowerCase();
    if (name === "referer" || name === "origin" || name === "user-agent") captured[name] = h.value;
  }
  pending.set(details.requestId, captured);
  if (pending.size > 800) pending.delete(pending.keys().next().value);
}
try { api.webRequest.onSendHeaders.addListener(onSendHeaders, FILTER, ["requestHeaders", "extraHeaders"]); }
catch (e) { api.webRequest.onSendHeaders.addListener(onSendHeaders, FILTER, ["requestHeaders"]); }

api.webRequest.onHeadersReceived.addListener((details) => {
  const captured = pending.get(details.requestId) || {};
  pending.delete(details.requestId);
  if (details.tabId < 0 || ownFetches.has(details.url)) return;
  if (details.statusCode >= 300) return;
  const info = classify(
    details.url,
    header(details.responseHeaders, "content-type"),
    Number(header(details.responseHeaders, "content-length")) || 0,
    details.statusCode,
    header(details.responseHeaders, "content-range"),
  );
  if (info) register(details, info, captured);
}, FILTER, ["responseHeaders"]);

api.webRequest.onErrorOccurred.addListener(details => pending.delete(details.requestId), FILTER);

api.tabs.onRemoved.addListener(forget);
api.tabs.onUpdated.addListener(async (tabId, change) => {
  if (!change.url) return;
  sayHello();
  const marker = RESOLVE_RE.exec(change.url);
  if (marker) startResolve(tabId, marker[1]);
  const state = await tabState(tabId);
  const origin = (() => { try { return new URL(change.url).origin; } catch (e) { return ""; } })();
  if (state.origin && origin !== state.origin) {
    tabs.set(tabId, {gen: 0, navGen: 0, origin, page: change.url.split("#")[0], streams: [], children: []});
  } else {
    state.origin = origin;
    // A new page (full load or an in-page route change, e.g. the next episode): everything
    // captured so far belongs to the previous page. Hash-only changes keep the streams.
    const page = change.url.split("#")[0];
    if (change.status === "loading" || (state.page && page !== state.page)) state.navGen = state.gen;
    state.page = page;
    currentStreams(state);
  }
  persist(tabId);
  announce(tabId);
});

// ------------------------------------------------------------------ menu model (popup + in-page button)
function fmtDuration(sec) {
  if (!sec) return "";
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = Math.floor(sec % 60);
  return (h ? h + ":" + String(m).padStart(2, "0") : m) + ":" + String(s).padStart(2, "0");
}

function fmtSize(bytes) {
  if (!bytes) return "";
  return bytes >= 1 << 30 ? (bytes / (1 << 30)).toFixed(2) + " GB" : Math.max(1, Math.round(bytes / (1 << 20))) + " MB";
}

function itemsFor(stream) {
  const base = {referer: stream.referer, headers: stream.headers, title: stream.title, pageUrl: stream.pageUrl};
  if (stream.drm) return [{label: "DRM protected", detail: "This stream is encrypted and cannot be downloaded.", disabled: true}];
  if (stream.kind === "hls" && stream.master && stream.variants.length) {
    const top = stream.variants[0];
    const items = [{...base, url: stream.url, quality: "Best available", best: true,
      label: "Best quality" + (top.height ? " · " + top.height + "p" : ""), detail: "Highest resolution this stream offers"}];
    for (const v of stream.variants) {
      const mbps = v.bandwidth ? (v.bandwidth / 1e6).toFixed(1) + " Mbps" : "";
      const res = v.width && v.height ? v.width + "×" + v.height : "";
      const detail = [res, mbps].filter(Boolean).join(" · ");
      if (v.height) {
        // Keep the master playlist so yt-dlp also gets separate audio tracks; pick by height.
        items.push({...base, url: stream.url, quality: v.height + "p", label: v.height + "p", detail});
      } else {
        items.push({...base, url: v.url, quality: "Best available", label: v.height ? v.height + "p" : "Audio / other", detail});
      }
    }
    return items;
  }
  if (stream.kind === "hls") return [{...base, url: stream.url, quality: "Best available", best: true, label: "HLS stream", detail: "Single-quality stream"}];
  if (stream.kind === "dash") return [{...base, url: stream.url, quality: "Best available", best: true, label: "DASH stream · best quality", detail: ""}];
  return [{...base, url: stream.url, quality: "Best available", best: true, mode: stream.audio ? "Audio" : "",
    label: (stream.ext || "media").toUpperCase() + " file", detail: fmtSize(stream.size)}];
}

// Only streams loaded by the page that is open now; anything from pages the tab showed
// before is hidden (and dropped) so the menu never offers an old video.
function currentStreams(state) {
  if (!state) return [];
  if (state.navGen && state.streams.some(s => s.gen <= state.navGen)) {
    state.streams = state.streams.filter(s => s.gen > state.navGen);
  }
  return state.streams;
}

async function menuFor(tabId) {
  const state = await tabState(tabId);
  const sorted = [...currentStreams(state)].sort((a, b) => b.gen - a.gen);
  let nowGiven = false;
  const groups = sorted.map(stream => {
    let label = "Earlier stream";
    if (!nowGiven) { label = "Now playing"; nowGiven = true; }
    const kind = stream.kind === "file" ? (stream.ext || "file").toUpperCase() : stream.kind.toUpperCase();
    const meta = [kind, fmtDuration(stream.duration), hostOf(stream.url)].filter(Boolean).join(" · ");
    const top = (stream.variants || [])[0];
    return {id: stream.id, label, meta, current: label === "Now playing", items: itemsFor(stream),
      duration: stream.duration || 0, title: stream.title || "", drm: !!stream.drm,
      height: (top && top.height) || 0, master: !!stream.master, kind: stream.kind};
  });
  return {groups};
}

// ------------------------------------------------------------------ browser UI hooks
try { api.runtime.onStartup.addListener(() => { lastHello = 0; sayHello(); }); } catch (e) {}

api.runtime.onInstalled.addListener(() => {
  lastHello = 0;
  sayHello();
  api.contextMenus.create({id: "md-page", title: "Download page with Media Downloader", contexts: ["page"]});
  api.contextMenus.create({id: "md-link", title: "Download link with Media Downloader", contexts: ["link"]});
  api.contextMenus.create({id: "md-media", title: "Download media with Media Downloader", contexts: ["video", "audio"]});
});

api.contextMenus.onClicked.addListener((info, tab) => {
  const pageUrl = (tab && tab.url) || "";
  const title = cleanTitle(tab && tab.title);
  if (info.menuItemId === "md-link" && info.linkUrl) sendAndNotify({url: info.linkUrl, referer: pageUrl, pageUrl, kind: "link"});
  else if (info.menuItemId === "md-media" && info.srcUrl && !info.srcUrl.startsWith("blob:")) sendAndNotify({url: info.srcUrl, referer: info.frameUrl || pageUrl, pageUrl, title, kind: "media"});
  else if (pageUrl) sendAndNotify({url: info.pageUrl || pageUrl, pageUrl});
});

// ------------------------------------------------------------------ catch the browser's downloads
// Like a desktop download manager: a file the browser starts saving is paused, handed to the
// app, and removed from the browser only when the app accepted it. If the app is closed or
// unpaired the browser simply carries on. Alt+click on a link lets the browser keep it.
let bypassUntil = 0;
const capturedIds = new Set();

function fileNameOf(item, url) {
  const fromItem = String(item.filename || "").split(/[\\/]/).pop();
  if (fromItem) return fromItem;
  try { return decodeURIComponent(new URL(url).pathname.split("/").pop() || ""); } catch (e) { return ""; }
}

function extOf(name) {
  const match = /\.([a-z0-9]{1,9})$/i.exec(String(name || ""));
  return match ? match[1].toLowerCase() : "";
}

function captureAllowed(name, mime) {
  const types = settings.captureTypes.toLowerCase().split(/[\s,;]+/).map(t => t.replace(/^\*?\./, "")).filter(Boolean);
  const ext = extOf(name);
  if (!types.length) return !/^(text\/html|application\/xhtml)/i.test(mime || "");
  if (ext) return types.includes(ext);
  // No extension yet (Chrome names the file a moment later): judge by the type the server sent.
  return /^(video|audio)\//i.test(mime || "") || /zip|rar|7z|x-msdownload|octet-stream|pdf|iso9660|android\.package/i.test(mime || "");
}

async function captureDownload(item) {
  if (!item || capturedIds.has(item.id)) return;
  await loadSettings();
  const url = item.finalUrl || item.url || "";
  if (!settings.capture || !/^https?:/i.test(url)) return;          // blob:, data:, file: stay in the browser
  if (item.byExtensionId || (item.state && item.state !== "in_progress")) return;
  if (Date.now() < bypassUntil) { bypassUntil = 0; return; }
  const pageUrl = item.referrer || "";
  if (isBlocked(url) || (pageUrl && isBlocked(pageUrl))) return;
  const name = fileNameOf(item, url);
  if (!captureAllowed(name, item.mime)) return;
  const size = Number(item.totalBytes || item.fileSize || 0);
  if (settings.minSizeKb && size > 0 && size < settings.minSizeKb * 1024) return;
  // Leave the download to the browser when the app is closed or its option is off.
  if (!(await findPort()) || !appCapture) return;
  capturedIds.add(item.id);
  try { await api.downloads.pause(item.id); } catch (e) {}
  const result = await sendToApp({url, referer: pageUrl, pageUrl, kind: "file", fileName: name, fileSize: size,
                                  mime: item.mime || "", title: name.replace(/\.[a-z0-9]{1,9}$/i, "")});
  if (result.ok) {
    try { await api.downloads.cancel(item.id); } catch (e) {}
    try { await api.downloads.erase({id: item.id}); } catch (e) {}
  } else {
    capturedIds.delete(item.id);
    try { await api.downloads.resume(item.id); } catch (e) {}
  }
}

try { api.downloads.onCreated.addListener(item => { captureDownload(item); }); } catch (e) {}

api.commands.onCommand.addListener(async (command) => {
  if (command !== "send-page") return;
  const [tab] = await api.tabs.query({active: true, currentWindow: true});
  if (tab && tab.url) sendAndNotify({url: tab.url, pageUrl: tab.url});
});

async function addBlockedSite(url) {
  const host = hostOf(url).replace(/^www\./, "");
  if (!host) return false;
  await loadSettings();
  if (isBlocked(url)) return true;
  const blocked = (settings.blocked.trim() ? settings.blocked.trim() + "\n" : "") + host;
  await api.storage.local.set({blocked});
  settings.blocked = blocked;
  return true;
}

api.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  const tab = sender.tab;
  const tabId = tab ? tab.id : msg.tabId;
  if (msg.type === "md-hello") {
    loadSettings().then(() => sendResponse({enabled: settings.overlay && !isBlocked(tab ? tab.url : "")}));
    return true;
  }
  if (msg.type === "md-menu") { menuFor(tabId).then(sendResponse); return true; }
  if (msg.type === "md-analyze") {
    if (!/^https?:/i.test(msg.url || "")) { sendResponse({ok: false, error: "No link for this video."}); return; }
    analyzeVideo(msg.url, msg.pageUrl || (tab && tab.url) || "", !!msg.prefetch).then(async result => {
      // The page's own video: the badge shows its quality count.
      if (tab && result && result.ok && Array.isArray(result.qualities) && pageOnlyUrl(msg.url)) {
        const state = await tabState(tab.id);
        const here = String(tab.url || "").split("#")[0];
        if (here && here === String(msg.url).split("#")[0]) {
          state.page = here;
          state.q = {page: here, n: result.qualities.length};
          persist(tab.id);
          announce(tab.id);
        }
      }
      sendResponse(result);
    });
    return true;
  }
  if (msg.type === "md-qcount") {
    // The page itself listed its qualities (see pageQualities in the page script).
    if (tab) {
      tabState(tab.id).then(state => {
        const here = String(tab.url || "").split("#")[0];
        state.page = here;
        state.q = {page: here, n: Number(msg.n) || 0};
        persist(tab.id);
        announce(tab.id);
      });
    }
    return;
  }
  if (msg.type === "md-found") {
    // Media links written in the page itself (<video src>, <source>, og:video, playlists in
    // the player's config): listed at once, before the player requests anything.
    if (tab && tab.id >= 0 && Array.isArray(msg.urls)) {
      for (const url of msg.urls.slice(0, 20)) foundInPage(tab, sender, String(url || ""));
    }
    return;
  }
  if (msg.type === "md-send") {
    const item = msg.item || {};
    const streamPage = item.pageUrl || (tab && tab.url) || msg.pageUrl || "";
    // The title and address are read again at the moment of the click: a stream remembers the
    // tab as it was when the player first loaded it, which on sites that switch videos without
    // a full page load (anime episode lists) is often the previous video's title.
    (async () => {
      let fresh = null;
      try { if (tabId !== undefined && tabId >= 0) fresh = await api.tabs.get(tabId); } catch (e) {}
      const here = fresh && fresh.url ? fresh.url.replace(RESOLVE_RE, "") : "";
      const samePage = !!here && (!streamPage || here.split("#")[0] === streamPage.split("#")[0]);
      const liveTitle = samePage ? cleanTitle(fresh && fresh.title) : "";
      const pageUrl = samePage ? here : streamPage;
      sendToApp({...item, pageUrl, title: liveTitle || item.title || msg.title || ""}).then(sendResponse);
    })();
    return true;
  }
  if (msg.type === "md-send-page") {
    const url = (tab && tab.url) || msg.url;
    sendToApp({url, pageUrl: url}).then(sendResponse);
    return true;
  }
  if (msg.type === "md-block-site") {
    addBlockedSite((tab && tab.url) || msg.url || "").then(ok => sendResponse({ok}));
    return true;
  }
  if (msg.type === "md-site-state") {
    loadSettings().then(() => sendResponse({blocked: isBlocked(msg.url || ""), host: hostOf(msg.url || "")}));
    return true;
  }
  if (msg.type === "md-bypass") { bypassUntil = Date.now() + 8000; return; }
  if (msg.type === "ping") { findPort().then(port => sendResponse({port})); return true; }
});
"""

CONTENT_JS = r"""
(() => {
  if (window.__mediaDownloaderConnector) return;
  window.__mediaDownloaderConnector = true;
  const api = typeof browser !== "undefined" ? browser : chrome;
  const MIN_W = 200, MIN_H = 110, HIDE_AFTER = 2600;

  // Alt+click on a link: the browser keeps that download (Media Downloader does not take it).
  document.addEventListener("click", event => {
    if (!event.altKey) return;
    const link = event.target && event.target.closest ? event.target.closest("a[href]") : null;
    if (link) { try { api.runtime.sendMessage({type: "md-bypass"}); } catch (e) {} }
  }, true);

  // The background may ask this frame to read a playlist that only the player's origin can load.
  // Firefox/Zen: content.fetch() runs as the page (its Origin, Referer and cookies); the plain
  // fetch() of a content script runs as the extension and many player CDNs refuse that.
  const pageFetch = (typeof content !== "undefined" && content && typeof content.fetch === "function")
    ? content.fetch.bind(content) : fetch;
  api.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
    if (msg && msg.type === "md-fetch") {
      pageFetch(msg.url, {credentials: "include"}).then(r => r.ok ? r.text() : "").then(text => sendResponse({text}), () => sendResponse({text: ""}));
      return true;
    }
    if (msg && msg.type === "md-autoplay") {
      // The app asked this tab to find the stream: start the player muted.
      const videos = Array.from(document.querySelectorAll("video"));
      for (const v of videos) {
        try { v.muted = true; if (v.paused) Promise.resolve(v.play()).catch(() => {}); } catch (e) {}
      }
      if (!videos.length || videos.every(v => v.paused)) {
        const selectors = [".jw-icon-display", ".jw-display-icon-container", ".vjs-big-play-button",
          ".plyr__control--overlaid", ".art-control-play", ".play-button", ".play-btn", "[aria-label*=play i]"];
        for (const s of selectors) {
          const el = document.querySelector(s);
          if (el) { try { el.click(); } catch (e) {} break; }
        }
      }
    }
  });

  let enabled = false, host = null, root = null, button = null, menu = null;
  let target = null, shownUntil = 0, menuOpen = false, timer = 0, lastMove = 0, count = 0;
  let pointerX = -1, pointerY = -1, pointerAt = 0;
  let medias = [];

  // ---------------------------------------------------------------- the video's own link
  // Facebook, Instagram, YouTube, X, TikTok... stream videos in small byte-range pieces
  // (often video and audio apart), which cannot be downloaded as one file. Their own link
  // (the reel/post/watch page) can: the app reads it and lists every quality with sound.
  const SITES = [
    {re: /(^|\.)youtube(-nocookie)?\.com$|(^|\.)youtu\.be$/, link: /\/watch\?(.*&)?v=|\/shorts\/|\/live\/|\/embed\//, page: /\/watch\?|\/shorts\/|\/live\/|\/embed\//},
    {re: /(^|\.)facebook\.com$|(^|\.)fb\.watch$/, link: /\/reel\/\d|\/videos\/|\/watch\/?\?v=|\/share\/[rv]\/|story_fbid=|\/posts\//, page: /\/reel\/\d|\/videos\/|\/watch\/?\?v=|story_fbid=/},
    {re: /(^|\.)instagram\.com$/, link: /\/(p|reel|reels|tv)\/[\w-]+/, page: /\/(p|reel|reels|tv)\/[\w-]+/},
    {re: /(^|\.)(twitter|x)\.com$/, link: /\/status\/\d+/, page: /\/status\/\d+/},
    {re: /(^|\.)tiktok\.com$/, link: /\/video\/\d+|\/photo\/\d+/, page: /\/video\/\d+/},
    {re: /(^|\.)threads\.(net|com)$/, link: /\/post\/[\w-]+/, page: /\/post\/[\w-]+/},
    {re: /(^|\.)reddit\.com$/, link: /\/comments\/\w+/, page: /\/comments\/\w+/},
    {re: /(^|\.)eporner\.com$/, link: /\/(video-|hd-porn\/)\w+/, page: /^\/(video-|hd-porn\/|embed\/)\w+/},
    {re: /(^|\.)xvideos\d*\.com$|(^|\.)xnxx\.com$/, link: /\/video[.\d]\w*/, page: /^\/video[.\d]\w*/},
    {re: /(^|\.)xhamster\d*\.(com|desi)$/, link: /\/videos\/[\w-]+/, page: /^\/videos\/[\w-]+/},
    {re: /(^|\.)pornhub\.(com|org)$/, link: /\/view_video\.php\?viewkey=/, page: /^\/view_video\.php\?viewkey=/, pageOnly: true},
    {re: /(^|\.)spankbang\.com$/, link: /\/\w+\/video\//, page: /^\/\w+\/video\//},
    {re: /(^|\.)(vimeo|dailymotion|twitch|bilibili|pinterest|linkedin|rumble|streamable|vk|ok|soundcloud|bitchute|9gag|imgur|tumblr|snapchat|likee|kick)\.(com|tv|ru|be|tw)$/, link: null, page: null},
  ];

  const FEED_ITEMS = "ytd-rich-item-renderer, ytd-video-renderer, ytd-compact-video-renderer, ytd-reel-item-renderer, ytd-playlist-panel-video-renderer";

  function siteFor(host) {
    return SITES.find(site => site.re.test(host)) || null;
  }
  // Signed CDN streams cannot be downloaded by the app: only the video's own link is offered.
  function pageOnly() {
    const site = siteFor(location.hostname.toLowerCase());
    return !!(site && site.pageOnly);
  }

  function videoLink(el) {
    const site = siteFor(location.hostname.toLowerCase());
    const here = location.href.split("#")[0];
    if (!site) return {url: here, known: false};
    // On a video's own page (a watch page, a reel, a post, an embed) the address is the link,
    // unless this video is one of the previews in a list on that page.
    const inList = el && el.closest && el.closest(FEED_ITEMS);
    if (!site.link || (site.page && site.page.test(location.pathname + location.search) && !inList)) {
      return {url: here, known: true};
    }
    // Feeds show many videos: take the link of the post that holds this one.
    let node = el;
    for (let depth = 0; node && depth < 25; depth++, node = node.parentElement) {
      const anchors = node.querySelectorAll ? node.querySelectorAll("a[href]") : [];
      for (const a of anchors) {
        const href = a.href || "";
        if (site.link.test(href) && !/[?&](comment_id|reply_comment_id)=/.test(href)) {
          try { const u = new URL(href, location.href); u.hash = ""; return {url: u.href, known: true}; } catch (e) {}
        }
      }
    }
    return {url: here, known: true};
  }

  function ask(message) {
    return new Promise(resolve => {
      try { Promise.resolve(api.runtime.sendMessage(message)).then(resolve, () => resolve(null)); }
      catch (e) { resolve(null); }
    });
  }

  function collect() {
    const found = Array.from(document.querySelectorAll("video, audio"));
    medias = found;
  }

  // ---------------------------------------------------------------- links written in the page
  // Like IDM/NDM: many players have their file or playlist in the HTML, so it is listed as
  // soon as the page loads instead of waiting for the player to request it.
  const MEDIA_LINK_RE = /\.(m3u8|mpd|mp4|webm|mkv|mov|m4v|m4a|mp3|ogg|opus|flac|wav)(\?|#|$)/i;
  const SCRIPT_PLAYLIST_RE = /https?:\\?\/\\?\/[^"'\s<>()]+?\.(?:m3u8|mpd)(?:\?[^"'\s<>()]*)?/gi;
  const reported = new Set();
  function absolute(url) {
    try { return new URL(String(url).replace(/\\\//g, "/").replace(/&amp;/g, "&"), location.href).href; } catch (e) { return ""; }
  }
  function scanPage() {
    if (!enabled) return;
    const urls = [];
    const add = url => {
      const href = absolute(url || "");
      if (href && /^https?:/i.test(href) && MEDIA_LINK_RE.test(href) && !reported.has(href)) { reported.add(href); urls.push(href); }
    };
    for (const el of document.querySelectorAll("video[src], audio[src], video source[src], audio source[src]")) {
      const player = el.closest("video, audio");
      if (player && player.tagName === "VIDEO") {
        // Thumbnail hover previews are small videos too: only real players count.
        const r = player.getBoundingClientRect();
        if (r.width < MIN_W || r.height < MIN_H) continue;
      }
      add(el.getAttribute("src"));
    }
    for (const el of document.querySelectorAll('meta[property="og:video"], meta[property="og:video:url"], meta[property="og:video:secure_url"], meta[name="twitter:player:stream"], meta[itemprop="contentUrl"]')) {
      add(el.getAttribute("content"));
    }
    for (const el of document.querySelectorAll('script[type="application/ld+json"]')) {
      const found = (el.textContent || "").match(/"contentUrl"\s*:\s*"[^"]+"/g) || [];
      for (const item of found) add(item.replace(/^"contentUrl"\s*:\s*"/, "").replace(/"$/, ""));
    }
    for (const el of document.querySelectorAll("script:not([src])")) {
      const text = el.textContent || "";
      if (text.length > 400000 || !/m3u8|mpd/i.test(text)) continue;
      for (const m of text.match(SCRIPT_PLAYLIST_RE) || []) add(m);
    }
    if (urls.length) ask({type: "md-found", urls});
  }

  // ---------------------------------------------------------------- read qualities early
  const prefetched = new Set();
  let dwellTimer = 0;
  function prefetch(el) {
    if (!enabled) return;
    const video = videoLink(el);
    if (!video.known || prefetched.has(video.url)) return;
    const site = siteFor(location.hostname.toLowerCase());
    // A feed page where this video has no link of its own: nothing to read yet.
    if (site && site.link && video.url === location.href.split("#")[0]
        && !(site.page && site.page.test(location.pathname + location.search))) return;
    prefetched.add(video.url);
    const analyze = () => ask({type: "md-analyze", url: video.url, pageUrl: location.href, prefetch: true}).then(r => {
      if (!r || !r.ok) prefetched.delete(video.url);
    });
    if (pageOnly() && video.url === location.href.split("#")[0]) {
      pageQualities().then(list => {
        if (list.length) publishCount(list.length);
        else { prefetched.delete(video.url); analyze(); }
      });
      return;
    }
    analyze();
  }

  function qualifies(el) {
    if (!el || !el.isConnected) return false;
    const r = el.getBoundingClientRect();
    if (el.tagName === "AUDIO") return el.controls && r.width >= 120 && r.height >= 20;
    if (r.width < MIN_W || r.height < MIN_H) return false;
    const style = getComputedStyle(el);
    return style.visibility !== "hidden" && style.display !== "none" && Number(style.opacity || 1) > 0.05;
  }

  function mediaAt(x, y) {
    let best = null, area = 0;
    for (const el of medias) {
      if (!qualifies(el)) continue;
      const r = el.getBoundingClientRect();
      if (x >= r.left && x <= r.right && y >= r.top && y <= r.bottom && r.width * r.height > area) { best = el; area = r.width * r.height; }
    }
    return best;
  }

  function build() {
    host = document.createElement("md-download-button");
    host.style.cssText = "all:initial;position:fixed;left:0;top:0;width:0;height:0;z-index:2147483647;";
    root = host.attachShadow({mode: "open"});
    root.innerHTML = `
<style>
  :host{all:initial}
  *{box-sizing:border-box;font-family:"Segoe UI",system-ui,sans-serif}
  .btn{position:fixed;display:none;align-items:center;gap:6px;height:28px;padding:0 10px 0 8px;border:1px solid rgba(255,255,255,.25);
    border-radius:8px;background:rgba(17,24,44,.92);color:#fff;font-size:12.5px;font-weight:600;cursor:pointer;
    box-shadow:0 4px 14px rgba(0,0,0,.45);backdrop-filter:blur(4px);white-space:nowrap;line-height:1;user-select:none}
  .btn:hover{background:#7657ff;border-color:#7657ff}
  .btn svg{width:15px;height:15px;flex:none}
  .count{min-width:18px;height:18px;padding:0 5px;border-radius:9px;background:#7657ff;font-size:11px;display:inline-flex;align-items:center;justify-content:center}
  .btn:hover .count{background:rgba(0,0,0,.3)}
  .count:empty{display:none}
  .menu{position:fixed;display:none;width:360px;max-width:calc(100vw - 16px);overflow:auto;border-radius:10px;background:#0f1729;color:#e8eefc;
    border:1px solid #273457;box-shadow:0 12px 32px rgba(0,0,0,.55);padding:6px 0;font-size:12.5px}
  .group{padding:8px 12px 3px;font-size:10.5px;letter-spacing:.06em;text-transform:uppercase;color:#8ea0c4;display:flex;justify-content:space-between;gap:8px}
  .group.now{color:#23d5ff}
  .group .meta{text-transform:none;letter-spacing:0;color:#6c7ea3;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .item{display:flex;justify-content:space-between;align-items:center;gap:10px;padding:7px 12px;cursor:pointer}
  .item:hover{background:#1c2846}
  .item.best .label{color:#fff;font-weight:600}
  .item.disabled{cursor:default;opacity:.6}
  .item.disabled:hover{background:none}
  .label{white-space:nowrap}
  .detail{color:#8ea0c4;font-size:11.5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .sep{height:1px;background:#22304f;margin:6px 0}
  .muted{padding:10px 12px;color:#8ea0c4;line-height:1.4}
  .foot{color:#9fb0d2}
  .title{padding:2px 12px 6px;color:#cfd8ee;font-size:11.5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
</style>
<div class="btn" part="button"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M12 4v11"/><path d="m6.5 10 5.5 5.5 5.5-5.5"/><path d="M5 20h14"/></svg><span class="text">Download</span><span class="count"></span></div>
<div class="menu" role="menu"></div>`;
    button = root.querySelector(".btn");
    menu = root.querySelector(".menu");
    for (const type of ["pointerdown", "mousedown", "mouseup", "click", "dblclick", "contextmenu", "touchstart"]) {
      host.addEventListener(type, e => e.stopPropagation());
    }
    button.addEventListener("click", e => { e.preventDefault(); menuOpen ? closeMenu() : openMenu(); });
    setCount(count);
    mount();
  }

  function mount() {
    const fs = document.fullscreenElement || document.webkitFullscreenElement;
    const parent = fs && fs.tagName !== "VIDEO" ? fs : document.documentElement;
    if (host && host.parentNode !== parent) parent.appendChild(host);
  }

  function place() {
    if (!target || !qualifies(target)) return false;
    const r = target.getBoundingClientRect();
    const vw = document.documentElement.clientWidth || innerWidth;
    const bw = button.offsetWidth || 110;
    const left = Math.max(4, Math.min(r.right - bw - 10, vw - bw - 4));
    const top = Math.max(4, r.top + 10);
    button.style.left = left + "px";
    button.style.top = top + "px";
    if (menuOpen) {
      const mw = Math.min(360, vw - 16);
      menu.style.left = Math.max(8, Math.min(left + bw - mw, vw - mw - 8)) + "px";
      menu.style.top = (top + 34) + "px";
      menu.style.maxHeight = Math.max(160, innerHeight - top - 50) + "px";
    }
    return true;
  }

  function show(el, ms) {
    if (!enabled || !el) return;
    if (!host) build();
    if (target !== el && menuOpen) closeMenu();
    target = el;
    shownUntil = Math.max(shownUntil, Date.now() + (ms || HIDE_AFTER));
    mount();
    if (place()) button.style.display = "inline-flex";
    if (!timer) timer = setInterval(tick, 200);
  }

  function hide() {
    if (button) button.style.display = "none";
    closeMenu();
    clearInterval(timer); timer = 0; target = null;
  }

  function tick() {
    if (menuOpen) { if (!place()) hide(); return; }
    if (!place()) { hide(); return; }
    const now = Date.now();
    const r = target.getBoundingClientRect();
    const over = pointerX >= r.left && pointerX <= r.right && pointerY >= r.top && pointerY <= r.bottom;
    if (over && now - pointerAt < 6000) shownUntil = Math.max(shownUntil, now + 600); // stay while the pointer is on the player
    if (now > shownUntil) hide();
  }

  function setCount(n) {
    count = n || 0;
    if (root) root.querySelector(".count").textContent = count ? String(count) : "";
  }

  function flash(text, ms) {
    if (!button) return;
    const label = root.querySelector(".text");
    label.textContent = text;
    shownUntil = Date.now() + (ms || 2600);
    setTimeout(() => { label.textContent = "Download"; }, ms || 2600);
  }

  function row(cls, label, detail) {
    const div = document.createElement("div");
    div.className = "item " + cls;
    const a = document.createElement("span"); a.className = "label"; a.textContent = label;
    const b = document.createElement("span"); b.className = "detail"; b.textContent = detail || "";
    div.append(a, b);
    return div;
  }

  async function send(message) {
    closeMenu();
    flash("Sending…", 8000);
    const result = await ask(message);
    flash(result && result.ok ? "✓ Sent to Media Downloader" : ((result && result.error) || "Could not send"), 3500);
  }

  let renderSeq = 0;

  function groupHead(label, metaText, current) {
    const head = document.createElement("div");
    head.className = "group" + (current ? " now" : "");
    const name = document.createElement("span"); name.textContent = label;
    const meta = document.createElement("span"); meta.className = "meta"; meta.textContent = metaText || "";
    head.append(name, meta);
    return head;
  }

  function fmtLength(sec) {
    sec = Math.round(Number(sec) || 0);
    if (!sec) return "";
    const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
    return (h ? h + ":" + String(m).padStart(2, "0") : m) + ":" + String(s).padStart(2, "0");
  }

  // Titles: a site that hides its title makes the app answer "Untitled"; the tab title is
  // better, and a file named "Untitled" would clash with every other such video.
  const PLACEHOLDER_RE = /^(untitled( video)?|no title|video|media|browser media|media[ _]download|download|watch|player|embed|index|master|playlist|stream|videoplayback)$/i;
  function goodTitle(text) {
    text = String(text || "").replace(/\s+/g, " ").trim();
    return text && !PLACEHOLDER_RE.test(text) ? text.slice(0, 180) : "";
  }
  function pageTitle() {
    const meta = document.querySelector('meta[property="og:title"], meta[name="twitter:title"]');
    for (const raw of [document.title, meta && meta.getAttribute("content")]) {
      let text = String(raw || "").replace(/\s+/g, " ").trim();
      const parts = text.split(/\s+[-|–—]\s+/);
      if (parts.length > 1 && parts[parts.length - 1].length <= 30) text = parts.slice(0, -1).join(" - ");
      text = goodTitle(text.replace(/^\(\d+\)\s*/, "").replace(/^watch\s+/i, ""));
      if (text) return text;
    }
    return "";
  }
  function heightOf(label) {
    const m = /(\d{3,4})p\b/.exec(String(label || ""));
    return m ? Number(m[1]) : 0;
  }
  function sep() { return Object.assign(document.createElement("div"), {className: "sep"}); }

  // ---------------------------------------------------------------- qualities written in the page
  // Pornhub lists every quality of the video (a playlist link each) in the page's own player
  // settings ("mediaDefinitions"). Reading them here needs no network access from the app,
  // which some networks block for pornhub.com while the browser works.
  function parseMediaDefinitions(text) {
    const key = text.indexOf('"mediaDefinitions"');
    if (key < 0) return [];
    const start = text.indexOf("[", key);
    if (start < 0) return [];
    let depth = 0, inStr = false, esc = false, end = -1;
    for (let i = start; i < text.length; i++) {
      const ch = text[i];
      if (inStr) { if (esc) esc = false; else if (ch === "\\") esc = true; else if (ch === '"') inStr = false; continue; }
      if (ch === '"') inStr = true;
      else if (ch === "[") depth++;
      else if (ch === "]") { depth--; if (depth === 0) { end = i; break; } }
    }
    if (end < 0) return [];
    try { const list = JSON.parse(text.slice(start, end + 1)); return Array.isArray(list) ? list : []; }
    catch (e) { return []; }
  }

  // [{height, url}] best first, one per height; only playlist (HLS) entries.
  function qualityList(defs) {
    const byHeight = new Map();
    for (const d of defs) {
      if (!d || String(d.format || "").toLowerCase() !== "hls") continue;
      const url = String(d.videoUrl || "");
      if (!/^https?:\/\//i.test(url)) continue;
      const heights = (Array.isArray(d.quality) ? d.quality : [d.quality]).map(Number).filter(h => h > 0);
      for (const h of heights) if (!byHeight.has(h)) byHeight.set(h, url);
    }
    return [...byHeight.entries()].sort((a, b) => b[0] - a[0]).map(([height, url]) => ({height, url}));
  }

  async function pageQualities() {
    const here = location.href.split("#")[0];
    let list = [];
    for (const el of document.querySelectorAll("script:not([src])")) {
      const text = el.textContent || "";
      if (!text.includes("mediaDefinitions")) continue;
      list = qualityList(parseMediaDefinitions(text));
      if (list.length) break;
    }
    if (!list.length) {
      try {
        const r = await fetch(here, {credentials: "include"});
        list = qualityList(parseMediaDefinitions(await r.text()));
      } catch (e) {}
    }
    return list;
  }

  function publishCount(n) {
    if (n === count) return;  // an unchanged count must not echo back and redraw the menu
    setCount(n);
    ask({type: "md-qcount", n});
  }

  function localItems(list, title) {
    const here = location.href.split("#")[0];
    // Pornhub's CDN answers "request incorrect" (410/412) unless the Referer is just the site
    // ("https://www.pornhub.com/", what the browser's own player sends) and no Origin header is
    // present; the full page address as Referer, or an Origin, is refused.
    const base = {referer: location.origin + "/", pageUrl: here, title, kind: "media",
      headers: {"User-Agent": navigator.userAgent}};
    const top = list[0];
    return [{...base, url: top.url, quality: "Best available", best: true,
        label: "Best quality · " + top.height + "p", detail: "Highest resolution this video offers"},
      ...list.map(q => ({...base, url: q.url, quality: q.height + "p", label: q.height + "p", detail: ""}))];
  }

  // One list for the video: the stream the player already loaded is shown at once (like
  // IDM/NDM); on video sites the app also reads the video's own link and its list replaces
  // the captured one only when it offers a higher quality. A failed lookup is shown only
  // when nothing was captured.
  async function renderMenu() {
    const seq = ++renderSeq;
    const data = await ask({type: "md-menu"});
    if (!menuOpen || seq !== renderSeq) return;
    menu.textContent = "";
    const groups = (data && data.groups) || [];
    const video = videoLink(target);
    // In a feed the captured streams may belong to another post: use them on the video's own page.
    const ownPage = !video.known || video.url === location.href.split("#")[0];
    const stream = ownPage && !pageOnly() ? (groups.find(g => !g.drm && g.master) || groups.find(g => !g.drm) || groups[0] || null) : null;
    let items = stream ? stream.items.slice() : [];
    let local = null;
    if (pageOnly() && ownPage) {
      const list = await pageQualities();
      if (!menuOpen || seq !== renderSeq) return;
      if (list.length) { local = list; publishCount(list.length); items = localItems(list, pageTitle()); }
    }
    const src = target && (target.currentSrc || target.src);
    if (!items.length && src && /^https?:/i.test(src) && !pageOnly()) {
      const audio = target.tagName === "AUDIO";
      items = [{url: src, referer: location.href, quality: "Best available", best: true, mode: audio ? "Audio" : "",
        label: (audio ? "Audio" : "Video") + " file", detail: ""}];
    }
    let host = "";
    try { host = new URL(video.url).hostname.replace(/^www\./, ""); } catch (e) {}
    menu.appendChild(groupHead("Download", host, true));
    const titleRow = document.createElement("div");
    titleRow.className = "title";
    menu.appendChild(titleRow);
    const body = document.createElement("div");
    menu.appendChild(body);
    let shownTitle = pageTitle() || goodTitle(stream && stream.title);
    const setTitle = (title, duration) => {
      if (title) shownTitle = title;
      titleRow.textContent = (shownTitle || location.hostname) + (duration ? "  ·  " + fmtLength(duration) : "");
      titleRow.title = shownTitle;
    };
    setTitle("", stream && stream.duration);

    const showItems = list => {
      body.textContent = "";
      for (const item of list) {
        const el = row((item.best ? "best " : "") + (item.disabled ? "disabled" : ""), item.label, item.detail);
        if (!item.disabled) el.addEventListener("click", () => send({type: "md-send", item: {...item, title: goodTitle(item.title) || shownTitle}}));
        body.appendChild(el);
      }
      place();
    };
    const capturedBest = Math.max(0, ...items.map(i => heightOf(i.label)));

    async function lookUp(waiting) {
      if (waiting) {
        body.textContent = "";
        const wait = document.createElement("div");
        wait.className = "muted";
        wait.textContent = "Finding qualities…";
        body.appendChild(wait);
        place();
      }
      const r = await ask({type: "md-analyze", url: video.url, pageUrl: location.href});
      if (!menuOpen || seq !== renderSeq) return;
      if (r && r.ok) {
        const title = goodTitle(r.title) || shownTitle;
        const base = {url: video.url, referer: location.href, pageUrl: video.url, title};
        const qualities = Array.isArray(r.qualities) ? r.qualities : [];
        if (pageOnly() && ownPage) setCount(qualities.length);
        const list = [{...base, quality: "Best available", best: true,
          label: "Best quality" + (qualities[0] ? " · " + qualities[0] : ""), detail: "video + audio"}];
        for (const q of qualities) list.push({...base, quality: q, label: q, detail: ""});
        list.push({...base, quality: "Best available", mode: "Audio", label: "Audio only", detail: "MP3"});
        if (!items.length || heightOf(list[0].label) > capturedBest) {
          setTitle(title, r.duration);
          showItems(list);
        } else {
          setTitle(title, (stream && stream.duration) || r.duration);
        }
        return;
      }
      if (items.length) return;  // the captured stream is enough: no error
      body.textContent = "";
      const fail = row("disabled", "Could not read this video", (r && r.error) || "");
      fail.title = (r && r.error) || "";
      body.appendChild(fail);
      const retry = row("", "Try again", "");
      retry.addEventListener("click", () => lookUp(true));
      body.appendChild(retry);
      const anyway = row("", "Send the link to the app anyway", "");
      anyway.addEventListener("click", () => send({type: "md-send", item: {url: video.url, referer: location.href,
        pageUrl: video.url, quality: "Best available", title: shownTitle}}));
      body.appendChild(anyway);
      place();
    }

    if (items.length) showItems(items);
    if (!local && (video.known || !items.length)) lookUp(!items.length);
    menu.appendChild(sep());
    const page = row("foot", "Send page link instead", "app finds the best quality");
    page.addEventListener("click", () => send({type: "md-send-page"}));
    menu.appendChild(page);
    const block = row("foot", "Never show on this site", "");
    block.addEventListener("click", async () => { await ask({type: "md-block-site"}); enabled = false; hide(); });
    menu.appendChild(block);
    place();
  }

  function openMenu() {
    menuOpen = true;
    menu.style.display = "block";
    menu.textContent = "";
    const loading = document.createElement("div"); loading.className = "muted"; loading.textContent = "Loading…";
    menu.appendChild(loading);
    place();
    renderMenu();
  }

  function closeMenu() {
    menuOpen = false;
    if (menu) menu.style.display = "none";
    shownUntil = Date.now() + 1200;
  }

  function onMove(e) {
    const now = Date.now();
    pointerX = e.clientX; pointerY = e.clientY; pointerAt = now;
    if (now - lastMove < 80) return;
    lastMove = now;
    const el = mediaAt(e.clientX, e.clientY);
    if (el) {
      show(el);
      clearTimeout(dwellTimer);
      dwellTimer = setTimeout(() => { if (target === el) prefetch(el); }, 450);
    }
  }

  async function start() {
    const state = await ask({type: "md-hello"});
    enabled = !!(state && state.enabled);
    if (!enabled) { hide(); return; }
    collect();
    if (start.done) return;
    start.done = true;
    let pendingScan = 0, pendingPageScan = 0;
    new MutationObserver(() => {
      if (!pendingScan) pendingScan = setTimeout(() => { pendingScan = 0; collect(); }, 400);
      if (!pendingPageScan) pendingPageScan = setTimeout(() => { pendingPageScan = 0; scanPage(); }, 1200);
    }).observe(document.documentElement, {childList: true, subtree: true});
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", () => { collect(); scanPage(); }, {once: true});
    else scanPage();
    document.addEventListener("mousemove", onMove, {capture: true, passive: true});
    if (window === window.top) {
      // On a video's own page (a YouTube watch page, a reel...) read the qualities as soon as
      // the page opens, so the menu is ready on the first click instead of after 3-4 seconds.
      // Single-page sites change the address without a reload: check it every second.
      let lastPage = "";
      const early = () => {
        const here = location.href.split("#")[0];
        if (here === lastPage || !enabled) return;
        lastPage = here;
        const site = siteFor(location.hostname.toLowerCase());
        if (site && site.page && site.page.test(location.pathname + location.search)) prefetch(null);
      };
      early();
      setInterval(early, 1000);
    }
    document.addEventListener("playing", e => {
      if (!(e.target instanceof HTMLMediaElement)) return;
      collect();
      if (qualifies(e.target)) { show(e.target, 4000); prefetch(e.target); }
      scanPage();
    }, true);
    document.addEventListener("pointerdown", e => { if (menuOpen && !e.composedPath().includes(host)) closeMenu(); }, true);
    document.addEventListener("keydown", e => { if (e.key === "Escape" && menuOpen) closeMenu(); }, true);
    document.addEventListener("fullscreenchange", mount);
    document.addEventListener("webkitfullscreenchange", mount);
    addEventListener("scroll", () => { if (target) place(); }, {capture: true, passive: true});
    addEventListener("resize", () => { if (target) place(); });
    api.runtime.onMessage.addListener(msg => {
      if (!msg || msg.type !== "md-streams-changed") return;
      const grew = (msg.count || 0) > count;
      if (pageOnly()) {
        // The badge counts the qualities of this page's video, not the player's requests.
        const here = videoLink(target).url === location.href.split("#")[0];
        if (here && msg.count) setCount(msg.count);
        return;  // the list comes from the page itself: never rebuild it under the pointer
      }
      setCount(msg.count);
      if (menuOpen) renderMenu();
      else if (grew && target) show(target, 3000);
    });
    api.storage.onChanged.addListener((changes, area) => {
      if (area === "local" && (changes.blocked || changes.overlay)) start();
    });
    const data = await ask({type: "md-menu"});
    if (!pageOnly()) setCount(data && data.groups ? data.groups.filter(g => !g.items.every(i => i.disabled)).length : 0);
  }

  start();
})();
"""

POPUP_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>Media Downloader</title>
<style>
 body{font:13px Segoe UI,system-ui,sans-serif;width:360px;margin:0;padding:14px;background:#0b1323;color:#f7faff}
 h1{font-size:15px;margin:0 0 10px}
 button{width:100%;padding:10px;border:0;border-radius:8px;background:#7657ff;color:#fff;font-size:13px;cursor:pointer}
 button.ghost{background:#14223a;margin-top:6px;font-size:12px}
 .group{font-size:10.5px;color:#8798b5;margin:12px 0 4px;letter-spacing:.06em;text-transform:uppercase;display:flex;justify-content:space-between;gap:8px}
 .group.now{color:#23d5ff}
 .group span:last-child{text-transform:none;letter-spacing:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
 .item{display:flex;justify-content:space-between;gap:8px;width:100%;background:#14223a;margin-top:4px;text-align:left;font-size:12px;padding:8px 10px}
 .item:hover{background:#1f3156}
 .item small{color:#8798b5;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
 .item[disabled]{opacity:.55;cursor:default}
 #msg{margin-top:10px;color:#8798b5;min-height:16px}
 h2{font-size:11px;color:#23d5ff;margin:14px 0 4px;letter-spacing:.06em}
 #empty{color:#8798b5}
</style></head><body>
<h1>Media Downloader</h1>
<button id="page">Send this page</button>
<h2>THIS TAB</h2>
<div id="list"><span id="empty">Nothing captured yet - play the video, or switch server/Sub/Dub.</span></div>
<button id="site" class="ghost">Never use on this site</button>
<div id="msg"></div>
<script src="popup.js"></script></body></html>
"""

POPUP_JS = r"""
const api = typeof browser !== "undefined" ? browser : chrome;
const msg = document.getElementById("msg");
async function send(message) {
  msg.textContent = "Sending...";
  const r = await api.runtime.sendMessage(message);
  msg.textContent = r && r.ok ? "Added to Media Downloader." : (r && r.error) || "Failed.";
}
(async () => {
  const [tab] = await api.tabs.query({active: true, currentWindow: true});
  if (!tab) return;
  document.getElementById("page").onclick = () => send({type: "md-send-page", url: tab.url});
  const site = document.getElementById("site");
  const state = await api.runtime.sendMessage({type: "md-site-state", url: tab.url});
  if (state && state.host) site.textContent = state.blocked ? `Blocked on ${state.host} (edit in options)` : `Never use on ${state.host}`;
  site.onclick = async () => {
    if (state && state.blocked) { api.runtime.openOptionsPage(); return; }
    await api.runtime.sendMessage({type: "md-block-site", url: tab.url});
    site.textContent = "Blocked. Reload the page.";
  };
  const data = await api.runtime.sendMessage({type: "md-menu", tabId: tab.id});
  const list = document.getElementById("list");
  if (!data || !data.groups || !data.groups.length) return;
  list.textContent = "";
  // One list: the stream the page is playing (the master playlist with every quality first).
  if (/(^|\.)pornhub\.(com|org)$/.test(new URL(tab.url).hostname)) return;  // signed CDN streams: use "Send this page"
  const groups = data.groups;
  const best = groups.find(g => !g.drm && g.master) || groups.find(g => !g.drm) || groups[0];
  for (const group of [best]) {
    const head = document.createElement("div");
    head.className = "group now";
    const a = document.createElement("span"); a.textContent = "Download";
    const b = document.createElement("span"); b.textContent = group.meta;
    head.append(a, b);
    list.appendChild(head);
    for (const item of group.items) {
      const button = document.createElement("button");
      button.className = "item";
      const label = document.createElement("span"); label.textContent = item.label;
      const detail = document.createElement("small"); detail.textContent = item.detail || "";
      button.append(label, detail);
      if (item.disabled) button.disabled = true;
      else button.onclick = () => send({type: "md-send", item, pageUrl: tab.url, title: tab.title});
      list.appendChild(button);
    }
  }
})();
"""

OPTIONS_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>Media Downloader Connector</title>
<style>
 body{font:14px Segoe UI,system-ui,sans-serif;max-width:620px;margin:40px auto;padding:0 16px;line-height:1.45}
 input[type=text],textarea{width:100%;box-sizing:border-box;padding:8px;margin:8px 0;font:13px Consolas,monospace}
 textarea{min-height:170px}
 button{padding:8px 18px}
 h2{font-size:16px;margin-top:28px}
 .hint{color:#555;font-size:13px}
 label{display:block;margin:8px 0}
</style></head><body>
<h1>Media Downloader Connector</h1>
<h2>1. Connection</h2>
<p id="pairstate">Checking the connection to Media Downloader…</p>
<details><summary class="hint">Pairing token (filled in automatically)</summary>
<p class="hint">The extension exported by Media Downloader pairs itself. Only paste a token here if you installed the
extension some other way (Media Downloader → Options → Browser).</p>
<input type="text" id="token" placeholder="Pairing token" autocomplete="off"></details>
<h2>2. Download button</h2>
<label><input type="checkbox" id="overlay"> Show a <b>Download</b> button on videos and audio players (like IDM)</label>
<h2>3. Catch browser downloads</h2>
<label><input type="checkbox" id="capture"> When the browser starts downloading a file, download it with
<b>Media Downloader</b> instead</label>
<p class="hint">File types to catch (separated by spaces). Leave empty to catch every file.
If Media Downloader is not running, the browser downloads the file as usual.
Hold <b>Alt</b> while clicking a link to let the browser download it.</p>
<input type="text" id="types" spellcheck="false">
<label>Skip files smaller than <input type="number" id="minsize" min="0" style="width:90px"> KB (0 = catch all sizes)</label>
<p><button id="resettypes" type="button">Restore the default file types</button></p>
<h2>4. Blocked sites</h2>
<p class="hint">The extension does nothing on these sites: no Download button and no stream capture.
One site per line. <code>example.com</code> also covers <code>www.example.com</code> and every other subdomain.
Lines that start with <code>#</code> are notes.</p>
<textarea id="blocked" spellcheck="false" placeholder="# Sites where Media Downloader must never run&#10;netflix.com&#10;bank.example.com"></textarea>
<p><button id="save">Save</button> <span id="state"></span></p>
<p class="hint">Reload open tabs after saving so the change applies to them.</p>
<script src="options.js"></script></body></html>
"""

OPTIONS_JS = r"""
const api = typeof browser !== "undefined" ? browser : chrome;
const token = document.getElementById("token");
const overlay = document.getElementById("overlay");
const blocked = document.getElementById("blocked");
const capture = document.getElementById("capture");
const types = document.getElementById("types");
const minsize = document.getElementById("minsize");
const DEFAULT_TYPES = "zip rar 7z tar gz tgz bz2 xz iso img bin exe msi msix apk dmg deb rpm pdf doc docx " +
  "xls xlsx ppt pptx epub torrent mp4 mkv webm mov avi wmv flv m4v 3gp mp3 m4a aac flac wav ogg opus wma";
api.runtime.sendMessage({type: "ping"}).then(r => {
  const text = document.getElementById("pairstate");
  api.storage.local.get("token").then(d => {
    if (r && r.port && d.token) text.textContent = "Connected to Media Downloader. Downloads are handed over automatically.";
    else if (d.token) text.textContent = "Paired. Start Media Downloader to use it (the browser downloads normally meanwhile).";
    else text.textContent = "Not paired yet: in Media Downloader choose Help → Install browser extension, then reload this extension.";
  });
}).catch(() => {});
api.storage.local.get(["token", "overlay", "blocked", "capture", "captureTypes", "minSizeKb"]).then(d => {
  token.value = d.token || "";
  overlay.checked = d.overlay !== false;
  blocked.value = d.blocked || "";
  capture.checked = d.capture !== false;
  types.value = typeof d.captureTypes === "string" ? d.captureTypes : DEFAULT_TYPES;
  minsize.value = String(Math.max(0, Number(d.minSizeKb) || 0));
});
document.getElementById("resettypes").onclick = () => { types.value = DEFAULT_TYPES; };
document.getElementById("save").onclick = async () => {
  await api.storage.local.set({token: token.value.trim(), overlay: overlay.checked, blocked: blocked.value.trim(),
                               capture: capture.checked, captureTypes: types.value.trim(),
                               minSizeKb: Math.max(0, Number(minsize.value) || 0)});
  document.getElementById("state").textContent = "Saved.";
};
"""

FILES = {
    "manifest.json": json.dumps(MANIFEST, indent=2),
    "background.js": BACKGROUND_JS.strip() + "\n",
    "content.js": CONTENT_JS.strip() + "\n",
    "popup.html": POPUP_HTML,
    "popup.js": POPUP_JS.strip() + "\n",
    "options.html": OPTIONS_HTML,
    "options.js": OPTIONS_JS.strip() + "\n",
}


def _files(token: str) -> dict[str, str]:
    files = dict(FILES)
    if token:
        files["pairing.json"] = json.dumps({"token": token, "app": "Media Downloader"}) + "\n"
    return files


def export_extension(target: Path, token: str = "") -> Path:
    """Write the extension files into ``target`` (created if needed) and return the folder.

    With ``token`` the extension pairs itself with the app (pairing.json)."""
    target.mkdir(parents=True, exist_ok=True)
    for name, content in _files(token).items():
        (target / name).write_text(content, encoding="utf-8")
    try:
        from PIL import Image, ImageDraw

        icon = Image.new("RGBA", (128, 128), (11, 19, 35, 255))
        draw = ImageDraw.Draw(icon)
        draw.rounded_rectangle((8, 8, 120, 120), radius=26, fill=(118, 87, 255, 255))
        draw.polygon([(40, 52), (88, 52), (64, 88)], fill=(255, 255, 255, 255))
        draw.rectangle((56, 30, 72, 54), fill=(255, 255, 255, 255))
        icon.save(target / "icon.png")
    except Exception:
        pass
    return target


def refresh_exported_extension(target: Path, token: str = "") -> bool:
    """Keep an extension folder the user already loaded in sync with this app version.

    The browser reads an unpacked extension from that folder, so after an app update the new
    extension is used when the browser restarts (or the extension is reloaded). Returns True
    when files were rewritten."""
    if not (target / "manifest.json").exists():
        return False
    stale = False
    for name, content in _files(token).items():
        try:
            if (target / name).read_text(encoding="utf-8") != content:
                stale = True
                break
        except OSError:
            stale = True
            break
    if stale:
        export_extension(target, token)
    return stale
