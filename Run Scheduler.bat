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
set "VENV=%APPDIR%\venv-win-x64"
set "PYTHON=%VENV%\Scripts\python.exe"

echo Parallel session scheduler
echo ==========================
echo.

REM Windows lets you double-click a file from inside a ZIP without extracting it,
REM which copies just that one file to a temp folder. Catch that first, because
REM the errors it causes further down make no sense to anyone.
if not exist "app.py" (
    echo It looks like this was run from inside the ZIP file, so the rest of the
    echo scheduler is not here.
    echo.
    echo To fix it:
    echo.
    echo    1. Close this window
    echo    2. Find the downloaded ZIP file, right-click it, choose "Extract All..."
    echo    3. Open the folder that appears
    echo    4. Double-click "Run Scheduler" inside that folder
    echo.
    pause
    exit /b 1
)

REM Keep the old venv untouched: it may have been created with incompatible Python.
if exist "%PYTHON%" (
    "%PYTHON%" -c "import sys, sysconfig; sys.exit(0 if sys.implementation.name == 'cpython' and (3, 9) <= sys.version_info[:2] <= (3, 14) and sysconfig.get_platform() == 'win-amd64' else 1)" >nul 2>&1
    if errorlevel 1 (
        echo The scheduler's Python environment is broken or incompatible:
        echo %VENV%
        echo Close this window and rename that folder, then run the scheduler again.
        echo Your meeting files will not be affected.
        pause
        exit /b 1
    )
    goto environment_ready
)
if exist "%VENV%" (
    echo The scheduler's Python environment is incomplete:
    echo %VENV%
    echo Close this window and rename that folder, then run the scheduler again.
    echo Your meeting files will not be affected.
    pause
    exit /b 1
)

REM ------------------------------------------------------------ find a python
REM Do not let the Python install manager install a runtime during detection.
set "PYTHON_MANAGER_AUTOMATIC_INSTALL=0"
set "BASE_PYTHON="
for %%V in (3.11 3.12 3.13 3.14 3.10 3.9) do (
    if not defined BASE_PYTHON (
        py -%%V-64 -c "import sys, sysconfig; sys.exit(0 if sys.implementation.name == 'cpython' and (3, 9) <= sys.version_info[:2] <= (3, 14) and sysconfig.get_platform() == 'win-amd64' else 1)" >nul 2>&1
        if not errorlevel 1 set "BASE_PYTHON=py -%%V-64"
    )
)
if not defined BASE_PYTHON (
    python -c "import sys, sysconfig; sys.exit(0 if sys.implementation.name == 'cpython' and (3, 9) <= sys.version_info[:2] <= (3, 14) and sysconfig.get_platform() == 'win-amd64' else 1)" >nul 2>&1
    if not errorlevel 1 set "BASE_PYTHON=python"
)

if not defined BASE_PYTHON (
    echo A compatible 64-bit x86 Python 3.9 through 3.14 was not found.
    echo The scheduler recommends Python 3.11.
    echo.
    echo Install the Python install manager from the page opening now.
    echo Then open Command Prompt and run:
    echo.
    echo    py install 3.11-64
    echo.
    echo Then double-click "Run Scheduler" again.
    echo.
    start "" https://www.python.org/downloads/windows/
    pause
    exit /b 1
)

REM ---------------------------------------------------------- build if needed
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

:environment_ready
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
        echo Could not install the dependencies. Check the error above.
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
