#!/bin/bash
# Interactive flow: URL -> extract images (progress) -> review -> confirm -> PDF.
# slides.py itself is non-interactive.
set -e
cd "$(dirname "$0")"
PY=.venv/bin/python

read -r -p "YouTube URL: " url
[ -n "$url" ] || { echo "A URL is required."; exit 1; }
read -r -p "Session name: " name
[ -n "$name" ] || { echo "A session name is required."; exit 1; }

# Run directly on the terminal so the live progress lines render properly.
export PYTHONUNBUFFERED=1
$PY slides.py extract-images "$url" --name "$name" --verbose "$@"

# Newest session folder is the one just created.
folder=$(ls -td sessions/*/ 2>/dev/null | head -1)
folder=${folder%/}
[ -n "$folder" ] || { echo "Extraction failed."; exit 1; }

echo
echo "Review the photos and delete any slides you don't want."
read -r -p "Create PDF now? [y/n]: " answer
if [ "$answer" != "y" ]; then
  echo "Cancelled. Later run: $PY slides.py generate-pdf \"$folder\""
  exit 0
fi
$PY slides.py generate-pdf "$folder"
