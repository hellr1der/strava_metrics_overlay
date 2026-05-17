// HUD overlay: единый canvas-рендер для превью и экспорта WebM.

const FONT_FAMILY = "Oxanium, sans-serif";

let canvasEl = null;
let ctx = null;
let videoEl = null;
let containerEl = null;
let currentData = { speed: null, power: null, hr: null, cadence: null };

function getVideoContainer(video) {
  let container = video.closest(".video-container");
  if (!container) {
    container = document.createElement("div");
    container.className = "video-container";
    const parent = video.parentNode;
    parent.insertBefore(container, video);
    container.appendChild(video);
  }
  Object.assign(container.style, {
    position: "relative",
    overflow: "hidden",
    width: "100%",
  });
  return container;
}

function drawWithShadow(ctx, px, fn) {
  ctx.save();
  ctx.shadowColor = "rgba(0,0,0,0.9)";
  ctx.shadowBlur = px(20);
  ctx.shadowOffsetX = px(1);
  ctx.shadowOffsetY = px(1);
  fn();
  ctx.restore();
}

/**
 * @param {CanvasRenderingContext2D} ctx
 * @param {{ speed?: number|null, power?: number|null, hr?: number|null, cadence?: number|null }|null} metrics
 * @param {number} w
 * @param {number} h
 */
export function renderFrame(ctx, metrics, w, h) {
  ctx.clearRect(0, 0, w, h);
  if (!metrics) return;

  const scale = w / 1080;
  const px = (n) => Math.round(n * scale);

  const speedY = h - Math.round(h * 0.13) - px(120);
  const speedX = px(43);
  const secBaseY = speedY + px(20) + px(52);

  if (metrics.speed !== null && metrics.speed !== undefined) {
    const speedStr = String(Math.round(metrics.speed));
    let speedW = 0;
    drawWithShadow(ctx, px, () => {
      ctx.font = `700 ${px(120)}px ${FONT_FAMILY}`;
      ctx.fillStyle = "#ffffff";
      speedW = ctx.measureText(speedStr).width;
      ctx.fillText(speedStr, speedX, speedY);
    });
    drawWithShadow(ctx, px, () => {
      ctx.font = `400 ${px(36)}px ${FONT_FAMILY}`;
      ctx.fillStyle = "rgba(255,255,255,0.5)";
      ctx.fillText("km/h", speedX + speedW + px(8), speedY);
    });
  }

  ctx.fillStyle = "rgba(255,255,255,0.25)";
  ctx.fillRect(speedX, speedY + px(10), px(3), px(70));

  const secFont = `600 ${px(52)}px ${FONT_FAMILY}`;
  const unitFont = `400 ${px(26)}px ${FONT_FAMILY}`;
  const gap = px(32);
  const xPower = speedX + px(14);
  const power = metrics.power ?? 0;

  ctx.font = secFont;
  ctx.textAlign = "left";

  const powerStr = String(Math.round(power));
  drawWithShadow(ctx, px, () => {
    ctx.font = secFont;
    ctx.fillStyle = "#ffffff";
    ctx.fillText(powerStr, xPower, secBaseY);
  });
  const powerW = ctx.measureText(powerStr).width;
  drawWithShadow(ctx, px, () => {
    ctx.font = unitFont;
    ctx.fillStyle = "rgba(255,255,255,0.5)";
    ctx.fillText("W", xPower + powerW + px(4), secBaseY);
  });
  ctx.font = unitFont;
  const powerUnitW = ctx.measureText("W").width;

  let nextX = xPower + powerW + px(4) + powerUnitW + gap;

  if (metrics.hr !== null && metrics.hr !== undefined) {
    const xHr = nextX;
    const hrStr = String(Math.round(metrics.hr));
    ctx.font = secFont;
    drawWithShadow(ctx, px, () => {
      ctx.font = secFont;
      ctx.fillStyle = "#ffffff";
      ctx.fillText(hrStr, xHr, secBaseY);
    });
    const hrW = ctx.measureText(hrStr).width;
    drawWithShadow(ctx, px, () => {
      ctx.font = unitFont;
      ctx.fillStyle = "rgba(255,255,255,0.5)";
      ctx.fillText("BPM", xHr + hrW + px(4), secBaseY);
    });
    ctx.font = unitFont;
    const hrUnitW = ctx.measureText("BPM").width;
    nextX = xHr + hrW + px(4) + hrUnitW + gap;
  }

  if (metrics.cadence !== null && metrics.cadence !== undefined) {
    const xRpm = nextX;
    const cadStr = String(Math.round(metrics.cadence));
    ctx.font = secFont;
    drawWithShadow(ctx, px, () => {
      ctx.font = secFont;
      ctx.fillStyle = "#ffffff";
      ctx.fillText(cadStr, xRpm, secBaseY);
    });
    const cadW = ctx.measureText(cadStr).width;
    drawWithShadow(ctx, px, () => {
      ctx.font = unitFont;
      ctx.fillStyle = "rgba(255,255,255,0.5)";
      ctx.fillText("RPM", xRpm + cadW + px(4), secBaseY);
    });
  }

  ctx.textAlign = "left";
}

export async function loadOverlayFonts() {
  await document.fonts.load(`700 100px ${FONT_FAMILY}`);
  await document.fonts.load(`400 100px ${FONT_FAMILY}`);
  await document.fonts.load(`600 100px ${FONT_FAMILY}`);
  await document.fonts.ready;
}

function resizeCanvas() {
  if (!canvasEl || !videoEl || !ctx) return;

  const w = videoEl.clientWidth;
  const h = videoEl.clientHeight;
  if (w === 0 || h === 0) return;

  canvasEl.width = w;
  canvasEl.height = h;
  renderFrame(ctx, currentData, w, h);
}

function paint() {
  if (!ctx || !canvasEl) return;
  renderFrame(ctx, currentData, canvasEl.width, canvasEl.height);
}

export function initOverlay(videoElement) {
  videoEl = videoElement;
  containerEl = getVideoContainer(videoEl);

  if (canvasEl?.parentElement) {
    canvasEl.remove();
  }

  canvasEl = document.createElement("canvas");
  canvasEl.id = "hud-overlay";
  Object.assign(canvasEl.style, {
    position: "absolute",
    top: "0",
    left: "0",
    pointerEvents: "none",
    zIndex: "10",
    display: "block",
  });

  ctx = canvasEl.getContext("2d", { alpha: true });
  containerEl.appendChild(canvasEl);

  const onResize = () => resizeCanvas();
  videoEl.addEventListener("loadedmetadata", onResize);
  videoEl.addEventListener("loadeddata", onResize);
  window.addEventListener("resize", onResize);

  loadOverlayFonts().then(() => resizeCanvas());
}

export function updateOverlay(data) {
  currentData = { ...currentData, ...data };
  paint();
}

export function hideOverlay() {
  if (canvasEl) canvasEl.style.display = "none";
}

export function showOverlay() {
  if (!canvasEl) return;
  canvasEl.style.display = "block";
  resizeCanvas();
}
