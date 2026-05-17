import { getMetricAt } from "./sync.js";
import { loadOverlayFonts, renderFrame } from "./overlay.js";

const MIME_CANDIDATES = ["video/webm;codecs=vp9", "video/webm"];
const FPS = 30;

function wait(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function selectMimeType() {
  for (const mimeType of MIME_CANDIDATES) {
    if (MediaRecorder.isTypeSupported(mimeType)) {
      return mimeType;
    }
  }
  throw new Error(
    "VP9 WebM не поддерживается в этом браузере. Используйте Chrome."
  );
}

function verifyCanvasAlpha(ctx, w, h, label) {
  ctx.clearRect(0, 0, w, h);
  const pixel = ctx.getImageData(0, 0, 1, 1).data;
  console.log(`Alpha check ${label} (should be 0):`, pixel[3]);
  if (pixel[3] !== 0) {
    console.warn(
      `Canvas не прозрачен (${label}): alpha=${pixel[3]}. ` +
        "Убедитесь что getContext('2d', { alpha: true })."
    );
  }
}

function metricsFromTimeline(timeline, t) {
  const raw = getMetricAt(timeline, t);
  if (!raw) return null;
  return {
    speed: raw.speed ?? null,
    power: raw.power ?? 0,
    hr: raw.hr ?? null,
    cadence: raw.cadence ?? null,
  };
}

/**
 * @param {{ videoEl: HTMLVideoElement, timeline: object, syncData?: object, onProgress?: (progress: number) => void }} options
 */
export async function exportOverlay({ videoEl, timeline, onProgress }) {
  const mimeType = selectMimeType();
  console.log("MediaRecorder mimeType:", mimeType);

  if (!videoEl.videoWidth || !videoEl.videoHeight) {
    await new Promise((resolve, reject) => {
      const onMeta = () => {
        videoEl.removeEventListener("loadedmetadata", onMeta);
        if (videoEl.videoWidth) resolve();
        else reject(new Error("Не удалось получить размеры видео"));
      };
      videoEl.addEventListener("loadedmetadata", onMeta);
    });
  }

  await loadOverlayFonts();

  const canvas = document.createElement("canvas");
  canvas.width = videoEl.videoWidth;
  canvas.height = videoEl.videoHeight;
  const ctx = canvas.getContext("2d", { alpha: true });
  if (!ctx) {
    throw new Error("Не удалось создать 2D-контекст canvas");
  }

  verifyCanvasAlpha(ctx, canvas.width, canvas.height, "before record");

  const stream = canvas.captureStream(FPS);
  const videoTrack = stream.getVideoTracks()[0];
  if (videoTrack) {
    console.log("captureStream video track:", videoTrack.label, videoTrack.getSettings());
  }

  const recorder = new MediaRecorder(stream, {
    mimeType,
    videoBitsPerSecond: 4_000_000,
  });
  const chunks = [];
  recorder.ondataavailable = (e) => {
    if (e.data.size > 0) chunks.push(e.data);
  };

  const duration = videoEl.duration;
  if (!Number.isFinite(duration) || duration <= 0) {
    throw new Error("Длительность видео неизвестна");
  }

  const totalFrames = Math.ceil(duration * FPS);
  const frameDelay = 1000 / FPS;

  const stopped = new Promise((resolve) => {
    recorder.onstop = resolve;
  });

  recorder.start();

  for (let frame = 0; frame < totalFrames; frame++) {
    const t = frame / FPS;
    const metrics = metricsFromTimeline(timeline, t);
    renderFrame(ctx, metrics, canvas.width, canvas.height);

    if (frame === 0) {
      const pixel = ctx.getImageData(0, 0, 1, 1).data;
      console.log("Alpha check (should be 0):", pixel[3]);
    }

    await wait(frameDelay);
    onProgress?.((frame + 1) / totalFrames);
  }

  onProgress?.(1);
  recorder.stop();
  await stopped;

  const blob = new Blob(chunks, { type: "video/webm" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "overlay.webm";
  a.click();
  URL.revokeObjectURL(url);
}
