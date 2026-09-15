"""Convert the bundled PNG to a multi-resolution Windows ICO; no new artwork."""
import struct
from pathlib import Path

from PySide6.QtCore import QBuffer, QIODevice, Qt
from PySide6.QtGui import QImage

ICON_SIZES = (16, 24, 32, 48, 64, 128, 256)


def encode_ico(source: QImage) -> bytes:
    """Wrap scaled PNG frames in the Windows ICO container (Windows 11 target)."""
    if source.isNull() or source.width() != source.height():
        raise ValueError("Serve un'immagine quadrata valida.")
    directory = bytearray(struct.pack("<HHH", 0, 1, len(ICON_SIZES)))
    frames = []
    offset = 6 + 16 * len(ICON_SIZES)
    for size in ICON_SIZES:
        frame = source.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio,
                              Qt.TransformationMode.SmoothTransformation)
        buffer = QBuffer()
        if not buffer.open(QIODevice.OpenModeFlag.WriteOnly) or not frame.save(buffer, "PNG"):
            raise RuntimeError("Impossibile codificare l'icona.")
        payload = bytes(buffer.data())
        buffer.close()
        # ICO encodes 256-pixel dimensions as zero.
        directory.extend(struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0,
                                     1, 32, len(payload), offset))
        frames.append(payload)
        offset += len(payload)
    return bytes(directory) + b"".join(frames)


def main() -> None:
    assets = Path(__file__).resolve().parents[1] / "src/local_meeting_assistant/assets"
    target = assets / "assistant-icon.ico"
    target.write_bytes(encode_ico(QImage(str(assets / "assistant-icon.png"))))
    print(f"Icona Windows generata: {target}")


if __name__ == "__main__":
    main()
