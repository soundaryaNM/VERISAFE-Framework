@echo off
REM Create a Python virtual environment in .venv and install requirements

REM Check for Python
where python >nul 2>nul
if errorlevel 1 (
    echo Python is not installed or not in PATH.
    exit /b 1
)

REM Create venv if it doesn't exist
if not exist .venv (
    python -m venv .venv
)

REM Activate venv
call .venv\Scripts\activate.bat

REM Upgrade pip
python -m pip install --upgrade pip

REM Install requirements if requirements.txt exists
if exist requirements.txt (
    pip install -r requirements.txt
)

REM Install editable package only if a src/ folder exists (matches pyproject layout)
if exist pyproject.toml (
    if exist src (
        pip install -e .
    ) else (
        echo Skipping editable install: src\ folder not found.
    )
)

REM Install sub-packages (root has no src/ layout)
for %%D in (CW_Test_Analyzer CW_Test_Cov CW_Test_Gen CW_Test_Run) do (
    if exist "%%D\pyproject.toml" (
        echo Installing %%D...
        pushd "%%D"
        pip install -e .
        popd
    )
)

echo Virtual environment setup complete.
