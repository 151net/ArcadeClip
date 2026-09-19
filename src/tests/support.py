"""Shared helper for tests that drive a child process."""
import subprocess
import tempfile
from pathlib import Path


def run_child(argv, timeout, **options):
    """Run a child to completion and return (returncode, stdout, stderr).

    Not subprocess.run: on Windows its timeout kills the child and then waits on the
    pipes with no limit of its own, so a grandchild holding them blocks for good. A
    killed child reports None, and files keep whatever it printed either way.
    """
    # ignore_cleanup_errors: a surviving grandchild still holds these files open.
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
        out, err = Path(directory) / "out.txt", Path(directory) / "err.txt"
        with out.open("wb") as to_out, err.open("wb") as to_err:
            process = subprocess.Popen(argv, stdout=to_out, stderr=to_err, **options)
            try:
                code = process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
                code = None
        return (code, *(path.read_text(encoding="utf-8", errors="replace") for path in (out, err)))
