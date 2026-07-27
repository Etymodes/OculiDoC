from __future__ import annotations

import json
from pathlib import Path

import pytest
from PySide6.QtGui import QColor, QImage, QImageReader

from oculidoc.image_library import (
    BUILT_IN_IMAGE_ASSETS,
    BUILT_IN_STIMULUS_DIRECTORY,
    IMAGE_UPLOAD_GUIDE,
    ImageAsset,
    ImageLibraryStore,
)
from oculidoc.tasks.image_choice import ImageChoiceConfig, eligible_image_assets


def write_test_image(path: Path, color: str) -> None:
    image = QImage(640, 640, QImage.Format.Format_ARGB32)
    image.fill(QColor(color))
    assert image.save(str(path))


def test_uploaded_image_is_copied_and_metadata_is_editable(tmp_path: Path) -> None:
    source = tmp_path / "target.png"
    write_test_image(source, "#22aa66")
    store = ImageLibraryStore(tmp_path / "library")

    asset = store.add_file(
        source,
        label="绿色追踪球",
        category="追踪球",
        style="透明图标",
    )
    managed = store.resolve_path(asset)

    assert managed is not None
    assert managed.is_file()
    assert managed.parent == store.files_directory
    assert "1:1" in IMAGE_UPLOAD_GUIDE

    updated = store.update_metadata(
        asset.image_id,
        label="绿色目标",
        category="追踪球",
        style="自定义图标",
    )
    assert updated.image_id == asset.image_id
    assert {item.image_id: item for item in store.load()}[asset.image_id].label == "绿色目标"


def test_reviewed_kimi_assets_are_packaged_with_readable_taxonomy_names(
    tmp_path: Path,
) -> None:
    packaged = tuple(asset for asset in BUILT_IN_IMAGE_ASSETS if asset.relative_path is not None)
    store = ImageLibraryStore(tmp_path / "library")

    assert len(packaged) == 76
    assert {asset.category for asset in packaged} == {
        "身体部位",
        "医疗器材",
        "水果",
        "饮品",
    }
    assert {asset.style for asset in packaged} == {"图标", "卡通"}
    assert {asset.image_id for asset in packaged} >= {
        "apple",
        "builtin-body-part-eye-icon",
        "builtin-medical-equipment-wheelchair-cartoon",
    }
    assert not any(
        excluded in str(asset.relative_path)
        for asset in packaged
        for excluded in ("feeding_tube", "left_foot", "right_foot")
    )

    for asset in packaged:
        path = store.resolve_path(asset)
        assert path is not None
        assert path.parent == BUILT_IN_STIMULUS_DIRECTORY
        assert path.name.count("__") == 2
        assert QImageReader(str(path)).canRead()


def test_category_and_style_filters_require_two_distinct_images(tmp_path: Path) -> None:
    store = ImageLibraryStore(tmp_path / "library")
    first = tmp_path / "first.png"
    second = tmp_path / "second.png"
    write_test_image(first, "#ff0000")
    write_test_image(second, "#0000ff")
    store.add_file(first, label="红球", category="追踪球", style="照片")
    store.add_file(second, label="蓝球", category="追踪球", style="照片")

    config = ImageChoiceConfig(
        category_filters=("追踪球",),
        style_filters=("照片",),
        question_count=2,
    )
    eligible = eligible_image_assets(config, store)

    assert {asset.label for asset in eligible} == {"红球", "蓝球"}

    with pytest.raises(ValueError, match="至少需要两张"):
        eligible_image_assets(
            ImageChoiceConfig(
                category_filters=("植物",),
                style_filters=("彩色图标",),
                question_count=1,
            ),
            store,
        )


def test_legacy_taxonomy_is_merged_and_saved_with_short_chinese_names(
    tmp_path: Path,
) -> None:
    store = ImageLibraryStore(tmp_path / "library")
    store.directory.mkdir(parents=True)
    store.metadata_path.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "images": [
                    {
                        "image_id": "cat-home",
                        "label": "小猫",
                        "category": "C12 家养与农场动物",
                        "style": "S03 写实孤立照片",
                        "relative_path": "cat.jpg",
                    },
                    {
                        "image_id": "lion-wild",
                        "label": "狮子",
                        "category": "C13 野生陆生动物",
                        "style": "实物照片",
                        "relative_path": "lion.jpg",
                    },
                    {
                        "image_id": "football",
                        "label": "踢足球",
                        "category": "C38 体育项目与器材",
                        "style": "S02 卡通插画",
                        "relative_path": "football.jpg",
                    },
                    {
                        "image_id": "boy",
                        "label": "男孩",
                        "category": "C08 家庭与熟悉人物角色",
                        "style": "S08 低细节 3D",
                        "relative_path": "boy.jpg",
                    },
                    {
                        "image_id": "bird",
                        "label": "小鸟",
                        "category": "C14 鸟类",
                        "style": "S04 写实情境照片",
                        "relative_path": "bird.jpg",
                    },
                    {
                        "image_id": "reading",
                        "label": "阅读",
                        "category": "C39 游戏与休闲活动",
                        "style": "S08 低细节 3D",
                        "relative_path": "reading.jpg",
                    },
                    {
                        "image_id": "vase",
                        "label": "花瓶",
                        "category": "C50 节日与文化活动",
                        "style": "S03 写实孤立照片",
                        "relative_path": "vase.jpg",
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    custom = tuple(asset for asset in store.load() if not asset.built_in)

    assert {(asset.category, asset.style) for asset in custom} == {
        ("动物", "写实"),
        ("活动", "卡通"),
        ("人物", "三维"),
        ("活动", "三维"),
        ("文化", "写实"),
    }
    persisted = json.loads(store.metadata_path.read_text(encoding="utf-8"))
    assert {item["category"] for item in persisted["images"]} == {
        "人物",
        "动物",
        "活动",
        "文化",
    }
    assert {item["style"] for item in persisted["images"]} == {
        "三维",
        "写实",
        "卡通",
    }


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("category", "C99 misc", "类别需使用二至四个汉字"),
        ("style", "3D", "风格需使用二至四个汉字"),
        ("category", "这是一个过长类别", "类别需使用二至四个汉字"),
    ],
)
def test_taxonomy_rejects_unknown_codes_and_non_short_names(
    field: str,
    value: str,
    message: str,
) -> None:
    category = value if field == "category" else "动物"
    style = value if field == "style" else "写实"

    with pytest.raises(ValueError, match=message):
        ImageAsset(
            image_id="custom",
            label="测试图",
            category=category,
            style=style,
            relative_path="test.jpg",
        )


def test_unknown_legacy_code_is_removed_when_name_is_already_valid() -> None:
    asset = ImageAsset(
        image_id="custom",
        label="测试图",
        category="C99 未分类",
        style="S99 新风格",
        relative_path="test.jpg",
    )

    assert asset.category == "未分类"
    assert asset.style == "新风格"
