@echo off
setlocal
set "PYTHONPATH=%~dp0"
python -m ai_test_runner.cli %*
