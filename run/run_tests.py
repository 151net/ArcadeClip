"""Run the test suite under a watchdog: python run/run_tests.py [--no-display]

A build machine has no audio endpoint and no real display, so a test that waits on
one can block until the whole job is killed. The watchdog prints every thread's
stack and exits instead, which names the test that stopped rather than timing out
with no output at all. --no-display leaves out the tests that drive a window or
media playback, so a release does not depend on hardware the runner lacks.
"""
from pathlib import Path
import faulthandler
import os
import sys
import unittest

# The app sets this in its launcher; the tests import numpy without going through it.
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")

TESTS = Path(__file__).resolve().parents[1] / "src/tests"
# The only modules that open a window or play media; the rest are plain logic.
DISPLAY = ("test_app", "test_preview")
TIMEOUT = float(os.environ.get("ARCADECLIP_TEST_TIMEOUT", 600))


def suite(display=True):
    names = sorted(path.stem for path in TESTS.glob("test_*.py"))
    if not display:
        names = [name for name in names if name not in DISPLAY]
    return unittest.defaultTestLoader.loadTestsFromNames(names)


def main(arguments):
    display = "--no-display" not in arguments
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    # Some tests spawn a child that imports the app modules off the working directory.
    os.chdir(TESTS.parent)
    sys.path.insert(0, str(TESTS.parent))
    sys.path.insert(0, str(TESTS))
    # exit=True: a hung test holds the GIL only intermittently, so asking politely can hang too.
    faulthandler.dump_traceback_later(TIMEOUT, exit=True)
    print(f"watchdog {TIMEOUT:.0f}s · display tests {'included' if display else 'left out'}", flush=True)
    result = unittest.TextTestRunner(verbosity=2, buffer=False).run(suite(display))
    faulthandler.cancel_dump_traceback_later()
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
