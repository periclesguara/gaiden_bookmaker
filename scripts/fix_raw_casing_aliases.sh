#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
raw_root="$root/data/raw"

if [[ ! -d "$raw_root" ]]; then
  echo "RAW dir not found: $raw_root"
  exit 0
fi

for book_dir in "$raw_root"/book_*; do
  [[ -d "$book_dir" ]] || continue
  for lang_dir in "$book_dir"/*; do
    [[ -d "$lang_dir" ]] || continue
    lang_base="$(basename "$lang_dir")"
    lower="$(echo "$lang_base" | tr 'A-Z' 'a-z')"
    lower="${lower//-/}"
    lower="${lower//_/}"
    if [[ "$lang_base" == "$lower" ]]; then
      continue
    fi
    target_dir="$book_dir/$lower"
    if [[ -d "$target_dir" ]]; then
      continue
    fi
    mkdir -p "$target_dir"
    for ext in txt md; do
      src="$lang_dir/source.$ext"
      dest="$target_dir/source.$ext"
      if [[ -f "$src" && ! -e "$dest" ]]; then
        ln -s "$src" "$dest" 2>/dev/null || cp -n "$src" "$dest"
      fi
    done
  done
done

echo "RAW casing aliases complete."
