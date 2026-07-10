"""Eye-gaze source protocol."""

from typing import Protocol, runtime_checkable

from oculidoc.gaze.models import GazeSample


@runtime_checkable
class GazeSource(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def is_running(self) -> bool: ...

    def start(self) -> None: ...

    def stop(self) -> None: ...

    def read(self) -> GazeSample | None: ...
