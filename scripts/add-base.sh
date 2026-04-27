#!/usr/bin/env bash
# Interactively append a new entry to bases.json.
#
# Usage: ./scripts/add-base.sh
#
# Reads the in-game share link from the clipboard (or prompts) and asks
# for the remaining fields. Runs the Python validator on the candidate
# file before committing the write, so a malformed entry never lands
# in bases.json.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FILE="$ROOT/bases.json"
TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT

if ! command -v jq >/dev/null 2>&1; then
  echo "error: jq is required" >&2
  exit 2
fi

read_clipboard() {
  if command -v pbpaste >/dev/null 2>&1; then pbpaste
  elif command -v wl-paste >/dev/null 2>&1; then wl-paste
  elif command -v xclip >/dev/null 2>&1; then xclip -selection clipboard -o
  else return 1
  fi
}

prompt() {
  local var="$1" msg="$2" default="${3:-}"
  local val
  if [ -n "$default" ]; then
    read -r -p "$msg [$default]: " val
    val="${val:-$default}"
  else
    read -r -p "$msg: " val
  fi
  printf -v "$var" '%s' "$val"
}

prompt_required() {
  local var="$1" msg="$2"
  while :; do
    prompt "$var" "$msg"
    if [ -n "${!var}" ]; then return; fi
    echo "  (required)"
  done
}

# Try clipboard first
clip_link="$(read_clipboard 2>/dev/null || true)"
case "$clip_link" in
  https://link.clashofclans.com/*) ;;
  *) clip_link="" ;;
esac

if [ -n "$clip_link" ]; then
  echo "Detected layout link in clipboard:"
  echo "  $clip_link"
  read -r -p "Use this link? [Y/n]: " yn
  case "$yn" in n|N) clip_link="" ;; esac
fi

if [ -z "$clip_link" ]; then
  while :; do
    prompt link "Layout link (https://link.clashofclans.com/...)"
    case "$link" in
      https://link.clashofclans.com/*) break ;;
      *) echo "  must start with https://link.clashofclans.com/" ;;
    esac
  done
else
  link="$clip_link"
fi

prompt_required name "Base name"
prompt_required town_hall "Town hall (e.g. 16)"
case "$town_hall" in
  *[!0-9]*) echo "town_hall must be an integer" >&2; exit 1 ;;
esac
if [ "$town_hall" -lt 1 ] || [ "$town_hall" -gt 20 ]; then
  echo "town_hall must be between 1 and 20" >&2
  exit 1
fi

prompt type "Type" "War"
prompt builder "Builder credit (optional)"
prompt description "One-line description (optional)"
prompt tags_raw "Tags, comma-separated (optional)"
prompt added "Date added (YYYY-MM-DD)" "$(date +%Y-%m-%d)"

slug() {
  printf '%s' "$1" | tr '[:upper:]' '[:lower:]' \
    | sed -E 's/[^a-z0-9]+/-/g; s/^-+|-+$//g'
}
id="th${town_hall}-$(slug "$type")-$(slug "$name")"
prompt id "Unique id" "$id"

# Tags -> JSON array
if [ -n "${tags_raw:-}" ]; then
  tags_json=$(printf '%s' "$tags_raw" | jq -R 'split(",") | map(gsub("^\\s+|\\s+$"; "")) | map(select(length > 0))')
else
  tags_json='[]'
fi

jq --arg id "$id" \
   --arg name "$name" \
   --argjson town_hall "$town_hall" \
   --arg type "$type" \
   --arg link "$link" \
   --arg description "$description" \
   --arg builder "$builder" \
   --argjson tags "$tags_json" \
   --arg added "$added" \
   '.bases += [{
        id: $id,
        name: $name,
        town_hall: $town_hall,
        type: $type,
        link: $link,
        description: $description,
        builder: $builder,
        tags: $tags,
        added: $added
      }]' "$FILE" > "$TMP"

if python3 "$ROOT/scripts/validate-bases.py" "$TMP" >/dev/null; then
  mv "$TMP" "$FILE"
  echo
  echo "Added '$name' to bases.json."
else
  echo "Validation failed -- not writing." >&2
  exit 1
fi
