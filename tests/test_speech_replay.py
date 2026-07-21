from pathlib import Path

from oculidoc.speech_replay import SpeechReplayStore


def test_speech_replay_requests_are_versioned_and_persisted(tmp_path: Path) -> None:
    store = SpeechReplayStore(tmp_path / "speech_replay.json")

    assert store.load().revision == 0

    first = store.request("tracking_ball")
    second = store.request("screen_keyboard")

    assert first.revision == 1
    assert second.revision == 2
    assert store.load() == second
