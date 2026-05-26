#!/usr/bin/env sh
set -e

REPO="https://github.com/LucaWichmann/Pawchestrator.git"
DEST="$HOME/.pawchestrator-cli"

echo "Pawchestrator installer"
echo ""

# Check uv
if ! command -v uv >/dev/null 2>&1; then
  echo "Error: uv is required but not found."
  echo "Install it from: https://docs.astral.sh/uv/getting-started/installation/"
  exit 1
fi

# Clone or update
if [ -d "$DEST/.git" ]; then
  echo "Updating existing clone at $DEST ..."
  git -C "$DEST" pull --ff-only
else
  echo "Cloning to $DEST ..."
  git clone "$REPO" "$DEST"
fi

# Install dependencies
echo ""
echo "Installing dependencies ..."
cd "$DEST"
uv sync

# Run doctor
echo ""
echo "Running doctor ..."
echo ""
uv run pawchestrator doctor

echo ""
echo "Done. To start Pawchestrator:"
echo "  cd $DEST && uv run pawchestrator serve"
echo ""
printf "Press Enter to exit ..."
read -r _
