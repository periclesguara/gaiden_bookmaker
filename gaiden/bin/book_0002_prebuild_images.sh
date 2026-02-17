#!/usr/bin/env bash
set -euo pipefail

BOOK="book_0002"
LANGS=("en" "de" "es" "fr" "it" "ptbr")

# 1) Canonizar imagens do miolo a partir do EN upload
python gaiden/sync_images_from_en_to_shared.py --book "$BOOK"

# 2) Replicar shared para cada língua (pasta build)
for L in "${LANGS[@]}"; do
  python gaiden/sync_shared_images.py --book "$BOOK" --lang "$L" --clean
done

echo "[DONE] Images prebuild sync complete for $BOOK"
