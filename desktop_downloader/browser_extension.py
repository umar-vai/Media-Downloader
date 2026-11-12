"""Source of the Media Downloader browser extension (Chrome / Edge / Brave / Firefox, Manifest V3).

The files are embedded here so the installed app can export them to a folder, which the user
then loads once with "Load unpacked" (Chrome/Edge) or "Load Temporary Add-on" (Firefox).
"""

from __future__ import annotations

import json
from pathlib import Path

EXTENSION_VERSION = "1.0.0"

MANIFEST = {
    "manifest_version": 3,
    "name": "Media Downloader Connector",
    "version": EXTENSION_VERSION,
    "description": "Send pages and detected media to the Media Downloader desktop app, with your logged-in cookies.",
    "icons": {"128": "icon.png"},
    "permissions": ["activeTab", "contextMenus", "cookies", "storage", "webRequest", "notifications"],
    "host_permissions": ["<all_urls>"],
    "background": {"service_worker": "background.js", "scripts": ["background.js"]},
    "action": {"default_title": "Send to Media Downloader", "default_popup": "popup.html"},
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
const MEDIA_RE = /\.(m3u8|mpd|mp4|webm|m4a|mp3)(\?|#|$)/i;
const detected = new Map(); // tabId -> [{url, kind}]

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
  try { return await api.cookies.getAll({url}); } catch (e) { return []; }
}

async function sendToApp(url, referer) {
  const token = await getToken();
  if (!token) return {ok: false, error: "Open the extension options and paste the pairing token from Media Downloader → Settings → Browser."};
  const port = await findPort();
  if (!port) return {ok: false, error: "Media Downloader is not running."};
  const cookies = await cookiesFor(referer || url);
  const mediaCookies = referer && referer !== url ? await cookiesFor(url) : [];
  try {
    const r = await fetch(`http://127.0.0.1:${port}/download`, {
      method: "POST",
      headers: {"Content-Type": "application/json", "X-MD-Token": token},
      body: JSON.stringify({url, referer: referer || "", cookies: cookies.concat(mediaCookies)}),
    });
    const j = await r.json().catch(() => ({}));
    return r.ok && j.ok ? {ok: true} : {ok: false, error: r.status === 401 ? "Wrong pairing token." : "The app refused the link."};
  } catch (e) {
    return {ok: false, error: "Could not reach Media Downloader."};
  }
}

function notify(message) {
  try { api.notifications.create({type: "basic", iconUrl: "icon.png", title: "Media Downloader", message}); } catch (e) {}
}

async function sendAndNotify(url, referer) {
  const result = await sendToApp(url, referer);
  notify(result.ok ? "Added to Media Downloader." : result.error);
  return result;
}

api.runtime.onInstalled.addListener(() => {
  api.contextMenus.create({id: "md-page", title: "Download page with Media Downloader", contexts: ["page"]});
  api.contextMenus.create({id: "md-link", title: "Download link with Media Downloader", contexts: ["link"]});
  api.contextMenus.create({id: "md-media", title: "Download media with Media Downloader", contexts: ["video", "audio"]});
});

api.contextMenus.onClicked.addListener((info, tab) => {
  if (info.menuItemId === "md-link" && info.linkUrl) sendAndNotify(info.linkUrl, tab && tab.url);
  else if (info.menuItemId === "md-media" && info.srcUrl && !info.srcUrl.startsWith("blob:")) sendAndNotify(info.srcUrl, tab && tab.url);
  else if (tab && tab.url) sendAndNotify(info.pageUrl || tab.url, "");
});

api.commands.onCommand.addListener(async (command) => {
  if (command !== "send-page") return;
  const [tab] = await api.tabs.query({active: true, currentWindow: true});
  if (tab && tab.url) sendAndNotify(tab.url, "");
});

api.webRequest.onBeforeRequest.addListener((details) => {
  if (details.tabId < 0 || !MEDIA_RE.test(details.url)) return;
  const list = detected.get(details.tabId) || [];
  if (list.some(item => item.url === details.url)) return;
  list.push({url: details.url, kind: (details.url.match(MEDIA_RE) || [])[1] || "media"});
  detected.set(details.tabId, list.slice(-15));
}, {urls: ["<all_urls>"], types: ["media", "xmlhttprequest", "other"]});

api.tabs.onRemoved.addListener(tabId => detected.delete(tabId));
api.tabs.onUpdated.addListener((tabId, change) => { if (change.status === "loading" && change.url) detected.delete(tabId); });

api.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg.type === "send") { sendToApp(msg.url, msg.referer || "").then(sendResponse); return true; }
  if (msg.type === "detected") { sendResponse(detected.get(msg.tabId) || []); return false; }
  if (msg.type === "ping") { findPort().then(port => sendResponse({port})); return true; }
});
"""

POPUP_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>Media Downloader</title>
<style>
 body{font:13px Segoe UI,system-ui,sans-serif;width:340px;margin:0;padding:14px;background:#0b1323;color:#f7faff}
 h1{font-size:15px;margin:0 0 10px}
 button{width:100%;padding:10px;border:0;border-radius:8px;background:#7657ff;color:#fff;font-size:13px;cursor:pointer}
 button.s{background:#14223a;margin-top:6px;text-align:left;font-size:11px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
 #msg{margin-top:10px;color:#8798b5;min-height:16px}
 h2{font-size:11px;color:#23d5ff;margin:14px 0 4px;letter-spacing:.06em}
</style></head><body>
<h1>Media Downloader</h1>
<button id="page">Send this page</button>
<h2>DETECTED MEDIA ON THIS PAGE</h2>
<div id="list"><span style="color:#8798b5">Nothing detected yet - play the video.</span></div>
<div id="msg"></div>
<script src="popup.js"></script></body></html>
"""

POPUP_JS = r"""
const api = typeof browser !== "undefined" ? browser : chrome;
const msg = document.getElementById("msg");
async function send(url, referer) {
  msg.textContent = "Sending...";
  const r = await api.runtime.sendMessage({type: "send", url, referer});
  msg.textContent = r && r.ok ? "Added to Media Downloader." : (r && r.error) || "Failed.";
}
(async () => {
  const [tab] = await api.tabs.query({active: true, currentWindow: true});
  document.getElementById("page").onclick = () => tab && tab.url && send(tab.url, "");
  const items = await api.runtime.sendMessage({type: "detected", tabId: tab.id});
  const list = document.getElementById("list");
  if (items && items.length) {
    list.textContent = "";
    for (const item of items) {
      const b = document.createElement("button");
      b.className = "s"; b.textContent = item.kind.toUpperCase() + "  " + item.url;
      b.onclick = () => send(item.url, tab.url);
      list.appendChild(b);
    }
  }
})();
"""

OPTIONS_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>Media Downloader Connector</title>
<style>body{font:14px Segoe UI,system-ui,sans-serif;max-width:560px;margin:40px auto;padding:0 16px}
input{width:100%;padding:8px;margin:8px 0}button{padding:8px 16px}</style></head><body>
<h1>Media Downloader Connector</h1>
<p>In Media Downloader open <b>Settings → Browser</b>, copy the pairing token and paste it here.</p>
<input id="token" placeholder="Pairing token" autocomplete="off">
<button id="save">Save</button> <span id="state"></span>
<script src="options.js"></script></body></html>
"""

OPTIONS_JS = r"""
const api = typeof browser !== "undefined" ? browser : chrome;
const input = document.getElementById("token");
api.storage.local.get("token").then(d => { input.value = d.token || ""; });
document.getElementById("save").onclick = async () => {
  await api.storage.local.set({token: input.value.trim()});
  document.getElementById("state").textContent = "Saved.";
};
"""

FILES = {
    "manifest.json": json.dumps(MANIFEST, indent=2),
    "background.js": BACKGROUND_JS.strip() + "\n",
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
