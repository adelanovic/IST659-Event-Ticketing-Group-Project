@echo off
rem Start the website with the project's own Python environment.
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Setting up .venv for the first time...
    python -m venv .venv || exit /b 1
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt || exit /b 1
)
echo Open http://127.0.0.1:5000  (press Ctrl+C to stop)
".venv\Scripts\python.exe" app.py
