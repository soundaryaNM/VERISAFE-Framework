# clone_gtest.ps1
$dest = Join-Path (Get-Location) 'third_party\googletest'
if (Test-Path $dest) {
    Write-Output 'third_party/googletest already exists; skipping clone.'
    exit 0
}
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Write-Error 'git not available in PATH; cannot clone. Please install git or provide a local copy.'
    exit 2
}
Write-Output 'Cloning googletest into third_party/googletest (this requires network and git)...'
$proc = Start-Process git -ArgumentList @('clone','--depth','1','https://github.com/google/googletest.git',$dest) -NoNewWindow -Wait -PassThru
if ($proc.ExitCode -ne 0) {
    Write-Error "git clone failed with exit code $($proc.ExitCode)"
    exit 3
}
Write-Output 'Clone completed.'
