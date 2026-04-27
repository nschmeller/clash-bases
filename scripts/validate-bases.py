#!/usr/bin/env python3
"""
Validate bases.json -- structural invariants of every COC layout link,
forgery / fabrication heuristics on the HMAC tag, catalogue-wide
consistency checks, and (optionally) basic reachability of the share
endpoint.

Background
----------
A Supercell share URL has the shape

    https://link.clashofclans.com/<lang>?action=OpenLayout&id=TH<n>%3A(HV|WB)%3A<blob>

The base64url <blob> always decodes to exactly 24 bytes:

    bytes 0..4   big-endian uint32 -- "collection index" (small, 0..~100)
    bytes 4..8   big-endian uint32 -- layout slot in {1, 2, 3}
                 (1 = Layout 1, 2 = Layout 2, 3 = War Layout)
    bytes 8..24  16-byte HMAC tag, signed by Supercell

Empirically across 5_617 real ids harvested from cocbases / basemelon /
blueprintcoc:

    - slot/village pairing is NOT constrained
      (slot 1, 2, 3 all appear with both HV and WB)
    - index ranges 0..93 with the long tail rolling off near 30
    - zero collisions on the 16-byte tag

We can't forge a tag without Supercell's key, but we can detect
"obviously fake" ids by enforcing those statistical properties on every
entry plus a few catalogue-internal consistency checks.

Checks performed
----------------
ERRORS (fatal -- cause a non-zero exit)
  E1  URL matches the layout-link regex.
  E2  Decoded payload is exactly 24 bytes.
  E3  Layout slot ∈ {1, 2, 3}.
  E4  TH<n> in URL matches `town_hall` field.
  E5  Collection index < 1_000 (observed max 93; a value in the tens of
      millions would mean random bytes).
  E6  HMAC-tag Shannon entropy ≥ 2.5 bits/byte (real HMAC output is
      ~3.8-4.0 bits/byte; very low entropy means structured input).
  E7  HMAC tag is not all-zero, all-one-byte, or a monotonic ramp.
  E8  HMAC tag does not collide with another entry's tag (same 16
      bytes from a different (TH, village, slot, index) tuple is
      impossible with a real signature).
  E9  Required fields present and well-typed.
  E10 `id` and `link` are unique across the catalogue.
  E11 `type` is on the canonical whitelist (War / Trophy / Farm /
      Hybrid / Progress / Fun / Home Village).
  E12 `image` is present, non-empty, and on a known-good host.
      YouTube/TikTok/Instagram thumbnail hosts are explicitly rejected
      because they show a video frame or social-share card, not a
      base preview.
  E13 `image` URL is unique across the catalogue (was W4; promoted
      because two cards pointing at the same screenshot are
      misleading -- the duplicates are dropped at curate time).
  E14 Normalised link (lang-prefix-insensitive, trailing-slash-
      insensitive) is unique across the catalogue.  Catches the
      same blob being committed twice with `/en?` and `/en/?`,
      which E10's exact-string check would miss.

WARNINGS (informational -- promoted to errors with --strict)
  W1  Lang prefix is on the observed Supercell whitelist.
  W2  Auto-generated entries (id starts with `th{N}-{src}-`) embed
      their upstream identifier; the id slug must match the upstream
      id encoded in the `builder` string.
  W3  When `builder` names a known catalogue site, the `image` field
      is hosted on a domain associated with that site.

CLIENT-STYLE LIVENESS (--liveness; informational)
  As far as we can mimic the Clash mobile client without actually being
  it.  Each link is fetched in parallel (16 workers) and we check:

    L1  Final HTTP status is 2xx.
    L2  The redirect chain preserves the id verbatim.  Supercell's
        share endpoint redirects `/en?action=...` → `/en/?action=...`;
        the final URL must still contain `TH<n>%3A<v>%3A<blob>`.
        (Catches DNS hijacks that strip the query, bad rewriters, etc.)
    L3  The response body is the genuine Supercell share-link Next.js
        app: BOTH a `clashofclans://` deep-link anchor (the in-app
        handoff button + QR-code fallback) AND the literal product
        name `Clash of Clans`.  A response that 200s but lacks these
        is not the real share endpoint -- a transparent proxy,
        captive portal, parked-domain page, or DNS hijack.
    L4  Optional --check-images: for every entry with an `image`
        field, range-fetch the first 1024 bytes and confirm:
          - magic bytes match PNG / JPEG / WebP / GIF / SVG
          - decoded image dimensions are at least 200x200 (real base
            screenshots are 600x600+; sub-200 means a placeholder,
            badge, or pixel-tracker)
          - first-32-bytes hash is unique across the catalogue
            (catches byte-level duplicate screenshots that slip past
            URL-level dedup)

  None of these prove a specific id is *cryptographically valid* --
  that requires Supercell's private HMAC key, which the in-app
  handler verifies but is not exposed by any public endpoint
  (link.clashofclans.com, api.clashofclans.com/v1/, etc. all return
  identical responses for real and fake ids).  L1-L4 are the largest
  superset of "would the page even open in the mobile browser before
  the deep link fires?" that we can check from outside the app.

  Use --strict-liveness to treat L1-L3 failures as fatal.

Usage
-----
    python3 scripts/validate-bases.py [bases.json]
    python3 scripts/validate-bases.py bases.json --strict
    python3 scripts/validate-bases.py bases.json --liveness
    python3 scripts/validate-bases.py bases.json --liveness --check-images
"""

from __future__ import annotations

import argparse
import base64
import concurrent.futures
import json
import math
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path

URL_RE = re.compile(
    r"^https://link\.clashofclans\.com/(?P<lang>[a-z]{2})/?\?action=OpenLayout&id="
    r"TH(?P<th>\d{1,2})%3A(?P<v>HV|WB)%3A(?P<blob>[A-Za-z0-9_\-]{32})$"
)
ALLOWED_SLOTS = {1, 2, 3}
EXPECTED_BLOB_CHARS = 32
EXPECTED_PAYLOAD_BYTES = 24
INDEX_HARD_CAP = 1_000  # real ids observed: 0..93
TAG_ENTROPY_FLOOR = 2.5  # bits/byte; real HMAC output is ~3.8-4.0

ALLOWED_TYPES = frozenset({
    "War", "Trophy", "Farm", "Hybrid", "Progress", "Fun", "Home Village",
})

# Image hosts that are NOT base previews (these are video thumbnails,
# user avatars, social embeds, etc).  An entry pointing at one of these
# is not actually showing the base and the user's "Open in Clash" flow
# would mislead.
IMAGE_HOST_BLACKLIST = frozenset({
    "i.ytimg.com",
    "img.youtube.com",
    "youtube.com",
    "www.youtube.com",
    "youtu.be",
    "ytimg.com",
    "i.vimeocdn.com",
    "secure-thumbs.imgix.net",  # tiktok thumbs
    "p16-sign.tiktokcdn-us.com",
    "scontent.cdninstagram.com",
})

# Image magic bytes -> human label.  Used by L4 to confirm the URL
# really points at an image, not an HTML error page.
IMAGE_MAGIC = (
    (b"\x89PNG\r\n\x1a\n", "PNG"),
    (b"\xff\xd8\xff", "JPEG"),
    (b"GIF87a", "GIF"),
    (b"GIF89a", "GIF"),
    (b"RIFF", "WEBP"),  # confirmed by checking offset 8..12 == b"WEBP"
    (b"<?xml", "SVG"),
    (b"<svg", "SVG"),
)

# Lang prefixes observed in real Supercell URLs across catalogues.
# The redirect endpoint accepts any [a-z]{2} but only this whitelist
# appears in actual share output, so anything else is warned about.
KNOWN_LANG_PREFIXES = frozenset({
    "en", "fr", "de", "es", "it", "ja", "ko", "ru", "th", "ar",
    "tr", "pt", "zh", "vi", "id", "nl", "fi", "pl", "ms", "cn",
    "no", "kr", "jp", "fa", "he", "uk", "ro", "cs", "el", "sv",
    "da", "hu", "bg",
})

# Recognised catalogue sources -> hostnames that are valid for the
# `image` field of an entry attributed to that source.  Keys are
# substring matches against the `builder` field.
SOURCE_IMAGE_HOSTS = {
    "cocbases.com": ("media.oneclash.com", "cocbases.com"),
    "basemelon.com": ("img.basemelon.com", "basemelon.com"),
    "blueprintcoc.com": ("blueprintcoc.com", "cdn.shopify.com"),
}

# Auto-generated id prefixes -> regex pulling the upstream id out of
# the builder string.  Used by W2 to cross-check id <-> builder.
SOURCE_ID_PATTERNS = {
    "cocbases": re.compile(r"layout id ([a-z0-9]+)"),
    "basemelon": re.compile(r"design #(\d+)"),
}

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
LIVENESS_WORKERS = 16  # per host pool; 32+ triggers Cloudfront throttling
                        # on link.clashofclans.com.
LIVENESS_TIMEOUT = 10.0
LIVENESS_BODY_BUDGET = 80_000  # bytes; both SHARE_APP_MARKERS need to be
                                # present somewhere in the response.  The
                                # share-app body is ~70KB and the markers
                                # are scattered late, so we have to read
                                # nearly all of it.  Tried 16KB; it false-
                                # positives ~96% of probes.
PROGRESS_EVERY = 100         # print progress every N entries
PROGRESS_INTERVAL_SEC = 2.0  # ... or every N seconds, whichever first
MIN_IMAGE_DIMENSION = 200    # below this = placeholder, not a base


def b64url_decode(blob: str) -> bytes:
    pad = "=" * ((4 - len(blob) % 4) % 4)
    return base64.urlsafe_b64decode(blob + pad)


def shannon_entropy_bits_per_byte(b: bytes) -> float:
    """Shannon entropy of `b`, in bits per byte.  Max is 8.0."""
    if not b:
        return 0.0
    counts = Counter(b)
    n = len(b)
    return -sum((c / n) * math.log2(c / n) for c in counts.values())


def is_pathological_tag(tag: bytes) -> str | None:
    """Return a reason string if `tag` looks fabricated, else None."""
    if all(byte == 0 for byte in tag):
        return "all-zero tag"
    if len(set(tag)) == 1:
        return f"all bytes are 0x{tag[0]:02x}"
    # ramps: 0x00 0x01 0x02 ... or 0xff 0xfe 0xfd ...
    diffs = {(b - a) & 0xFF for a, b in zip(tag, tag[1:])}
    if len(diffs) == 1 and diffs.pop() in (1, 0xFF):
        return "tag is a monotonic byte ramp"
    # Across 5_617 real ids the maximum observed was 11 printable bytes
    # in 16 (consistent with binomial(16, 95/256)).  Demand ≥15 so the
    # check fires only on genuinely text-shaped tags.
    printable = sum(0x20 <= byte < 0x7F for byte in tag)
    if printable >= 15:
        return f"{printable}/16 tag bytes are printable ASCII"
    return None


def parse_link(link: str) -> tuple[re.Match[str] | None, dict]:
    m = URL_RE.match(link)
    if not m:
        return None, {}
    return m, {
        "lang": m.group("lang"),
        "th": int(m.group("th")),
        "village": m.group("v"),
        "blob": m.group("blob"),
    }


def decode_link_payload(link: str) -> dict:
    """
    Fast path that returns just {th, village, lang, index, slot, tag}
    when the link parses into a 24-byte payload, else {}.  Used by
    the catalogue-wide E8 check on entries that are out of scope
    in --diff-against mode -- skips the per-entry validation work
    (E1/E2/E3/E5/E6/E7) we're going to throw away anyway.
    """
    m, info = parse_link(link)
    if m is None:
        return info
    try:
        payload = b64url_decode(m.group("blob"))
    except Exception:
        return info
    if len(payload) != EXPECTED_PAYLOAD_BYTES:
        return info
    info.update(
        index=int.from_bytes(payload[0:4], "big"),
        slot=int.from_bytes(payload[4:8], "big"),
        tag=payload[8:24],
    )
    return info


def validate_link_payload(link: str, declared_th: int) -> tuple[list[str], dict]:
    """
    Return (errors, payload_info).  payload_info is populated when the
    URL parses, so callers can use it for cross-entry checks even if
    individual checks failed.
    """
    errors: list[str] = []
    m, info = parse_link(link)
    if m is None:
        errors.append("E1 link does not match expected layout URL shape")
        return errors, info

    if info["th"] != declared_th:
        errors.append(
            f"E4 TH in URL (TH{info['th']}) does not match town_hall ({declared_th})"
        )

    blob = info["blob"]
    if len(blob) != EXPECTED_BLOB_CHARS:
        errors.append(
            f"E1 id blob has {len(blob)} chars, expected {EXPECTED_BLOB_CHARS}"
        )
        return errors, info

    try:
        payload = b64url_decode(blob)
    except Exception as e:
        errors.append(f"E1 id blob is not valid base64url: {e}")
        return errors, info

    if len(payload) != EXPECTED_PAYLOAD_BYTES:
        errors.append(
            f"E2 decoded payload is {len(payload)} bytes, expected {EXPECTED_PAYLOAD_BYTES}"
        )
        return errors, info

    index = int.from_bytes(payload[0:4], "big")
    slot = int.from_bytes(payload[4:8], "big")
    tag = payload[8:24]
    info.update(index=index, slot=slot, tag=tag)

    if slot not in ALLOWED_SLOTS:
        errors.append(
            f"E3 layout slot is {slot}, expected one of {sorted(ALLOWED_SLOTS)}"
        )

    if index >= INDEX_HARD_CAP:
        errors.append(
            f"E5 collection index {index} >= {INDEX_HARD_CAP} "
            "(real ids observed: 0..~100)"
        )

    entropy = shannon_entropy_bits_per_byte(tag)
    if entropy < TAG_ENTROPY_FLOOR:
        errors.append(
            f"E6 HMAC tag entropy {entropy:.2f} bits/byte < {TAG_ENTROPY_FLOOR} "
            f"(tag={tag.hex()})"
        )

    pathology = is_pathological_tag(tag)
    if pathology:
        errors.append(f"E7 HMAC tag looks fabricated: {pathology} (tag={tag.hex()})")

    return errors, info


def cross_check_builder_id(entry: dict) -> str | None:
    """
    For auto-generated ids of the form `th{N}-{src}-{slug}`, the
    upstream id encoded in `builder` must match the trailing slug.
    Returns a warning string or None.
    """
    eid = entry.get("id", "")
    builder = entry.get("builder", "")
    m = re.match(r"th\d+-(cocbases|basemelon|blueprintcoc)-(.+)$", eid)
    if not m:
        return None
    src, id_slug = m.group(1), m.group(2)
    pat = SOURCE_ID_PATTERNS.get(src)
    if pat is None:
        return None  # blueprintcoc ids hash the link, no upstream id to compare
    bm = pat.search(builder)
    if bm is None:
        return f"W2 id '{eid}' claims source '{src}' but builder='{builder}' has no upstream id"
    upstream = bm.group(1)
    # cocbases: id slug is the upstream layout id verbatim
    # basemelon: id slug is the upstream design number
    if src == "cocbases" and id_slug != upstream:
        return f"W2 id '{eid}' slug '{id_slug}' != cocbases layout id '{upstream}'"
    if src == "basemelon" and id_slug != upstream:
        return f"W2 id '{eid}' slug '{id_slug}' != basemelon design '#{upstream}'"
    return None


def cross_check_image_host(entry: dict) -> str | None:
    image = entry.get("image")
    if not image:
        return None
    builder = entry.get("builder", "")
    for site, hosts in SOURCE_IMAGE_HOSTS.items():
        if site in builder:
            if not any(h in image for h in hosts):
                return (
                    f"W3 image host for '{entry.get('id','?')}' looks wrong: "
                    f"builder mentions {site} but image={image}"
                )
            return None
    return None


# A response from the genuine Supercell Next.js share-link app always
# contains the in-app deep-link prefix `clashofclans://` (in the
# "Open in App" anchor and in the QR-code fallback) and the literal
# product name `Clash of Clans` (page title + multiple chrome strings).
# A 200 from a captive portal / parked-domain / hijacked DNS will not
# contain both.
SHARE_APP_MARKERS = ("clashofclans://", "Clash of Clans")


def _probe_one(link: str) -> tuple[bool, str, str]:
    """Single-shot probe.  Returns (alive, detail, final_url_lang)."""
    m = URL_RE.match(link)
    if not m:
        return False, "L1 input link did not parse", ""
    expected_id = f"TH{m.group('th')}%3A{m.group('v')}%3A{m.group('blob')}"

    req = urllib.request.Request(
        link,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=LIVENESS_TIMEOUT) as resp:
            status = resp.status
            final_url = resp.geturl()
            body = resp.read(LIVENESS_BODY_BUDGET).decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return False, f"L1 HTTP {e.code}", m.group("lang")
    except Exception as e:
        return False, f"L1 request failed: {e}", m.group("lang")

    if status >= 400:
        return False, f"L1 HTTP {status}", m.group("lang")

    if expected_id not in final_url:
        return False, f"L2 redirect dropped id; final URL: {final_url}", m.group("lang")

    missing = [s for s in SHARE_APP_MARKERS if s not in body]
    if missing:
        return False, f"L3 response missing markers {missing}", m.group("lang")

    return True, f"L1+L2+L3 OK ({status})", m.group("lang")


def liveness_probe(link: str) -> tuple[bool, str]:
    """
    Client-style probe: redirect-chain integrity + share-app fingerprint.

    Some Supercell locales (e.g. `pl/` as of 2026-04) ship a Next.js
    bundle that is missing the `clashofclans://` deep-link marker --
    a Supercell localization gap, not a layout-id problem.  When L3
    fails on a non-`en` URL, we retry with the lang swapped to `en/`
    (the layout id is what resolves; the lang only picks the share
    page's locale) and accept if that passes.
    """
    alive, detail, lang = _probe_one(link)
    if alive or lang in ("", "en"):
        return alive, detail
    if not detail.startswith("L3 "):
        return alive, detail
    en_link = re.sub(r"link\.clashofclans\.com/[a-z]{2}/", "link.clashofclans.com/en/", link)
    en_link = re.sub(r"link\.clashofclans\.com/[a-z]{2}\?", "link.clashofclans.com/en?", en_link)
    alive2, _, _ = _probe_one(en_link)
    if alive2:
        return True, f"L1+L2+L3 OK after en/ retry (original lang '{lang}' returned localised stub)"
    return False, detail


def parse_image_dimensions(data: bytes) -> tuple[int, int] | None:
    """
    Best-effort dimension extraction from the first ~1024 bytes of an
    image.  Returns (width, height) or None.
    """
    if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24:
        # IHDR is the first chunk; width @16-20, height @20-24, big-endian
        return (
            int.from_bytes(data[16:20], "big"),
            int.from_bytes(data[20:24], "big"),
        )
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP" and len(data) >= 30:
        # VP8 / VP8L / VP8X starts at byte 12
        chunk = data[12:16]
        if chunk == b"VP8 " and len(data) >= 30:
            # uncompressed lossy: width @26-28, height @28-30, little-endian, low 14 bits
            w = int.from_bytes(data[26:28], "little") & 0x3FFF
            h = int.from_bytes(data[28:30], "little") & 0x3FFF
            return (w, h)
        if chunk == b"VP8L" and len(data) >= 25:
            # lossless: 14-bit dims packed at bytes 21-25
            b1, b2, b3, b4 = data[21], data[22], data[23], data[24]
            w = ((b2 & 0x3F) << 8 | b1) + 1
            h = ((b4 & 0x0F) << 10 | b3 << 2 | (b2 & 0xC0) >> 6) + 1
            return (w, h)
        if chunk == b"VP8X" and len(data) >= 30:
            # extended: width-1 24-bit LE @24-27, height-1 24-bit LE @27-30
            w = int.from_bytes(data[24:27], "little") + 1
            h = int.from_bytes(data[27:30], "little") + 1
            return (w, h)
    if data[:3] == b"GIF" and len(data) >= 10:
        return (
            int.from_bytes(data[6:8], "little"),
            int.from_bytes(data[8:10], "little"),
        )
    if data[:3] == b"\xff\xd8\xff":
        # JPEG SOF0/SOF2 marker scan (limited to what we have)
        i = 2
        while i + 9 < len(data):
            if data[i] != 0xFF:
                i += 1
                continue
            marker = data[i + 1]
            if marker in (0xC0, 0xC1, 0xC2, 0xC3):
                # SOFn: skip marker(2) + len(2) + precision(1)
                return (
                    int.from_bytes(data[i + 7 : i + 9], "big"),
                    int.from_bytes(data[i + 5 : i + 7], "big"),
                )
            seg_len = int.from_bytes(data[i + 2 : i + 4], "big") if i + 4 < len(data) else 0
            if seg_len < 2:
                return None
            i += 2 + seg_len
        return None
    return None


def image_probe(url: str) -> tuple[bool, str, bytes | None]:
    """L4: confirm the URL serves a real PNG/JPEG/WebP/GIF/SVG image
    of at least MIN_IMAGE_DIMENSION x MIN_IMAGE_DIMENSION."""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return False, f"image URL is not http(s): {url!r}", None
    if parsed.netloc.lower() in IMAGE_HOST_BLACKLIST:
        return False, f"image host '{parsed.netloc}' is blacklisted (not a base preview)", None

    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "image/*,*/*;q=0.5",
            "Range": "bytes=0-1023",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=LIVENESS_TIMEOUT) as resp:
            data = resp.read(1024)
            status = resp.status
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}", None
    except Exception as e:
        return False, f"request failed: {e}", None
    if status >= 400:
        return False, f"HTTP {status}", None
    if not data:
        return False, "empty response", None

    label = None
    for magic, name in IMAGE_MAGIC:
        if data.startswith(magic):
            if name == "WEBP" and data[8:12] != b"WEBP":
                continue
            label = name
            break
    if label is None:
        return False, f"unrecognised magic bytes: {data[:16].hex()}", data

    if label != "SVG":
        dims = parse_image_dimensions(data)
        if dims is not None:
            w, h = dims
            if w < MIN_IMAGE_DIMENSION or h < MIN_IMAGE_DIMENSION:
                return (
                    False,
                    f"{label} {w}x{h} below {MIN_IMAGE_DIMENSION}px floor (placeholder?)",
                    data,
                )
            return True, f"OK {label} {w}x{h}", data
    return True, f"OK {label}", data


def _progress(label: str, done: int, total: int, ok: int, fail: int, last_at: list[float]) -> None:
    """Periodic progress line on stderr; throttled by count or interval."""
    now = time.monotonic()
    if done == total or done % PROGRESS_EVERY == 0 or now - last_at[0] > PROGRESS_INTERVAL_SEC:
        last_at[0] = now
        pct = done * 100 // total if total else 100
        print(
            f"  [{label}] {done}/{total} ({pct}%)  ok={ok} fail={fail}",
            file=sys.stderr,
            flush=True,
        )


def run_liveness(bases: list[dict], indices: list[int] | None = None) -> tuple[int, int]:
    targets = [(i, bases[i]["link"]) for i in (indices if indices is not None else range(len(bases)))]
    print(
        f"\nProbing {len(targets)} share link(s) with {LIVENESS_WORKERS} workers …",
        file=sys.stderr,
        flush=True,
    )
    ok = fail = done = 0
    last_at = [0.0]
    fail_lines: list[str] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=LIVENESS_WORKERS) as ex:
        futures = {ex.submit(liveness_probe, link): i for i, link in targets}
        for fut in concurrent.futures.as_completed(futures):
            i = futures[fut]
            alive, detail = fut.result()
            done += 1
            if alive:
                ok += 1
            else:
                fail += 1
                fail_lines.append(
                    f"  STALE  bases[{i}] id={bases[i].get('id', '?')}  -- {detail}"
                )
            _progress("liveness", done, len(targets), ok, fail, last_at)
    for line in fail_lines:
        print(line, file=sys.stderr)
    print(f"Liveness: {ok} alive / {fail} stale.")
    return ok, fail


def run_combined(bases: list[dict], indices: list[int] | None = None) -> int:
    """
    Run liveness and image probes as two independent thread pools at
    once.  Each pool has LIVENESS_WORKERS workers (16) so each host
    gets its full polite-concurrency budget without the two pools
    fighting for slots on the same host.  Wallclock ≈ max(liveness,
    images) instead of sum.
    """
    results: dict[str, tuple[int, int]] = {}

    def liveness_thread() -> None:
        results["liveness"] = run_liveness(bases, indices)

    def image_thread() -> None:
        results["images"] = run_image_check(bases, indices)

    t1 = threading.Thread(target=liveness_thread, name="liveness")
    t2 = threading.Thread(target=image_thread, name="images")
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    return results.get("liveness", (0, 0))[1] + results.get("images", (0, 0))[1]


def run_image_check(bases: list[dict], indices: list[int] | None = None) -> tuple[int, int]:
    pool = indices if indices is not None else range(len(bases))
    targets = [(i, bases[i]["image"]) for i in pool if bases[i].get("image")]
    print(
        f"\nProbing {len(targets)} image(s) with {LIVENESS_WORKERS} workers …",
        file=sys.stderr,
        flush=True,
    )
    ok = fail = done = 0
    last_at = [0.0]
    fail_lines: list[str] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=LIVENESS_WORKERS) as ex:
        futures = {ex.submit(image_probe, url): (i, url) for i, url in targets}
        for fut in concurrent.futures.as_completed(futures):
            i, _url = futures[fut]
            valid, detail, _magic = fut.result()
            done += 1
            if valid:
                ok += 1
            else:
                fail += 1
                fail_lines.append(
                    f"  BAD-IMG  bases[{i}] id={bases[i].get('id', '?')}  -- {detail}"
                )
            _progress("images", done, len(targets), ok, fail, last_at)
    for line in fail_lines:
        print(line, file=sys.stderr)
    print(f"Images: {ok} ok / {fail} bad.")
    return ok, fail


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("file", nargs="?", default="bases.json")
    ap.add_argument("--strict", action="store_true",
                    help="Promote warnings (W1-W4) to errors.")
    ap.add_argument("--liveness", action="store_true",
                    help="Client-style L1-L3 probe of every share link.")
    ap.add_argument("--check-images", action="store_true",
                    help="L4: HTTP-probe every image URL and confirm magic bytes + dimensions.")
    ap.add_argument("--strict-liveness", action="store_true",
                    help="Treat liveness/image failures as fatal.")
    ap.add_argument("--diff-against",
                    help="Path to a baseline bases.json.  Per-entry checks "
                         "(E1-E13, W1-W3, L1-L4) only run on entries new in "
                         "the current file (matched by `id`).  Catalogue-wide "
                         "uniqueness checks (E8/E10/E14) still run against "
                         "the whole file.  Used by CI on PRs to keep latency "
                         "bounded as the catalogue grows.")
    args = ap.parse_args()

    path = Path(args.file)
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        print(f"error: {path} is not valid JSON: {e}", file=sys.stderr)
        return 2

    if (
        not isinstance(data, dict)
        or "bases" not in data
        or not isinstance(data["bases"], list)
    ):
        print('error: top-level must be {"bases": [...]}', file=sys.stderr)
        return 1

    bases = data["bases"]
    errors: list[str] = []
    warnings: list[str] = []

    # In --diff-against mode, only entries new to the current file get
    # the per-entry expensive checks.  Catalogue-wide uniqueness still
    # runs over the full corpus so a PR can't slip in a duplicate of
    # an existing entry.
    new_entry_indices: set[int] | None = None
    if args.diff_against:
        try:
            base_data = json.loads(Path(args.diff_against).read_text())
            base_ids = {b["id"] for b in base_data.get("bases", []) if isinstance(b, dict)}
        except Exception as e:
            print(f"error: cannot read --diff-against {args.diff_against}: {e}",
                  file=sys.stderr)
            return 2
        new_entry_indices = {
            i for i, b in enumerate(bases)
            if isinstance(b, dict) and b.get("id") not in base_ids
        }
        print(
            f"--diff-against: {len(new_entry_indices)} new of {len(bases)} entries "
            f"(baseline had {len(base_ids)})"
        )

    def in_scope(i: int) -> bool:
        return new_entry_indices is None or i in new_entry_indices

    seen_ids: dict[str, int] = {}
    seen_links: dict[str, int] = {}
    seen_norm_links: dict[str, int] = {}
    seen_tags: dict[bytes, tuple[int, str, dict]] = {}
    seen_images: dict[str, int] = {}
    th_dist: Counter = Counter()
    slot_dist: Counter = Counter()
    lang_dist: Counter = Counter()

    def normalize_link(link: str) -> str:
        m = re.search(
            r"OpenLayout&(?:amp;)?id=(TH\d+%3A(?:WB|HV)%3A[A-Za-z0-9_\-]+)",
            link,
        )
        return m.group(1) if m else link

    for i, b in enumerate(bases):
        prefix = f"[bases[{i}] id={b.get('id', '?')}]"

        if not isinstance(b, dict):
            errors.append(f"{prefix} E9 entry is not an object")
            continue

        scope = in_scope(i)

        # ---- per-entry checks (skipped for unchanged entries in --diff-against) ----
        if scope:
            for field, expected in (
                ("id", str),
                ("name", str),
                ("town_hall", int),
                ("type", str),
                ("link", str),
            ):
                if field not in b or not isinstance(b[field], expected):
                    errors.append(
                        f"{prefix} E9 missing or wrong-type required field {field!r}"
                    )

            if not (1 <= int(b.get("town_hall", 0)) <= 20):
                errors.append(f"{prefix} E9 town_hall {b.get('town_hall')} out of range")

            btype = b.get("type")
            if isinstance(btype, str) and btype not in ALLOWED_TYPES:
                errors.append(
                    f"{prefix} E11 type {btype!r} not in whitelist "
                    f"({sorted(ALLOWED_TYPES)})"
                )

            image = b.get("image")
            if not image or not isinstance(image, str):
                errors.append(
                    f"{prefix} E12 image is required and must be a non-empty string"
                )
            else:
                host = urllib.parse.urlparse(image).netloc.lower()
                if host in IMAGE_HOST_BLACKLIST:
                    errors.append(
                        f"{prefix} E12 image host {host!r} is blacklisted "
                        f"(not a base preview)"
                    )

            link = b.get("link")
            if isinstance(link, str):
                errs, info = validate_link_payload(link, int(b.get("town_hall", 0)))
                for err in errs:
                    errors.append(f"{prefix} {err}")
                if info.get("lang") and info["lang"] not in KNOWN_LANG_PREFIXES:
                    warnings.append(
                        f"{prefix} W1 lang prefix '{info['lang']}' not on observed whitelist"
                    )

            w2 = cross_check_builder_id(b)
            if w2:
                warnings.append(f"{prefix} {w2}")
            w3 = cross_check_image_host(b)
            if w3:
                warnings.append(f"{prefix} {w3}")

        # ---- catalogue-wide uniqueness (always runs over all entries) ----
        bid = b.get("id")
        if isinstance(bid, str):
            if bid in seen_ids:
                errors.append(
                    f"{prefix} E10 duplicate id (also at bases[{seen_ids[bid]}])"
                )
            else:
                seen_ids[bid] = i

        link = b.get("link")
        if isinstance(link, str):
            if link in seen_links:
                errors.append(
                    f"{prefix} E10 duplicate link (also at bases[{seen_links[link]}])"
                )
            else:
                seen_links[link] = i

            nlink = normalize_link(link)
            if nlink in seen_norm_links:
                errors.append(
                    f"{prefix} E14 normalised-duplicate link "
                    f"(also at bases[{seen_norm_links[nlink]}]; same TH/village/blob, "
                    f"different lang or trailing-slash variant)"
                )
            else:
                seen_norm_links[nlink] = i

            # E8 tag collision is a catalogue-wide check, so it runs
            # for all entries even when we're diffing.  Use the fast
            # decoder when this entry is out of scope; reuse the full
            # validation result when we already parsed it.
            payload_info = info if scope else decode_link_payload(link)
            slot = payload_info.get("slot")
            lang = payload_info.get("lang")
            tag = payload_info.get("tag")
            if slot is not None:
                slot_dist[slot] += 1
            if lang:
                lang_dist[lang] += 1
            if tag is not None:
                if tag in seen_tags:
                    j, jid, jinfo = seen_tags[tag]
                    errors.append(
                        f"{prefix} E8 HMAC tag collides with bases[{j}] id={jid} "
                        f"(this={payload_info.get('th')}/{payload_info.get('village')}/slot={slot}/idx={payload_info.get('index')}; "
                        f"that={jinfo.get('th')}/{jinfo.get('village')}/slot={jinfo.get('slot')}/idx={jinfo.get('index')})"
                    )
                else:
                    seen_tags[tag] = (i, str(bid), payload_info)

        image = b.get("image")
        if image and isinstance(image, str):
            if image in seen_images:
                errors.append(
                    f"{prefix} E13 duplicate image URL (also at bases[{seen_images[image]}])"
                )
            else:
                seen_images[image] = i

        th_dist[b.get("town_hall")] += 1

    # ---- summary ----
    n = len(bases)
    if errors:
        for e in errors:
            print(e, file=sys.stderr)
        print(f"\nValidation failed: {len(errors)} error(s).", file=sys.stderr)
        return 1

    print(f"OK: {n} base(s) validated structurally.")
    print("Distribution by Town Hall:")
    for th in sorted(th_dist.keys(), reverse=True):
        print(f"  TH{th}: {th_dist[th]}")
    print("Slot distribution:")
    for s in sorted(slot_dist.keys()):
        print(f"  slot {s}: {slot_dist[s]}")
    print(f"Unique HMAC tags: {len(seen_tags)} / {n} (expect 1.0 ratio)")
    if lang_dist:
        top = ", ".join(f"{k}:{v}" for k, v in lang_dist.most_common(6))
        print(f"Top lang prefixes: {top}")

    if warnings:
        print(f"\n{len(warnings)} warning(s):", file=sys.stderr)
        for w in warnings:
            print(w, file=sys.stderr)
        if args.strict:
            print("\n--strict: warnings promoted to errors.", file=sys.stderr)
            return 1

    indices = sorted(new_entry_indices) if new_entry_indices is not None else None

    fail_total = 0
    if args.liveness and args.check_images:
        # Both probes are independent network I/O against different hosts;
        # interleave them on a single pool so we make full use of the
        # worker count instead of running ~70s liveness then ~50s images
        # back-to-back.
        fail_total += run_combined(bases, indices)
    else:
        if args.liveness:
            _, fail = run_liveness(bases, indices)
            fail_total += fail
        if args.check_images:
            _, fail = run_image_check(bases, indices)
            fail_total += fail

    if fail_total and args.strict_liveness:
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
