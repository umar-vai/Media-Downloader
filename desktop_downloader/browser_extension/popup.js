const pairingInput = document.getElementById("pairing");
const saveButton = document.getElementById("savePairing");
const testButton = document.getElementById("testConnection");
const clearButton = document.getElementById("clearTab");
const statusBadge = document.getElementById("status");
const connectionText = document.getElementById("connectionText");
const capturesRoot = document.getElementById("captures");
const countLabel = document.getElementById("count");

async function settings() {
  return chrome.storage.local.get({port: 38471, token: ""});
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

async function sendCapture(item, button) {
  const cfg = await settings();
  if (!cfg.token) {
    connectionText.textContent = "Pair the extension first.";
    return;
  }
  const old = button.textContent;
  button.disabled = true;
  button.textContent = "Sending…";
  try {
    const response = await fetch(`http://127.0.0.1:${cfg.port}/capture`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Media-Downloader-Token": cfg.token
      },
      body: JSON.stringify(item)
    });
    const data = await response.json();
    if (!response.ok || !data.ok) throw new Error(data.error || "Send failed");
    button.textContent = "Sent";
    statusBadge.textContent = "Connected";
    statusBadge.classList.add("ok");
    setTimeout(() => { button.disabled = false; button.textContent = old; }, 1100);
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
    meta.innerHTML = `<span class="kind">${kind}</span> • ${host}`;
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
    send.textContent = "Send to app";
    send.addEventListener("click", () => sendCapture(item, send));
    actions.appendChild(send);
    card.appendChild(actions);
    capturesRoot.appendChild(card);
  }
}

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
  if (areaName === "session") render().catch(() => {});
});

document.addEventListener("DOMContentLoaded", async () => {
  await testConnection(false);
  await render();
  setInterval(() => render().catch(() => {}), 1500);
});
