import ast
import inspect
import pytest
from test_plugin import plugin

KEYS=('badgeBgColor','badgeXPercent','badgeYPercent','badgeOpacity','badgeBorderOpacity','badgeEffectStrength')

@pytest.mark.parametrize('key',KEYS)
def test_badge_settings_persist_and_all_buttons_forward(key):
    assert key in plugin.GENERATION_SETTING_KEYS
    source=inspect.getsource(plugin)
    # Single, batch, and explicit save all forward the selected state.
    assert source.count('"'+key+'": ui.ref("state.'+key+'")')==3
    assert 'bind="state.'+key+'"'in source


def test_badge_effect_translated_to_engine():
    source=inspect.getsource(plugin.Plugin.generate_cover)
    assert '"badgeEffectStrength"'in source
