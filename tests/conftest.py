"""Shared pytest configuration."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("OCULIDOC_ENVIRONMENT", "test")
