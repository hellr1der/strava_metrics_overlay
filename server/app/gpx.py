from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone


EARTH_RADIUS_KM = 6371.0
SPEED_WINDOW_SEC = 10
POWER_WINDOW_SEC = 3
CADENCE_WINDOW_SEC = 3


@dataclass
class GpxPoint:
    time: datetime
    lat: float
    lon: float
    hr: float | None
    power: float | None
    cadence: float | None
    speed: float = 0.0


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    to_rad = math.radians
    d_lat = to_rad(lat2 - lat1)
    d_lon = to_rad(lon2 - lon1)
    a = (
        math.sin(d_lat / 2) ** 2
        + math.cos(to_rad(lat1)) * math.cos(to_rad(lat2)) * math.sin(d_lon / 2) ** 2
    )
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def parse_iso_datetime(text: str) -> datetime:
    text = text.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


GPX_NS = "http://www.topografix.com/GPX/1/1"


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def find_extensions_element(trkpt) -> object | None:
    """Strava/Garmin GPX: <extensions> в default namespace GPX 1.1."""
    ext = trkpt.find(f"{{{GPX_NS}}}extensions")
    if ext is not None:
        return ext
    for child in trkpt:
        if local_name(child.tag) == "extensions":
            return child
    return None


def parse_optional_number(text: str | None) -> float | None:
    if text is None or not str(text).strip():
        return None
    try:
        return float(text)
    except ValueError:
        return None


def parse_extensions(trkpt) -> tuple[float | None, float | None, float | None]:
    hr = power = cadence = None
    ext = find_extensions_element(trkpt)
    if ext is None:
        return hr, power, cadence
    for el in ext.iter():
        if el is ext:
            continue
        name = local_name(el.tag).lower()
        value = parse_optional_number(el.text)
        if value is None:
            continue
        if name == "hr":
            hr = value
        elif name == "cad":
            cadence = value
        elif name == "power":
            power = value
    return hr, power, cadence


def index_at_or_after(points: list[GpxPoint], time_ms: float) -> int:
    lo, hi = 0, len(points) - 1
    while lo < hi:
        mid = (lo + hi) // 2
        if points[mid].time.timestamp() * 1000 < time_ms:
            lo = mid + 1
        else:
            hi = mid
    return lo


def index_at_or_before(points: list[GpxPoint], time_ms: float) -> int:
    lo, hi = 0, len(points) - 1
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if points[mid].time.timestamp() * 1000 > time_ms:
            hi = mid - 1
        else:
            lo = mid
    return lo


def get_window_range(points: list[GpxPoint], i: int, window_sec: int) -> tuple[int, int]:
    half_ms = window_sec * 500
    track_start = points[0].time.timestamp() * 1000
    track_end = points[-1].time.timestamp() * 1000
    center_ms = points[i].time.timestamp() * 1000
    start_ms = max(track_start, center_ms - half_ms)
    end_ms = min(track_end, center_ms + half_ms)
    j = index_at_or_after(points, start_ms)
    k = index_at_or_before(points, end_ms)
    if j > k:
        j, k = k, j
    return j, k


def apply_cadence_zero_when_no_power(points: list[GpxPoint]) -> None:
    for pt in points:
        if pt.power == 0 or pt.power is None:
            pt.cadence = 0


def compute_smoothed_speeds(points: list[GpxPoint]) -> None:
    n = len(points)
    if n == 0:
        return
    if n == 1:
        points[0].speed = 0
        return
    for i, pt in enumerate(points):
        j, k = get_window_range(points, i, SPEED_WINDOW_SEC)
        if j == k:
            j = max(0, i - 1)
            k = min(n - 1, i + 1)
        dt_ms = (points[k].time - points[j].time).total_seconds() * 1000
        if dt_ms <= 0:
            pt.speed = 0
            continue
        dist = haversine_km(points[j].lat, points[j].lon, points[k].lat, points[k].lon)
        pt.speed = (dist / dt_ms) * 3_600_000


def compute_window_average(
    points: list[GpxPoint], window_sec: int, getter
) -> list[float | None]:
    values: list[float | None] = []
    for i in range(len(points)):
        j, k = get_window_range(points, i, window_sec)
        total = 0.0
        count = 0
        for idx in range(j, k + 1):
            value = getter(points[idx])
            if value is None:
                continue
            total += value
            count += 1
        values.append(total / count if count else None)
    return values


def compute_smoothed_cadence(points: list[GpxPoint]) -> list[float | None]:
    window_ms = CADENCE_WINDOW_SEC * 1000
    track_start_ms = points[0].time.timestamp() * 1000
    values: list[float | None] = []
    for i, pt in enumerate(points):
        if pt.cadence == 0:
            values.append(0)
            continue
        center_ms = pt.time.timestamp() * 1000
        start_ms = max(track_start_ms, center_ms - window_ms)
        j = index_at_or_after(points, start_ms)
        if j > i:
            j = i
        total = 0.0
        count = 0
        for idx in range(j, i + 1):
            value = points[idx].cadence
            if value is None:
                continue
            total += value
            count += 1
        values.append(total / count if count else None)
    return values


def parse_gpx(path) -> list[GpxPoint]:
    import xml.etree.ElementTree as ET

    tree = ET.parse(path)
    root = tree.getroot()
    raw: list[GpxPoint] = []

    for trkpt in root.iter():
        if local_name(trkpt.tag) != "trkpt":
            continue
        time_el = None
        for child in trkpt:
            if local_name(child.tag) == "time":
                time_el = child
                break
        if time_el is None or not (time_el.text or "").strip():
            continue
        lat = float(trkpt.attrib["lat"])
        lon = float(trkpt.attrib["lon"])
        hr, power, cadence = parse_extensions(trkpt)
        raw.append(
            GpxPoint(
                time=parse_iso_datetime(time_el.text),
                lat=lat,
                lon=lon,
                hr=hr,
                power=power,
                cadence=cadence,
            )
        )

    if not raw:
        raise ValueError("GPX не содержит точек с тегом <time>")

    apply_cadence_zero_when_no_power(raw)
    compute_smoothed_speeds(raw)
    powers = compute_window_average(raw, POWER_WINDOW_SEC, lambda p: p.power)
    cadences = compute_smoothed_cadence(raw)

    for i, pt in enumerate(raw):
        pt.power = powers[i]
        pt.cadence = cadences[i]

    return raw


def nearest_point(points: list[GpxPoint], target: datetime) -> GpxPoint | None:
    if not points:
        return None
    target_ms = target.timestamp() * 1000
    first_ms = points[0].time.timestamp() * 1000
    last_ms = points[-1].time.timestamp() * 1000
    if target_ms < first_ms or target_ms > last_ms:
        return None

    lo, hi = 0, len(points) - 1
    while lo < hi:
        mid = (lo + hi) // 2
        if points[mid].time.timestamp() * 1000 < target_ms:
            lo = mid + 1
        else:
            hi = mid

    if lo > 0:
        prev = points[lo - 1]
        curr = points[lo]
        prev_diff = abs(prev.time.timestamp() * 1000 - target_ms)
        curr_diff = abs(curr.time.timestamp() * 1000 - target_ms)
        return prev if prev_diff <= curr_diff else curr
    return points[lo]
