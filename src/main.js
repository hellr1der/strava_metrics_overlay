import { parseGPX } from "./gpx.js";
import { buildTimeline, getMetricAt } from "./sync.js";
import { initOverlay, updateOverlay, hideOverlay, showOverlay } from "./overlay.js";
import { exportOverlay } from "./exporter.js";

const EPOCH_1904_MS = Date.UTC(1904, 0, 1);
const MIN_CREATION_MS = Date.UTC(1990, 0, 1);
const MAX_CREATION_MS = Date.UTC(2035, 0, 1);

const QUICKTIME_CREATION_KEY = "com.apple.quicktime.creationdate";
const QUICKTIME_KEY_BYTES = new TextEncoder().encode(QUICKTIME_CREATION_KEY);
const ISO_DATE_RE =
  /\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}(?::?\d{2})?)?/;

const TIME_SOURCE = {
  QUICKTIME: "quicktime",
  MVHD: "mvhd",
  MANUAL: "manual",
};

const TIME_SOURCE_LABELS = {
  quicktime: "QuickTime metadata",
  mvhd: "mvhd",
  manual: "manual",
};

let overlayInitialized = false;

function readArrayBuffer(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = () => reject(new Error("Ошибка чтения видеофайла"));
    reader.readAsArrayBuffer(file);
  });
}

function mvhdSecondsToDate(version, seconds) {
  if (version === 1) {
    return new Date(seconds * 1000);
  }
  return new Date(EPOCH_1904_MS + seconds * 1000);
}

function isPlausibleCreationDate(date) {
  const t = date.getTime();
  return t >= MIN_CREATION_MS && t <= MAX_CREATION_MS;
}

/** datetime-local → Date в локальной timezone браузера (не UTC). */
function parseDatetimeLocal(value) {
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2}))?$/.exec(value);
  if (!match) {
    throw new Error("Некорректный формат даты");
  }
  const [, y, mo, d, h, mi, s = "0"] = match;
  return new Date(+y, +mo - 1, +d, +h, +mi, +s);
}

/** QuickTime user data: com.apple.quicktime.creationdate */
function readCreationTimeFromQuickTime(buffer) {
  const bytes = new Uint8Array(buffer);
  const scanWindow = 512;

  for (let i = 0; i <= bytes.length - QUICKTIME_KEY_BYTES.length; i++) {
    let found = true;
    for (let j = 0; j < QUICKTIME_KEY_BYTES.length; j++) {
      if (bytes[i + j] !== QUICKTIME_KEY_BYTES[j]) {
        found = false;
        break;
      }
    }
    if (!found) continue;

    const afterKey = i + QUICKTIME_KEY_BYTES.length;
    const end = Math.min(afterKey + scanWindow, bytes.length);
    const chunk = new TextDecoder("utf-8").decode(bytes.subarray(afterKey, end));
    const match = chunk.match(ISO_DATE_RE);
    if (!match) continue;

    const date = new Date(match[0]);
    if (isPlausibleCreationDate(date)) {
      return date;
    }
  }

  return null;
}

/** Прямой поиск атома mvhd в бинарнике (MP4/MOV). */
function readCreationTimeFromMvhd(buffer) {
  const bytes = new Uint8Array(buffer);
  const view = new DataView(buffer);

  for (let i = 4; i < bytes.length - 28; i++) {
    if (bytes[i] !== 0x6d || bytes[i + 1] !== 0x76 || bytes[i + 2] !== 0x68 || bytes[i + 3] !== 0x64) {
      continue;
    }

    const version = bytes[i + 4];
    if (version !== 0 && version !== 1) continue;

    const timeOffset = i + 8;

    try {
      let seconds;
      if (version === 1) {
        const hi = view.getUint32(timeOffset, false);
        const lo = view.getUint32(timeOffset + 4, false);
        seconds = hi * 0x1_0000_0000 + lo;
      } else {
        seconds = view.getUint32(timeOffset, false);
        if (seconds === 0) continue;
      }

      const date = mvhdSecondsToDate(version, seconds);
      if (isPlausibleCreationDate(date)) {
        return date;
      }
    } catch {
      continue;
    }
  }

  return null;
}

/** @returns {Promise<{ date: Date, source: string }>} */
async function readVideoCreationTime(file) {
  const buffer = await readArrayBuffer(file);

  const quickTime = readCreationTimeFromQuickTime(buffer);
  if (quickTime) {
    return { date: quickTime, source: TIME_SOURCE.QUICKTIME };
  }

  const mvhd = readCreationTimeFromMvhd(buffer);
  if (mvhd) {
    return { date: mvhd, source: TIME_SOURCE.MVHD };
  }

  throw new Error("creation_time не найден");
}

const state = {
  gpxData: null,
  videoFile: null,
  videoStartTime: null,
  videoTimeSource: null,
  timeline: null,
  videoObjectUrl: null,
};

const els = {
  gpxZone: document.getElementById("gpx-zone"),
  videoZone: document.getElementById("video-zone"),
  gpxInput: document.getElementById("gpx-input"),
  videoInput: document.getElementById("video-input"),
  gpxFilename: document.getElementById("gpx-filename"),
  videoFilename: document.getElementById("video-filename"),
  errorAlert: document.getElementById("error-alert"),
  warningAlert: document.getElementById("warning-alert"),
  manualTime: document.getElementById("manual-time"),
  manualDatetime: document.getElementById("manual-datetime"),
  manualApply: document.getElementById("manual-apply"),
  video: document.getElementById("video"),
  playerSection: document.getElementById("player-section"),
  statusVideoStart: document.getElementById("status-video-start"),
  statusVideoStartUtc: document.getElementById("status-video-start-utc"),
  statusTimeSource: document.getElementById("status-time-source"),
  statusGpxStart: document.getElementById("status-gpx-start"),
  statusOffset: document.getElementById("status-offset"),
  exportSync: document.getElementById("export-sync"),
  exportSyncBtn: document.getElementById("export-sync-btn"),
  exportWebmBtn: document.getElementById("export-webm-btn"),
  exportWebmProgress: document.getElementById("export-webm-progress"),
  exportWebmProgressFill: document.getElementById("export-webm-progress-fill"),
  exportWebmStatus: document.getElementById("export-webm-status"),
};

function formatDateTime(date) {
  return date.toLocaleString(undefined, {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function formatDateTimeUtc(date) {
  return `${date.toISOString().slice(0, 19).replace("T", " ")} UTC`;
}

function formatOffset(sec) {
  const sign = sec >= 0 ? "+" : "−";
  const abs = Math.abs(sec);
  const m = Math.floor(abs / 60);
  const s = (abs % 60).toFixed(1);
  return `${sign}${m}m ${s}s (${sec.toFixed(1)} с)`;
}

function updateSyncStatus(timeline, timeSource) {
  els.statusVideoStart.textContent = formatDateTime(timeline.videoStartTime);
  els.statusVideoStartUtc.textContent = formatDateTimeUtc(timeline.videoStartTime);
  els.statusTimeSource.textContent = TIME_SOURCE_LABELS[timeSource] ?? "—";
  els.statusGpxStart.textContent = formatDateTime(timeline.gpxStartTime);
  els.statusOffset.textContent = formatOffset(timeline.offsetSec);
}

function showPlayer() {
  els.playerSection.classList.add("visible");
}

function setExportSyncVisible(visible) {
  els.exportSync.classList.toggle("visible", visible);
}

function downloadSyncJson() {
  if (!state.timeline || !state.videoStartTime) return;

  const payload = {
    video_start_time: state.videoStartTime.toISOString(),
    gpx_start_time: state.timeline.gpxStartTime.toISOString(),
    offset_sec: state.timeline.offsetSec,
    time_source: TIME_SOURCE_LABELS[state.videoTimeSource] ?? state.videoTimeSource,
  };

  const base = state.videoFile?.name?.replace(/\.[^.]+$/i, "") || "video";
  const blob = new Blob([JSON.stringify(payload, null, 2)], {
    type: "application/json",
  });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `${base}-sync.json`;
  a.click();
  URL.revokeObjectURL(url);
}

function ensureOverlay() {
  if (!overlayInitialized) {
    initOverlay(els.video);
    overlayInitialized = true;
  }
}

function showError(message) {
  els.errorAlert.textContent = message;
  els.errorAlert.classList.add("visible");
}

function hideError() {
  els.errorAlert.classList.remove("visible");
}

function showWarning() {
  els.warningAlert.classList.add("visible");
  els.manualTime.classList.add("visible");
}

function hideWarning() {
  els.warningAlert.classList.remove("visible");
  els.manualTime.classList.remove("visible");
}

function markZoneLoaded(zone, filenameEl, name) {
  zone.classList.add("loaded");
  filenameEl.textContent = name;
}

function setupDropZone(zone, input, onFile) {
  zone.addEventListener("dragover", (e) => {
    e.preventDefault();
    zone.classList.add("drag-over");
  });
  zone.addEventListener("dragleave", () => zone.classList.remove("drag-over"));
  zone.addEventListener("drop", (e) => {
    e.preventDefault();
    zone.classList.remove("drag-over");
    const file = e.dataTransfer.files[0];
    if (file) onFile(file);
  });
  input.addEventListener("change", () => {
    const file = input.files[0];
    if (file) onFile(file);
  });
}

async function handleGpxFile(file) {
  hideError();
  try {
    state.gpxData = await parseGPX(file);
    markZoneLoaded(els.gpxZone, els.gpxFilename, file.name);
    trySync();
  } catch (err) {
    state.gpxData = null;
    els.gpxZone.classList.remove("loaded");
    els.gpxFilename.textContent = "";
    showError(err.message);
  }
}

async function handleVideoFile(file) {
  hideError();
  hideWarning();
  state.videoFile = file;
  state.videoStartTime = null;
  state.videoTimeSource = null;
  markZoneLoaded(els.videoZone, els.videoFilename, file.name);

  if (state.videoObjectUrl) {
    URL.revokeObjectURL(state.videoObjectUrl);
  }
  state.videoObjectUrl = URL.createObjectURL(file);
  els.video.src = state.videoObjectUrl;

  const initOnce = () => {
    ensureOverlay();
    els.video.removeEventListener("loadedmetadata", initOnce);
  };
  if (els.video.readyState >= 1) {
    ensureOverlay();
  } else {
    els.video.addEventListener("loadedmetadata", initOnce);
  }

  try {
    const { date, source } = await readVideoCreationTime(file);
    state.videoStartTime = date;
    state.videoTimeSource = source;
    hideWarning();
    trySync();
  } catch {
    showWarning();
    if (state.gpxData) {
      showPlayer();
    }
  }
}

function applyManualTime() {
  const value = els.manualDatetime.value;
  if (!value) return;

  try {
    state.videoStartTime = parseDatetimeLocal(value);
    state.videoTimeSource = TIME_SOURCE.MANUAL;
  } catch (err) {
    showError(err.message);
    return;
  }

  hideWarning();
  trySync();
}

function trySync() {
  if (!state.gpxData || !state.videoStartTime) {
    setExportSyncVisible(false);
    return;
  }

  state.timeline = buildTimeline(state.gpxData.points, state.videoStartTime);
  showPlayer();
  updateSyncStatus(state.timeline, state.videoTimeSource);
  setExportSyncVisible(true);
  updateOverlayForCurrentTime();
}

function updateOverlayForCurrentTime() {
  if (!state.timeline) return;

  const metric = getMetricAt(state.timeline, els.video.currentTime);
  if (!metric) {
    hideOverlay();
    return;
  }

  showOverlay();
  updateOverlay({
    speed: metric.speed ?? null,
    power: metric.power ?? 0,
    hr: metric.hr ?? null,
    cadence: metric.cadence ?? null,
  });
}

setupDropZone(els.gpxZone, els.gpxInput, handleGpxFile);
setupDropZone(els.videoZone, els.videoInput, handleVideoFile);

els.exportSyncBtn.addEventListener("click", downloadSyncJson);

async function handleExportWebm() {
  if (!state.timeline) return;

  hideError();
  els.exportWebmBtn.disabled = true;
  els.exportSyncBtn.disabled = true;
  els.exportWebmProgress.classList.add("visible");
  els.exportWebmProgressFill.style.width = "0%";
  els.exportWebmStatus.textContent = "Рендеринг: 0%";

  try {
    await exportOverlay({
      videoEl: els.video,
      timeline: state.timeline,
      syncData: {
        video_start_time: state.videoStartTime?.toISOString(),
        offset_sec: state.timeline.offsetSec,
        time_source: state.videoTimeSource,
      },
      onProgress: (p) => {
        const pct = Math.round(p * 100);
        els.exportWebmProgressFill.style.width = `${pct}%`;
        els.exportWebmStatus.textContent = `Рендеринг: ${pct}%`;
      },
    });
    els.exportWebmStatus.textContent = "Готово — overlay.webm скачан";
  } catch (err) {
    showError(err.message);
    els.exportWebmProgress.classList.remove("visible");
  } finally {
    els.exportWebmBtn.disabled = false;
    els.exportSyncBtn.disabled = false;
  }
}

els.exportWebmBtn.addEventListener("click", handleExportWebm);

els.manualApply.addEventListener("click", applyManualTime);
els.manualDatetime.addEventListener("keydown", (e) => {
  if (e.key === "Enter") applyManualTime();
});

els.video.addEventListener("timeupdate", updateOverlayForCurrentTime);
els.video.addEventListener("seeked", updateOverlayForCurrentTime);
els.video.addEventListener("pause", updateOverlayForCurrentTime);
els.video.addEventListener("play", updateOverlayForCurrentTime);
