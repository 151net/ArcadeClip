"""Restrict the bundled yt-dlp to the YouTube extractors this app downloads from.

yt-dlp imports every site extractor through ``yt_dlp.extractor._extractors``
unless ``yt_dlp.extractor.lazy_extractors`` is importable. Seeding that module
with the YouTube classes keeps the registry at about twenty entries instead of
roughly eighteen hundred, and lets the packaged build leave the rest out.
"""
import sys
from types import ModuleType

from yt_dlp.extractor import generic, youtube

MODULE = 'yt_dlp.extractor.lazy_extractors'


def class_lookup():
    lookup = {name: getattr(youtube, name) for name in dir(youtube) if name.endswith('IE')}
    # yt-dlp resolves unmatched URLs through GenericIE and lists it last.
    lookup['GenericIE'] = generic.GenericIE
    return lookup


def install():
    """Seed the lazy registry before yt_dlp.extractor.extractors is first imported."""
    module = ModuleType(MODULE)
    module._CLASS_LOOKUP = class_lookup()
    sys.modules[MODULE] = module
