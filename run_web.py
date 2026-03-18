"""Launcher for the Verisafe Web Dashboard."""
import subprocess
import sys
from pathlib import Path

def main():
    root = Path(__file__).resolve().parent
    app_path = root / 'web' / 'app.py'
    
    # Try to find the local virtual environment python
    venv_python = root / '.venv' / 'Scripts' / 'python.exe'
    if not venv_python.exists():
        # Fallback for Linux/macOS or different venv structures
        venv_python = root / '.venv' / 'bin' / 'python'
    
    executable = str(venv_python) if venv_python.exists() else sys.executable

    if not app_path.exists():
        print(f"Error: Could not find app.py at {app_path}")
        return

    print("--- Starting Verisafe Web Dashboard ---")
    print(f"Using Python: {executable}")
    print("Dashboard will be available at: http://127.0.0.1:5000")
    print("Press Ctrl+C to stop the server.\n")

    try:
        # Run Flask app with the detected executable
        subprocess.run([executable, str(app_path)], check=True)
    except KeyboardInterrupt:
        print("\n\nDashboard server stopped.")
    except Exception as e:
        print(f"Error launching dashboard: {e}")

if __name__ == '__main__':
    main()
