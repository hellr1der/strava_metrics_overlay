/**
 * @param {{ time: Date, speed: number, hr?: number|null, power?: number|null, cadence?: number|null, atemp?: number|null }[]} gpxPoints
 * @param {Date} videoStartTime
 */
export function buildTimeline(gpxPoints, videoStartTime) {
  const gpxStartTime = gpxPoints[0].time;
  const offsetMs = videoStartTime.getTime() - gpxStartTime.getTime();

  return {
    points: gpxPoints,
    videoStartTime,
    gpxStartTime,
    offsetMs,
    offsetSec: offsetMs / 1000,
  };
}

/**
 * @param {ReturnType<typeof buildTimeline>} timeline
 * @param {number} videoCurrentTime — секунды от начала видео
 * @returns {{ speed: number, hr?: number|null, power?: number|null, cadence?: number|null, atemp?: number|null } | null}
 */
export function getMetricAt(timeline, videoCurrentTime) {
  const { points, videoStartTime } = timeline;
  if (!points.length) return null;

  const targetMs = videoStartTime.getTime() + videoCurrentTime * 1000;

  const firstMs = points[0].time.getTime();
  const lastMs = points[points.length - 1].time.getTime();

  if (targetMs < firstMs || targetMs > lastMs) return null;

  let lo = 0;
  let hi = points.length - 1;

  while (lo < hi) {
    const mid = Math.floor((lo + hi) / 2);
    if (points[mid].time.getTime() < targetMs) {
      lo = mid + 1;
    } else {
      hi = mid;
    }
  }

  if (lo > 0) {
    const prev = points[lo - 1];
    const curr = points[lo];
    const prevDiff = Math.abs(prev.time.getTime() - targetMs);
    const currDiff = Math.abs(curr.time.getTime() - targetMs);
    const pt = prevDiff <= currDiff ? prev : curr;
    return { speed: pt.speed, hr: pt.hr, power: pt.power, cadence: pt.cadence, atemp: pt.atemp };
  }

  const pt = points[lo];
  return { speed: pt.speed, hr: pt.hr, power: pt.power, cadence: pt.cadence, atemp: pt.atemp };
}
