"""Structural tests for workbench review policy."""

import ast
import inspect
import textwrap

from oculidoc.vision.camera_preview_window import (
    CameraPreviewWindow,
)


def method_tree(method) -> ast.Module:
    """Parse one class method independently."""
    return ast.parse(textwrap.dedent(inspect.getsource(method)))


def contains_enum_member(
    tree: ast.AST,
    *,
    enum_name: str,
    member_name: str,
) -> bool:
    """Return whether an enum member is referenced."""
    return any(
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == enum_name
        and node.attr == member_name
        for node in ast.walk(tree)
    )


def is_self_attribute(
    node: ast.AST,
    attribute_name: str,
) -> bool:
    """Return whether a node is self.<attribute>."""
    return (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "self"
        and node.attr == attribute_name
    )


def test_face_proposals_start_unreviewed() -> None:
    tree = method_tree(CameraPreviewWindow._finish_eye_selection)

    has_review_mapping_assignment = any(
        isinstance(node, ast.Assign)
        and any(
            is_self_attribute(
                target,
                "_eye_review_statuses",
            )
            for target in node.targets
        )
        for node in ast.walk(tree)
    )

    assert has_review_mapping_assignment
    assert contains_enum_member(
        tree,
        enum_name="ObservationReviewStatus",
        member_name="PROPOSED",
    )


def test_operator_can_confirm_proposals() -> None:
    tree = method_tree(CameraPreviewWindow._confirm_eye_proposals)

    assert contains_enum_member(
        tree,
        enum_name="ObservationReviewStatus",
        member_name="CONFIRMED",
    )


def test_manual_correction_is_recorded() -> None:
    tree = method_tree(CameraPreviewWindow._finish_eye_selection)

    assert contains_enum_member(
        tree,
        enum_name="ObservationReviewStatus",
        member_name="CORRECTED",
    )
    assert contains_enum_member(
        tree,
        enum_name="ObservationReviewStatus",
        member_name="MANUAL",
    )


def test_unreviewed_proposals_cannot_be_saved() -> None:
    tree = method_tree(CameraPreviewWindow._save_snapshot)

    assert contains_enum_member(
        tree,
        enum_name="ObservationReviewStatus",
        member_name="PROPOSED",
    )

    string_values = {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }

    assert any("建议眼框尚未确认" in value for value in string_values)
