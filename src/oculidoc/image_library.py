"""Persistent OculiDoC picture library shared by visual gaze tasks."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile
from uuid import uuid4

from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QImageReader, QPainter, QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

IMAGE_UPLOAD_GUIDE = (
    "支持 PNG、JPG/JPEG、WebP、BMP；单张不超过 10 MB。建议使用 1:1 方图、"
    "至少 512×512；追踪球优先使用透明背景 PNG。"
)
ALLOWED_IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp", ".bmp"})
MAX_IMAGE_BYTES = 10 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class ImageAsset:
    """One named visual stimulus with searchable clinical metadata."""

    image_id: str
    label: str
    category: str
    style: str
    symbol: str = ""
    relative_path: str | None = None
    built_in: bool = False

    def __post_init__(self) -> None:
        for name in ("image_id", "label", "category", "style"):
            normalized = str(getattr(self, name)).strip()

            if not normalized:
                raise ValueError(f"{name} cannot be empty.")

            object.__setattr__(self, name, normalized)

        normalized_path = str(self.relative_path).strip() if self.relative_path is not None else ""
        object.__setattr__(self, "relative_path", normalized_path or None)

        if not self.symbol and self.relative_path is None:
            raise ValueError("An image asset needs a built-in symbol or a managed file.")

    def to_dict(self) -> dict[str, object]:
        return {
            "image_id": self.image_id,
            "label": self.label,
            "category": self.category,
            "style": self.style,
            "relative_path": self.relative_path,
        }

    @classmethod
    def from_dict(cls, value: object) -> ImageAsset:
        if not isinstance(value, dict):
            raise TypeError("Image asset must be an object.")

        return cls(
            image_id=str(value["image_id"]),
            label=str(value["label"]),
            category=str(value["category"]),
            style=str(value["style"]),
            relative_path=(
                str(value["relative_path"]) if value.get("relative_path") is not None else None
            ),
        )


BUILT_IN_IMAGE_ASSETS: tuple[ImageAsset, ...] = (
    ImageAsset("banana", "香蕉", "水果", "彩色图标", "🍌", built_in=True),
    ImageAsset("apple", "苹果", "水果", "彩色图标", "🍎", built_in=True),
    ImageAsset("lion", "狮子", "动物", "彩色图标", "🦁", built_in=True),
    ImageAsset("dog", "小狗", "动物", "彩色图标", "🐶", built_in=True),
    ImageAsset("cat", "小猫", "动物", "彩色图标", "🐱", built_in=True),
    ImageAsset("cup", "水杯", "日常用品", "彩色图标", "🥤", built_in=True),
    ImageAsset("bed", "床", "日常用品", "彩色图标", "🛏", built_in=True),
    ImageAsset("shoe", "鞋", "日常用品", "彩色图标", "👟", built_in=True),
    ImageAsset("sun", "太阳", "自然", "彩色图标", "☀", built_in=True),
    ImageAsset("moon", "月亮", "自然", "彩色图标", "🌙", built_in=True),
    ImageAsset("flower", "花", "植物", "彩色图标", "🌼", built_in=True),
    ImageAsset("car", "汽车", "交通工具", "彩色图标", "🚗", built_in=True),
)


class ImageLibraryStore:
    """Copy uploaded images into application data and atomically save metadata."""

    schema_version = "1.0"

    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory).expanduser().resolve()
        self.files_directory = self.directory / "files"
        self.metadata_path = self.directory / "images.json"

    def _load_custom_assets(self) -> tuple[ImageAsset, ...]:
        if not self.metadata_path.is_file():
            return ()

        payload = json.loads(self.metadata_path.read_text(encoding="utf-8"))

        if not isinstance(payload, dict) or payload.get("schema_version") != self.schema_version:
            raise ValueError("Unsupported image-library document.")

        raw_images = payload.get("images", [])

        if not isinstance(raw_images, list):
            raise ValueError("Image-library images must be a list.")

        return tuple(ImageAsset.from_dict(item) for item in raw_images)

    def load(self) -> tuple[ImageAsset, ...]:
        combined = {asset.image_id: asset for asset in BUILT_IN_IMAGE_ASSETS}

        for asset in self._load_custom_assets():
            combined[asset.image_id] = asset

        return tuple(combined.values())

    def resolve_path(self, asset: ImageAsset) -> Path | None:
        if asset.relative_path is None:
            return None

        return (self.files_directory / Path(asset.relative_path).name).resolve()

    def add_file(
        self,
        source_path: str | Path,
        *,
        label: str,
        category: str,
        style: str,
    ) -> ImageAsset:
        source = Path(source_path).expanduser().resolve()

        if not source.is_file():
            raise ValueError("请选择一个存在的图片文件。")

        suffix = source.suffix.lower()

        if suffix not in ALLOWED_IMAGE_SUFFIXES:
            raise ValueError("图片格式不受支持，请使用 PNG、JPG/JPEG、WebP 或 BMP。")

        file_size = source.stat().st_size

        if file_size <= 0 or file_size > MAX_IMAGE_BYTES:
            raise ValueError("图片必须大于 0 字节且不超过 10 MB。")

        reader = QImageReader(str(source))

        if not reader.canRead():
            raise ValueError("图片内容无法读取，文件可能损坏或格式与扩展名不一致。")

        dimensions = reader.size()

        if not dimensions.isValid() or dimensions.width() < 64 or dimensions.height() < 64:
            raise ValueError("图片宽高至少需要 64×64 像素。")

        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        relative_name = f"{digest[:24]}{suffix}"
        self.files_directory.mkdir(parents=True, exist_ok=True)
        destination = self.files_directory / relative_name

        if not destination.exists():
            shutil.copy2(source, destination)

        asset = ImageAsset(
            image_id=f"user-{uuid4()}",
            label=label,
            category=category,
            style=style,
            relative_path=relative_name,
        )
        custom = list(self._load_custom_assets())
        custom.append(asset)
        self._write(custom)
        return asset

    def update_metadata(
        self,
        image_id: str,
        *,
        label: str,
        category: str,
        style: str,
    ) -> ImageAsset:
        custom = list(self._load_custom_assets())

        for index, asset in enumerate(custom):
            if asset.image_id != image_id:
                continue

            updated = ImageAsset(
                image_id=asset.image_id,
                label=label,
                category=category,
                style=style,
                relative_path=asset.relative_path,
            )
            custom[index] = updated
            self._write(custom)
            return updated

        raise KeyError(f"Unknown custom image: {image_id}")

    def delete(self, image_id: str) -> None:
        custom = list(self._load_custom_assets())
        removed = next((asset for asset in custom if asset.image_id == image_id), None)

        if removed is None:
            raise KeyError(f"Unknown custom image: {image_id}")

        remaining = [asset for asset in custom if asset.image_id != image_id]
        self._write(remaining)

        if removed.relative_path and not any(
            asset.relative_path == removed.relative_path for asset in remaining
        ):
            path = self.resolve_path(removed)

            if path is not None:
                path.unlink(missing_ok=True)

    def _write(self, assets: list[ImageAsset]) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": self.schema_version,
            "images": [asset.to_dict() for asset in assets],
        }

        with NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            prefix=f".{self.metadata_path.name}.",
            suffix=".tmp",
            dir=self.directory,
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())

        try:
            temporary_path.replace(self.metadata_path)
        finally:
            temporary_path.unlink(missing_ok=True)


def asset_preview_pixmap(
    asset: ImageAsset,
    store: ImageLibraryStore,
    *,
    size: int,
    background: str = "#f7fbff",
) -> QPixmap:
    """Render a square stimulus without adding its label as visible text."""
    pixmap = QPixmap(size, size)
    pixmap.fill(QColor(background))
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    path = store.resolve_path(asset)

    if path is not None:
        source = QPixmap(str(path))

        if not source.isNull():
            scaled = source.scaled(
                QSize(size, size),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            x = (size - scaled.width()) // 2
            y = (size - scaled.height()) // 2
            painter.drawPixmap(x, y, scaled)
    else:
        font = QFont("Segoe UI Emoji", max(64, int(size * 0.62)))
        painter.setFont(font)
        painter.setPen(QColor("#17324d"))
        painter.drawText(QRect(0, 0, size, size), Qt.AlignmentFlag.AlignCenter, asset.symbol)

    painter.end()
    return pixmap


class ImageAssetDialog(QDialog):
    """Collect one upload and its category/style metadata."""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        asset: ImageAsset | None = None,
        default_category: str = "日常用品",
        default_style: str = "实物照片",
    ) -> None:
        super().__init__(parent)
        self.asset = asset
        self.setWindowTitle("修改图片资料" if asset is not None else "上传图片到 OculiDoC 图片库")
        self.resize(620, 300)
        form = QFormLayout()

        self.file_edit = QLineEdit()
        self.file_edit.setReadOnly(True)
        choose_button = QPushButton("选择图片…")
        choose_button.clicked.connect(self._choose_file)
        file_row = QHBoxLayout()
        file_row.addWidget(self.file_edit, 1)
        file_row.addWidget(choose_button)
        form.addRow("图片文件：", file_row)
        self.file_edit.setEnabled(asset is None)
        choose_button.setEnabled(asset is None)

        self.label_edit = QLineEdit(asset.label if asset is not None else "")
        self.category_edit = QLineEdit(asset.category if asset is not None else default_category)
        self.style_edit = QLineEdit(asset.style if asset is not None else default_style)
        form.addRow("图片名称：", self.label_edit)
        form.addRow("类别：", self.category_edit)
        form.addRow("风格：", self.style_edit)

        guide = QLabel(IMAGE_UPLOAD_GUIDE)
        guide.setWordWrap(True)
        guide.setStyleSheet("color:#365269; background:#eef7ff; padding:8px;")

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        root = QVBoxLayout(self)
        root.addWidget(guide)
        root.addLayout(form)
        root.addStretch(1)
        root.addWidget(buttons)

    def _choose_file(self) -> None:
        filename, _filter = QFileDialog.getOpenFileName(
            self,
            "选择图片",
            "",
            "图片 (*.png *.jpg *.jpeg *.webp *.bmp)",
        )

        if filename:
            self.file_edit.setText(filename)

            if not self.label_edit.text().strip():
                self.label_edit.setText(Path(filename).stem)

    def values(self) -> tuple[str, str, str, str]:
        return (
            self.file_edit.text().strip(),
            self.label_edit.text().strip(),
            self.category_edit.text().strip(),
            self.style_edit.text().strip(),
        )


class ImageLibraryDialog(QDialog):
    """Upload and maintain reusable visual stimuli."""

    def __init__(self, store: ImageLibraryStore, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.store = store
        self._assets: dict[str, ImageAsset] = {}
        self.setWindowTitle("OculiDoC 图片库")
        self.resize(820, 560)

        guide = QLabel(IMAGE_UPLOAD_GUIDE)
        guide.setWordWrap(True)
        guide.setStyleSheet("color:#365269; background:#eef7ff; padding:8px;")

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["图片/名称", "类别", "风格", "来源"])
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.verticalHeader().setDefaultSectionSize(72)
        self.table.horizontalHeader().setStretchLastSection(True)

        add_button = QPushButton("上传图片…")
        edit_button = QPushButton("修改名称/类别/风格")
        delete_button = QPushButton("删除自定义图片")
        close_button = QPushButton("完成")
        add_button.clicked.connect(self._add)
        edit_button.clicked.connect(self._edit)
        delete_button.clicked.connect(self._delete)
        close_button.clicked.connect(self.accept)

        actions = QHBoxLayout()
        actions.addWidget(add_button)
        actions.addWidget(edit_button)
        actions.addWidget(delete_button)
        actions.addStretch(1)
        actions.addWidget(close_button)

        root = QVBoxLayout(self)
        root.addWidget(guide)
        root.addWidget(self.table, 1)
        root.addLayout(actions)
        self._reload()

    def _reload(self, selected_id: str | None = None) -> None:
        assets = self.store.load()
        self._assets = {asset.image_id: asset for asset in assets}
        self.table.setRowCount(len(assets))

        for row, asset in enumerate(assets):
            name_item = QTableWidgetItem(
                QIcon(asset_preview_pixmap(asset, self.store, size=64)),
                asset.label,
            )
            name_item.setData(Qt.ItemDataRole.UserRole, asset.image_id)
            self.table.setItem(row, 0, name_item)
            self.table.setItem(row, 1, QTableWidgetItem(asset.category))
            self.table.setItem(row, 2, QTableWidgetItem(asset.style))
            self.table.setItem(row, 3, QTableWidgetItem("内置" if asset.built_in else "自定义"))

            if asset.image_id == selected_id:
                self.table.selectRow(row)

        self.table.resizeColumnsToContents()

    def _selected_asset(self) -> ImageAsset | None:
        row = self.table.currentRow()

        if row < 0:
            return None

        item = self.table.item(row, 0)
        return self._assets.get(str(item.data(Qt.ItemDataRole.UserRole))) if item else None

    def _add(self) -> None:
        dialog = ImageAssetDialog(self)

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        filename, label, category, style = dialog.values()

        try:
            asset = self.store.add_file(
                filename,
                label=label,
                category=category,
                style=style,
            )
        except (OSError, TypeError, ValueError) as error:
            QMessageBox.warning(self, "无法加入图片库", str(error))
            return

        self._reload(asset.image_id)

    def _edit(self) -> None:
        asset = self._selected_asset()

        if asset is None:
            QMessageBox.information(self, "尚未选择图片", "请先选择一张自定义图片。")
            return

        if asset.built_in:
            QMessageBox.information(self, "内置图片", "内置图片不可改名；可上传自定义版本。")
            return

        dialog = ImageAssetDialog(self, asset=asset)

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        _filename, label, category, style = dialog.values()

        try:
            self.store.update_metadata(
                asset.image_id,
                label=label,
                category=category,
                style=style,
            )
        except (OSError, KeyError, TypeError, ValueError) as error:
            QMessageBox.warning(self, "无法修改图片", str(error))
            return

        self._reload(asset.image_id)

    def _delete(self) -> None:
        asset = self._selected_asset()

        if asset is None:
            QMessageBox.information(self, "尚未选择图片", "请先选择一张自定义图片。")
            return

        if asset.built_in:
            QMessageBox.information(self, "内置图片", "内置图片不能删除。")
            return

        if (
            QMessageBox.question(
                self,
                "确认删除图片",
                f"从 OculiDoC 图片库删除“{asset.label}”？",
            )
            != QMessageBox.StandardButton.Yes
        ):
            return

        try:
            self.store.delete(asset.image_id)
        except (OSError, KeyError) as error:
            QMessageBox.warning(self, "无法删除图片", str(error))
            return

        self._reload()
