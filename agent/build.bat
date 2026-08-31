@echo off
REM Build the OpenPatch agent executable.
REM Requires the build dependencies:  pip install -r requirements-dev.txt
setlocal
cd /d "%~dp0.."
python packaging\build.py agent %*
endlocal
