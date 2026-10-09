const pairingInput = document.getElementById("pairing");
const saveButton = document.getElementById("savePairing");
const testButton = document.getElementById("testConnection");
const clearButton = document.getElementById("clearTab");
const statusBadge = document.getElementById("status");
const connectionText = document.getElementById("connectionText");
const capturesRoot = document.getElementById("captures");
const countLabel = document.getElementById("count");
const autoSendBestHls = document.getElementById("autoSendBestHls");
const autoSendText = document.getElementById("autoSendText");

async function settings() {
  return chrome.storage.local.get({
    port: 38471,
    token: "",
    autoSendBestHls: true,
    lastAutoSendStatus: null
  });
}

function renderAutoSendStatus(cfg) {
  const enabled = Boolean(cfg.autoSendBestHls);
  autoSendBestHls.checked = enabled;
  autoSendText.classList.remove("off", "error");

  if (!enabled) {
    autoSendText.textContent = "Disabled — use Send best to app manually.";
    autoSendText.classList.add("off");
    return;
  }

  const status = cfg.lastAutoSendStatus;
  if (status && status.ok === false) {
    autoSendText.textContent = "Enabled — last auto-send could not reach the desktop app.";
    autoSendText.classList.add("error");
    return;
  }
  if (status && status.ok === true) {
    const count = Number(status.count || 0);
    autoSendText.textContent = count > 1
      ? `Enabled — last playback auto-sent ${count} HLS candidates.`
      : "Enabled — last playback auto-sent HLS to the app.";
    return;
  }
  autoSendText.textContent = "Enabled — play a video and HLS will be sent automatically.";
}

async function currentTab() {
  const tabs = await chrome.tabs.query({active: true, currentWindow: true});
  return tabs[0] || null;
}

function parsePairing(value) {
  const raw = String(value || "").trim();
  const separator = raw.indexOf("|");
  if (separator <= 0) return null;
  const port = Number(raw.slice(0, separator));
  const token = raw.slice(separator + 1).trim();
  if (!Number.isInteger(port) || port < 1 || port > 65535 || !token) return null;
  return {port, token};
}

async function testConnection(showText = true) {
  const cfg = await settings();
  if (!cfg.token) {
    statusBadge.textContent = "Not paired";
    statusBadge.classList.remove("ok");
    if (showText) connectionText.textContent = "Paste the code from Media Downloader.";
    return false;
  }
  try {
    const response = await fetch(`http://127.0.0.1:${cfg.port}/health`, {cache: "no-store"});
    const data = await response.json();
    if (!response.ok || !data.ok) throw new Error("Offline");
    statusBadge.textContent = "Connected";
    statusBadge.classList.add("ok");
    if (showText) connectionText.textContent = `Desktop app on port ${cfg.port}`;
    return true;
  } catch (_) {
    statusBadge.textContent = "Offline";
    statusBadge.classList.remove("ok");
    if (showText) connectionText.textContent = "Open Media Downloader and check the pairing code.";
    return false;
  }
}

function mediaHost(url) {
  try { return new URL(url).hostname; } catch (_) { return ""; }
}

function inferQualityHint(url) {
  const value = String(url || "").toLowerCase();
  const explicit = value.match(/(?:^|[^0-9])(2160|1440|1080|720|540|480|360|240)p(?:[^0-9]|$)/);
  if (explicit) return `${explicit[1]}p`;

  const dimensions = value.match(/(?:^|[^0-9])(\d{3,4})x(\d{3,4})(?:[^0-9]|$)/);
  if (dimensions) {
    const height = Number(dimensions[2]);
    if ([2160, 1440, 1080, 720, 540, 480, 360, 240].includes(height)) return `${height}p`;
  }

  try {
    const parsed = new URL(url);
    for (const key of ["height", "h", "quality", "res", "resolution"]) {
      const raw = String(parsed.searchParams.get(key) || "").toLowerCase();
      const match = raw.match(/(2160|1440|1080|720|540|480|360|240)/);
      if (match) return `${match[1]}p`;
    }
  } catch (_) {}
  return "";
}

function relatedCaptures(item, allItems) {
  const page = String(item.page_url || "");
  const title = String(item.title || "");
  const kind = String(item.kind || "");
  const host = mediaHost(item.url);
  if (!["hls", "dash", "direct"].includes(kind)) return [item];

  const related = allItems.filter((candidate) => {
    return String(candidate.kind || "") === kind &&
      String(candidate.page_url || "") === page &&
      String(candidate.title || "") === title &&
      mediaHost(candidate.url) === host;
  });
  return related.length ? related : [item];
}

async function sendCaptureGroup(item, allItems, button) {
  const cfg = await settings();
  if (!cfg.token) {
    connectionText.textContent = "Pair the extension first.";
    return;
  }

  const candidates = relatedCaptures(item, allItems);
  const batchId = crypto.randomUUID();
  const old = button.textContent;
  button.disabled = true;

  try {
    for (let index = 0; index < candidates.length; index += 1) {
      button.textContent = candidates.length > 1 ? `Sending ${index + 1}/${candidates.length}…` : "Sending…";
      const payload = {
        ...candidates[index],
        capture_group_id: batchId
      };
      const response = await fetch(`http://127.0.0.1:${cfg.port}/capture`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Media-Downloader-Token": cfg.token
        },
        body: JSON.stringify(payload)
      });
      const data = await response.json();
      if (!response.ok || !data.ok) throw new Error(data.error || "Send failed");
    }

    button.textContent = "Sent";
    statusBadge.textContent = "Connected";
    statusBadge.classList.add("ok");
    connectionText.textContent = candidates.length > 1
      ? `Sent ${candidates.length} related streams. Desktop will auto-select the best quality.`
      : "Sent to desktop. Best available quality will be selected automatically.";
    setTimeout(() => { button.disabled = false; button.textContent = old; }, 1400);
  } catch (error) {
    button.disabled = false;
    button.textContent = "Retry";
    connectionText.textContent = error.message || "Could not reach desktop app.";
  }
}

async function render() {
  const tab = await currentTab();
  const state = await chrome.storage.session.get({captures: []});
  const all = Array.isArray(state.captures) ? state.captures : [];
  const items = tab ? all.filter((item) => item.tab_id === tab.id) : [];
  countLabel.textContent = `${items.length} stream${items.length === 1 ? "" : "s"}`;
  capturesRoot.replaceChildren();

  if (!items.length) {
    const empty = document.createElement("div");
    empty.className = "empty";
    empty.textContent = "Play a video to detect media streams.";
    capturesRoot.appendChild(empty);
    return;
  }

  for (const item of items) {
    const card = document.createElement("div");
    card.className = "capture";

    const title = document.createElement("h2");
    title.textContent = item.title || "Captured media";
    card.appendChild(title);

    const meta = document.createElement("div");
    meta.className = "meta";
    let host = "media";
    try { host = new URL(item.url).hostname; } catch (_) {}
    const kind = String(item.kind || "media").toUpperCase();
    const qualityHint = inferQualityHint(item.url);
    const qualityText = qualityHint || (item.kind === "page" ? "Auto" : "Best resolved in app");
    meta.innerHTML = `<span class="kind">${kind}</span> • ${qualityText} • ${host}`;
    card.appendChild(meta);

    if (item.kind === "page") {
      const note = document.createElement("div");
      note.className = "meta";
      note.textContent = "Page fallback: the desktop app will try to extract the playing video.";
      card.appendChild(note);
    }

    const actions = document.createElement("div");
    actions.className = "row";
    const send = document.createElement("button");
    send.textContent = item.kind === "page" ? "Send to app" : "Send best to app";
    send.addEventListener("click", () => sendCaptureGroup(item, items, send));
    actions.appendChild(send);
    card.appendChild(actions);
    capturesRoot.appendChild(card);
  }
}

autoSendBestHls.addEventListener("change", async () => {
  const enabled = Boolean(autoSendBestHls.checked);
  await chrome.storage.local.set({autoSendBestHls: enabled});
  const cfg = await settings();
  renderAutoSendStatus(cfg);
});

saveButton.addEventListener("click", async () => {
  const parsed = parsePairing(pairingInput.value);
  if (!parsed) {
    connectionText.textContent = "Pairing code should look like 38471|token";
    return;
  }
  await chrome.storage.local.set(parsed);
  pairingInput.value = "";
  await testConnection(true);
});

testButton.addEventListener("click", () => testConnection(true));

clearButton.addEventListener("click", async () => {
  const tab = await currentTab();
  if (!tab) return;
  const state = await chrome.storage.session.get({captures: []});
  const all = Array.isArray(state.captures) ? state.captures : [];
  await chrome.storage.session.set({captures: all.filter((item) => item.tab_id !== tab.id)});
  await render();
});

chrome.storage.onChanged.addListener((_changes, areaName) => {
  if (areaName === "session") {
    render().catch(() => {});
  } else if (areaName === "local") {
    settings().then(renderAutoSendStatus).catch(() => {});
  }
});

document.addEventListener("DOMContentLoaded", async () => {
  const cfg = await settings();
  renderAutoSendStatus(cfg);
  await testConnection(false);
  await render();
  setInterval(() => render().catch(() => {}), 1500);
});
