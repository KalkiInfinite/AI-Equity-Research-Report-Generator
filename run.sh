#!/usr/bin/env bash
# Launch the Streamlit app. On macOS, WeasyPrint needs the Homebrew libs
# (pango/glib) on the dynamic-loader path; we add it here so you don't have to.
set -e
cd "$(dirname "$0")"

if command -v brew >/dev/null 2>&1; then
  export DYLD_FALLBACK_LIBRARY_PATH="$(brew --prefix)/lib:${DYLD_FALLBACK_LIBRARY_PATH:-}"
fi

exec ./venv/bin/streamlit run app.py
