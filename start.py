"""
Simple launcher — just run:  python start.py

This sets the system library path WeasyPrint needs on macOS, then starts the
Streamlit app. Use this instead of `streamlit run app.py` directly.
"""

import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main():
    env = os.environ.copy()

    # On macOS, point the dynamic loader at Homebrew's libs (pango/glib) so
    # WeasyPrint can generate PDFs. Harmless on other platforms.
    try:
        prefix = subprocess.run(["brew", "--prefix"], capture_output=True, text=True).stdout.strip()
    except FileNotFoundError:
        prefix = ""
    if prefix:
        lib = f"{prefix}/lib"
        env["DYLD_FALLBACK_LIBRARY_PATH"] = lib + os.pathsep + env.get("DYLD_FALLBACK_LIBRARY_PATH", "")

    # Launch Streamlit as a fresh subprocess so it inherits the env above.
    subprocess.run(
        [sys.executable, "-m", "streamlit", "run", str(HERE / "app.py")],
        env=env,
    )


if __name__ == "__main__":
    main()
