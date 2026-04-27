#!/usr/bin/env python3
"""
Validate bases.json -- schema, structural invariants of every COC layout
link, and (optionally) basic reachability of the share endpoint.

Structural checks
-----------------
A public layout id of the form

    https://link.clashofclans.com/<lang>?action=OpenLayout&id=TH<n>%3A(HV|WB)%3A<blob>

always decodes to a fixed-size payload:

    - <blob> is exactly 32 url-safe base64 characters
    - the decoded payload is exactly 24 bytes
    - bytes 0..4 form a big-endian uint32 ("collection index")
    - bytes 4..8 form a big-endian uint32 in {1, 2, 3} -- the in-game
      layout slot (Layout 1 / Layout 2 / War Layout)
    - bytes 8..24 are an opaque 16-byte tag (likely an HMAC)

The TH<n> embedded in the URL is also cross-checked against the
`town_hall` field of the JSON entry.  Ids and links must be unique
across the catalogue.

Liveness probing (`--liveness`)
-------------------------------
Optional and informational only.  As of 2026-04 the Supercell share
endpoint serves a byte-identical 69_438-byte landing page for every
TH/blob combination -- including syntactically correct but
cryptographically invalid blobs -- so a 200 response only confirms the
endpoint is reachable, not that a specific id is real.  Verified by
comparing four known-good ids (FWA Basic/Ice/Rising Dawn TH18 and
KLAWKLA TH18) against an all-zero blob: identical body, identical
`<title>Clash of Clans</title>`, identical `og:image`.  Only the in-app
deep-link handler can resolve the HMAC.

The probe runs in parallel (16 workers); it can detect TLS / DNS / 4xx
regressions at the share endpoint.  Liveness failures are reported but
don't fail the run unless `--strict-liveness` is passed.

Usage
-----
    python3 scripts/validate-bases.py [bases.json]
    python3 scripts/validate-bases.py bases.json --liveness
    python3 scripts/validate-bases.py bases.json --liveness --strict-liveness
"""

from __future__ import annotations

import argparse
import base64
import concurrent.futures
import json
import re
import sys
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path

URL_RE = re.compile(
    r"^https://link\.clashofclans\.com/[a-z]{2}/?\?action=OpenLayout&id="
    r"TH(?P<th>\d{1,2})%3A(?P<v>HV|WB)%3A(?P<blob>[A-Za-z0-9_\-]+)$"
)
ALLOWED_SLOTS = {1, 2, 3}
EXPECTED_BLOB_CHARS = 32
EXPECTED_PAYLOAD_BYTES = 24
LIVENESS_WORKERS = 16
LIVENESS_TIMEOUT = 10.0

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)


def b64url_decode(blob: str) -> bytes:
    pad = "=" * ((4 - len(blob) % 4) % 4)
    return base64.urlsafe_b64decode(blob + pad)


def validate_link(link: str, declared_th: int) -> list[str]:
    errors: list[str] = []
    m = URL_RE.match(link)
    if not m:
        errors.append("link does not match expected layout URL shape")
        return errors

    th_in_url = int(m.group("th"))
    if th_in_url != declared_th:
        errors.append(
            f"TH in URL (TH{th_in_url}) does not match town_hall field ({declared_th})"
        )

    blob = m.group("blob")
    if len(blob) != EXPECTED_BLOB_CHARS:
        errors.append(
            f"id blob has {len(blob)} chars, expected {EXPECTED_BLOB_CHARS}"
        )

    try:
        payload = b64url_decode(blob)
    except Exception as e:
        errors.append(f"id blob is not valid base64url: {e}")
        return errors

    if len(payload) != EXPECTED_PAYLOAD_BYTES:
        errors.append(
            f"decoded payload is {len(payload)} bytes, expected {EXPECTED_PAYLOAD_BYTES}"
        )
        return errors

    slot = int.from_bytes(payload[4:8], "big")
    if slot not in ALLOWED_SLOTS:
        errors.append(
            f"layout slot is {slot}, expected one of {sorted(ALLOWED_SLOTS)}"
        )

    return errors


def liveness_probe(link: str) -> tuple[bool, str]:
    """Return (reachable, detail) for a single share-link URL."""
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
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}"
    except Exception as e:
        return False, f"request failed: {e}"

    if status >= 400:
        return False, f"HTTP {status}"
    return True, f"HTTP {status}"


def run_liveness(bases: list[dict]) -> tuple[int, int]:
    print(
        f"\nProbing {len(bases)} link(s) with {LIVENESS_WORKERS} workers …",
        file=sys.stderr,
    )
    ok = fail = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=LIVENESS_WORKERS) as ex:
        future_to_idx = {
            ex.submit(liveness_probe, b["link"]): i for i, b in enumerate(bases)
        }
        for fut in concurrent.futures.as_completed(future_to_idx):
            i = future_to_idx[fut]
            alive, detail = fut.result()
            if alive:
                ok += 1
            else:
                fail += 1
                print(
                    f"  STALE  bases[{i}] id={bases[i].get('id', '?')}  -- {detail}",
                    file=sys.stderr,
                )
    print(f"Liveness: {ok} alive / {fail} stale.")
    return ok, fail


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("file", nargs="?", default="bases.json")
    ap.add_argument(
        "--liveness",
        action="store_true",
        help="HTTP-probe each link.clashofclans.com URL (informational only).",
    )
    ap.add_argument(
        "--strict-liveness",
        action="store_true",
        help="Treat liveness failures as fatal errors.",
    )
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

    seen_ids: dict[str, int] = {}
    seen_links: dict[str, int] = {}
    fatal = 0
    th_dist: Counter = Counter()

    for i, b in enumerate(data["bases"]):
        prefix = f"[bases[{i}] id={b.get('id', '?')}]"
        if not isinstance(b, dict):
            print(f"{prefix} entry is not an object", file=sys.stderr)
            fatal += 1
            continue
        for field, expected in (
            ("id", str),
            ("name", str),
            ("town_hall", int),
            ("type", str),
            ("link", str),
        ):
            if field not in b or not isinstance(b[field], expected):
                print(
                    f"{prefix} missing or wrong-type required field {field!r}",
                    file=sys.stderr,
                )
                fatal += 1

        if not (1 <= int(b.get("town_hall", 0)) <= 20):
            print(
                f"{prefix} town_hall {b.get('town_hall')} out of range",
                file=sys.stderr,
            )
            fatal += 1

        bid = b.get("id")
        if isinstance(bid, str):
            if bid in seen_ids:
                print(
                    f"{prefix} duplicate id (also at bases[{seen_ids[bid]}])",
                    file=sys.stderr,
                )
                fatal += 1
            else:
                seen_ids[bid] = i

        link = b.get("link")
        if isinstance(link, str):
            if link in seen_links:
                print(
                    f"{prefix} duplicate link (also at bases[{seen_links[link]}])",
                    file=sys.stderr,
                )
                fatal += 1
            else:
                seen_links[link] = i

            errs = validate_link(link, int(b.get("town_hall", 0)))
            for err in errs:
                print(f"{prefix} {err}", file=sys.stderr)
                fatal += 1

        th_dist[b.get("town_hall")] += 1

    if fatal:
        print(f"\nValidation failed: {fatal} structural error(s).", file=sys.stderr)
        return 1

    print(f"OK: {len(data['bases'])} base(s) validated structurally.")
    print("Distribution by Town Hall:")
    for th in sorted(th_dist.keys(), reverse=True):
        print(f"  TH{th}: {th_dist[th]}")

    if args.liveness:
        _, fail = run_liveness(data["bases"])
        if fail and args.strict_liveness:
            return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
