@echo off
setlocal
cd /d "%~dp0"
set "HDH_PYTHON=.venv-hdh\Scripts\python.exe"
if not exist "%HDH_PYTHON%" set "HDH_PYTHON=.venv\Scripts\python.exe"
if not exist "%HDH_PYTHON%" (
  echo Install HDHcounsel using README.md first.
  exit /b 1
)
set "PATH=%CD%\tools\ffmpeg\bin;%PATH%"
"%HDH_PYTHON%" -I -m hdhcounsel.cli ui
