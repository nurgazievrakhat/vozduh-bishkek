"""Fetch the latest air-quality readings for Bishkek and update data/latest.json and data/history.json.

Primary source: meteo.kg station feed. When its newest reading is older than STALE_AFTER,
the script takes the latest readings of Sensor.Community sensors around the city instead.
The two networks are never mixed in one snapshot: each snapshot names its source.

Usage:
  python scripts/update.py                         # fetch from the internet
  python scripts/update.py --meteo-file a.json --sc-file b.json --now 2026-10-29T22:20  # offline test
"""
import argparse
import datetime as dt
import json
import os
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
METEO_URL = "https://www.meteo.kg/air-quality-data.json"
SC_URL = "https://data.sensor.community/airrohr/v1/filter/area=42.865,74.6,14"
BISHKEK = dt.timezone(dt.timedelta(hours=6))
STALE_AFTER = dt.timedelta(hours=2)
FRESH_WINDOW = dt.timedelta(hours=2)
KEEP_DAYS = 14
BOUNDS = (42.70, 43.02, 74.35, 74.90)  # lat min, lat max, lon min, lon max
MIN_STATIONS = 5


def fetch_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "vozduh-bishkek/1.0 (+https://github.com/nurgazievrakhat/vozduh-bishkek)"})
    with urllib.request.urlopen(req, timeout=40) as r:
        return json.loads(r.read().decode("utf-8"))


def num(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if x == x else None


def local_str(d):
    return d.astimezone(BISHKEK).strftime("%Y-%m-%d %H:%M:%S")


def parse_local(s):
    try:
        return dt.datetime.strptime(str(s)[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=BISHKEK)
    except ValueError:
        return None


def in_bounds(lat, lon):
    return lat is not None and lon is not None and BOUNDS[0] <= lat <= BOUNDS[1] and BOUNDS[2] <= lon <= BOUNDS[3]


def read_meteo(raw):
    """meteo.kg: list of {id, name, lat, long, pm25, pm1, pm10, aqi, measurementDate (Bishkek time)}."""
    recs = []
    for r in raw if isinstance(raw, list) else []:
        try:
            rid = int(str(r.get("id", "")).strip())
        except ValueError:
            continue
        t = parse_local(r.get("measurementDate"))
        lat, lon = num(r.get("lat")), num(r.get("long"))
        if not t or not in_bounds(lat, lon):
            continue
        recs.append({
            "id": str(rid), "lat": lat, "lon": lon,
            "pm25": num(r.get("pm25")), "pm1": num(r.get("pm1")), "pm10": num(r.get("pm10")),
            "t": local_str(t), "name": " ".join(str(r.get("name") or "").split()),
        })
    return recs


def read_sc(raw):
    """Sensor.Community: list of measurements; keep each sensor's newest PM2.5 (P2) reading, outdoor only."""
    best = {}
    for r in raw if isinstance(raw, list) else []:
        loc = r.get("location") or {}
        sensor = r.get("sensor") or {}
        if str(loc.get("indoor", 0)) in ("1", "true", "True"):
            continue
        lat, lon = num(loc.get("latitude")), num(loc.get("longitude"))
        if not in_bounds(lat, lon):
            continue
        vals = {v.get("value_type"): num(v.get("value")) for v in r.get("sensordatavalues") or []}
        p2 = vals.get("P2")
        if p2 is None or p2 <= 0 or p2 >= 999:
            continue
        try:
            t = dt.datetime.strptime(str(r.get("timestamp"))[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=dt.timezone.utc)
        except ValueError:
            continue
        sid = str(sensor.get("id") or "")
        if not sid:
            continue
        cur = best.get(sid)
        if cur is None or t > cur["_t"]:
            stype = ((sensor.get("sensor_type") or {}).get("name")) or ""
            best[sid] = {"id": "sc" + sid, "lat": lat, "lon": lon, "pm25": round(p2, 2), "pm1": None,
                         "pm10": vals.get("P1"), "t": local_str(t), "name": "", "sensor": stype, "_t": t}
    out = []
    for v in best.values():
        v.pop("_t")
        out.append(v)
    return out


def fresh_points(recs):
    times = [parse_local(r["t"]) for r in recs if r.get("pm25") and r["pm25"] > 0]
    times = [t for t in times if t]
    if not times:
        return None, {}
    newest = max(times)
    pts = {}
    for r in recs:
        t = parse_local(r["t"])
        if t and r.get("pm25") and r["pm25"] > 0 and newest - t <= FRESH_WINDOW:
            pts[r["id"]] = r["pm25"]
    return newest, pts


def load(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def write(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--meteo-file")
    ap.add_argument("--sc-file")
    ap.add_argument("--now", help="Bishkek time for tests, e.g. 2026-10-29T22:20")
    a = ap.parse_args()
    now = dt.datetime.fromisoformat(a.now).replace(tzinfo=BISHKEK) if a.now else dt.datetime.now(BISHKEK)

    notes = []
    meteo, meteo_newest, meteo_pts = [], None, {}
    try:
        raw = load(a.meteo_file, None) if a.meteo_file else fetch_json(METEO_URL)
        meteo = read_meteo(raw)
        meteo_newest, meteo_pts = fresh_points(meteo)
    except Exception as e:  # network or format problem: fall through to the backup
        notes.append("meteo.kg: %s" % e)

    use = None
    if meteo_newest and len(meteo_pts) >= MIN_STATIONS and now - meteo_newest <= STALE_AFTER:
        use = ("meteo", meteo, meteo_newest, meteo_pts)
    else:
        try:
            raw = load(a.sc_file, None) if a.sc_file else fetch_json(SC_URL)
            sc = read_sc(raw)
            sc_newest, sc_pts = fresh_points(sc)
            if sc_newest and len(sc_pts) >= MIN_STATIONS:
                use = ("sc", sc, sc_newest, sc_pts)
            else:
                notes.append("sensor.community: %d sensors" % len(sc_pts))
        except Exception as e:
            notes.append("sensor.community: %s" % e)
    if use is None:
        # both sources quiet: keep whatever is newer, the stored snapshot or the last meteo.kg slice
        prev = load(os.path.join(DATA, "latest.json"), {})
        prev_newest = parse_local(prev.get("newest")) if prev else None
        if prev_newest and (not meteo_newest or prev_newest >= meteo_newest):
            print("no new data, keeping the stored snapshot from", local_str(prev_newest), ";", "; ".join(notes))
            return 0
        if meteo_newest and len(meteo_pts) >= MIN_STATIONS:
            use = ("meteo", meteo, meteo_newest, meteo_pts)
    if use is None:
        print("no usable data;", "; ".join(notes))
        return 1

    src, recs, newest, pts = use
    latest = {
        "fetchedAt": now.isoformat(timespec="seconds"),
        "source": src,
        "sourceUrl": METEO_URL if src == "meteo" else SC_URL,
        "newest": local_str(newest),
        "meteoNewest": local_str(meteo_newest) if meteo_newest else None,
        "records": recs,
        "meteoStations": [{"id": r["id"], "lat": r["lat"], "lon": r["lon"]} for r in meteo],
    }
    os.makedirs(DATA, exist_ok=True)
    write(os.path.join(DATA, "latest.json"), latest)

    hist = load(os.path.join(DATA, "history.json"), {"v": 1, "hours": {}})
    hours = hist.setdefault("hours", {})
    key = newest.strftime("%Y-%m-%dT%H")
    hours[key] = {"t": local_str(newest), "src": src, "pts": pts}
    cutoff = (now - dt.timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%dT%H")
    hist["hours"] = {k: v for k, v in sorted(hours.items()) if k >= cutoff}
    write(os.path.join(DATA, "history.json"), hist)
    print(json.dumps({"source": src, "newest": local_str(newest), "stations": len(pts), "hours": len(hist["hours"]), "notes": notes}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
