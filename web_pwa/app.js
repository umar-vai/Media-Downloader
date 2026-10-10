const state = {
  key: "",
  analysisId: "",
  analysis: null,
  mode: "Video",
  installPrompt: null,
  settings: {},
  updatePoll: null,
  agent: {},
  downloads: {},
  editorInfo: null,
  editorExportId: "",
  editorObjectUrl: "",
  editorLibrary: {presets: [], projects: []},
  cropDrag: null,
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

async function apiBlob(path, options = {}) {
  const headers = new Headers(options.headers || {});
  if (state.key) headers.set("X-Media-Core-Key", state.key);
  if (options.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  const response = await fetch(path, {...options, headers});
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new Error(data.detail || data.error || `Request failed (${response.status})`);
  }
  return response.blob();
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
  const preferredQuality = state.settings.video_quality || "720p";
  if (qualities.includes(preferredQuality)) $("qualitySelect").value = preferredQuality;
  else if (qualities.includes("720p")) $("qualitySelect").value = "720p";

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
  const actions = [];
  if (["running","queued","cancelling"].includes(job.status)) {
    actions.push(`<button data-cancel="${job.id}" class="mini danger">${job.status === "cancelling" ? "Cancelling" : "Cancel"}</button>`);
  } else if (job.status === "failed") {
    actions.push(`<button data-retry="${job.id}" class="mini retry">Retry</button>`);
  } else if (job.status === "completed" && job.result?.path) {
    actions.push(`<button data-edit-job="${job.id}" class="mini retry">Edit</button>`);
  }

  const metricParts = [];
  if (Number(job.metrics?.speed_bps || 0) > 0) metricParts.push(`${(Number(job.metrics.speed_bps) / 1024 / 1024).toFixed(1)} MB/s`);
  if (job.metrics?.eta_seconds != null) metricParts.push(`ETA ${formatDuration(job.metrics.eta_seconds)}`);
  if (Number(job.meta?.retry_count || 0) > 0) metricParts.push(`Retries ${job.meta.retry_count}`);
  if (Number(job.meta?.recovery_count || 0) > 0) metricParts.push(`Recovered ${job.meta.recovery_count}×`);
  const statusDetail = [job.error || job.detail || "", metricParts.join(" • ")].filter(Boolean).join(" • ");

  row.innerHTML = `
    <div class="row-top">
      <strong>${escapeHtml(job.request?.filename || "Media download")}</strong>
      <span class="job-status ${job.status}">${job.status.toUpperCase()}</span>
    </div>
    <div class="progress"><span style="width:${job.status === "completed" ? 100 : percent}%"></span></div>
    <div class="row-bottom"><span>${escapeHtml(statusDetail)}</span><div class="row-actions">${actions.join("")}</div></div>
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
    state.downloads = Object.fromEntries(downloads.map((job) => [job.id, job]));
    const list = $("downloadsList");
    list.innerHTML = "";
    if (!downloads.length) {
      list.innerHTML = '<div class="empty">No downloads yet.</div>';
      return;
    }
    downloads.forEach((job) => list.appendChild(jobRow(job)));
  } catch {}
}

function clearEditorPreviewObjectUrl() {
  if (state.editorObjectUrl) {
    URL.revokeObjectURL(state.editorObjectUrl);
    state.editorObjectUrl = "";
  }
}

function showEditorImage(blob, showCropOverlay = false) {
  clearEditorPreviewObjectUrl();
  state.editorObjectUrl = URL.createObjectURL(blob);
  $("editorPreviewVideo").pause();
  $("editorPreviewVideo").removeAttribute("src");
  $("editorPreviewVideo").classList.add("hidden");
  $("editorPreviewImage").src = state.editorObjectUrl;
  $("editorPreviewImage").classList.remove("hidden");
  $("editorPreviewPlaceholder").classList.add("hidden");
  if (showCropOverlay) requestAnimationFrame(updateCropOverlay);
  else $("editorCropOverlay").classList.add("hidden");
}

function showEditorVideo(blob) {
  clearEditorPreviewObjectUrl();
  state.editorObjectUrl = URL.createObjectURL(blob);
  $("editorCropOverlay").classList.add("hidden");
  $("editorPreviewImage").classList.add("hidden");
  $("editorPreviewVideo").src = state.editorObjectUrl;
  $("editorPreviewVideo").classList.remove("hidden");
  $("editorPreviewPlaceholder").classList.add("hidden");
  $("editorPreviewVideo").play().catch(() => {});
}

function editorCustomCropPayload() {
  return {
    custom_x: Number($("editorCropX").value || 0),
    custom_y: Number($("editorCropY").value || 0),
    custom_width: Number($("editorCropWidth").value || 0),
    custom_height: Number($("editorCropHeight").value || 0),
  };
}

function cropGeometry() {
  const info = state.editorInfo;
  const shell = $("editorPreviewShell");
  if (!info?.has_video || !Number(info.width) || !Number(info.height)) return null;
  const style = getComputedStyle(shell);
  const padLeft = Number.parseFloat(style.paddingLeft) || 0;
  const padRight = Number.parseFloat(style.paddingRight) || 0;
  const padTop = Number.parseFloat(style.paddingTop) || 0;
  const padBottom = Number.parseFloat(style.paddingBottom) || 0;
  const innerWidth = Math.max(1, shell.clientWidth - padLeft - padRight);
  const innerHeight = Math.max(1, shell.clientHeight - padTop - padBottom);
  const scale = Math.min(innerWidth / Number(info.width), innerHeight / Number(info.height));
  const displayWidth = Number(info.width) * scale;
  const displayHeight = Number(info.height) * scale;
  return {
    scale,
    left: padLeft + (innerWidth - displayWidth) / 2,
    top: padTop + (innerHeight - displayHeight) / 2,
    displayWidth,
    displayHeight,
  };
}

function normalizeCropValues(values = editorCustomCropPayload()) {
  const sourceWidth = Math.max(2, Number(state.editorInfo?.width || 2));
  const sourceHeight = Math.max(2, Number(state.editorInfo?.height || 2));
  let x = Math.max(0, Math.min(Math.round(Number(values.custom_x || 0)), sourceWidth - 2));
  let y = Math.max(0, Math.min(Math.round(Number(values.custom_y || 0)), sourceHeight - 2));
  let width = Math.max(2, Math.round(Number(values.custom_width || sourceWidth) / 2) * 2);
  let height = Math.max(2, Math.round(Number(values.custom_height || sourceHeight) / 2) * 2);
  width = Math.min(width, sourceWidth - x);
  height = Math.min(height, sourceHeight - y);
  if (width % 2) width -= 1;
  if (height % 2) height -= 1;
  width = Math.max(2, width);
  height = Math.max(2, height);
  if (x + width > sourceWidth) x = Math.max(0, sourceWidth - width);
  if (y + height > sourceHeight) y = Math.max(0, sourceHeight - height);
  return {custom_x:x, custom_y:y, custom_width:width, custom_height:height};
}

function writeCropValues(values) {
  const crop = normalizeCropValues(values);
  $("editorCropX").value = String(crop.custom_x);
  $("editorCropY").value = String(crop.custom_y);
  $("editorCropWidth").value = String(crop.custom_width);
  $("editorCropHeight").value = String(crop.custom_height);
  return crop;
}

function updateCropOverlay() {
  const overlay = $("editorCropOverlay");
  const imageVisible = !$("editorPreviewImage").classList.contains("hidden");
  if ($("editorCrop").value !== "Custom" || !state.editorInfo?.has_video || !imageVisible) {
    overlay.classList.add("hidden");
    return;
  }
  const geometry = cropGeometry();
  if (!geometry) {
    overlay.classList.add("hidden");
    return;
  }
  const crop = writeCropValues(editorCustomCropPayload());
  overlay.style.left = `${geometry.left + crop.custom_x * geometry.scale}px`;
  overlay.style.top = `${geometry.top + crop.custom_y * geometry.scale}px`;
  overlay.style.width = `${crop.custom_width * geometry.scale}px`;
  overlay.style.height = `${crop.custom_height * geometry.scale}px`;
  overlay.classList.remove("hidden");
}

function beginCropDrag(event) {
  if ($("editorCrop").value !== "Custom" || !state.editorInfo?.has_video) return;
  const geometry = cropGeometry();
  if (!geometry) return;
  const handle = event.target.closest("[data-crop-handle]")?.dataset.cropHandle || "move";
  state.cropDrag = {
    handle,
    startX:event.clientX,
    startY:event.clientY,
    crop:normalizeCropValues(editorCustomCropPayload()),
    scale:geometry.scale,
  };
  event.preventDefault();
}

function moveCropDrag(event) {
  const drag = state.cropDrag;
  if (!drag) return;
  const dx = (event.clientX - drag.startX) / drag.scale;
  const dy = (event.clientY - drag.startY) / drag.scale;
  const sourceWidth = Number(state.editorInfo?.width || 2);
  const sourceHeight = Number(state.editorInfo?.height || 2);
  let {custom_x:x, custom_y:y, custom_width:width, custom_height:height} = drag.crop;

  if (drag.handle === "move") {
    x = Math.max(0, Math.min(sourceWidth - width, x + dx));
    y = Math.max(0, Math.min(sourceHeight - height, y + dy));
  } else {
    if (drag.handle.includes("w")) { x += dx; width -= dx; }
    if (drag.handle.includes("e")) { width += dx; }
    if (drag.handle.includes("n")) { y += dy; height -= dy; }
    if (drag.handle.includes("s")) { height += dy; }

    if (width < 16) {
      if (drag.handle.includes("w")) x -= (16 - width);
      width = 16;
    }
    if (height < 16) {
      if (drag.handle.includes("n")) y -= (16 - height);
      height = 16;
    }
    x = Math.max(0, Math.min(x, sourceWidth - 2));
    y = Math.max(0, Math.min(y, sourceHeight - 2));
    width = Math.min(width, sourceWidth - x);
    height = Math.min(height, sourceHeight - y);
  }

  writeCropValues({
    custom_x:x,
    custom_y:y,
    custom_width:width,
    custom_height:height,
  });
  updateCropOverlay();
}

function endCropDrag() {
  if (!state.cropDrag) return;
  state.cropDrag = null;
  $("editorStatus").textContent = "Custom crop updated. Play proxy to verify before export.";
}

function currentEditorSettings() {
  return {
    crop_preset:$("editorCrop").value,
    ...editorCustomCropPayload(),
    rotate:$("editorRotate").value,
    speed:Number($("editorSpeed").value || 1),
    mute:$("editorMute").checked,
    volume_percent:Number($("editorVolume").value || 100),
    fade_in:Number($("editorFadeIn").value || 0),
    fade_out:Number($("editorFadeOut").value || 0),
    audio_preset:$("editorAudioPreset").value || "Flat",
    noise_reduction:$("editorNoiseReduction").checked,
    quality:$("editorQuality").value || "Balanced",
  };
}

function currentEditorProject() {
  return {
    source_path:$("editorSourcePath").value.trim(),
    output_dir:$("editorOutputDir").value.trim(),
    output_name:$("editorOutputName").value.trim() || "edited_media",
    start:Number($("editorStart").value || 0),
    end:Number($("editorEnd").value || 0),
    preview_position:Number($("editorPreviewPosition").value || 0),
    ...currentEditorSettings(),
  };
}

function applyEditorSettings(data = {}) {
  if ("crop_preset" in data) $("editorCrop").value = data.crop_preset || "Original";
  if ("custom_x" in data) $("editorCropX").value = String(data.custom_x ?? 0);
  if ("custom_y" in data) $("editorCropY").value = String(data.custom_y ?? 0);
  if ("custom_width" in data) $("editorCropWidth").value = String(data.custom_width ?? state.editorInfo?.width ?? 2);
  if ("custom_height" in data) $("editorCropHeight").value = String(data.custom_height ?? state.editorInfo?.height ?? 2);
  if ("rotate" in data) $("editorRotate").value = data.rotate || "0°";
  if ("speed" in data) $("editorSpeed").value = String(data.speed ?? 1);
  if ("mute" in data) $("editorMute").checked = Boolean(data.mute);
  if ("volume_percent" in data) $("editorVolume").value = String(data.volume_percent ?? 100);
  if ("fade_in" in data) $("editorFadeIn").value = String(data.fade_in ?? 0);
  if ("fade_out" in data) $("editorFadeOut").value = String(data.fade_out ?? 0);
  if ("audio_preset" in data) $("editorAudioPreset").value = data.audio_preset || "Flat";
  if ("noise_reduction" in data) $("editorNoiseReduction").checked = Boolean(data.noise_reduction);
  if ("quality" in data) $("editorQuality").value = data.quality || "Balanced";
  if ("start" in data) $("editorStart").value = String(data.start ?? 0);
  if ("end" in data) $("editorEnd").value = String(data.end ?? state.editorInfo?.duration ?? 0);
  if ("preview_position" in data) $("editorPreviewPosition").value = String(data.preview_position ?? 0);
  if ("output_dir" in data) $("editorOutputDir").value = data.output_dir || "";
  if ("output_name" in data) $("editorOutputName").value = data.output_name || "edited_media";

  $("editorCustomCropPanel").classList.toggle("hidden", $("editorCrop").value !== "Custom");
  if (state.editorInfo?.has_video && $("editorCrop").value === "Custom") {
    writeCropValues(editorCustomCropPayload());
  }
  $("editorVolume").disabled = $("editorMute").checked || !state.editorInfo?.has_audio;
  $("editorAudioPreset").disabled = !state.editorInfo?.has_audio;
  $("editorNoiseReduction").disabled = !state.editorInfo?.has_audio;
  if (state.editorInfo) syncTimelineFromNumbers();
  updateCropOverlay();
}

function renderEditorLibrary(data = {}) {
  state.editorLibrary = {
    presets:Array.isArray(data.presets) ? data.presets : [],
    projects:Array.isArray(data.projects) ? data.projects : [],
  };
  const presetSelect = $("editorPresetSelect");
  const projectSelect = $("editorProjectSelect");
  const previousPreset = presetSelect.value;
  const previousProject = projectSelect.value;
  presetSelect.innerHTML = '<option value="">Choose preset…</option>';
  projectSelect.innerHTML = '<option value="">Choose project…</option>';

  for (const item of state.editorLibrary.presets) {
    const option = document.createElement("option");
    option.value = item.id;
    option.textContent = item.name;
    presetSelect.appendChild(option);
  }
  for (const item of state.editorLibrary.projects) {
    const option = document.createElement("option");
    option.value = item.id;
    option.textContent = item.name;
    projectSelect.appendChild(option);
  }
  if ([...presetSelect.options].some((option) => option.value === previousPreset)) presetSelect.value = previousPreset;
  if ([...projectSelect.options].some((option) => option.value === previousProject)) projectSelect.value = previousProject;
}

async function refreshEditorLibrary() {
  if (!state.key) return;
  try {
    renderEditorLibrary(await api("/api/editor/library"));
  } catch (error) {
    $("editorStatus").textContent = error.message;
  }
}

async function saveEditorPreset() {
  const selected = state.editorLibrary.presets.find((item) => item.id === $("editorPresetSelect").value);
  const name = window.prompt("Preset name", selected?.name || "My preset");
  if (!name) return;
  const data = await api("/api/editor/presets", {
    method:"POST",
    body:JSON.stringify({name, item_id:selected?.id || null, data:currentEditorSettings()}),
  });
  renderEditorLibrary(data);
  $("editorPresetSelect").value = data.preset.id;
  $("editorStatus").textContent = `Preset saved: ${data.preset.name}`;
}

function applySelectedEditorPreset() {
  const preset = state.editorLibrary.presets.find((item) => item.id === $("editorPresetSelect").value);
  if (!preset) return;
  applyEditorSettings(preset.settings || {});
  $("editorStatus").textContent = `Preset applied: ${preset.name}`;
}

async function deleteEditorPreset() {
  const id = $("editorPresetSelect").value;
  if (!id) return;
  const preset = state.editorLibrary.presets.find((item) => item.id === id);
  if (!window.confirm(`Delete preset "${preset?.name || "this preset"}"?`)) return;
  renderEditorLibrary(await api(`/api/editor/presets/${id}`, {method:"DELETE"}));
  $("editorStatus").textContent = "Preset deleted.";
}

async function saveEditorProject() {
  if (!state.editorInfo) {
    $("editorStatus").textContent = "Load media before saving a project.";
    return;
  }
  const selected = state.editorLibrary.projects.find((item) => item.id === $("editorProjectSelect").value);
  const name = window.prompt("Project name", selected?.name || $("editorOutputName").value.trim() || "Editor project");
  if (!name) return;
  const data = await api("/api/editor/projects", {
    method:"POST",
    body:JSON.stringify({name, item_id:selected?.id || null, data:currentEditorProject()}),
  });
  renderEditorLibrary(data);
  $("editorProjectSelect").value = data.project.id;
  $("editorStatus").textContent = `Project saved: ${data.project.name}`;
}

async function loadSelectedEditorProject() {
  const project = state.editorLibrary.projects.find((item) => item.id === $("editorProjectSelect").value);
  if (!project) return;
  const data = project.data || {};
  if (data.source_path) {
    const loaded = await loadEditorPath(data.source_path);
    if (!loaded) return;
  }
  applyEditorSettings(data);
  $("editorStatus").textContent = `Project loaded: ${project.name}`;
}

async function deleteEditorProject() {
  const id = $("editorProjectSelect").value;
  if (!id) return;
  const project = state.editorLibrary.projects.find((item) => item.id === id);
  if (!window.confirm(`Delete project "${project?.name || "this project"}"?`)) return;
  renderEditorLibrary(await api(`/api/editor/projects/${id}`, {method:"DELETE"}));
  $("editorStatus").textContent = "Project deleted.";
}

function updateTimelineSummary() {
  const start = Number($("editorStart").value || 0);
  const end = Number($("editorEnd").value || 0);
  const duration = Math.max(0, end - start);
  $("editorTimelineSummary").textContent = `${formatDuration(start)} → ${formatDuration(end)} • ${formatDuration(duration)} selected`;
}

function syncTimelineFromRanges(changed) {
  let start = Number($("editorStartRange").value || 0);
  let end = Number($("editorEndRange").value || 0);
  const step = 0.05;
  if (start >= end) {
    if (changed === "start") start = Math.max(0, end - step);
    else end = Math.min(Number($("editorEndRange").max || end + step), start + step);
  }
  $("editorStartRange").value = String(start);
  $("editorEndRange").value = String(end);
  $("editorStart").value = start.toFixed(2);
  $("editorEnd").value = end.toFixed(2);
  updateTimelineSummary();
}

function syncTimelineFromNumbers() {
  const duration = Number(state.editorInfo?.duration || 0);
  let start = Math.max(0, Math.min(Number($("editorStart").value || 0), duration));
  let end = Math.max(0, Math.min(Number($("editorEnd").value || duration), duration));
  if (end <= start) end = Math.min(duration, start + 0.05);
  $("editorStart").value = String(start);
  $("editorEnd").value = String(end);
  $("editorStartRange").value = String(start);
  $("editorEndRange").value = String(end);
  updateTimelineSummary();
}

function renderEditorSource(data) {
  const info = data.info || {};
  state.editorInfo = info;
  $("editorSourcePath").value = data.path || data.selected || "";
  const duration = Number(info.duration || 0);
  $("editorStart").value = "0";
  $("editorEnd").value = String(duration.toFixed(3));
  $("editorEnd").max = String(duration);
  $("editorStartRange").max = String(duration);
  $("editorEndRange").max = String(duration);
  $("editorStartRange").value = "0";
  $("editorEndRange").value = String(duration);
  $("editorPreviewPosition").value = String(Math.min(duration / 2, 5).toFixed(2));
  $("editorPreviewPosition").max = String(duration);
  $("editorOutputName").value = data.output_name || "edited_media";
  $("editorOutputDir").value = data.output_dir || state.settings.download_dir || "";
  $("editorPreviewBtn").disabled = !info.has_video;
  $("editorProxyBtn").disabled = !info.has_video;
  $("editorWaveformBtn").disabled = !info.has_audio;
  $("editorExportBtn").disabled = false;
  $("editorCrop").disabled = !info.has_video;
  $("editorRotate").disabled = !info.has_video;
  $("editorVolume").disabled = !info.has_audio;
  $("editorMute").disabled = !info.has_audio;
  $("editorAudioPreset").disabled = !info.has_audio;
  $("editorNoiseReduction").disabled = !info.has_audio;
  $("editorFadeIn").disabled = !info.has_audio;
  $("editorFadeOut").disabled = !info.has_audio;

  $("editorCropX").value = "0";
  $("editorCropY").value = "0";
  $("editorCropWidth").value = String(info.width || 2);
  $("editorCropHeight").value = String(info.height || 2);
  $("editorCropX").max = String(Math.max(0, Number(info.width || 0) - 2));
  $("editorCropY").max = String(Math.max(0, Number(info.height || 0) - 2));
  $("editorCropWidth").max = String(info.width || 2);
  $("editorCropHeight").max = String(info.height || 2);
  $("editorCustomCropPanel").classList.toggle("hidden", $("editorCrop").value !== "Custom");
  updateTimelineSummary();

  const dimensions = info.has_video ? `${info.width || "?"}×${info.height || "?"}` : "Audio only";
  $("editorSourceMeta").textContent = [
    data.filename || "Media",
    formatDuration(info.duration),
    dimensions,
    info.has_audio ? "Audio" : "",
  ].filter(Boolean).join(" • ");
  $("editorStatus").textContent = "Source loaded. Adjust controls, preview, then export.";

  clearEditorPreviewObjectUrl();
  $("editorPreviewVideo").pause();
  $("editorPreviewVideo").removeAttribute("src");
  $("editorPreviewVideo").classList.add("hidden");
  $("editorPreviewImage").classList.add("hidden");
  $("editorCropOverlay").classList.add("hidden");
  $("editorPreviewPlaceholder").classList.remove("hidden");
  $("editorPreviewPlaceholder").textContent = info.has_video
    ? "Click Preview frame to render the current crop/rotation."
    : "Click Waveform to preview this audio file.";
}

async function loadEditorPath(path) {
  const source = String(path || $("editorSourcePath").value || "").trim();
  if (!source) return;
  $("editorStatus").textContent = "Reading local media…";
  try {
    const data = await api("/api/editor/probe", {
      method:"POST",
      body:JSON.stringify({path:source}),
    });
    renderEditorSource(data);
    $("editorCard").scrollIntoView({behavior:"smooth", block:"start"});
    return data;
  } catch (error) {
    state.editorInfo = null;
    $("editorExportBtn").disabled = true;
    $("editorStatus").textContent = error.message;
    return null;
  }
}

async function chooseEditorFile() {
  $("editorStatus").textContent = "Opening media picker…";
  try {
    const data = await api("/api/editor/choose-file", {method:"POST"});
    if (data.selected) {
      renderEditorSource(data);
    } else {
      $("editorStatus").textContent = "No file selected.";
    }
  } catch (error) {
    $("editorStatus").textContent = error.message;
  }
}

async function refreshEditorPreview() {
  if (!state.editorInfo?.has_video) return;
  $("editorStatus").textContent = "Rendering preview frame…";
  $("editorPreviewBtn").disabled = true;
  try {
    const customMode = $("editorCrop").value === "Custom";
    const blob = await apiBlob("/api/editor/preview", {
      method:"POST",
      body:JSON.stringify({
        path:$("editorSourcePath").value.trim(),
        position:Number($("editorPreviewPosition").value || 0),
        crop_preset:customMode ? "Original" : $("editorCrop").value,
        rotate:customMode ? "0°" : $("editorRotate").value,
        ...(customMode ? {} : editorCustomCropPayload()),
      }),
    });
    showEditorImage(blob, customMode);
    $("editorStatus").textContent = customMode
      ? "Original frame loaded. Drag/resize the cyan crop box, then play proxy to verify."
      : "Preview frame updated.";
  } catch (error) {
    $("editorStatus").textContent = error.message;
  } finally {
    $("editorPreviewBtn").disabled = !state.editorInfo?.has_video;
  }
}

async function refreshEditorProxy() {
  if (!state.editorInfo?.has_video) return;
  $("editorStatus").textContent = "Rendering 6-second playable proxy…";
  $("editorProxyBtn").disabled = true;
  try {
    const blob = await apiBlob("/api/editor/proxy", {
      method:"POST",
      body:JSON.stringify({
        path:$("editorSourcePath").value.trim(),
        position:Number($("editorPreviewPosition").value || $("editorStart").value || 0),
        duration:6,
        crop_preset:$("editorCrop").value,
        rotate:$("editorRotate").value,
        ...editorCustomCropPayload(),
        speed:Number($("editorSpeed").value || 1),
        mute:$("editorMute").checked,
        volume_percent:Number($("editorVolume").value || 100),
        audio_preset:$("editorAudioPreset").value || "Flat",
        noise_reduction:$("editorNoiseReduction").checked,
      }),
    });
    showEditorVideo(blob);
    $("editorStatus").textContent = "Playable proxy ready. This preview is temporary and local.";
  } catch (error) {
    $("editorStatus").textContent = error.message;
  } finally {
    $("editorProxyBtn").disabled = !state.editorInfo?.has_video;
  }
}

async function refreshEditorWaveform() {
  if (!state.editorInfo?.has_audio) return;
  $("editorStatus").textContent = "Rendering waveform…";
  $("editorWaveformBtn").disabled = true;
  try {
    const blob = await apiBlob("/api/editor/waveform", {
      method:"POST",
      body:JSON.stringify({path:$("editorSourcePath").value.trim()}),
    });
    showEditorImage(blob);
    $("editorStatus").textContent = "Waveform updated.";
  } catch (error) {
    $("editorStatus").textContent = error.message;
  } finally {
    $("editorWaveformBtn").disabled = !state.editorInfo?.has_audio;
  }
}

async function chooseEditorOutputFolder() {
  try {
    const data = await api("/api/system/choose-folder", {
      method:"POST",
      body:JSON.stringify({current_dir:$("editorOutputDir").value.trim() || null}),
    });
    if (data.selected) $("editorOutputDir").value = data.selected;
  } catch (error) {
    $("editorStatus").textContent = error.message;
  }
}

function setEditorExportBusy(busy) {
  $("editorExportBtn").disabled = busy || !state.editorInfo;
  $("editorCancelExportBtn").disabled = !busy;
}

async function pollEditorExport(id) {
  while (state.editorExportId === id) {
    const {job} = await api(`/api/jobs/${id}`);
    const percent = Math.round(Number(job.progress || 0) * 100);
    $("editorExportProgressBar").style.width = `${percent}%`;
    $("editorStatus").textContent = job.error || job.detail || job.status;

    if (job.status === "completed") {
      setEditorExportBusy(false);
      $("editorExportProgressBar").style.width = "100%";
      $("editorStatus").textContent = `Export complete: ${job.result?.path || job.result?.filename || ""}`;
      state.editorExportId = "";
      await refreshEditorExports();
      return;
    }
    if (["failed","cancelled"].includes(job.status)) {
      setEditorExportBusy(false);
      state.editorExportId = "";
      await refreshEditorExports();
      return;
    }
    await sleep(450);
  }
}

async function startEditorExport() {
  if (!state.editorInfo) return;
  const start = Number($("editorStart").value || 0);
  const end = Number($("editorEnd").value || 0);
  if (!(end > start)) {
    $("editorStatus").textContent = "Trim end must be greater than trim start.";
    return;
  }

  setEditorExportBusy(true);
  $("editorExportProgressBar").style.width = "0%";
  $("editorStatus").textContent = "Starting local FFmpeg export…";
  try {
    const {job} = await api("/api/editor/exports", {
      method:"POST",
      body:JSON.stringify({
        path:$("editorSourcePath").value.trim(),
        output_dir:$("editorOutputDir").value.trim(),
        output_name:$("editorOutputName").value.trim() || "edited_media",
        start,
        end,
        crop_preset:$("editorCrop").value,
        rotate:$("editorRotate").value,
        speed:Number($("editorSpeed").value || 1),
        mute:$("editorMute").checked,
        volume_percent:Number($("editorVolume").value || 100),
        fade_in:Number($("editorFadeIn").value || 0),
        fade_out:Number($("editorFadeOut").value || 0),
        audio_preset:$("editorAudioPreset").value || "Flat",
        noise_reduction:$("editorNoiseReduction").checked,
        ...editorCustomCropPayload(),
        quality:$("editorQuality").value,
      }),
    });
    state.editorExportId = job.id;
    await pollEditorExport(job.id);
  } catch (error) {
    setEditorExportBusy(false);
    $("editorStatus").textContent = error.message;
  }
}

async function cancelEditorExport() {
  if (!state.editorExportId) return;
  try {
    await api(`/api/jobs/${state.editorExportId}`, {method:"DELETE"});
    $("editorStatus").textContent = "Cancelling export…";
  } catch (error) {
    $("editorStatus").textContent = error.message;
  }
}

function editorExportRow(job) {
  const row = document.createElement("article");
  row.className = "download-row";
  const percent = Math.round(Number(job.progress || 0) * 100);
  const actions = [];
  if (["queued","running","cancelling"].includes(job.status)) {
    actions.push(`<button data-cancel-export="${job.id}" class="mini danger">${job.status === "cancelling" ? "Cancelling" : "Cancel"}</button>`);
  } else if (job.status === "failed") {
    actions.push(`<button data-retry-export="${job.id}" class="mini retry">Retry</button>`);
  } else if (job.status === "completed" && job.result?.path) {
    actions.push(`<button data-edit-export="${job.id}" data-export-path="${escapeHtml(job.result.path)}" class="mini retry">Edit output</button>`);
  }
  row.innerHTML = `
    <div class="row-top">
      <strong>${escapeHtml(job.request?.output_name || job.result?.filename || "Editor export")}</strong>
      <span class="job-status ${job.status}">${String(job.status || "").toUpperCase()}</span>
    </div>
    <div class="progress"><span style="width:${job.status === "completed" ? 100 : percent}%"></span></div>
    <div class="row-bottom"><span>${escapeHtml(job.error || job.detail || job.result?.path || "")}</span><div class="row-actions">${actions.join("")}</div></div>
  `;
  return row;
}

async function refreshEditorExports() {
  if (!state.key) return;
  try {
    const {exports} = await api("/api/editor/exports");
    const list = $("editorExportsList");
    list.innerHTML = "";
    if (!exports.length) {
      list.innerHTML = '<div class="empty">No editor exports yet.</div>';
      return;
    }
    exports.forEach((job) => list.appendChild(editorExportRow(job)));
  } catch {}
}

function renderSettings(settings = {}) {
  state.settings = {...settings};
  $("downloadDirInput").value = settings.download_dir || "";
  $("defaultModeSelect").value = settings.default_mode || "Video";
  $("defaultVideoQualitySelect").value = settings.video_quality || "720p";
  $("defaultAudioFormatSelect").value = settings.audio_format || "MP3";
  $("defaultAudioQualitySelect").value = settings.audio_quality || "192";
  $("maxDownloadsSelect").value = String(settings.max_concurrent_downloads || 3);
  $("updateChannelSelect").value = settings.update_channel || "stable";
  $("updatePolicySelect").value = settings.update_policy || "notify";
  $("updateInstallHourSelect").value = String(settings.update_install_hour ?? 3);
  $("autoUpdateCheck").checked = Boolean(settings.auto_check_core_updates);
  $("openBrowserCheck").checked = settings.open_browser_on_start !== false;
  $("trayIconCheck").checked = settings.tray_icon_enabled !== false;
  $("launchAtLoginCheck").checked = Boolean(settings.launch_at_login);

  $("audioFormatSelect").value = settings.audio_format || "MP3";
  $("audioQualitySelect").value = settings.audio_quality || "192";
  setMode(settings.default_mode || "Video");
}

async function saveCoreSettings(extra = {}) {
  const payload = {
    download_dir: $("downloadDirInput").value.trim(),
    default_mode: $("defaultModeSelect").value,
    video_quality: $("defaultVideoQualitySelect").value,
    audio_format: $("defaultAudioFormatSelect").value,
    audio_quality: $("defaultAudioQualitySelect").value,
    max_concurrent_downloads: Number($("maxDownloadsSelect").value || 3),
    auto_check_core_updates: $("autoUpdateCheck").checked,
    update_channel: $("updateChannelSelect").value,
    update_policy: $("updatePolicySelect").value,
    update_install_hour: Number($("updateInstallHourSelect").value || 3),
    open_browser_on_start: $("openBrowserCheck").checked,
    tray_icon_enabled: $("trayIconCheck").checked,
    launch_at_login: $("launchAtLoginCheck").checked,
    ...extra,
  };
  const data = await api("/api/settings", {method:"PUT", body:JSON.stringify(payload)});
  renderSettings(data.settings);
  $("settingsStatus").textContent = data.restart_required
    ? "Saved. Restart Local Core to apply concurrency/startup changes."
    : "Settings saved.";
  return data;
}

function renderAgent(agent = {}) {
  state.agent = {...agent};
  const connected = Boolean(agent.connected);
  const selfUpdate = Boolean(agent.self_update);
  const rollback = Boolean(agent.rollback_available);
  $("agentStatus").textContent = connected
    ? `Agent connected • self-update ${selfUpdate ? "ready" : "unavailable"} • rollback ${rollback ? "available" : "none"}`
    : "Agent supervisor is not connected. Developer launcher mode may be active.";
  $("restartAgentBtn").disabled = !connected;
  $("quitAgentBtn").disabled = !connected;
  $("openAgentAppBtn").disabled = !connected;
  $("rollbackCoreBtn").disabled = !connected || !rollback;
  $("launchAtLoginCheck").disabled = !Boolean(agent.startup_management);
  if ("launch_at_login" in agent) $("launchAtLoginCheck").checked = Boolean(agent.launch_at_login);
}

async function refreshAgentStatus() {
  if (!state.key) return;
  try {
    renderAgent(await api("/api/agent/status"));
  } catch (error) {
    renderAgent({connected:false});
  }
}

async function agentCommand(path, message) {
  try {
    await api(path, {method:"POST"});
    $("agentStatus").textContent = message;
  } catch (error) {
    $("agentStatus").textContent = error.message;
  }
}

function renderUpdate(update = {}) {
  const status = update.status || "idle";
  const progress = Math.round(Number(update.progress || 0) * 100);
  let text = update.detail || "Update check has not run yet.";
  if (status === "downloading" && progress) text += ` (${progress}%)`;
  $("coreUpdateStatus").textContent = text;
  $("checkCoreUpdateBtn").disabled = ["checking","downloading"].includes(status);
  $("downloadCoreUpdateBtn").disabled = !(status === "available" && update.asset_name);
  $("applyCoreUpdateBtn").disabled = !(status === "ready" && update.can_apply);
}

async function pollCoreUpdate() {
  clearTimeout(state.updatePoll);
  try {
    const {update} = await api("/api/update/status");
    renderUpdate(update);
    if (["checking","downloading"].includes(update.status)) {
      state.updatePoll = setTimeout(pollCoreUpdate, 700);
    }
  } catch {}
}

async function checkCoreUpdate() {
  try {
    const {update} = await api("/api/update/check", {method:"POST"});
    renderUpdate(update);
    state.updatePoll = setTimeout(pollCoreUpdate, 500);
  } catch (error) {
    $("coreUpdateStatus").textContent = error.message;
  }
}

async function downloadCoreUpdate() {
  try {
    const {update} = await api("/api/update/download", {method:"POST"});
    renderUpdate(update);
    state.updatePoll = setTimeout(pollCoreUpdate, 500);
  } catch (error) {
    $("coreUpdateStatus").textContent = error.message;
  }
}

async function applyCoreUpdate() {
  if (!window.confirm("Apply the verified Local Core update and restart now?")) return;
  try {
    const data = await api("/api/update/apply", {method:"POST"});
    $("coreUpdateStatus").textContent = data.detail || "Applying update…";
    $("applyCoreUpdateBtn").disabled = true;
  } catch (error) {
    $("coreUpdateStatus").textContent = error.message;
  }
}

async function bootstrap() {
  try {
    const data = await api("/api/bootstrap");
    state.key = data.core_key;
    renderSettings(data.settings || {download_dir:data.download_dir});
    renderUpdate(data.update || {});
    renderAgent(data.agent || {});
    $("coreBadge").textContent = `LOCAL CORE • v${data.core_version || "?"}`;
    $("coreBadge").classList.remove("offline");
    $("coreBadge").classList.add("ready");
    $("analysisStatus").textContent = "Ready for a media link.";
    setInterval(refreshDownloads, 900);
    refreshDownloads();
    refreshEditorExports();
    refreshEditorLibrary();
    refreshAgentStatus();
    loadDiagnostics();
    if (["checking","downloading"].includes((data.update || {}).status)) {
      state.updatePoll = setTimeout(pollCoreUpdate, 500);
    }
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
$("downloadDirInput").addEventListener("change", async () => {
  try { await saveCoreSettings({download_dir:$("downloadDirInput").value.trim()}); }
  catch (error) { $("settingsStatus").textContent = error.message; }
});
$("chooseFolderBtn").addEventListener("click", async () => {
  try {
    const data = await api("/api/system/choose-folder", {
      method:"POST",
      body:JSON.stringify({current_dir:$("downloadDirInput").value.trim() || null}),
    });
    if (data.selected) {
      $("downloadDirInput").value = data.selected;
      await saveCoreSettings({download_dir:data.selected});
    }
  } catch (error) {
    $("analysisStatus").textContent = error.message;
  }
});

async function exportDiagnostics() {
  try {
    const blob = await apiBlob("/api/diagnostics/bundle");
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    const stamp = new Date().toISOString().replace(/[:.]/g, "-");
    link.href = url;
    link.download = `MediaDownloaderDiagnostics-${stamp}.zip`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1500);
    $("settingsStatus").textContent = "Diagnostics bundle exported.";
  } catch (error) {
    $("settingsStatus").textContent = error.message;
  }
}

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
      `Settings: ${data.settings_file}`,
      `Editor library: ${data.editor_library_file}`,
      `Log: ${data.log_file}`,
      `Updates: ${data.update_dir}`,
      `Disk free: ${data.disk_free_bytes ? (Number(data.disk_free_bytes) / 1024 / 1024 / 1024).toFixed(1) + " GB" : "unknown"}`,
      `Agent: ${data.agent?.connected ? "connected" : "not connected"}`,
      `Self-update: ${data.agent?.self_update ? "ready" : "unavailable"}`,
      `Rollback: ${data.agent?.rollback_available ? "available" : "none"}`,
      `Update policy: ${data.settings?.update_policy || "notify"} @ ${String(data.settings?.update_install_hour ?? 3).padStart(2, "0")}:00`,
      `Launch at sign-in: ${data.agent?.launch_at_login ? "enabled" : "disabled"}`,
      `Downloads: ${JSON.stringify(data.download_counts || {})}`,
    ].join("\n");
  } catch (error) {
    $("diagnosticsBox").textContent = error.message;
  }
}
$("chooseEditorFileBtn").addEventListener("click", chooseEditorFile);
$("loadEditorPathBtn").addEventListener("click", () => loadEditorPath());
$("editorPreviewBtn").addEventListener("click", refreshEditorPreview);
$("editorProxyBtn").addEventListener("click", refreshEditorProxy);
$("editorWaveformBtn").addEventListener("click", refreshEditorWaveform);
$("chooseEditorOutputBtn").addEventListener("click", chooseEditorOutputFolder);
$("editorExportBtn").addEventListener("click", startEditorExport);
$("editorCancelExportBtn").addEventListener("click", cancelEditorExport);
$("editorCrop").addEventListener("change", () => {
  const custom = $("editorCrop").value === "Custom";
  $("editorCustomCropPanel").classList.toggle("hidden", !custom);
  if (custom && state.editorInfo?.has_video) {
    refreshEditorPreview();
  } else {
    $("editorCropOverlay").classList.add("hidden");
    if (state.editorInfo?.has_video) $("editorStatus").textContent = "Crop changed. Preview frame or proxy to refresh.";
  }
});
["editorCropX","editorCropY","editorCropWidth","editorCropHeight"].forEach((id) => {
  $(id).addEventListener("input", updateCropOverlay);
});
$("editorCropOverlay").addEventListener("pointerdown", beginCropDrag);
window.addEventListener("pointermove", moveCropDrag);
window.addEventListener("pointerup", endCropDrag);
window.addEventListener("resize", updateCropOverlay);
$("editorStartRange").addEventListener("input", () => syncTimelineFromRanges("start"));
$("editorEndRange").addEventListener("input", () => syncTimelineFromRanges("end"));
$("editorStart").addEventListener("change", syncTimelineFromNumbers);
$("editorEnd").addEventListener("change", syncTimelineFromNumbers);
$("editorRotate").addEventListener("change", () => {
  if (state.editorInfo?.has_video) $("editorStatus").textContent = "Rotation changed. Click Preview frame to refresh.";
});
$("editorMute").addEventListener("change", () => {
  $("editorVolume").disabled = $("editorMute").checked || !state.editorInfo?.has_audio;
});
$("editorAudioPreset").addEventListener("change", () => {
  if (state.editorInfo?.has_audio) $("editorStatus").textContent = "Audio preset changed. Play proxy to hear the result.";
});
$("editorNoiseReduction").addEventListener("change", () => {
  if (state.editorInfo?.has_audio) $("editorStatus").textContent = "Noise reduction changed. Play proxy to hear the result.";
});

$("applyEditorPresetBtn").addEventListener("click", applySelectedEditorPreset);
$("saveEditorPresetBtn").addEventListener("click", () => saveEditorPreset().catch((error) => $("editorStatus").textContent = error.message));
$("deleteEditorPresetBtn").addEventListener("click", () => deleteEditorPreset().catch((error) => $("editorStatus").textContent = error.message));
$("loadEditorProjectBtn").addEventListener("click", () => loadSelectedEditorProject().catch((error) => $("editorStatus").textContent = error.message));
$("saveEditorProjectBtn").addEventListener("click", () => saveEditorProject().catch((error) => $("editorStatus").textContent = error.message));
$("deleteEditorProjectBtn").addEventListener("click", () => deleteEditorProject().catch((error) => $("editorStatus").textContent = error.message));
$("refreshEditorExportsBtn").addEventListener("click", refreshEditorExports);
$("editorExportsList").addEventListener("click", async (event) => {
  const retry = event.target.closest("[data-retry-export]");
  const cancel = event.target.closest("[data-cancel-export]");
  const edit = event.target.closest("[data-edit-export]");
  try {
    if (retry) await api(`/api/editor/exports/${retry.dataset.retryExport}/retry`, {method:"POST"});
    if (cancel) await api(`/api/jobs/${cancel.dataset.cancelExport}`, {method:"DELETE"});
    if (edit?.dataset.exportPath) await loadEditorPath(edit.dataset.exportPath);
    await refreshEditorExports();
  } catch (error) {
    $("editorStatus").textContent = error.message;
  }
});
$("saveCoreSettingsBtn").addEventListener("click", async () => {
  try { await saveCoreSettings(); }
  catch (error) { $("settingsStatus").textContent = error.message; }
});
$("checkCoreUpdateBtn").addEventListener("click", checkCoreUpdate);
$("downloadCoreUpdateBtn").addEventListener("click", downloadCoreUpdate);
$("applyCoreUpdateBtn").addEventListener("click", applyCoreUpdate);
$("openAgentAppBtn").addEventListener("click", () => agentCommand("/api/agent/open", "Opening Media Downloader…"));
$("restartAgentBtn").addEventListener("click", () => {
  if (window.confirm("Restart the Local Core now? Active analysis/download/export work will be interrupted.")) {
    agentCommand("/api/agent/restart", "Restarting Local Core…");
  }
});
$("rollbackCoreBtn").addEventListener("click", () => {
  if (window.confirm("Roll back to the previous Local Core version? The current Core will stop and the previous version will be health-checked before it is kept.")) {
    agentCommand("/api/agent/rollback", "Rolling back to the previous Local Core…");
  }
});
$("quitAgentBtn").addEventListener("click", () => {
  if (window.confirm("Quit Media Downloader Core? This page will go offline until you start it again.")) {
    agentCommand("/api/agent/quit", "Quitting Local Core…");
  }
});
$("refreshDiagnosticsBtn").addEventListener("click", loadDiagnostics);
$("exportDiagnosticsBtn").addEventListener("click", exportDiagnostics);
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
  const edit = event.target.closest("[data-edit-job]");
  try {
    if (cancel) await api(`/api/downloads/${cancel.dataset.cancel}`, {method:"DELETE"});
    if (retry) await api(`/api/downloads/${retry.dataset.retry}/retry`, {method:"POST"});
    if (edit) {
      const job = state.downloads[edit.dataset.editJob];
      if (job?.result?.path) await loadEditorPath(job.result.path);
    }
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
