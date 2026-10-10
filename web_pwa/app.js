const state = {
  key: "",
  analysisId: "",
  analysis: null,
  mode: "Video",
  installPrompt: null,
};

const $ = (id) => document.getElementById(id);
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function api(path, options = {}) {
  const headers = new Headers(options.headers || {});
  if (state.key) headers.set("X-Media-Core-Key", state.key);
  if (options.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  const response = await fetch(path, {...options, headers});
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || data.error || `Request failed (${response.status})`);
  return data;
}

function formatDuration(seconds) {
  const total = Math.max(0, Math.floor(Number(seconds || 0)));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  return h ? `${h}:${String(m).padStart(2,"0")}:${String(s).padStart(2,"0")}` : `${m}:${String(s).padStart(2,"0")}`;
}

function setMode(mode) {
  state.mode = mode;
  $("videoMode").classList.toggle("active", mode === "Video");
  $("audioMode").classList.toggle("active", mode === "Audio");
  $("qualitySelect").disabled = mode !== "Video";
  $("audioFormatSelect").disabled = mode !== "Audio";
  $("audioQualitySelect").disabled = mode !== "Audio";
  $("downloadBtn").textContent = mode === "Video" ? "Download video" : "Download audio";
}

function setAnalysisBusy(busy) {
  $("analyzeBtn").disabled = busy;
  $("cancelAnalyzeBtn").disabled = !busy;
  $("analyzeBtn").textContent = busy ? "Analyzing…" : "Analyze";
}

function showAnalysis(result) {
  state.analysis = result;
  $("mediaCard").classList.remove("muted-card");
  $("mediaBadge").textContent = `${result.platform || "MEDIA"} • READY`.toUpperCase();
  $("mediaTitle").textContent = result.title || "Media";
  $("mediaMeta").textContent = [result.creator, result.platform, formatDuration(result.duration)].filter(Boolean).join(" • ");
  $("filenameInput").value = result.filename || "media_download";
  $("downloadBtn").disabled = false;

  const qualities = Array.isArray(result.qualities) && result.qualities.length ? result.qualities : ["Best available"];
  $("qualitySelect").innerHTML = "";
  for (const quality of qualities) {
    const option = document.createElement("option");
    option.value = quality;
    option.textContent = quality;
    $("qualitySelect").appendChild(option);
  }
  if (qualities.includes("720p")) $("qualitySelect").value = "720p";

  const preview = $("preview");
  preview.innerHTML = "";
  if (result.thumbnail) {
    const img = new Image();
    img.src = result.thumbnail;
    img.alt = "";
    preview.appendChild(img);
  } else {
    preview.innerHTML = "<span>VIDEO<br/>PREVIEW</span>";
  }
}

async function pollAnalysis(id) {
  while (state.analysisId === id) {
    const {job} = await api(`/api/jobs/${id}`);
    $("analysisStatus").textContent = job.error || job.detail || job.status;
    if (job.status === "completed") {
      setAnalysisBusy(false);
      showAnalysis(job.result);
      $("analysisStatus").textContent = "Analysis complete. Ready to download.";
      return;
    }
    if (["failed","cancelled"].includes(job.status)) {
      setAnalysisBusy(false);
      $("mediaBadge").textContent = job.status === "failed" ? "ANALYSIS FAILED" : "ANALYSIS CANCELLED";
      $("downloadBtn").disabled = true;
      throw new Error(job.error || job.detail || "Analysis stopped.");
    }
    await sleep(500);
  }
}

async function analyze() {
  const url = $("urlInput").value.trim();
  if (!url) return;
  state.analysis = null;
  $("downloadBtn").disabled = true;
  $("mediaBadge").textContent = "ANALYZING";
  $("mediaTitle").textContent = "Analyzing media…";
  $("mediaMeta").textContent = "Trying compatible extraction paths.";
  setAnalysisBusy(true);
  try {
    const {job} = await api("/api/analyze", {method:"POST", body:JSON.stringify({url})});
    state.analysisId = job.id;
    await pollAnalysis(job.id);
  } catch (error) {
    $("analysisStatus").textContent = error.message;
    $("mediaTitle").textContent = "Could not analyze this link";
    setAnalysisBusy(false);
  }
}

async function cancelAnalysis() {
  if (!state.analysisId) return;
  try { await api(`/api/jobs/${state.analysisId}`, {method:"DELETE"}); } catch {}
  $("analysisStatus").textContent = "Cancelling analysis…";
}

async function startDownload() {
  if (!state.analysisId || !state.analysis) return;
  $("downloadBtn").disabled = true;
  try {
    await api("/api/downloads", {
      method:"POST",
      body:JSON.stringify({
        analysis_id: state.analysisId,
        mode: state.mode,
        video_quality: $("qualitySelect").value || "Best available",
        audio_format: $("audioFormatSelect").value || "MP3",
        audio_quality: $("audioQualitySelect").value || "192",
        filename: $("filenameInput").value.trim() || state.analysis.filename || "media_download",
        download_dir: $("downloadDirInput").value.trim(),
      })
    });
    $("analysisStatus").textContent = "Added to download activity. You can paste another link now.";
  } catch (error) {
    $("analysisStatus").textContent = error.message;
  } finally {
    $("downloadBtn").disabled = false;
  }
}

function jobRow(job) {
  const row = document.createElement("article");
  row.className = "download-row";
  const percent = Math.round((Number(job.progress || 0)) * 100);
  const action = ["running","queued","cancelling"].includes(job.status)
    ? `<button data-cancel="${job.id}" class="mini danger">${job.status === "cancelling" ? "Cancelling" : "Cancel"}</button>`
    : job.status === "failed"
      ? `<button data-retry="${job.id}" class="mini retry">Retry</button>`
      : "";
  row.innerHTML = `
    <div class="row-top">
      <strong>${escapeHtml(job.request?.filename || "Media download")}</strong>
      <span class="job-status ${job.status}">${job.status.toUpperCase()}</span>
    </div>
    <div class="progress"><span style="width:${job.status === "completed" ? 100 : percent}%"></span></div>
    <div class="row-bottom"><span>${escapeHtml(job.error || job.detail || "")}</span>${action}</div>
  `;
  return row;
}

function escapeHtml(value) {
  return String(value || "").replace(/[&<>"']/g, (ch) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#039;"}[ch]));
}

async function refreshDownloads() {
  if (!state.key) return;
  try {
    const {downloads} = await api("/api/downloads");
    const list = $("downloadsList");
    list.innerHTML = "";
    if (!downloads.length) {
      list.innerHTML = '<div class="empty">No downloads yet.</div>';
      return;
    }
    downloads.forEach((job) => list.appendChild(jobRow(job)));
  } catch {}
}

async function bootstrap() {
  try {
    const data = await api("/api/bootstrap");
    state.key = data.core_key;
    $("downloadDirInput").value = localStorage.getItem("media-download-dir") || data.download_dir;
    $("coreBadge").textContent = `LOCAL CORE • v${data.core_version || "?"}`;
    $("coreBadge").classList.remove("offline");
    $("coreBadge").classList.add("ready");
    $("analysisStatus").textContent = "Ready for a media link.";
    setInterval(refreshDownloads, 900);
    refreshDownloads();
    loadDiagnostics();
  } catch (error) {
    $("coreBadge").textContent = "CORE OFFLINE";
    $("analysisStatus").textContent = "Local Core is not available. Start Media Downloader Core.";
  }
}

$("analyzeBtn").addEventListener("click", analyze);
$("cancelAnalyzeBtn").addEventListener("click", cancelAnalysis);
$("downloadBtn").addEventListener("click", startDownload);
$("videoMode").addEventListener("click", () => setMode("Video"));
$("audioMode").addEventListener("click", () => setMode("Audio"));
$("pasteBtn").addEventListener("click", async () => {
  try { $("urlInput").value = await navigator.clipboard.readText(); } catch {}
});
$("downloadDirInput").addEventListener("change", () => localStorage.setItem("media-download-dir", $("downloadDirInput").value.trim()));
$("chooseFolderBtn").addEventListener("click", async () => {
  try {
    const data = await api("/api/system/choose-folder", {
      method:"POST",
      body:JSON.stringify({current_dir:$("downloadDirInput").value.trim() || null}),
    });
    if (data.selected) {
      $("downloadDirInput").value = data.selected;
      localStorage.setItem("media-download-dir", data.selected);
    }
  } catch (error) {
    $("analysisStatus").textContent = error.message;
  }
});

async function loadDiagnostics() {
  try {
    const data = await api("/api/diagnostics");
    $("diagnosticsBox").textContent = [
      `Core: v${data.version}`,
      `Python: ${data.python}`,
      `Platform: ${data.platform}`,
      `Network: ${data.network}`,
      `Browser resolver: ${data.browser || "Not found"}`,
      `FFmpeg: ${data.ffmpeg}`,
      `State: ${data.state_file}`,
      `Log: ${data.log_file}`,
      `Downloads: ${JSON.stringify(data.download_counts || {})}`,
    ].join("\n");
  } catch (error) {
    $("diagnosticsBox").textContent = error.message;
  }
}
$("refreshDiagnosticsBtn").addEventListener("click", loadDiagnostics);
$("openLogBtn").addEventListener("click", async () => {
  try { await api("/api/system/open-log", {method:"POST"}); }
  catch (error) { $("analysisStatus").textContent = error.message; }
});
$("openDownloadsBtn").addEventListener("click", async () => {
  try {
    await api("/api/system/open-downloads", {
      method:"POST",
      body:JSON.stringify({download_dir:$("downloadDirInput").value.trim()}),
    });
  } catch (error) { $("analysisStatus").textContent = error.message; }
});
$("downloadsList").addEventListener("click", async (event) => {
  const cancel = event.target.closest("[data-cancel]");
  const retry = event.target.closest("[data-retry]");
  try {
    if (cancel) await api(`/api/downloads/${cancel.dataset.cancel}`, {method:"DELETE"});
    if (retry) await api(`/api/downloads/${retry.dataset.retry}/retry`, {method:"POST"});
    await refreshDownloads();
  } catch (error) {
    $("analysisStatus").textContent = error.message;
  }
});

window.addEventListener("beforeinstallprompt", (event) => {
  event.preventDefault();
  state.installPrompt = event;
  $("installBtn").classList.remove("hidden");
});
$("installBtn").addEventListener("click", async () => {
  if (!state.installPrompt) return;
  state.installPrompt.prompt();
  await state.installPrompt.userChoice;
  state.installPrompt = null;
  $("installBtn").classList.add("hidden");
});

if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
setMode("Video");
bootstrap();
