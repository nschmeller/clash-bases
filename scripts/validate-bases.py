#!/usr/bin/env python3
"""
Validate bases.json -- schema, structural invariants of every COC layout
link, optional liveness probing against link.clashofclans.com, and
optional staleness detection.

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
`town_hall` field of the JSON entry.

Liveness probing
----------------
Pass `--liveness` to additionally fetch each layout URL from
link.clashofclans.com and inspect the response body.  Supercell does
*not* expose an "is this layout valid?" API, but the share endpoint
renders different OpenGraph metadata depending on whether it recognised
the id:

    - On a recognised layout the rendered page references the TH level
      and village type (HV/WB) somewhere in its body / meta tags.
    - On an unknown id the endpoint still returns 200 OK, but with the
      generic landing page that does *not* reference the requested TH.

We treat presence of the TH/village markers as evidence the layout is
real and currently resolvable.  This is heuristic -- the response shape
may change as Supercell updates the share endpoint -- so any liveness
failure is reported but does not by itself fail the run unless
`--strict-liveness` is passed.

Staleness
---------
Each successful liveness check stamps `last_verified: YYYY-MM-DD` on the
entry (with `--update-verified`).  `--max-age-days N` flags any entry
whose `last_verified` (or `added`, if it has never been verified) is
older than N days, so a periodic CI cron can surface entries that have
not been recently confirmed live.

Usage
-----
    python3 scripts/validate-bases.py [bases.json]
    python3 scripts/validate-bases.py bases.json --liveness
    python3 scripts/validate-bases.py bases.json --liveness --update-verified
    python3 scripts/validate-bases.py bases.json --max-age-days 90
"""

from __future__ import annotations

import argparse
import base64
import datetime as _dt
import json
import re
import sys
import urllib.parse
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


def parse_link_meta(link: str) -> tuple[int, str]:
    m = URL_RE.match(link)
    assert m, link
    return int(m.group("th")), m.group("v")


def liveness_probe(link: str, timeout: float = 10.0) -> tuple[bool, str]:
    """
    Probe `link` and decide whether the response indicates a recognised
    layout.

    Returns (alive, detail).  `alive=True` only when the response body
    references the URL's TH level and village type, which only happens
    when Supercell rendered a layout-specific preview.
    """
    th, village = parse_link_meta(link)
    req = urllib.request.Request(
        link,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = resp.status
            body = resp.read(200_000).decode("utf-8", errors="replace")
    except Exception as e:
        return False, f"request failed: {e}"

    if status >= 400:
        return False, f"HTTP {status}"

    body_l = body.lower()
    th_marker = f"th{th}".lower()
    full_id = link.split("id=", 1)[1] if "id=" in link else ""
    decoded_id = urllib.parse.unquote(full_id)

    # The recognised-layout response embeds the layout id verbatim in OG
    # tags / canonical URL. The fallback landing page does not.
    if decoded_id and decoded_id in body:
        return True, f"HTTP {status}; layout id present in response"
    if th_marker in body_l and village.lower() in body_l:
        return True, f"HTTP {status}; TH and village markers present"
    return False, f"HTTP {status}; response did not reference TH{th}/{village} (looks like fallback page)"


def is_stale(entry: dict, today: _dt.date, max_age_days: int) -> tuple[bool, str]:
    raw = entry.get("last_verified") or entry.get("added") or ""
    try:
        d = _dt.date.fromisoformat(raw)
    except ValueError:
        return True, "no usable last_verified or added date"
    age = (today - d).days
    if age > max_age_days:
        kind = "last_verified" if entry.get("last_verified") else "added"
        return True, f"{kind}={raw} is {age} days old (>{max_age_days})"
    return False, ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("file", nargs="?", default="bases.json")
    ap.add_argument(
        "--liveness",
        action="store_true",
        help="HTTP-probe each link.clashofclans.com URL.",
    )
    ap.add_argument(
        "--strict-liveness",
        action="store_true",
        help="Treat liveness failures as fatal errors.",
    )
    ap.add_argument(
        "--update-verified",
        action="store_true",
        help="Write last_verified=<today> on entries that pass liveness.",
    )
    ap.add_argument(
        "--max-age-days",
        type=int,
        default=0,
        help="Flag entries whose last_verified/added is older than N days (0 = off).",
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

    today = _dt.date.today()

    # --- Staleness check ---
    if args.max_age_days > 0:
        stale = 0
        for i, b in enumerate(data["bases"]):
            bad, reason = is_stale(b, today, args.max_age_days)
            if bad:
                print(
                    f"[bases[{i}] id={b.get('id', '?')}] STALE: {reason}",
                    file=sys.stderr,
                )
                stale += 1
        if stale:
            print(
                f"\nStaleness: {stale} entry/entries exceeded "
                f"{args.max_age_days}-day freshness budget.",
                file=sys.stderr,
            )
            return 1
        print(f"Freshness OK: every entry is within {args.max_age_days} days.")

    # --- Liveness probing ---
    if args.liveness:
        print("\nProbing liveness against link.clashofclans.com …")
        live_fails = 0
        live_ok = 0
        updated = 0
        for i, b in enumerate(data["bases"]):
            alive, detail = liveness_probe(b["link"])
            tag = "ALIVE" if alive else "STALE"
            print(f"  [{i+1}/{len(data['bases'])}] {tag} {b['id']}  -- {detail}")
            if alive:
                live_ok += 1
                if args.update_verified:
                    if b.get("last_verified") != today.isoformat():
                        b["last_verified"] = today.isoformat()
                        updated += 1
            else:
                live_fails += 1
        print(f"\nLiveness: {live_ok} alive / {live_fails} stale.")
        if updated:
            path.write_text(json.dumps(data, indent=2) + "\n")
            print(f"Updated last_verified on {updated} entry/entries.")
        if live_fails and args.strict_liveness:
            return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
