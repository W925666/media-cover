import ast
import inspect
import pytest
from test_plugin import plugin

KEYS=('badgeBgColor','badgeXPercent','badgeYPercent','badgeOpacity','badgeBorderOpacity','badgeEffectStrength')

@pytest.mark.parametrize('key',KEYS)
def test_advanced_badge_settings_use_layout_defaults_not_ui(key):
    assert key not in plugin.GENERATION_SETTING_KEYS
    source=inspect.getsource(plugin)
    assert '"'+key+'": ui.ref("state.'+key+'")' not in source
    assert 'bind="state.'+key+'"' not in source


def test_badge_effect_translated_to_engine():
    source=inspect.getsource(plugin.Plugin.generate_cover)
    assert '"badgeEffectStrength"'in source
