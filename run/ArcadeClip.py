"""Launch the desktop app from any working directory."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

if __name__ == "__main__":
    from launcher import launch
    raise SystemExit(launch())
