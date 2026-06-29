"""
Launcher for the unified app — run:  python start_unified.py

Sets the system library path WeasyPrint needs on macOS (for chart + PDF rendering),
then starts the Streamlit app.
"""

import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main():
    env = os.environ.copy()

    try:
        prefix = subprocess.run(["brew", "--prefix"], capture_output=True, text=True).stdout.strip()
    except FileNotFoundError:
        prefix = ""
    if prefix:
        lib = f"{prefix}/lib"
        env["DYLD_FALLBACK_LIBRARY_PATH"] = lib + os.pathsep + env.get("DYLD_FALLBACK_LIBRARY_PATH", "")

    subprocess.run(
        [sys.executable, "-m", "streamlit", "run", str(HERE / "app_unified.py")],
        env=env,
    )


if __name__ == "__main__":
    main()
