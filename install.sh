#!/usr/bin/env sh
set -e

REPO="https://github.com/LucaWichmann/Pawchestrator.git"
PACKAGE="git+$REPO"

echo "Pawchestrator installer"
echo ""

# Check uv
if ! command -v uv >/dev/null 2>&1; then
  echo "Error: uv is required but not found."
  echo "Install it from: https://docs.astral.sh/uv/getting-started/installation/"
  exit 1
fi

# Install CLI
echo ""
echo "Installing Pawchestrator ..."
uv tool install "$PACKAGE"

# Run doctor
echo ""
echo "Running doctor ..."
echo ""
pawchestrator doctor

echo ""
echo "Done. To start Pawchestrator:"
echo "  pawchestrator serve"
echo ""
echo "To update later:"
echo "  uv tool upgrade pawchestrator"
echo ""
echo "Contributor install path:"
echo "  git clone $REPO"
echo "  cd Pawchestrator && uv tool install --editable ."
echo ""
printf "Press Enter to exit ..."
read -r _
