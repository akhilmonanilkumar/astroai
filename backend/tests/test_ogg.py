import os
import struct

import httpx
from pydantic import SecretStr

from guruji.voice import ogg
from guruji.voice.speech import SarvamSpeech


def _page(flags: int, granule: int, seq: int, body: bytes) -> bytes:
    segs = bytes([255] * (len(body) // 255) + [len(body) % 255])
    p = ogg.Page(flags, granule, 0xABCD, segs, body)
    return p.encode(seq, granule, flags)


def _stream(seconds: float, page_seconds: float = 0.5) -> bytes:
    head = b"OpusHead" + bytes([1, 1]) + struct.pack("<HIhB", 312, 48000, 0, 0)
    out = _page(0x02, 0, 0, head) + _page(0, 0, 1, b"OpusTags" + b"\0" * 8)
    n = int(seconds / page_seconds)
    for i in range(n):
        flags = 0x04 if i == n - 1 else 0
        out += _page(flags, int((i + 1) * page_seconds * 48000), i + 2, os.urandom(300))
    return out


def _valid(chunk: bytes) -> list[ogg.Page]:
    pages = ogg.parse_pages(chunk)
    pos = 0
    for seq, _ in enumerate(pages):  # every page's checksum and sequence number is right
        nseg = chunk[pos + 26]
        size = 27 + nseg + sum(chunk[pos + 27 : pos + 27 + nseg])
        raw = bytearray(chunk[pos : pos + size])
        stored = struct.unpack_from("<I", raw, 22)[0]
        struct.pack_into("<I", raw, 22, 0)
        assert ogg.ogg_crc(bytes(raw)) == stored
        assert struct.unpack_from("<I", raw, 18)[0] == seq
        pos += size
    return pages


def test_short_note_is_untouched() -> None:
    data = _stream(20)
    assert ogg.split(data, 28) == [data]
    assert ogg.duration_seconds(data) == 20


def test_long_note_splits_into_valid_playable_chunks() -> None:
    data = _stream(70)
    chunks = ogg.split(data, 28)
    assert len(chunks) == 3
    total = 0.0
    for c in chunks:
        pages = _valid(c)
        assert pages[0].body.startswith(b"OpusHead") and pages[1].body.startswith(b"OpusTags")
        assert pages[-1].flags & 0x04  # end of stream on each chunk's last page
        assert not any(p.flags & 0x04 for p in pages[:-1])
        d = ogg.duration_seconds(c)
        assert 0 < d <= 28
        total += d
    assert total == 70  # nothing lost or duplicated


def test_not_ogg() -> None:
    assert not ogg.is_ogg_opus(b"RIFF....WAVE")
    try:
        ogg.parse_pages(b"garbage")
    except ogg.OggError:
        pass
    else:
        raise AssertionError("expected OggError")


async def test_long_voice_note_is_transcribed_in_parts() -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(len(request.content))
        return httpx.Response(
            200, json={"transcript": f"part{len(calls)}", "language_code": "hi-IN"}
        )

    speech = SarvamSpeech(
        SecretStr("k"),
        "aditya",
        "http://sarvam.test",
        httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    t = await speech.transcribe(_stream(50), "audio/ogg")
    assert len(calls) == 2
    assert sorted(t.text.split()) == ["part1", "part2"]
    assert t.language_code == "hi-IN"
