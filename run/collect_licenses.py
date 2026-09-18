"""Collect installed runtime dependency notices for the offline About dialog."""
import json
import argparse
import os
import subprocess
import sys
import tomllib
from importlib.metadata import distribution
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ROOT / 'run/license_sources'


def upstream_notices(names):
    urls = json.loads((SOURCES / 'sources.json').read_text(encoding='utf-8'))
    return [{'file': urls[name], 'text': (SOURCES / name).read_text(encoding='utf-8')} for name in names]


def collect_external(tools_directory=None):
    sys.path.insert(0, str(ROOT / 'src'))
    from sources import find_tool
    entries = []
    for name in ('ffmpeg', 'ffprobe', 'deno'):
        filename = name + ('.exe' if os.name == 'nt' else '')
        bundled = tools_directory / filename if tools_directory else None
        # Deno can be supplied by the Python package when not in the tools folder.
        tool = str(bundled) if bundled and (name != 'deno' or bundled.is_file()) else None
        try:
            tool = tool or find_tool(name)
        except RuntimeError:
            if tools_directory:
                raise
        flags = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
        def output(option):
            result = subprocess.run([tool, option], capture_output=True, text=True,
                                    encoding='utf-8', errors='replace', timeout=15, check=True, **flags)
            return (result.stdout + result.stderr).strip()
        version = output('--version' if name == 'deno' else '-version') if tool else 'Not installed at collection time'
        notices = [{'file': 'Collected binary version and build configuration', 'text': version}]
        if name == 'deno':
            notices += upstream_notices(['Deno-LICENSE.txt'])
            license_name = 'MIT'
            homepage = 'https://deno.com/'
        else:
            if tool:
                notices.append({'file': name + ' -L', 'text': output('-L')})
            notices += upstream_notices(['LICENSE.md', 'COPYING.GPLv2', 'COPYING.GPLv3', 'COPYING.LGPLv2.1', 'COPYING.LGPLv3'])
            license_name = 'Build-specific: see -L notice; GPL/LGPL reference texts below'
            homepage = 'https://ffmpeg.org/legal.html'
        entries.append({'name': name + ' (executable)', 'version': version.splitlines()[0],
                        'license': license_name, 'homepage': homepage, 'urls': [], 'notices': notices})
    if tools_directory:
        # Keep distributors' additional notices alongside the actual build report.
        notices = []
        for path in sorted(tools_directory.rglob('*')):
            if (path.is_file() and any(word in path.name.lower() for word in ('license', 'copying', 'notice', 'copyright'))
                    and path.suffix.lower() in ('', '.txt', '.md', '.rst')):
                notices.append({'file': str(path.relative_to(tools_directory)), 'text': path.read_text(encoding='utf-8', errors='replace')})
        if notices:
            entries.append({'name': 'Tools distribution notices', 'version': '', 'license': '',
                            'homepage': '', 'urls': [], 'notices': notices})
    entries.append({'name': 'Python runtime', 'version': sys.version.split()[0], 'license': 'PSF-2.0 and included notices',
                    'homepage': 'https://www.python.org/', 'urls': [], 'notices': [
                        {'file': 'Python LICENSE.txt', 'text': (Path(sys.base_prefix) / 'LICENSE.txt').read_text(encoding='utf-8')}]})
    for language in ('korean', 'ch', 'en'):
        model = language + '_PP-OCRv5_rec_mobile.onnx'
        entries.append({'name': model, 'version': 'RapidOCR v3.9.2 / PP-OCRv5', 'license': 'Apache-2.0',
                        'homepage': 'https://github.com/RapidAI/RapidOCR',
                        'urls': ['Model: https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve/v3.9.2/onnx/PP-OCRv5/rec/' + model,
                                 'Original models: https://github.com/PaddlePaddle/PaddleOCR'],
                        'notices': upstream_notices(['RapidOCR-LICENSE.txt', 'PaddleOCR-LICENSE.txt'])})
    return entries


def collect(requirements):
    pending = [Requirement(value) for value in requirements]
    seen, entries = set(), {}
    while pending:
        requirement = pending.pop()
        key = (canonicalize_name(requirement.name), tuple(sorted(requirement.extras)))
        if key in seen:
            continue
        seen.add(key)
        package = distribution(requirement.name)
        for value in package.requires or []:
            dependency = Requirement(value)
            if not dependency.marker or any(dependency.marker.evaluate({'extra': extra})
                                             for extra in ('', *requirement.extras)):
                pending.append(dependency)
        if key[0] in entries:
            continue
        metadata = package.metadata
        notices = []
        for path in package.files or []:
            name = Path(str(path)).name.lower()
            if (name.startswith(('license', 'copying', 'notice', 'copyright'))
                    or '/licenses/' in str(path).replace('\\', '/').lower()):
                if Path(name).suffix.lower() not in ('', '.txt', '.md', '.rst'):
                    continue
                notices.append({'file': str(path), 'text': Path(package.locate_file(path)).read_text(encoding='utf-8', errors='replace')})
        license_name = metadata.get('License-Expression') or metadata.get('License') or ''
        if not license_name or license_name == 'UNKNOWN':
            license_name = '\n'.join(item for item in metadata.get_all('Classifier', []) if item.startswith('License ::'))
        entries[key[0]] = {'name': metadata['Name'], 'version': package.version,
                          'license': license_name, 'urls': metadata.get_all('Project-URL', []),
                          'homepage': metadata.get('Home-page', ''), 'notices': notices}
    return sorted(entries.values(), key=lambda entry: entry['name'].casefold())


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--tools-directory', type=Path)
    options = parser.parse_args()
    root = ROOT
    project = tomllib.loads((root / 'pyproject.toml').read_text(encoding='utf-8'))
    entries = collect(project['project']['dependencies'])
    entries.extend(collect_external(options.tools_directory))
    entries.sort(key=lambda entry: entry['name'].casefold())
    target = root / 'src' / 'licenses.json'
    target.write_text(json.dumps(entries, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f'Collected notices for {len(entries)} runtime components: {target}')
