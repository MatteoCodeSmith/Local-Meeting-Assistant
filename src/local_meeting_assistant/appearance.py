"""Native Qt themes and lightweight vector animations; no web view or GPU model."""

from __future__ import annotations

from dataclasses import dataclass, replace

from PySide6.QtCore import QEvent, QEasingCurve, QPointF, QRectF, Qt, QTimer, QVariantAnimation
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QFrame, QLabel, QPushButton, QSizePolicy, QWidget


@dataclass(frozen=True)
class Style:
    key: str
    name: str
    description: str
    background: str
    surface: str
    text: str
    muted: str
    accent: str
    secondary: str
    border: str
    radius: int
    width: int
    height: int
    font: str = "Segoe UI"


STYLES = {
    "obsidian": Style(
        "obsidian",
        "Obsidian",
        "Monocromatico · dock orizzontale · luce radente",
        "#17191d",
        "#272a30",
        "#f4f5f7",
        "#989da8",
        "#f3f4f6",
        "#a9b0bf",
        "#42464f",
        28,
        770,
        116,
    ),
    "aurora": Style(
        "aurora",
        "Aurora",
        "Blu e viola · gradienti luminosi · controlli a capsula",
        "#151d36",
        "#253658",
        "#edf6ff",
        "#a0b2d4",
        "#74e5eb",
        "#a88cff",
        "#465a84",
        22,
        660,
        168,
    ),
    "porcelain": Style(
        "porcelain",
        "Porcelain",
        "Avorio · superfici morbide · scheda compatta",
        "#f3f1eb",
        "#ffffff",
        "#272b35",
        "#656979",
        "#5264bf",
        "#9a8cc8",
        "#d2d2db",
        20,
        500,
        208,
    ),
    "cyberpunk": Style(
        "cyberpunk",
        "Cyberpunk",
        "Giallo elettrico · tagli diagonali · segnali neon",
        "#14171a",
        "#252b30",
        "#efff7e",
        "#a3b4b8",
        "#edff64",
        "#36e3eb",
        "#566238",
        0,
        710,
        164,
        "Consolas",
    ),
    "studio": Style(
        "studio",
        "Studio",
        "Verde salvia · console audio · grande visualizzatore",
        "#152522",
        "#253a33",
        "#eef4dc",
        "#a5b9a8",
        "#b5e7ad",
        "#e6bd83",
        "#486557",
        18,
        460,
        254,
    ),
}


def get_style(key: str) -> Style:
    return STYLES.get(key, STYLES["obsidian"])


ICON_COLOR_LABELS = {
    "body": "Sfondo",
    "hover": "Sfondo hover",
    "active": "Sfondo REC / AI",
    "edge": "Bordo / alone",
    "active_edge": "Bordo REC / AI",
    "ink": "Simbolo / testo",
    "glitch": "Interferenza",
}
ICON_PRESETS = {
    "Ghiaccio": ("#152b40", "#204562", "#225578", "#79ddff", "#a5eeff", "#f2fbff", "#b998ff"),
    "Grafite": ("#202226", "#373b42", "#454b57", "#a4aab5", "#e5e7eb", "#ffffff", "#d0d6e0"),
    "Viola": ("#291d42", "#442966", "#633b8a", "#bf98ff", "#e5c9ff", "#fcf4ff", "#71e8ed"),
    "Salvia": ("#20382f", "#335345", "#466b54", "#9dcea7", "#d8efb7", "#f0f7e6", "#eed69b"),
    "Ambra": ("#392718", "#573822", "#734a29", "#e8b063", "#ffda8c", "#fff7e9", "#f5e5b8"),
}


def icon_palette(style: Style, overrides=None):
    values = dict(
        zip(
            ICON_COLOR_LABELS,
            (
                style.background,
                style.surface,
                style.surface,
                style.accent,
                style.secondary,
                style.accent,
                style.secondary,
            ),
        )
    )
    if style.key == "cyberpunk":
        values.update(
            zip(
                ICON_COLOR_LABELS,
                (
                    "#ff184c",
                    "#cc133c",
                    "#8b00ff",
                    "#fded00",
                    "#00e572",
                    "#ffffff",
                    "#f5f53d",
                ),
            )
        )
    if isinstance(overrides, dict):
        for key, color in overrides.items():
            if key in values and isinstance(color, str) and QColor(color).isValid():
                values[key] = QColor(color).name()
    return values


def mix(a: str, b: str, fraction: float) -> QColor:
    x, y = QColor(a), QColor(b)
    t = max(0.0, min(1.0, fraction))
    return QColor(*(round(v + (w - v) * t) for v, w in zip(x.getRgb(), y.getRgb())))


def readable_ink(background: str, preferred: str) -> str:
    def luminance(value):
        color = QColor(value)
        channels = [color.redF(), color.greenF(), color.blueF()]
        linear = [v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4 for v in channels]
        return sum(v * weight for v, weight in zip(linear, (0.2126, 0.7152, 0.0722)))
    bg, ink = luminance(background), luminance(preferred)
    if (max(bg, ink) + 0.05) / (min(bg, ink) + 0.05) >= 4.5:
        return preferred
    return "#000000" if (bg + 0.05) / 0.05 >= 1.05 / (bg + 0.05) else "#ffffff"


def panel_style(style: Style, overrides=None) -> Style:
    """Share custom icon colors with the full control surface, without mutating base themes."""
    if not isinstance(overrides, dict):
        return style
    valid = {key: value for key, value in (overrides or {}).items()
             if key in ICON_COLOR_LABELS and isinstance(value, str) and QColor(value).isValid()}
    if not valid:
        return style
    palette = icon_palette(style, valid)
    background = palette["body"] if "body" in valid else style.background
    surface = palette["hover"] if "hover" in valid else style.surface
    text = palette["ink"] if "ink" in valid else style.text
    accent = palette["edge"] if "edge" in valid else style.accent
    return replace(style, background=background, surface=surface,
                   text=readable_ink(background, text),
                   muted=mix(background, readable_ink(background, text), 0.70).name(),
                   accent=accent,
                   secondary=palette["glitch"] if "glitch" in valid else style.secondary,
                   border=mix(background, accent, 0.45).name())


def outline(rect: QRectF, style: Style) -> QPainterPath:
    path = QPainterPath()
    if style.key == "cyberpunk":
        cut = min(14.0, rect.height() / 3)
        path.moveTo(rect.left() + cut, rect.top())
        path.lineTo(rect.right(), rect.top())
        path.lineTo(rect.right(), rect.bottom() - cut)
        path.lineTo(rect.right() - cut, rect.bottom())
        path.lineTo(rect.left(), rect.bottom())
        path.lineTo(rect.left(), rect.top() + cut)
        path.closeSubpath()
    else:
        radius = min(style.radius, rect.height() / 2)
        path.addRoundedRect(rect, radius, radius)
    return path


class Surface(QFrame):
    def __init__(self, style: Style, parent=None):
        super().__init__(parent)
        self.style = style

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        path = outline(rect, self.style)
        gradient = QLinearGradient(rect.topLeft(), rect.bottomRight())
        gradient.setColorAt(0, mix(self.style.background, self.style.surface, 0.5))
        gradient.setColorAt(1, QColor(self.style.background))
        if self.style.key == "aurora":
            gradient.setColorAt(0.6, mix(self.style.background, self.style.secondary, 0.18))
        p.fillPath(path, gradient)
        p.setPen(QPen(QColor(self.style.border), 1))
        p.drawPath(path)
        p.setClipPath(path)
        if self.style.key == "cyberpunk":
            p.fillRect(QRectF(1, 18, 3, self.height() - 36), QColor(self.style.accent))
            p.setPen(QPen(mix(self.style.background, self.style.surface, 0.4), 1))
            for y in range(6, self.height(), 6):
                p.drawLine(8, y, self.width() - 8, y)
        elif self.style.key == "porcelain":
            p.setPen(QPen(QColor("#ffffff"), 2))
            p.drawLine(22, 2, self.width() - 22, 2)
        elif self.style.key == "studio":
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(self.style.border))
            for x in (10, self.width() - 10):
                for y in (10, self.height() - 10):
                    p.drawEllipse(QPointF(x, y), 1.5, 1.5)


class AnimatedButton(QPushButton):
    def __init__(self, text: str, role: str = "normal", parent=None):
        super().__init__(text, parent)
        self.role = role
        self.style = get_style("obsidian")
        self.motion = True
        self.hover = 0.0
        self._pointer_inside = False
        self._glitch_elapsed = 0.0
        self._glitch_timer = QTimer(self)
        self._glitch_timer.setInterval(40)
        self._glitch_timer.timeout.connect(self._glitch_tick)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(34)
        self.setMinimumWidth(66)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.animation = QVariantAnimation(self)
        self.animation.setDuration(170)
        self.animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.animation.valueChanged.connect(self._animate)

    def _animate(self, value):
        self.hover = float(value)
        self.update()

    def set_style(self, style: Style, reduce_motion: bool = False):
        self.style = style
        self.motion = not reduce_motion
        self.setFont(QFont(style.font, 9, QFont.Weight.DemiBold))
        if reduce_motion:
            self.animation.stop()
            self.hover = float(self.underMouse())
        self._sync_glitch()
        self.update()

    @property
    def glitch_active(self):
        return (
            self.style.key == "cyberpunk"
            and self.motion
            and self._pointer_inside
            and self.isEnabled()
        )

    def _sync_glitch(self):
        if self.glitch_active:
            if not self._glitch_timer.isActive():
                self._glitch_elapsed = 0.0
                self._glitch_timer.start()
        else:
            self._glitch_timer.stop()
            self._glitch_elapsed = 0.0
        self.update()

    def _glitch_tick(self):
        self._glitch_elapsed += 0.04
        self.update()

    def changeEvent(self, event):
        if event.type() == QEvent.Type.EnabledChange:
            self._sync_glitch()
        super().changeEvent(event)

    def hideEvent(self, event):
        self._pointer_inside = False
        self._sync_glitch()
        super().hideEvent(event)

    def _target(self, value):
        self.animation.stop()
        if self.motion:
            self.animation.setStartValue(self.hover)
            self.animation.setEndValue(value)
            self.animation.start()
        else:
            self._animate(value)

    def enterEvent(self, event):
        self._pointer_inside = True
        self._sync_glitch()
        self._target(1.0)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._pointer_inside = False
        self._sync_glitch()
        self._target(0.0)
        super().leaveEvent(event)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        s = self.style
        r = QRectF(self.rect()).adjusted(1, 2, -1, -3)
        if self.isDown():
            r.translate(0, 2)
        elif self.motion:
            r.translate(0, -self.hover)
        shape = outline(r, s)
        primary = self.role == "record"
        color = s.accent if primary else s.surface
        ink = readable_ink(color, s.background if primary else s.text)
        if self.role == "cancel" and self.hover:
            color = "#773d48"
            ink = "#fff1f4"
        if not self.isEnabled():
            p.setOpacity(0.4)
        gradient = QLinearGradient(r.topLeft(), r.bottomLeft())
        gradient.setColorAt(0, mix(color, s.secondary, 0.16 * self.hover))
        gradient.setColorAt(1, QColor(color))
        p.fillPath(shape, gradient)
        p.setPen(QPen(mix(s.border, s.accent, self.hover), 1))
        p.drawPath(shape)
        p.setPen(QColor(ink))
        p.setFont(self.font())
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, self.text())
        if self.glitch_active:
            vertices, shift = _cyber_frame(self._glitch_elapsed, False)
            p.save()
            p.setClipPath(shape)
            p.setClipPath(_polygon(r, vertices), Qt.ClipOperation.IntersectClip)
            p.translate(shift * r.width() / 100, 0)
            p.fillPath(shape.translated(-2, 0), QColor(s.secondary))
            p.fillPath(shape, gradient)
            for offset, text_color in ((-2, s.secondary), (2, s.accent), (0, ink)):
                p.setPen(QColor(text_color))
                p.drawText(r.translated(offset, 0), Qt.AlignmentFlag.AlignCenter, self.text())
            p.restore()
        if self.hasFocus():
            p.setPen(QPen(QColor(s.accent), 1, Qt.PenStyle.DotLine))
            p.drawPath(outline(r.adjusted(3, 3, -3, -3), s))


class ElidedLabel(QLabel):
    """Long device/error strings must not force the floating widget off screen."""

    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)

    def setText(self, text):
        super().setText(text)
        self.setToolTip(text)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setFont(self.font())
        p.setPen(self.palette().color(self.foregroundRole()))
        p.drawText(
            self.rect(),
            Qt.AlignmentFlag.AlignVCenter,
            self.fontMetrics().elidedText(self.text(), Qt.TextElideMode.ElideRight, self.width()),
        )


class AudioBars(QWidget):
    """Level history, not a synthetic spectrum: silence always settles to a baseline."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.style = get_style("obsidian")
        self.history = [0.0] * 32
        self.setMinimumSize(80, 24)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setToolTip("Livello audio MIC + PC · cronologia del segnale")

    def push(self, level):
        self.history = self.history[1:] + [max(0.0, min(1.0, level))]
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        s = self.style
        p.setPen(Qt.PenStyle.NoPen)
        count = min(32, max(8, self.width() // 9))
        step = self.width() / count
        for index, value in enumerate(self.history[-count:]):
            height = max(2.0, value**0.65 * (self.height() - 6))
            color = mix(s.secondary, s.accent, index / count)
            color.setAlphaF(0.28 + 0.72 * min(1, value * 4))
            p.setBrush(color)
            x = index * step + 1
            if s.key == "studio":
                for y in range(0, round(height), 5):
                    p.drawRoundedRect(QRectF(x, self.height() - y - 4, step - 3, 3), 1, 1)
            else:
                p.drawRoundedRect(
                    QRectF(x, (self.height() - height) / 2, step - 3, height),
                    0 if s.key == "cyberpunk" else 2,
                    2,
                )


# Polygon and keyframe adaptation of andrew-demchenk0's lucky-bobcat-25.
# Uiverse MIT attribution: THIRD_PARTY_NOTICES.md.
_CYBER_OUTLINE = (
    (11, 0),
    (95, 0),
    (100, 25),
    (90, 90),
    (95, 90),
    (85, 90),
    (85, 100),
    (7, 100),
    (0, 80),
)
_CYBER_CLIPS = (
    ((0, 2), (100, 2), (100, 95), (95, 95), (95, 90), (85, 90), (85, 95), (8, 95), (0, 70)),
    ((0, 78), (100, 78), (100, 100), (95, 100), (95, 90), (85, 90), (85, 100), (8, 100), (0, 78)),
    ((0, 44), (100, 44), (100, 54), (95, 54), (95, 54), (85, 54), (85, 54), (8, 54), (0, 54)),
    ((0, 0), (100, 0), (100, 0), (95, 0), (95, 0), (85, 0), (85, 0), (8, 0), (0, 0)),
    ((0, 40), (100, 40), (100, 85), (95, 85), (95, 85), (85, 85), (85, 85), (8, 85), (0, 70)),
    ((0, 63), (100, 63), (100, 80), (95, 80), (95, 80), (85, 80), (85, 80), (8, 80), (0, 70)),
)
# percent, clip index, horizontal displacement (%); empty clips create quiet gaps.
_CYBER_KEYFRAMES = (
    (0, 0, 0),
    (2, 1, -5),
    (6, 1, 5),
    (8, 1, -5),
    (9, 1, 0),
    (10, 2, 5),
    (13, 2, 0),
    (14, 3, 5),
    (21, 3, 5),
    (25, 3, 5),
    (30, 3, -5),
    (31, 3, -5),
    (35, 4, -5),
    (40, 4, 5),
    (45, 4, -5),
    (50, 4, 0),
    (55, 5, 5),
    (60, 5, 0),
    (61, 3, 0),
    (100, 3, 0),
)


def _polygon(rect, vertices):
    path = QPainterPath()
    for index, (x, y) in enumerate(vertices):
        point = QPointF(rect.left() + x * rect.width() / 100, rect.top() + y * rect.height() / 100)
        if index == 0:
            path.moveTo(point)
        else:
            path.lineTo(point)
    path.closeSubpath()
    return path


def _cyber_frame(elapsed, active):
    percent = (elapsed % (5.0 if active else 2.0)) / (5.0 if active else 2.0) * 100
    for left, right in zip(_CYBER_KEYFRAMES, _CYBER_KEYFRAMES[1:]):
        if left[0] <= percent < right[0]:
            t = (percent - left[0]) / (right[0] - left[0])
            vertices = tuple(
                (x + (nx - x) * t, y + (ny - y) * t)
                for (x, y), (nx, ny) in zip(_CYBER_CLIPS[left[1]], _CYBER_CLIPS[right[1]])
            )
            return vertices, left[2] + (right[2] - left[2]) * t
    return _CYBER_CLIPS[3], 0


def paint_cyber_icon(
    p: QPainter,
    rect: QRectF,
    *,
    level=0.0,
    hovered=False,
    elapsed=0.0,
    recording=False,
    busy=False,
    reduce_motion=False,
    colors=None,
):
    """Glitched main icon only. The tray, panel and action buttons use their own painters."""
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.translate(rect.topLeft())
    p.scale(rect.width() / 64, rect.height() / 64)
    body = QRectF(5, 7, 50, 50)
    shape = _polygon(body, _CYBER_OUTLINE)
    active = recording or busy
    palette = icon_palette(get_style("cyberpunk"), colors)
    primary = palette["hover" if hovered else "active" if active else "body"]
    shadow = palette["active_edge" if active else "edge"]

    def content(offset=0.0, ink=None):
        ink = ink or palette["ink"]
        p.save()
        p.translate(offset, 0)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(ink))
        # Retain the audio identity and real level feedback inside the CSS silhouette.
        for index, weight in enumerate((0.4, 0.7, 1.0, 0.7, 0.4)):
            h = 4 + (12 + 10 * level) * weight
            p.drawRect(QRectF(15 + 6 * index, 29 - h / 2, 3, h))
        font = QFont("Consolas")
        font.setPixelSize(8)
        font.setBold(True)
        p.setFont(font)
        p.setPen(QColor(ink))
        p.drawText(
            QRectF(7, 40, 45, 11),
            Qt.AlignmentFlag.AlignCenter,
            "_REC" if recording else "_AI" if busy else "_CYBER",
        )
        p.restore()

    p.fillPath(shape.translated(3, 0), QColor(shadow))
    p.fillPath(shape, QColor(primary))
    content()
    if (hovered or active) and not reduce_motion:
        vertices, shift = _cyber_frame(elapsed, active)
        p.save()
        p.translate(shift * body.width() / 100, 0)
        p.setClipPath(_polygon(body.adjusted(-2, -2, 2, 2), vertices))
        p.fillPath(shape.translated(-2, 0), QColor(shadow))
        p.fillPath(shape, QColor(primary))
        content(2, shadow)
        content(-2, palette["glitch"])
        content()
        p.restore()
    # The small tag and REC dot stay legible even during interference.
    p.fillRect(QRectF(42, 7, 13, 7), QColor(shadow))
    font = QFont("Consolas")
    font.setPixelSize(6)
    font.setBold(True)
    p.setFont(font)
    p.setPen(QColor("#202025"))
    p.drawText(QRectF(42, 7, 13, 7), Qt.AlignmentFlag.AlignCenter, "r1")
    if recording:
        p.setBrush(QColor("#ff5067"))
        p.setPen(QPen(QColor("#14171a"), 2))
        p.drawEllipse(QPointF(57, 5), 4, 4)
    p.restore()


def paint_orb(
    p: QPainter,
    rect: QRectF,
    style: Style,
    *,
    level: float = 0,
    hover: float = 0,
    phase: float = 0,
    recording=False,
    busy=False,
    colors=None,
):
    palette = icon_palette(style, colors)
    custom = bool(colors)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    r = rect.adjusted(5, 5, -5, -5)
    path = QPainterPath()
    if style.key == "cyberpunk":
        path = outline(r, style)
    elif style.key in ("porcelain", "studio"):
        path.addRoundedRect(r, 17, 17)
    else:
        path.addEllipse(r)
    # Thin concentric halos react to hover and to the real input level.
    edge = palette["active_edge"] if (recording or busy) and custom else palette["edge"]
    glow = QColor(edge)
    for width, alpha in ((9, 12), (5, 22)):
        glow.setAlpha(round(alpha * (0.5 + hover + level)))
        p.setPen(QPen(glow, width))
        p.drawPath(path)
    gradient = QLinearGradient(r.topLeft(), r.bottomRight())
    gradient.setColorAt(
        0,
        mix(palette["active"] if recording or busy else palette["body"], palette["hover"], hover)
        if custom
        else mix(style.surface, style.secondary, 0.22 * hover),
    )
    gradient.setColorAt(1, QColor(palette["body"]))
    p.fillPath(path, gradient)
    p.setPen(QPen(mix(edge if custom else style.border, edge, 0.35 + 0.65 * hover), 1.3))
    p.drawPath(path)
    if busy:
        p.setPen(
            QPen(
                QColor(palette["active_edge"]), 2.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap
            )
        )
        p.drawArc(r.adjusted(3, 3, -3, -3), round(phase * 95) * 16, 100 * 16)
    cx, cy = r.center().x(), r.center().y()
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(palette["ink"]))
    for i, base in enumerate((0.35, 0.68, 1.0, 0.68, 0.35)):
        h = 5 + (11 + 17 * level + 5 * hover) * base
        p.drawRoundedRect(QRectF(cx - 16 + i * 7, cy - h / 2, 4, h), 2, 2)
    if recording:
        p.setBrush(QColor("#ff5067"))
        p.setPen(QPen(QColor(style.background), 2))
        p.drawEllipse(QPointF(r.right() - 3, r.top() + 5), 5, 5)


def dialog_css(style: Style) -> str:
    return f"""
        QDialog {{ background: {style.background}; }}
        QScrollArea#recapScroll, QWidget#recapForm {{ background: {style.background}; }}
        QLabel, QCheckBox {{ color: {style.text}; font-family: 'Segoe UI'; }}
        QPushButton {{ background: {style.surface}; color: {style.text};
            border: 1px solid {style.border}; border-radius: 8px; padding: 9px 16px; }}
        QPushButton:hover {{ border-color: {style.accent}; }}
        QPushButton:disabled {{ color: {style.muted}; }}
        QComboBox, QListWidget, QTableWidget, QPlainTextEdit {{ background: {style.surface};
            color: {style.text}; border: 1px solid {style.border}; border-radius: 8px; }}
        QComboBox {{ padding: 7px; }}
        QComboBox QAbstractItemView {{ background: {style.surface}; color: {style.text}; }}
        QListWidget::item {{ padding: 15px 10px; border-radius: 6px; }}
        QListWidget::item:selected {{ background: {style.border}; color: {style.text}; }}
        QHeaderView::section {{ background: {style.surface}; color: {style.text}; padding: 8px; }}
    """
