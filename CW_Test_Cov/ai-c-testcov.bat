@echo off
setlocal
REM Resolve repo_path to an absolute path before changing directories.
set "REPO_PATH="
if not "%~1"=="" (
	for %%I in ("%~1") do set "REPO_PATH=%%~fI"
	shift
)
set "REST_ARGS=%*"

pushd "%~dp0"

REM Prefer the workspace venv if present.
if exist "..\.venv\Scripts\python.exe" (
	"..\.venv\Scripts\python.exe" -m ai_c_test_coverage.cli "%REPO_PATH%" %REST_ARGS%
) else (
	python -m ai_c_test_coverage.cli "%REPO_PATH%" %REST_ARGS%
)

popd
endlocal
