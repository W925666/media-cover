"""Pinned remote fonts, isolated per task and removed in finally."""
import functools
import hashlib
import json
import tempfile
import threading
import urllib.request
from pathlib import Path

_LOCK = threading.RLock()
_LOCAL = threading.local()
ROOT = Path(__file__).resolve().parent

def catalog():
    return json.loads((ROOT / 'font-catalog.json').read_text(encoding='utf-8'))

def fetch_font(row, directory):
    target = Path(directory) / row['name']
    digest = hashlib.sha256()
    size = 0
    with urllib.request.urlopen(row['url'], timeout=90) as response, target.open('wb') as output:
        while True:
            chunk = response.read(256 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > row['bytes']:
                raise ValueError('Font size exceeds pinned manifest: ' + row['name'])
            digest.update(chunk)
            output.write(chunk)
    if size != row['bytes'] or digest.hexdigest() != row['sha256']:
        raise ValueError('Font integrity mismatch: ' + row['name'])
    return target

def temporary_fonts(method):
    @functools.wraps(method)
    def wrapped(self, input, ctx):
        with _LOCK:
            # Development checkout can retain untracked original fonts; release ZIP has none.
            if (ROOT / 'fonts' / 'SourceHanSansCN-Bold.otf').is_file():
                return method(self, input, ctx)
            if getattr(_LOCAL, 'active', False):
                return method(self, input, ctx)
            rows = catalog()
            # Read saved selection without altering user settings.
            saved = ctx.mt.storage.get(key='ui-settings') or {}
            saved = saved.get('value', {}) if saved.get('found') else {}
            selection = dict(saved) if isinstance(saved, dict) else {}
            cfg = self._plugin_config(ctx)
            selection.update({k:v for k,v in cfg.items() if k in ('fontTitle','fontSubtitle','badgeFont')})
            selection.update(input or {})
            names = {'OPPOSans-B.ttf', '剑豪体.ttf', 'SourceHanSansCN-Bold.otf'}
            for key in ('fontTitle', 'fontSubtitle', 'badgeFont'):
                if selection.get(key):
                    names.add(selection[key])
            by_name = {row['name']:row for row in rows}
            unknown = names - by_name.keys()
            if unknown:
                raise ValueError('Font unavailable; select a listed font: ' + ', '.join(sorted(unknown)))
            previous = getattr(self, '_engine', None)
            with tempfile.TemporaryDirectory(prefix='cover-fonts-') as directory:
                try:
                    for name in sorted(names):
                        fetch_font(by_name[name], directory)
                    from engine import PosterEngine
                    self._engine = PosterEngine(fonts_dir=directory, layouts_dir=str(ROOT/'layouts'))
                    _LOCAL.active = True
                    return method(self, input, ctx)
                finally:
                    _LOCAL.active = False
                    if self._engine is not previous and self._engine is not None:
                        self._engine.clear_image_cache()
                    self._engine = previous
    return wrapped
