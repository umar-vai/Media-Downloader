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

EXTENSION_VERSION = "2.1.0"

MANIFEST = {
    "manifest_version": 3,
    "name": "Media Downloader Connector",
    "version": EXTENSION_VERSION,
    "description": "Download button on every video: sends the playing stream (with all qualities) to the Media Downloader desktop app.",
    "icons": {"128": "icon.png"},
    "permissions": [
        "activeTab", "contextMenus", "cookies", "storage", "webRequest", "notifications",
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
            "run_at": "document_idle",
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

let settings = {blocked: "", overlay: true};
const tabs = new Map();      // tabId -> {gen, navGen, origin, streams: [], children: []}
const pending = new Map();   // requestId -> captured request headers
const ownFetches = new Set(); // playlist URLs we are reading ourselves (ignore their traffic)

// ------------------------------------------------------------------ settings / blocked sites
async function loadSettings() {
  const data = await api.storage.local.get(["blocked", "overlay"]);
  settings = {blocked: String(data.blocked || ""), overlay: data.overlay !== false};
}
loadSettings();
api.storage.onChanged.addListener((changes, area) => {
  if (area === "local" && (changes.blocked || changes.overlay)) loadSettings();
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
async function getToken() {
  const data = await api.storage.local.get("token");
  return (data.token || "").trim();
}

async function findPort() {
  const cached = (await api.storage.local.get("port")).port;
  const order = cached ? [cached, ...PORTS.filter(p => p !== cached)] : PORTS;
  for (const port of order) {
    try {
      const r = await fetch(`http://127.0.0.1:${port}/ping`, {cache: "no-store"});
      const j = await r.json();
      if (j && j.app === "Media Downloader") { await api.storage.local.set({port}); return port; }
    } catch (e) {}
  }
  return 0;
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

// payload: {url, referer, headers, title, quality, mode, pageUrl}
async function sendToApp(payload) {
  const token = await getToken();
  if (!token) return {ok: false, error: "Open the extension options and paste the pairing token from Media Downloader → Settings → Browser."};
  const port = await findPort();
  if (!port) return {ok: false, error: "Media Downloader is not running."};
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
    cookies,
  };
  try {
    const r = await fetch(`http://127.0.0.1:${port}/download`, {
      method: "POST",
      headers: {"Content-Type": "application/json", "X-MD-Token": token},
      body: JSON.stringify(body),
    });
    const j = await r.json().catch(() => ({}));
    return r.ok && j.ok ? {ok: true} : {ok: false, error: r.status === 401 ? "Wrong pairing token." : "The app refused the link."};
  } catch (e) {
    return {ok: false, error: "Could not reach Media Downloader."};
  }
}

async function postToApp(path, body) {
  const token = await getToken();
  if (!token) return {ok: false};
  const port = await findPort();
  if (!port) return {ok: false};
  try {
    const r = await fetch(`http://127.0.0.1:${port}${path}`, {
      method: "POST",
      headers: {"Content-Type": "application/json", "X-MD-Token": token},
      body: JSON.stringify(body || {}),
    });
    const j = await r.json().catch(() => ({}));
    return {ok: r.ok && !!j.ok, status: r.status};
  } catch (e) {
    return {ok: false};
  }
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
  notify(result.ok ? "Added to Media Downloader." : result.error);
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
  const streams = state ? state.streams.filter(s => !s.drm) : [];
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

function announce(tabId) {
  const state = tabs.get(tabId);
  const count = state ? state.streams.filter(s => !s.drm).length : 0;
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
    tabs.set(tabId, {gen: 0, navGen: 0, origin, streams: [], children: []});
  } else {
    state.origin = origin;
    if (change.status === "loading") state.navGen = state.gen; // older streams belong to the previous page
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

async function menuFor(tabId) {
  const state = await tabState(tabId);
  const sorted = [...state.streams].sort((a, b) => b.gen - a.gen);
  let nowGiven = false;
  const groups = sorted.map(stream => {
    let label = "Earlier stream";
    if (!nowGiven && stream.gen > state.navGen) { label = "Now playing"; nowGiven = true; }
    else if (stream.gen <= state.navGen) label = "From the previous page";
    const kind = stream.kind === "file" ? (stream.ext || "file").toUpperCase() : stream.kind.toUpperCase();
    const meta = [kind, fmtDuration(stream.duration), hostOf(stream.url)].filter(Boolean).join(" · ");
    return {id: stream.id, label, meta, current: label === "Now playing", items: itemsFor(stream)};
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
  if (info.menuItemId === "md-link" && info.linkUrl) sendAndNotify({url: info.linkUrl, referer: pageUrl, pageUrl});
  else if (info.menuItemId === "md-media" && info.srcUrl && !info.srcUrl.startsWith("blob:")) sendAndNotify({url: info.srcUrl, referer: info.frameUrl || pageUrl, pageUrl, title});
  else if (pageUrl) sendAndNotify({url: info.pageUrl || pageUrl, pageUrl});
});

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
  if (msg.type === "md-send") {
    const item = msg.item || {};
    const pageUrl = item.pageUrl || (tab && tab.url) || msg.pageUrl || "";
    sendToApp({...item, pageUrl, title: item.title || cleanTitle(tab && tab.title) || msg.title || ""}).then(sendResponse);
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
  if (msg.type === "ping") { findPort().then(port => sendResponse({port})); return true; }
});
"""

CONTENT_JS = r"""
(() => {
  if (window.__mediaDownloaderConnector) return;
  window.__mediaDownloaderConnector = true;
  const api = typeof browser !== "undefined" ? browser : chrome;
  const MIN_W = 200, MIN_H = 110, HIDE_AFTER = 2600;

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

  async function renderMenu() {
    const data = await ask({type: "md-menu"});
    if (!menuOpen) return;
    menu.textContent = "";
    const groups = (data && data.groups) || [];
    for (const group of groups) {
      const head = document.createElement("div");
      head.className = "group" + (group.current ? " now" : "");
      const name = document.createElement("span"); name.textContent = group.label;
      const meta = document.createElement("span"); meta.className = "meta"; meta.textContent = group.meta;
      head.append(name, meta);
      menu.appendChild(head);
      for (const item of group.items) {
        const el = row((item.best ? "best " : "") + (item.disabled ? "disabled" : ""), item.label, item.detail);
        if (!item.disabled) el.addEventListener("click", () => send({type: "md-send", item}));
        menu.appendChild(el);
      }
    }
    const src = target && (target.currentSrc || target.src);
    if (src && /^https?:/i.test(src) && !groups.some(g => g.items.some(i => i.url === src))) {
      if (groups.length) menu.appendChild(Object.assign(document.createElement("div"), {className: "sep"}));
      const el = row("best", "This " + (target.tagName === "AUDIO" ? "audio" : "video") + " file", "");
      el.addEventListener("click", () => send({type: "md-send", item: {url: src, referer: location.href, quality: "Best available", mode: target.tagName === "AUDIO" ? "Audio" : ""}}));
      menu.appendChild(el);
    } else if (!groups.length) {
      const note = document.createElement("div");
      note.className = "muted";
      note.textContent = "No stream captured yet. Start playing the video (or pick a server/Sub/Dub), then open this menu again.";
      menu.appendChild(note);
    }
    menu.appendChild(Object.assign(document.createElement("div"), {className: "sep"}));
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
    if (el) show(el);
  }

  async function start() {
    const state = await ask({type: "md-hello"});
    enabled = !!(state && state.enabled);
    if (!enabled) { hide(); return; }
    collect();
    if (start.done) return;
    start.done = true;
    let pendingScan = 0;
    new MutationObserver(() => {
      if (pendingScan) return;
      pendingScan = setTimeout(() => { pendingScan = 0; collect(); }, 400);
    }).observe(document.documentElement, {childList: true, subtree: true});
    document.addEventListener("mousemove", onMove, {capture: true, passive: true});
    document.addEventListener("playing", e => { if (e.target instanceof HTMLMediaElement) { collect(); if (qualifies(e.target)) show(e.target, 4000); } }, true);
    document.addEventListener("pointerdown", e => { if (menuOpen && !e.composedPath().includes(host)) closeMenu(); }, true);
    document.addEventListener("keydown", e => { if (e.key === "Escape" && menuOpen) closeMenu(); }, true);
    document.addEventListener("fullscreenchange", mount);
    document.addEventListener("webkitfullscreenchange", mount);
    addEventListener("scroll", () => { if (target) place(); }, {capture: true, passive: true});
    addEventListener("resize", () => { if (target) place(); });
    api.runtime.onMessage.addListener(msg => {
      if (!msg || msg.type !== "md-streams-changed") return;
      const grew = (msg.count || 0) > count;
      setCount(msg.count);
      if (menuOpen) renderMenu();
      else if (grew && target) show(target, 3000);
    });
    api.storage.onChanged.addListener((changes, area) => {
      if (area === "local" && (changes.blocked || changes.overlay)) start();
    });
    const data = await ask({type: "md-menu"});
    setCount(data && data.groups ? data.groups.filter(g => !g.items.every(i => i.disabled)).length : 0);
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
<h2>STREAMS ON THIS TAB</h2>
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
  for (const group of data.groups) {
    const head = document.createElement("div");
    head.className = "group" + (group.current ? " now" : "");
    const a = document.createElement("span"); a.textContent = group.label;
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
<h2>1. Pairing</h2>
<p>In Media Downloader open <b>Settings → Browser</b>, copy the pairing token and paste it here.</p>
<input type="text" id="token" placeholder="Pairing token" autocomplete="off">
<h2>2. Download button</h2>
<label><input type="checkbox" id="overlay"> Show a <b>Download</b> button on videos and audio players (like IDM)</label>
<h2>3. Blocked sites</h2>
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
api.storage.local.get(["token", "overlay", "blocked"]).then(d => {
  token.value = d.token || "";
  overlay.checked = d.overlay !== false;
  blocked.value = d.blocked || "";
});
document.getElementById("save").onclick = async () => {
  await api.storage.local.set({token: token.value.trim(), overlay: overlay.checked, blocked: blocked.value.trim()});
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


def export_extension(target: Path) -> Path:
    """Write the extension files into ``target`` (created if needed) and return the folder."""
    target.mkdir(parents=True, exist_ok=True)
    for name, content in FILES.items():
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
