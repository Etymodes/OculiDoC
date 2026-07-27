from __future__ import annotations

import sys


def test_release_validation_runs_on_locked_python_version() -> None:
    assert sys.version_info[:2] == (3, 11)
