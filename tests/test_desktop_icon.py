"""Icon checks use only a synthetic image and bundled public app assets."""
import importlib.util
import struct
from pathlib import Path

import pytest
from PySide6.QtGui import QImage

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("build_desktop_icon", ROOT / "scripts/build_desktop_icon.py")
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


def verify_frames(data):
    assert struct.unpack_from("<HHH", data) == (0, 1, len(builder.ICON_SIZES))
    expected_offset = 6 + 16 * len(builder.ICON_SIZES)
    for index, size in enumerate(builder.ICON_SIZES):
        width, height, colors, reserved, planes, bits, length, offset = struct.unpack_from(
            "<BBBBHHII", data, 6 + index * 16)
        assert (width, height, colors, reserved, planes, bits) == (size % 256, size % 256, 0, 0, 1, 32)
        assert offset == expected_offset
        image = QImage.fromData(data[offset:offset + length], "PNG")
        assert not image.isNull()
        assert (image.width(), image.height()) == (size, size)
        expected_offset += length
    assert expected_offset == len(data)


def test_synthetic_icon_conversion():
    source = QImage(64, 64, QImage.Format.Format_ARGB32)
    source.fill(0xFF33CCDD)
    verify_frames(builder.encode_ico(source))


def test_invalid_source_rejected():
    with pytest.raises(ValueError):
        builder.encode_ico(QImage())


def test_bundled_icon_and_distribution_hooks():
    verify_frames((ROOT / "src/local_meeting_assistant/assets/assistant-icon.ico").read_bytes())
    shortcut = (ROOT / "create-desktop-shortcut.ps1").read_text(encoding="utf-8")
    export = (ROOT / "scripts/export-source.ps1").read_text(encoding="utf-8")
    assert '$shortcut.IconLocation = "$iconPath,0"' in shortcut
    assert "assets\\assistant-icon.ico" in shortcut
    assert "assets\\assistant-icon.ico" in export
