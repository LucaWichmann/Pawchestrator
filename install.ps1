$ErrorActionPreference = "Stop"

$Repo = "https://github.com/LucaWichmann/Pawchestrator.git"
$Package = "git+$Repo"

Write-Host "Pawchestrator installer"
Write-Host ""

# Check uv
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "Error: uv is required but not found."
    Write-Host "Install it from: https://docs.astral.sh/uv/getting-started/installation/"
    Write-Host ""
    Write-Host "Press Enter to exit ..."
    $null = Read-Host
    exit 1
}

# Install CLI
Write-Host ""
Write-Host "Installing Pawchestrator ..."
uv tool install $Package

# Run doctor
Write-Host ""
Write-Host "Running doctor ..."
Write-Host ""
pawchestrator doctor

Write-Host ""
Write-Host "Done. To start Pawchestrator:"
Write-Host "  pawchestrator serve"
Write-Host ""
Write-Host "To update later:"
Write-Host "  uv tool upgrade pawchestrator"
Write-Host ""
Write-Host "Contributor install path:"
Write-Host "  git clone $Repo"
Write-Host "  cd Pawchestrator; uv tool install --editable ."
Write-Host ""
Write-Host "Press Enter to exit ..."
$null = Read-Host
