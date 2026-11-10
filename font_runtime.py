"""Pinned remote fonts, isolated per task and removed in finally."""
import functools
import hashlib
import json
import os
import shutil
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

def cleanup_abandoned():
    """Only remove owned directories whose owning process is no longer alive."""
    for directory in Path(tempfile.gettempdir()).glob('cover-fonts-*'):
        marker = directory / '.owner'
        try:
            if directory.is_symlink() or not marker.is_file():
                continue
            pid = int(marker.read_text())
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                shutil.rmtree(directory)
            except PermissionError:
                pass
        except (OSError, ValueError):
            continue


def temporary_fonts(method):
    @functools.wraps(method)
    def wrapped(self, input, ctx):
        with _LOCK:
            # Development checkout can retain untracked original fonts; release ZIP has none.
            if (ROOT / 'fonts' / 'SourceHanSansCN-Bold.otf').is_file():
                return method(self, input, ctx)
            if getattr(_LOCAL, 'owner', None) is self:
                return method(self, input, ctx)
            cleanup_abandoned()
            if hasattr(self, '_prepare_font_input'):
                input = self._prepare_font_input(method.__name__, input, ctx)
            rows = catalog()
            # Read saved selection without altering user settings.
            saved = ctx.mt.storage.get(key='ui-settings') or {}
            saved = saved.get('value', {}) if saved.get('found') else {}
            cfg = self._plugin_config(ctx)
            selection = {k:v for k,v in cfg.items() if k in ('fontTitle','fontSubtitle')}
            if isinstance(saved, dict):
                selection.update({k:v for k,v in saved.items() if v is not None and k in ('fontTitle', 'fontSubtitle', 'badgeFont', 'layout')})
            selection.update(input or {})
            # Empty UI selection means layout/config default, just as generate_cover does.
            for key in ('fontTitle', 'fontSubtitle'):
                if not selection.get(key) and cfg.get(key):
                    selection[key] = cfg[key]
            defaults = self._layout_defaults(selection.get('layout') or '经典九宫格')
            aliases = {'字魂白鸽天行体(商用需授权).ttf': '字魂白鸽天行体.ttf'}
            names = {'OPPOSans-B.ttf', '剑豪体.ttf', 'SourceHanSansCN-Bold.otf'}
            for key in ('font_title', 'font_subtitle', 'font_sub', 'font_count', 'badge_font'):
                name = aliases.get(defaults.get(key), defaults.get(key))
                if name and name != 'default.ttf':
                    names.add(name)
            for key in ('fontTitle', 'fontSubtitle', 'badgeFont', 'fontCount'):
                if selection.get(key):
                    names.add(selection[key])
            by_name = {row['name']:row for row in rows}
            unknown = names - by_name.keys()
            if unknown:
                raise ValueError('Font unavailable; select a listed font: ' + ', '.join(sorted(unknown)))
            previous = getattr(self, '_engine', None)
            previous_owner = getattr(_LOCAL, 'owner', None)
            with tempfile.TemporaryDirectory(prefix='cover-fonts-') as directory:
                Path(directory, '.owner').write_text(str(os.getpid()))
                try:
                    for name in sorted(names):
                        fetch_font(by_name[name], directory)
                    from engine import PosterEngine
                    self._engine = PosterEngine(fonts_dir=directory, layouts_dir=str(ROOT/'layouts'))
                    _LOCAL.owner = self
                    return method(self, input, ctx)
                finally:
                    _LOCAL.owner = previous_owner
                    try:
                        if self._engine is not previous and self._engine is not None:
                            self._engine.clear_image_cache()
                    finally:
                        self._engine = previous
    return wrapped
