"""Loads (and on request downloads) the JPL ephemeris behind guruji.astro.

This is the astro engine's only file and network access; astro/ itself stays pure.
The file is not committed (32 MB): run `python -m guruji fetch-ephemeris` once.
"""

import hashlib
import logging
from pathlib import Path

import httpx
from skyfield.api import load
from skyfield.jpllib import SpiceKernel

from guruji.astro.sky import Sky

log = logging.getLogger(__name__)

DE440S_URL = "https://naif.jpl.nasa.gov/pub/naif/generic_kernels/spk/planets/de440s.bsp"
DE440S_SHA256 = "c1c7feeab882263fc493a9d5a5b2ddd71b54826cdf65d8d17a76126b260a49f2"


class EphemerisMissingError(FileNotFoundError):
    pass


def load_sky(path: str | Path) -> Sky:
    p = Path(path)
    if not p.is_file():
        raise EphemerisMissingError(f"{p} not found; run `python -m guruji fetch-ephemeris`")
    # builtin=True: ΔT and leap seconds shipped with Skyfield, no network at runtime.
    return Sky(ts=load.timescale(builtin=True), kernel=SpiceKernel(str(p)))


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def fetch_ephemeris(path: str | Path, url: str = DE440S_URL) -> Path:
    """Download DE440s to `path` unless a verified copy is already there. Idempotent."""
    dest = Path(path)
    if dest.is_file() and _sha256(dest) == DE440S_SHA256:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    log.info("downloading ephemeris to %s", dest)
    with httpx.stream("GET", url, follow_redirects=True, timeout=60) as r:
        r.raise_for_status()
        with tmp.open("wb") as f:
            for chunk in r.iter_bytes(1 << 20):
                f.write(chunk)
    digest = _sha256(tmp)
    if digest != DE440S_SHA256:
        tmp.unlink()
        raise ValueError(f"ephemeris checksum mismatch: {digest}")
    tmp.replace(dest)
    return dest
