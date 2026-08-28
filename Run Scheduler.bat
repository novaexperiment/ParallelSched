@echo off
REM Double-click this file to open the scheduler in your web browser.
REM
REM First run takes a few minutes: it downloads the solver (about 100 MB) into
REM a private folder under %LOCALAPPDATA%. Later runs start in seconds.
REM
REM The environment deliberately lives OUTSIDE this folder, because this project
REM may sit in Dropbox and there is no reason to sync 100 MB of solver binaries.

setlocal
cd /d "%~dp0"

set "APPDIR=%LOCALAPPDATA%\ParallelSched"
set "VENV=%APPDIR%\venv"
set "PYTHON=%VENV%\Scripts\python.exe"

echo Parallel session scheduler
echo ==========================
echo.

REM ------------------------------------------------------------ find a python
set "BASE_PYTHON="
for %%P in (py python) do (
    if not defined BASE_PYTHON (
        %%P -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>&1
        if not errorlevel 1 set "BASE_PYTHON=%%P"
    )
)

if not defined BASE_PYTHON (
    echo Python 3.9 or newer was not found, and it is needed to run the scheduler.
    echo.
    echo Install Python from https://www.python.org/downloads/ and be sure to
    echo tick "Add Python to PATH" during setup, then double-click this file again.
    echo.
    pause
    exit /b 1
)

REM ---------------------------------------------------------- build if needed
if not exist "%PYTHON%" (
    echo First-time setup. This downloads about 100 MB and may take a few minutes.
    echo Installing into: %VENV%
    echo.
    if not exist "%APPDIR%" mkdir "%APPDIR%"
    %BASE_PYTHON% -m venv "%VENV%"
    if errorlevel 1 (
        echo.
        echo Could not create the Python environment.
        pause
        exit /b 1
    )
)

REM Reinstall when requirements.txt is newer than our success marker.
set "STAMP=%VENV%\.requirements-installed"
set "NEEDS_INSTALL="
if not exist "%STAMP%" set "NEEDS_INSTALL=1"
if not defined NEEDS_INSTALL (
    for /f %%I in ('dir /b /o-d "requirements.txt" "%STAMP%" 2^>nul ^| findstr /n .') do (
        if "%%I"=="1:requirements.txt" set "NEEDS_INSTALL=1"
    )
)

if defined NEEDS_INSTALL (
    echo Checking dependencies...
    "%PYTHON%" -m pip install --quiet --upgrade pip
    "%PYTHON%" -m pip install --quiet -r requirements.txt
    if errorlevel 1 (
        echo.
        echo Could not install the dependencies. Are you online?
        pause
        exit /b 1
    )
    echo. > "%STAMP%"
    echo Dependencies ready.
    echo.
)

REM On its very first launch Streamlit interactively asks for an email address,
REM which stalls startup and baffles anyone who just wanted a schedule. Writing
REM the same file Streamlit would write after you press Enter there skips it.
if not exist "%USERPROFILE%\.streamlit\credentials.toml" (
    if not exist "%USERPROFILE%\.streamlit" mkdir "%USERPROFILE%\.streamlit"
    (
        echo [general]
        echo email = ""
    ) > "%USERPROFILE%\.streamlit\credentials.toml"
)

REM ------------------------------------------------------------------- run it
echo Starting the scheduler. It will open in your browser.
echo Leave this window open while you work; close it to quit.
echo.
"%PYTHON%" -m streamlit run app.py --browser.gatherUsageStats false

echo.
pause
