"""Reject unpinned native executables before collection or packaging."""
import argparse
import hashlib
import json
from pathlib import Path


def verify(directory, expected, deno_fallback=None):
    for name, checksum in expected.items():
        path = directory / name
        if name == 'deno.exe' and not path.exists() and deno_fallback:
            path = Path(deno_fallback)
        with path.open('rb') as source:
            actual = hashlib.file_digest(source, 'sha256').hexdigest()
        if actual != checksum:
            raise ValueError(f'{name}: pinned build checksum mismatch ({actual})')


if __name__ == '__main__':
    from deno import find_deno_bin
    parser = argparse.ArgumentParser()
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    lock = json.loads(Path(__file__).with_name('tools.lock.json').read_text(encoding='utf-8'))
    verify(args.directory, lock['files'], find_deno_bin())
    print(f"Pinned tools verified: FFmpeg {lock['ffmpeg_release']}; Deno {lock['deno_release']}")
