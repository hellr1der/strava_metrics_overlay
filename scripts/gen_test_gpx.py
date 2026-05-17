from datetime import datetime, timedelta, timezone
from pathlib import Path

out = Path(__file__).resolve().parent.parent / "sample" / "test.gpx"
lines = ['<?xml version="1.0" encoding="UTF-8"?>', '<gpx version="1.1" creator="test">', "  <trk><trkseg>"]
start = datetime(2026, 5, 15, 7, 23, 0, tzinfo=timezone.utc)
for i in range(180):
    t = start + timedelta(seconds=i)
    lines.append(f'      <trkpt lat="{55.7558 + i * 8e-5:.6f}" lon="{37.6173 + i * 8e-5:.6f}">')
    lines.append(f'        <time>{t:%Y-%m-%dT%H:%M:%SZ}</time><extensions>')
    lines.append(f'          <power>{180 + i % 35}</power><hr>{145 + i % 12}</hr><cad>{85 + i % 8}</cad>')
    lines.append("        </extensions></trkpt>")
lines += ["    </trkseg></trk>", "</gpx>"]
out.write_text("\n".join(lines) + "\n", encoding="utf-8")
