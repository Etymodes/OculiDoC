"""Eye-gaze data source interfaces."""

from oculidoc.gaze.base import GazeSource
from oculidoc.gaze.mock import MockGazeSource
from oculidoc.gaze.models import GazeSample

__all__ = ["GazeSample", "GazeSource", "MockGazeSource"]
