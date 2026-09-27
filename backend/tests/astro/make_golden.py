"""Regenerate tests/astro/golden/charts.json with Swiss Ephemeris as the oracle.

    uv run python tests/astro/make_golden.py

Swiss Ephemeris is AGPL, so it is a dev-only dependency (pysweph) used here and never
under src/. Its data files (sepl_18/semo_18, 1800-2400) are downloaded to
data/cache/sweph/ and not committed; only the resulting JSON is.
"""

import json
import random
import urllib.request
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import swisseph as swe

HERE = Path(__file__).parent
OUT = HERE / "golden" / "charts.json"
SWEPH_DIR = HERE.parents[1] / "data" / "cache" / "sweph"
SWEPH_FILES = ("sepl_18.se1", "semo_18.se1")
SWEPH_URL = "https://raw.githubusercontent.com/aloistr/swisseph/master/ephe/"

# (name, tz, lat, lon)
PLACES = [
    ("Delhi", "Asia/Kolkata", 28.6139, 77.2090),
    ("Mumbai", "Asia/Kolkata", 19.0760, 72.8777),
    ("Kolkata", "Asia/Kolkata", 22.5726, 88.3639),
    ("Chennai", "Asia/Kolkata", 13.0827, 80.2707),
    ("Bengaluru", "Asia/Kolkata", 12.9716, 77.5946),
    ("Hyderabad", "Asia/Kolkata", 17.3850, 78.4867),
    ("Kochi", "Asia/Kolkata", 9.9312, 76.2673),
    ("Thiruvananthapuram", "Asia/Kolkata", 8.5241, 76.9366),
    ("Srinagar", "Asia/Kolkata", 34.0837, 74.7973),
    ("Guwahati", "Asia/Kolkata", 26.1445, 91.7362),
    ("Jaipur", "Asia/Kolkata", 26.9124, 75.7873),
    ("Varanasi", "Asia/Kolkata", 25.3176, 82.9739),
    ("Kathmandu", "Asia/Kathmandu", 27.7172, 85.3240),
    ("Colombo", "Asia/Colombo", 6.9271, 79.8612),
    ("Dhaka", "Asia/Dhaka", 23.8103, 90.4125),
    ("Dubai", "Asia/Dubai", 25.2048, 55.2708),
    ("Singapore", "Asia/Singapore", 1.3521, 103.8198),
    ("London", "Europe/London", 51.5074, -0.1278),
    ("New York", "America/New_York", 40.7128, -74.0060),
    ("San Francisco", "America/Los_Angeles", 37.7749, -122.4194),
    ("Toronto", "America/Toronto", 43.6532, -79.3832),
    ("Sydney", "Australia/Sydney", -33.8688, 151.2093),
    ("Nairobi", "Africa/Nairobi", -1.2921, 36.8219),
    ("Oslo", "Europe/Oslo", 59.9139, 10.7522),
    ("Anchorage", "America/Anchorage", 61.2181, -149.9003),
]
_PLACE = {p[0]: p for p in PLACES}

# Hand-picked edge cases: historical Indian offsets, midnight, DST folds, far south/north.
FIXED = [
    ("independence", "Delhi", "1947-08-15T00:00"),
    ("madras-time-1901", "Chennai", "1901-02-03T05:30"),
    ("hmt-1875", "Kolkata", "1875-01-01T12:00"),
    ("wartime-ist-1943", "Mumbai", "1943-03-10T10:00"),
    ("y2k-midnight", "Mumbai", "2000-01-01T00:00"),
    ("leap-day", "Bengaluru", "1996-02-29T23:59"),
    ("kochi-dawn", "Kochi", "1988-11-11T05:47"),
    ("srinagar-north", "Srinagar", "1972-06-21T12:00"),
    ("nepal-545", "Kathmandu", "1990-04-14T06:15"),
    ("london-dst-fold", "London", "2021-10-31T01:30"),
    ("new-york-dst", "New York", "1985-07-04T21:10"),
    ("sydney-south", "Sydney", "1999-12-31T23:30"),
    ("nairobi-equator", "Nairobi", "2010-03-20T18:00"),
    ("oslo-high-lat", "Oslo", "1979-12-21T08:00"),
    ("anchorage-high-lat", "Anchorage", "2005-06-21T14:00"),
    ("recent", "Hyderabad", "2026-09-28T09:00"),
    ("near-future", "Guwahati", "2035-01-15T16:45"),
]
N_RANDOM = 33
SEED = 108
RANDOM_FROM = date(1900, 1, 1)
RANDOM_TO = date(2035, 12, 31)

BODIES = {
    "Sun": swe.SUN,
    "Moon": swe.MOON,
    "Mars": swe.MARS,
    "Mercury": swe.MERCURY,
    "Jupiter": swe.JUPITER,
    "Venus": swe.VENUS,
    "Saturn": swe.SATURN,
    "Rahu": swe.MEAN_NODE,
}


def _ensure_sweph_files() -> None:
    SWEPH_DIR.mkdir(parents=True, exist_ok=True)
    for name in SWEPH_FILES:
        path = SWEPH_DIR / name
        if not path.exists():
            urllib.request.urlretrieve(SWEPH_URL + name, path)


def _cases() -> list[tuple[str, str, str]]:
    rng = random.Random(SEED)
    cases = list(FIXED)
    span = (RANDOM_TO - RANDOM_FROM).days
    for i in range(N_RANDOM):
        day = RANDOM_FROM + timedelta(days=rng.randrange(span))
        minute = rng.randrange(24 * 60)
        place = rng.choice(PLACES)[0]
        cases.append((f"random-{i:02d}", place, f"{day}T{minute // 60:02d}:{minute % 60:02d}"))
    return cases


def main() -> None:
    _ensure_sweph_files()
    swe.set_ephe_path(str(SWEPH_DIR))
    swe.set_sid_mode(swe.SIDM_LAHIRI)
    flags = swe.FLG_SWIEPH | swe.FLG_SIDEREAL | swe.FLG_SPEED
    out = []
    for case_id, place, local in _cases():
        _, tz, lat, lon = _PLACE[place]
        naive = datetime.fromisoformat(local)
        utc = datetime.combine(naive.date(), naive.time(), ZoneInfo(tz)).astimezone(UTC)
        hours = utc.hour + utc.minute / 60 + utc.second / 3600
        jd = swe.julday(utc.year, utc.month, utc.day, hours)
        grahas = {}
        for name, body in BODIES.items():
            xx, ret, _err = swe.calc_ut(jd, body, flags)
            if ret & swe.FLG_SWIEPH != swe.FLG_SWIEPH:
                raise RuntimeError(f"{case_id}: Swiss Ephemeris files not used")
            grahas[name] = {"longitude": xx[0], "speed": xx[3]}
        grahas["Ketu"] = {
            "longitude": (grahas["Rahu"]["longitude"] + 180.0) % 360.0,
            "speed": grahas["Rahu"]["speed"],
        }
        _, ascmc = swe.houses_ex(jd, lat, lon, b"W", swe.FLG_SIDEREAL)
        out.append(
            {
                "id": case_id,
                "place": place,
                "tz": tz,
                "latitude": lat,
                "longitude": lon,
                "local": local,
                "utc": utc.isoformat(),
                "ayanamsa": swe.get_ayanamsa_ex_ut(jd, swe.FLG_SWIEPH)[1],
                "ascendant": ascmc[0],
                "grahas": grahas,
            }
        )
    OUT.parent.mkdir(parents=True, exist_ok=True)
    doc = {
        "oracle": f"Swiss Ephemeris {swe.version} (SE files), Lahiri, mean node",
        "charts": out,
    }
    OUT.write_text(json.dumps(doc, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {len(out)} charts to {OUT}")


if __name__ == "__main__":
    main()
