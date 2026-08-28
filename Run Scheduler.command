#!/bin/bash
# Double-click this file in Finder to open the scheduler in your web browser.
#
# First run takes a few minutes: it downloads the solver (about 100 MB) into a
# private folder under ~/Library/Application Support. Later runs start in seconds.
#
# The environment deliberately lives OUTSIDE this folder, because this project
# may sit in Dropbox and there is no reason to sync 100 MB of solver binaries.

# Finder launches scripts from an arbitrary working directory, and `import
# ParallelSched` depends on being in the project folder.
cd "$(dirname "$0")" || exit 1

APP_SUPPORT="$HOME/Library/Application Support/ParallelSched"
VENV="$APP_SUPPORT/venv"
PYTHON="$VENV/bin/python"

pause_on_exit() {
  echo
  echo "Press Return to close this window."
  read -r _
}

echo "Parallel session scheduler"
echo "=========================="
echo

# ---------------------------------------------------------------- find a python
if command -v python3 >/dev/null 2>&1; then
  BASE_PYTHON="$(command -v python3)"
else
  echo "Python 3 is not installed on this Mac, and it is needed to run the"
  echo "scheduler."
  echo
  echo "The easiest fix: install Python from https://www.python.org/downloads/"
  echo "then double-click this file again."
  pause_on_exit
  exit 1
fi

# Streamlit needs 3.9 or newer.
if ! "$BASE_PYTHON" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)'; then
  echo "Found $("$BASE_PYTHON" -V), but Python 3.9 or newer is required."
  echo "Install a current version from https://www.python.org/downloads/"
  pause_on_exit
  exit 1
fi

# ------------------------------------------------------------ build if needed
if [ ! -x "$PYTHON" ]; then
  echo "First-time setup. This downloads about 100 MB and may take a few minutes."
  echo "Installing into: $VENV"
  echo
  mkdir -p "$APP_SUPPORT" || { echo "Could not create $APP_SUPPORT"; pause_on_exit; exit 1; }
  if ! "$BASE_PYTHON" -m venv "$VENV"; then
    echo
    echo "Could not create the Python environment."
    pause_on_exit
    exit 1
  fi
fi

# Install or update dependencies whenever requirements.txt is newer than the
# marker we drop after a successful install.
STAMP="$VENV/.requirements-installed"
if [ ! -f "$STAMP" ] || [ requirements.txt -nt "$STAMP" ]; then
  echo "Checking dependencies..."
  "$PYTHON" -m pip install --quiet --upgrade pip
  if ! "$PYTHON" -m pip install --quiet -r requirements.txt; then
    echo
    echo "Could not install the dependencies. Are you online?"
    pause_on_exit
    exit 1
  fi
  touch "$STAMP"
  echo "Dependencies ready."
  echo
fi

# On its very first launch Streamlit interactively asks for an email address,
# which stalls startup and baffles anyone who just wanted a schedule. Writing the
# same file Streamlit would write after you press Return at that prompt skips it.
CREDENTIALS="$HOME/.streamlit/credentials.toml"
if [ ! -f "$CREDENTIALS" ]; then
  mkdir -p "$HOME/.streamlit"
  printf '[general]\nemail = ""\n' > "$CREDENTIALS"
fi

# ------------------------------------------------------------------- run it
echo "Starting the scheduler. It will open in your browser."
echo "Leave this window open while you work; close it to quit."
echo
"$PYTHON" -m streamlit run app.py --browser.gatherUsageStats false

# If streamlit exits immediately something went wrong, so keep the output visible.
pause_on_exit
