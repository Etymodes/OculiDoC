"""Shared pytest configuration."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("OCULIDOC_ENVIRONMENT", "test")
# Clinical workstations may define a real eye-tracker source globally.
# Keep tests that exercise the documented defaults independent of that setting.
os.environ["OCULIDOC_GAZE_SOURCE"] = "mock"
