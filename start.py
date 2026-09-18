"""Run with uv run .\\start.py [--debug]."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

if __name__ == "__main__":
    from launcher import launch
    raise SystemExit(launch())
