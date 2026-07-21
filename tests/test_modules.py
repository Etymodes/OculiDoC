from oculidoc.modules import DEFAULT_MODULES


def test_required_modules_are_registered() -> None:
    identifiers = {module.module_id for module in DEFAULT_MODULES}

    assert len(identifiers) == len(DEFAULT_MODULES)
    assert {
        "tracking_ball",
        "screen_keyboard",
        "binary_horizontal",
        "binary_vertical",
        "multiple_choice",
        "image_choice",
        "instruction_fixation",
    } <= identifiers
    keyboard = next(module for module in DEFAULT_MODULES if module.module_id == "screen_keyboard")
    assert keyboard.status == "available"
