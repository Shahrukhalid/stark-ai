from __future__ import annotations

import sys
from stark.power import keep_awake, set_keep_awake


def test_power_keep_awake_toggle():
    res = set_keep_awake(True)
    if sys.platform == "win32":
        assert isinstance(res, bool)
    else:
        assert res is False
    set_keep_awake(False)


def test_power_keep_awake_context():
    with keep_awake():
        pass
