const EARTH_RADIUS_KM = 6371;
export const SPEED_WINDOW_SEC = 10;
export const POWER_WINDOW_SEC = 3;
export const CADENCE_WINDOW_SEC = 3;

function haversineKm(lat1, lon1, lat2, lon2) {
  const toRad = (deg) => (deg * Math.PI) / 180;
  const dLat = toRad(lat2 - lat1);
  const dLon = toRad(lon2 - lon1);
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLon / 2) ** 2;
  return 2 * EARTH_RADIUS_KM * Math.asin(Math.sqrt(a));
}

function parseTime(text) {
  const d = new Date(text.trim());
  if (Number.isNaN(d.getTime())) {
    throw new Error(`Некорректное время в GPX: ${text}`);
  }
  return d;
}

function parseOptionalNumber(text) {
  if (text == null || text === "") return null;
  const n = parseFloat(text);
  return Number.isNaN(n) ? null : n;
}

function parseExtensions(pt) {
  const result = { hr: null, power: null, cadence: null, atemp: null };
  const ext = pt.querySelector("extensions");
  if (!ext) return result;

  for (const el of ext.getElementsByTagName("*")) {
    const name = el.localName?.toLowerCase();
    const value = parseOptionalNumber(el.textContent);
    if (value == null) continue;

    if (name === "hr") result.hr = value;
    else if (name === "cad") result.cadence = value;
    else if (name === "power") result.power = value;
    else if (name === "atemp") result.atemp = value;
  }

  return result;
}

function indexAtOrAfter(points, timeMs) {
  let lo = 0;
  let hi = points.length - 1;
  while (lo < hi) {
    const mid = Math.floor((lo + hi) / 2);
    if (points[mid].time.getTime() < timeMs) lo = mid + 1;
    else hi = mid;
  }
  return lo;
}

function indexAtOrBefore(points, timeMs) {
  let lo = 0;
  let hi = points.length - 1;
  while (lo < hi) {
    const mid = Math.ceil((lo + hi) / 2);
    if (points[mid].time.getTime() > timeMs) hi = mid - 1;
    else lo = mid;
  }
  return lo;
}

function getWindowRange(raw, i, windowSec) {
  const n = raw.length;
  const halfMs = (windowSec * 1000) / 2;
  const trackStart = raw[0].time.getTime();
  const trackEnd = raw[n - 1].time.getTime();
  const centerMs = raw[i].time.getTime();
  const startMs = Math.max(trackStart, centerMs - halfMs);
  const endMs = Math.min(trackEnd, centerMs + halfMs);

  let j = indexAtOrAfter(raw, startMs);
  let k = indexAtOrBefore(raw, endMs);

  if (j > k) {
    const tmp = j;
    j = k;
    k = tmp;
  }

  return { j, k };
}

/** power === 0 или отсутствует (null) → cadence = 0, до сглаживания */
function applyCadenceZeroWhenNoPower(raw) {
  for (const pt of raw) {
    if (pt.power === 0 || pt.power === null) {
      pt.cadence = 0;
    }
  }
}

function computeSmoothedSpeeds(raw) {
  const n = raw.length;
  if (n === 0) return [];
  if (n === 1) return [0];

  const speeds = [];

  for (let i = 0; i < n; i++) {
    let { j, k } = getWindowRange(raw, i, SPEED_WINDOW_SEC);

    if (j === k) {
      j = Math.max(0, i - 1);
      k = Math.min(n - 1, i + 1);
    }

    const dtMs = raw[k].time.getTime() - raw[j].time.getTime();
    if (dtMs <= 0) {
      speeds.push(0);
      continue;
    }

    const distKm = haversineKm(raw[j].lat, raw[j].lon, raw[k].lat, raw[k].lon);
    speeds.push((distKm / dtMs) * 3_600_000);
  }

  return speeds;
}

/**
 * Скользящее среднее по симметричному окну ±windowSec/2.
 * null в selector — точка пропускается; 0 учитывается.
 */
function computeWindowAverage(raw, windowSec, selector) {
  const n = raw.length;
  if (n === 0) return [];

  const values = [];

  for (let i = 0; i < n; i++) {
    const { j, k } = getWindowRange(raw, i, windowSec);
    let sum = 0;
    let count = 0;

    for (let idx = j; idx <= k; idx++) {
      const value = selector(raw[idx]);
      if (value == null) continue;
      sum += value;
      count++;
    }

    values.push(count > 0 ? sum / count : null);
  }

  return values;
}

/** Каденс: одностороннее окно [t − CADENCE_WINDOW_SEC, t], только idx <= i; при cadence=0 на i → 0. */
function computeSmoothedCadence(raw) {
  const n = raw.length;
  if (n === 0) return [];

  const windowMs = CADENCE_WINDOW_SEC * 1000;
  const values = [];

  for (let i = 0; i < n; i++) {
    if (raw[i].cadence === 0) {
      values.push(0);
      continue;
    }

    const centerMs = raw[i].time.getTime();
    const startMs = Math.max(raw[0].time.getTime(), centerMs - windowMs);
    let j = indexAtOrAfter(raw, startMs);
    if (j > i) j = i;

    let sum = 0;
    let count = 0;

    for (let idx = j; idx <= i; idx++) {
      const value = raw[idx].cadence;
      if (value == null) continue;
      sum += value;
      count++;
    }

    values.push(count > 0 ? sum / count : null);
  }

  return values;
}

/**
 * @param {File} file
 * @returns {Promise<{
 *   points: { time: Date, speed: number, hr: number|null, power: number|null, cadence: number|null, atemp: number|null }[],
 *   startTime: Date
 * }>}
 */
export function parseGPX(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      try {
        const doc = new DOMParser().parseFromString(reader.result, "application/xml");
        if (doc.querySelector("parsererror")) {
          reject(new Error("Не удалось разобрать GPX файл"));
          return;
        }

        const trkpts = [...doc.querySelectorAll("trkpt")];
        const raw = [];

        for (const pt of trkpts) {
          const timeEl = pt.querySelector("time");
          if (!timeEl?.textContent?.trim()) continue;

          const lat = parseFloat(pt.getAttribute("lat"));
          const lon = parseFloat(pt.getAttribute("lon"));
          if (Number.isNaN(lat) || Number.isNaN(lon)) continue;

          const { hr, power, cadence, atemp } = parseExtensions(pt);
          raw.push({ lat, lon, time: parseTime(timeEl.textContent), hr, power, cadence, atemp });
        }

        if (raw.length === 0) {
          reject(new Error("GPX не содержит точек с тегом <time>"));
          return;
        }

        const rawLogFrom = new Date("2026-05-15T07:24:40Z");
        const rawLogTo = new Date("2026-05-15T07:25:00Z");
        for (const pt of raw) {
          if (pt.time >= rawLogFrom && pt.time <= rawLogTo) {
            console.log(
              "RAW:",
              pt.time.toISOString(),
              "power=",
              pt.power,
              "cadence=",
              pt.cadence,
            );
          }
        }

        let noPowerLogCount = 0;
        for (const pt of raw) {
          if (pt.power === null && noPowerLogCount < 3) {
            console.log("no power at", pt.time);
            noPowerLogCount++;
          }
        }

        applyCadenceZeroWhenNoPower(raw);

        const speeds = computeSmoothedSpeeds(raw);
        const powers = computeWindowAverage(raw, POWER_WINDOW_SEC, (pt) => pt.power);
        const cadences = computeSmoothedCadence(raw);

        const points = raw.map((pt, i) => ({
          time: pt.time,
          speed: speeds[i],
          hr: pt.hr,
          power: powers[i],
          cadence: cadences[i],
          atemp: pt.atemp,
        }));

        const smoothLogFrom = new Date("2026-05-15T07:24:47Z");
        const smoothLogTo = new Date("2026-05-15T07:24:52Z");
        for (const point of points) {
          if (point.time >= smoothLogFrom && point.time <= smoothLogTo) {
            console.log(
              "SMOOTHED:",
              point.time.toISOString(),
              "cadence=",
              point.cadence,
              "power=",
              point.power,
            );
          }
        }

        console.log("GPX points sample:", points.slice(0, 5));
        resolve({ points, startTime: points[0].time });
      } catch (err) {
        reject(err);
      }
    };
    reader.onerror = () => reject(new Error("Ошибка чтения GPX файла"));
    reader.readAsText(file);
  });
}
