import pytest

from oculidoc.gaze import GazeSource, MockGazeSource


def test_mock_gaze_source() -> None:
    source = MockGazeSource(1000, 600)

    assert isinstance(source, GazeSource)
    assert source.read() is None

    source.start()
    sample = source.read()

    assert sample is not None
    assert sample.valid is True
    assert sample.source == "mock"
    assert 0 <= sample.x_px <= 1000
    assert 0 <= sample.y_px <= 600

    source.stop()
    assert source.read() is None


def test_mock_source_rejects_invalid_screen_size() -> None:
    with pytest.raises(ValueError, match="positive"):
        MockGazeSource(0, 600)
