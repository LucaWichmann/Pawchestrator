$ErrorActionPreference = "Stop"

$Repo = "https://github.com/LucaWichmann/Pawchestrator.git"
$Dest = "$HOME\.pawchestrator-cli"

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

# Clone or update
if (Test-Path "$Dest\.git") {
    Write-Host "Updating existing clone at $Dest ..."
    git -C $Dest pull --ff-only
} else {
    Write-Host "Cloning to $Dest ..."
    git clone $Repo $Dest
}

# Install dependencies
Write-Host ""
Write-Host "Installing dependencies ..."
Set-Location $Dest
uv sync

# Run doctor
Write-Host ""
Write-Host "Running doctor ..."
Write-Host ""
uv run pawchestrator doctor

Write-Host ""
Write-Host "Done. To start Pawchestrator:"
Write-Host "  cd $Dest; uv run pawchestrator serve"
Write-Host ""
Write-Host "Press Enter to exit ..."
$null = Read-Host
