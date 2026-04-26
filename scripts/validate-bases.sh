#!/usr/bin/env bash
# Sanity-check bases.json before deploy.
#
# This validates the *structure* of every entry (required fields, types,
# unique ids, well-formed link). It cannot verify that a layout link is
# still resolvable in-game -- Supercell does not expose a public API for
# that, and the link.clashofclans.com endpoint returns the same landing
# page for every id.

set -euo pipefail

FILE="${1:-bases.json}"

if ! command -v jq >/dev/null 2>&1; then
  echo "error: jq is required" >&2
  exit 2
fi

# 1. JSON parses
jq -e . "$FILE" >/dev/null

# 2. Top-level shape: { "bases": [...] }
jq -e 'has("bases") and (.bases | type == "array")' "$FILE" >/dev/null \
  || { echo "error: top-level must be {\"bases\": [...]}"; exit 1; }

# 3. Per-entry checks
errors=$(jq -r '
  .bases as $bs
  | [
      ($bs | to_entries[] | . as $e | $e.value | [
          (if .id and (.id | type == "string") then empty else "[\($e.key)] missing string id" end),
          (if .name and (.name | type == "string") then empty else "[\($e.key)] missing string name" end),
          (if .town_hall and (.town_hall | type == "number") then empty else "[\($e.key)] missing numeric town_hall" end),
          (if .type and (.type | type == "string") then empty else "[\($e.key)] missing string type" end),
          (if .link and (.link | type == "string") and (.link | startswith("https://link.clashofclans.com/")) then empty else "[\($e.key)] link must start with https://link.clashofclans.com/" end)
        ] | .[]
      )
    ]
  | .[]
' "$FILE")

if [ -n "$errors" ]; then
  echo "Validation errors:" >&2
  echo "$errors" >&2
  exit 1
fi

# 4. Unique ids
dupes=$(jq -r '.bases | group_by(.id) | map(select(length>1) | .[0].id) | .[]' "$FILE")
if [ -n "$dupes" ]; then
  echo "Validation errors: duplicate ids:" >&2
  echo "$dupes" >&2
  exit 1
fi

count=$(jq '.bases | length' "$FILE")
echo "OK: $count base(s) validated."
