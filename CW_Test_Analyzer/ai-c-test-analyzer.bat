@echo off
REM AI C Test Analyzer Batch Script

if "%1"=="" goto :usage

python -m ai_c_test_analyzer.cli %*
goto :eof

:usage
echo Usage: ai-c-test-analyzer.bat --repo-path ^<path^> [--verbose] [--wait-before-exit]
echo.
echo Example: ai-c-test-analyzer.bat --repo-path ..\Door-Monitoring --verbose
pause