@echo off
REM Local workspace entrypoint for the generator CLI.
REM Usage:
REM   ai-test-gen --safety-level SIL2 --repo-path RailwaySignalSystem

setlocal
set REPO_ROOT=%~dp0
set PY=%REPO_ROOT%.venv\Scripts\python.exe

if not exist "%PY%" (
  echo [ERROR] Python venv not found at "%PY%" 1>&2
  echo         Create it in this workspace as .venv, or run: python -m ai_c_test_generator.cli ... 1>&2
  exit /b 2
)

"%PY%" -m ai_c_test_generator.cli %*
exit /b %ERRORLEVEL%
