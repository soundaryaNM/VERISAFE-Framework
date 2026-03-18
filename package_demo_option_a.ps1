param(
  [Parameter(Mandatory=$true)]
  [string]$OutDir
)

$ErrorActionPreference = 'Stop'

function Resolve-FullPath([string]$path) {
  return (Resolve-Path -Path $path).Path
}

$repoRoot = $PSScriptRoot
$python = Join-Path $repoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
  throw "Python venv not found at $python. Create it first (e.g. python -m venv .venv; .venv\\Scripts\\python -m pip install -U pip)."
}

$outDirFull = $OutDir
if (-not (Test-Path $outDirFull)) {
  New-Item -ItemType Directory -Force -Path $outDirFull | Out-Null
}
$outDirFull = Resolve-FullPath $outDirFull

$tmpDir = Join-Path $env:TEMP ("UnitTestGen_demo_pack_" + [Guid]::NewGuid().ToString('N'))
$wheelsDir = Join-Path $tmpDir "wheels"
New-Item -ItemType Directory -Force -Path $wheelsDir | Out-Null

Write-Host "Building wheels into $wheelsDir" -ForegroundColor Cyan

$packages = @(
  @{ Name = "ai-c-test-analyzer"; Path = "CW_Test_Analyzer" },
  @{ Name = "ai-c-test-cov";      Path = "CW_Test_Cov" },
  @{ Name = "ai-c-test-generator"; Path = "CW_Test_Gen" },
  @{ Name = "ai-test-runner";     Path = "CW_Test_Run" }
)

foreach ($pkg in $packages) {
  $pkgPath = Join-Path $repoRoot $pkg.Path
  if (-not (Test-Path $pkgPath)) { throw "Missing package folder: $pkgPath" }
  & $python -m pip wheel --no-deps --wheel-dir $wheelsDir $pkgPath | Out-Null
}

# Assemble bundle
$bundleWheels = Join-Path $outDirFull "wheels"
New-Item -ItemType Directory -Force -Path $bundleWheels | Out-Null

Copy-Item -Force -Path (Join-Path $wheelsDir "*.whl") -Destination $bundleWheels

Copy-Item -Force -Path (Join-Path $repoRoot "run_demo.py") -Destination $outDirFull
Copy-Item -Force -Path (Join-Path $repoRoot "QUICK_START.md") -Destination $outDirFull -ErrorAction SilentlyContinue
Copy-Item -Force -Path (Join-Path $repoRoot "DEMO_READY.md") -Destination $outDirFull -ErrorAction SilentlyContinue
Copy-Item -Force -Path (Join-Path $repoRoot "README.md") -Destination $outDirFull -ErrorAction SilentlyContinue
Copy-Item -Force -Path (Join-Path $repoRoot "safety_policy.yaml") -Destination $outDirFull -ErrorAction SilentlyContinue

$installBat = @'
@echo off
setlocal enableextensions

REM Creates a local venv and installs the provided wheels.

set "ROOT=%~dp0"
cd /d "%ROOT%" || exit /b 1

if not exist ".venv" (
  python -m venv .venv || exit /b 1
)

call ".venv\Scripts\activate.bat" || exit /b 1
python -m pip install --upgrade pip || exit /b 1

REM Install required third-party dependencies (demo venv starts empty)
REM NOTE: Gemini support requires google-genai (provides google.genai)
python -m pip install --upgrade requests openai google-genai openpyxl gcovr || exit /b 1

REM Resolve wheel filenames (cmd does not expand globs for pip)
set "WHEELS=%ROOT%wheels"

for %%F in ("%WHEELS%\ai_c_test_analyzer-*.whl") do set "W_ANALYZER=%%~fF"
for %%F in ("%WHEELS%\ai_c_test_cov-*.whl") do set "W_COV=%%~fF"
for %%F in ("%WHEELS%\ai_c_test_generator-*.whl") do set "W_GEN=%%~fF"
for %%F in ("%WHEELS%\ai_test_runner-*.whl") do set "W_RUN=%%~fF"

if not defined W_ANALYZER (echo Missing analyzer wheel in %WHEELS% & exit /b 1)
if not defined W_COV (echo Missing coverage wheel in %WHEELS% & exit /b 1)
if not defined W_GEN (echo Missing generator wheel in %WHEELS% & exit /b 1)
if not defined W_RUN (echo Missing runner wheel in %WHEELS% & exit /b 1)

REM Install project wheels (order avoids inter-dependency hiccups)
python -m pip install "%W_ANALYZER%" || exit /b 1
python -m pip install "%W_COV%" || exit /b 1
python -m pip install "%W_GEN%" || exit /b 1
python -m pip install "%W_RUN%" || exit /b 1

echo.
echo Installed. Try: verisafe.bat "C:\path\to\your\repo"
'@

$verisafeBat = @'
@echo off
setlocal enableextensions enabledelayedexpansion

set "ROOT=%~dp0"
cd /d "%ROOT%" || exit /b 1

if not exist ".venv\Scripts\python.exe" (
  echo Missing .venv. Run install.bat first.
  pause
  exit /b 1
)

echo VERISAFE - AI-Assisted Safety Testing
echo.
REM Load .env file if it exists
if exist ".env" (
  REM Basic .env parser: KEY=VALUE (skips blank lines and comments). Strips surrounding quotes.
  for /f "usebackq eol=# tokens=1,* delims==" %%A in (".env") do (
    set "K=%%A"
    set "V=%%B"

    REM Trim spaces around key
    for /f "tokens=*" %%K in ("!K!") do set "K=%%K"

    REM Ignore empty keys
    if not "!K!"=="" (
      REM Strip quotes from value
      set "V=!V:^"=!"
      for /f "tokens=*" %%V in ("!V!") do set "V=%%V"

      if /I "!K!"=="GEMINI_API_KEY" set "GEMINI_API_KEY=!V!"
      if /I "!K!"=="GROQ_API_KEY" set "GROQ_API_KEY=!V!"
    )
  )
)

set /p "REPO=Enter the path to your repository (e.g., C:\path\to\repo): "
if "%REPO%"=="" (
  echo No repository path provided. Exiting.
  pause
  exit /b 2
)

REM Check for API key
if "%GEMINI_API_KEY%"=="" (
  echo.
  echo Gemini API key not found in environment.
  set /p "GEMINI_API_KEY=Enter your Gemini API key (or press Enter to skip): "
  if "%GEMINI_API_KEY%"=="" (
    echo No API key provided. Generation may fail.
  )
)

"%ROOT%.venv\Scripts\python.exe" "%ROOT%run_demo.py" --repo-path "%REPO%"

set "ERR=%ERRORLEVEL%"
if not "%ERR%"=="0" (
  echo.
  echo VERISAFE exited with error code %ERR%.
  echo (The output above should show the reason.)
  pause
)
'@

Set-Content -Path (Join-Path $outDirFull "install.bat") -Value $installBat -Encoding ASCII
Set-Content -Path (Join-Path $outDirFull "verisafe.bat") -Value $verisafeBat -Encoding ASCII

# Compatibility alias (older instructions may reference run_demo.bat)
Set-Content -Path (Join-Path $outDirFull "run_demo.bat") -Value $verisafeBat -Encoding ASCII

Write-Host "Demo bundle created at: $outDirFull" -ForegroundColor Green
Write-Host "Contents: wheels\\, install.bat, run_demo.bat, run_demo.py" -ForegroundColor Green

Remove-Item -Recurse -Force $tmpDir
