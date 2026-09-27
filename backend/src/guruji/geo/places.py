"""Offline birthplace search over GeoNames (cities500: every place with 500+ people).

Each place carries its IANA timezone, which geo.tz uses for the historical UTC offset.
Answers like "Cochin", "Bombay", "दिल्ली" or "Kochi, Kerala" work: names, ASCII names and
alternate names are indexed; text after a comma narrows by state or country. India is
preferred when names tie, since most users are Indian.

Data (not committed): `python -m guruji fetch-geonames` downloads it into geonames_dir.
"""

import io
import logging
import re
import unicodedata
import zipfile
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import httpx
from rapidfuzz import fuzz, process

log = logging.getLogger(__name__)

GEONAMES_BASE = "https://download.geonames.org/export/dump/"
CITIES_FILE = "cities500.txt"
ADMIN1_FILE = "admin1CodesASCII.txt"
COUNTRY_FILE = "countryInfo.txt"
# Alternate names only for places at least this big: keeps the index small and precise.
_ALT_NAME_MIN_POP = 15_000
_HOME_COUNTRY = "IN"
_FUZZY_CUTOFF = 85


@dataclass(frozen=True)
class Place:
    geoname_id: int
    name: str
    admin1: str
    country_code: str
    country: str
    latitude: float
    longitude: float
    tz_name: str
    population: int

    @property
    def label(self) -> str:
        parts = [self.name]
        if self.admin1 and self.admin1 != self.name:
            parts.append(self.admin1)
        parts.append(self.country)
        return ", ".join(parts)


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).casefold().strip()
    text = re.sub(r"\b(district|city|town|village|dist|tehsil)\b\.?", " ", text)
    # Keep letters, combining marks (Devanagari vowel signs) and digits; drop the rest.
    text = "".join(c if unicodedata.category(c)[0] in "LMN" else " " for c in text)
    return re.sub(r"\s+", " ", text).strip()


class PlaceIndex:
    def __init__(self, places: list[Place], names: dict[int, set[str]]) -> None:
        self.places = {p.geoname_id: p for p in places}
        self._by_name: dict[str, list[int]] = defaultdict(list)
        for gid, ns in names.items():
            for n in ns:
                self._by_name[n].append(gid)
        self._keys = list(self._by_name)

    def __len__(self) -> int:
        return len(self.places)

    def search(self, query: str, limit: int = 3) -> list[Place]:
        """Best matches, most likely first. Empty if nothing plausible."""
        head, comma, rest = query.partition(",")
        found = self._search(normalize(head), normalize(rest), limit)
        if found or comma:
            return found
        # "Aurangabad Maharashtra", "Kochi Kerala India": the trailing words may be a hint.
        words = normalize(head).split()
        for cut in range(len(words) - 1, 0, -1):
            found = self._search(" ".join(words[:cut]), " ".join(words[cut:]), limit)
            if found:
                return found
        return []

    def _search(self, name: str, hint: str, limit: int) -> list[Place]:
        if not name:
            return []
        ids = list(self._by_name.get(name, []))
        if not ids:
            for key, _score, _ in process.extract(
                name, self._keys, scorer=fuzz.ratio, limit=10, score_cutoff=_FUZZY_CUTOFF
            ):
                ids.extend(self._by_name[key])
        candidates = [self.places[i] for i in dict.fromkeys(ids)]
        if hint:
            narrowed = [p for p in candidates if self._matches_hint(p, hint)]
            candidates = narrowed or candidates
        candidates.sort(key=lambda p: (p.country_code != _HOME_COUNTRY, -p.population))
        return self._dedupe(candidates)[:limit]

    @staticmethod
    def _matches_hint(p: Place, hint: str) -> bool:
        fields = [
            f for f in (normalize(p.admin1), normalize(p.country), p.country_code.casefold()) if f
        ]
        # The whole hint ("uttar pradesh") or any of its words ("kerala india") may match.
        for part in (hint, *hint.split()):
            if any(part in f or fuzz.ratio(part, f) >= _FUZZY_CUTOFF for f in fields):
                return True
        return False

    @staticmethod
    def _dedupe(places: list[Place]) -> list[Place]:
        """Drop same-labelled duplicates (GeoNames lists some cities and their districts)."""
        seen: set[str] = set()
        out = []
        for p in places:
            if p.label not in seen:
                seen.add(p.label)
                out.append(p)
        return out


def load_places(directory: str | Path) -> PlaceIndex:
    d = Path(directory)
    admin1: dict[str, str] = {}
    for line in (d / ADMIN1_FILE).read_text("utf-8").splitlines():
        code, name, *_ = line.split("\t")
        admin1[code] = name
    countries: dict[str, str] = {}
    for line in (d / COUNTRY_FILE).read_text("utf-8").splitlines():
        if line and not line.startswith("#"):
            cols = line.split("\t")
            countries[cols[0]] = cols[4]

    places: list[Place] = []
    names: dict[int, set[str]] = {}
    with (d / CITIES_FILE).open(encoding="utf-8") as f:
        for line in f:
            c = line.rstrip("\n").split("\t")
            if len(c) < 18 or c[6] != "P":  # populated places only
                continue
            gid, pop = int(c[0]), int(c[14] or 0)
            place = Place(
                geoname_id=gid,
                name=c[1],
                admin1=admin1.get(f"{c[8]}.{c[10]}", ""),
                country_code=c[8],
                country=countries.get(c[8], c[8]),
                latitude=float(c[4]),
                longitude=float(c[5]),
                tz_name=c[17],
                population=pop,
            )
            keys = {normalize(c[1]), normalize(c[2])}
            if pop >= _ALT_NAME_MIN_POP and c[3]:
                keys |= {normalize(a) for a in c[3].split(",")}
            places.append(place)
            names[gid] = {k for k in keys if k}
    log.info("loaded %d places", len(places))
    return PlaceIndex(places, names)


def fetch_geonames(directory: str | Path) -> Path:
    """Download the GeoNames files if missing. Idempotent."""
    d = Path(directory)
    d.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=120, follow_redirects=True) as http:
        if not (d / CITIES_FILE).exists():
            log.info("downloading %s", CITIES_FILE)
            r = http.get(GEONAMES_BASE + "cities500.zip")
            r.raise_for_status()
            with zipfile.ZipFile(io.BytesIO(r.content)) as z:
                z.extract(CITIES_FILE, d)
        for name in (ADMIN1_FILE, COUNTRY_FILE):
            if not (d / name).exists():
                r = http.get(GEONAMES_BASE + name)
                r.raise_for_status()
                (d / name).write_bytes(r.content)
    return d
