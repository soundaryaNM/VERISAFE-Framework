# vendor_googletest.ps1
# Copy the bundled `third_party/googletest` into the repository harness overlay.
# Run this from the repository root (UnitTestGenLocal) in PowerShell.
$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Definition
$thirdParty = Join-Path $scriptRoot 'tools\\third_party\\googletest-1.17.0'
if (-not (Test-Path $thirdParty)) {
    Write-Error "Bundled googletest not found at: $thirdParty"
    Write-Output "If you want to vendor googletest, place a copy under $scriptRoot\\third_party\\googletest"
    exit 1
}
# Ensure destination overlay directories exist
New-Item -Path .verisafe -ItemType Directory -Force | Out-Null
New-Item -Path .verisafe\extern -ItemType Directory -Force | Out-Null

function Copy-Tree($src, $dst) {
    if (Test-Path $dst) { Remove-Item -Recurse -Force $dst }
    Copy-Item -Path $src -Destination $dst -Recurse -Force -ErrorAction Stop
}

try {
    $dest1 = Join-Path (Get-Location) '.verisafe\extern\googletest'
    Write-Output "Copying $thirdParty -> $dest1"
    Copy-Tree $thirdParty $dest1
    Write-Output "Done. Refresh the harness/build."
} catch {
    Write-Error "Failed to copy googletest: $_"
    exit 1
}