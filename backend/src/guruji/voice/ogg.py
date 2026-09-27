"""Split an OGG/Opus voice note into shorter, independently playable OGG files.

Sarvam's real-time speech-to-text takes at most 30 seconds of audio; WhatsApp voice notes
are often longer. OGG is a sequence of pages, so a long note can be cut at page
boundaries without decoding: each chunk gets the stream's header pages (OpusHead,
OpusTags) and a run of audio pages, with page sequence numbers, granule positions and
checksums rewritten. No ffmpeg, no re-encoding, no quality loss.

Format: RFC 3533 (Ogg) and RFC 7845 (Opus in Ogg; granule positions count 48 kHz samples).
"""

import struct
from dataclasses import dataclass

OPUS_RATE = 48_000
_HEADER = struct.Struct("<4sBBqIII B")  # capture, version, flags, granule, serial, seq, crc, nseg
_EOS = 0x04


def _crc_table() -> list[int]:
    table = []
    for i in range(256):
        r = i << 24
        for _ in range(8):
            r = ((r << 1) ^ 0x04C11DB7) if r & 0x80000000 else (r << 1)
        table.append(r & 0xFFFFFFFF)
    return table


_CRC = _crc_table()


def ogg_crc(data: bytes) -> int:
    crc = 0
    for b in data:
        crc = ((crc << 8) & 0xFFFFFFFF) ^ _CRC[((crc >> 24) & 0xFF) ^ b]
    return crc


@dataclass
class Page:
    flags: int
    granule: int
    serial: int
    segments: bytes  # the lacing table
    body: bytes

    def encode(self, seq: int, granule: int, flags: int) -> bytes:
        head = _HEADER.pack(b"OggS", 0, flags, granule, self.serial, seq, 0, len(self.segments))
        raw = bytearray(head + self.segments + self.body)
        struct.pack_into("<I", raw, 22, ogg_crc(bytes(raw)))
        return bytes(raw)


class OggError(ValueError):
    pass


def parse_pages(data: bytes) -> list[Page]:
    pages: list[Page] = []
    pos = 0
    while pos < len(data):
        if data[pos : pos + 4] != b"OggS" or len(data) - pos < _HEADER.size:
            raise OggError(f"not an Ogg page at byte {pos}")
        _, _, flags, granule, serial, _, _, nseg = _HEADER.unpack_from(data, pos)
        seg_start = pos + _HEADER.size
        segments = data[seg_start : seg_start + nseg]
        body_len = sum(segments)
        body_start = seg_start + nseg
        body = data[body_start : body_start + body_len]
        if len(body) != body_len:
            raise OggError("truncated Ogg page")
        pages.append(Page(flags, granule, serial, bytes(segments), body))
        pos = body_start + body_len
    return pages


def is_ogg_opus(data: bytes) -> bool:
    return data[:4] == b"OggS" and b"OpusHead" in data[:100]


def duration_seconds(data: bytes) -> float:
    pages = parse_pages(data)
    last = max((p.granule for p in pages if p.granule > 0), default=0)
    return last / OPUS_RATE


def split(data: bytes, max_seconds: float = 28.0) -> list[bytes]:
    """Chunks of at most ~max_seconds each; the input itself if it is short enough."""
    pages = parse_pages(data)
    # Header pages (OpusHead, OpusTags) carry granule 0; audio pages carry positions > 0.
    n_head = next((i for i, p in enumerate(pages) if p.granule > 0), len(pages))
    head, audio = pages[:n_head], pages[n_head:]
    if not audio or audio[-1].granule / OPUS_RATE <= max_seconds:
        return [data]
    limit = int(max_seconds * OPUS_RATE)
    groups: list[list[Page]] = [[]]
    base = 0  # granule where the current chunk starts
    for page in audio:
        if groups[-1] and page.granule - base > limit:
            base = groups[-1][-1].granule
            groups.append([])
        groups[-1].append(page)
    chunks = []
    start = 0
    for group in groups:
        out = bytearray()
        seq = 0
        for p in head:
            out += p.encode(seq, 0, p.flags & ~_EOS)
            seq += 1
        for i, p in enumerate(group):
            flags = (p.flags & ~_EOS) | (_EOS if i == len(group) - 1 else 0)
            granule = p.granule - start if p.granule > 0 else p.granule
            out += p.encode(seq, granule, flags)
            seq += 1
        chunks.append(bytes(out))
        start = group[-1].granule
    return chunks
