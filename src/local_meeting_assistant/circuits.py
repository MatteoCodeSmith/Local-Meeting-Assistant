"""Compact Qt circuit halo inspired by the CSS/SVG supplied by om_6153 (Uiverse).

Paths run from an outer contact toward the central icon. Only the AI processing
state enables inward light pulses; hover and audio levels do not drive this halo.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen

ICON_SIZE = 84
CORE_SIZE = 64


def core_rect(rect: QRectF) -> QRectF:
    side = min(rect.width(), rect.height()) * CORE_SIZE / ICON_SIZE
    return QRectF(rect.center().x() - side / 2, rect.center().y() - side / 2, side, side)


@dataclass(frozen=True)
class CircuitRoute:
    points: tuple[QPointF, ...]
    distances: tuple[float, ...]
    path: QPainterPath

    def point_at(self, progress: float) -> QPointF:
        distance = max(0.0, min(1.0, progress)) * self.distances[-1]
        for i in range(1, len(self.points)):
            if distance <= self.distances[i]:
                fraction = (distance - self.distances[i - 1]) / (
                    self.distances[i] - self.distances[i - 1]
                )
                return self.points[i - 1] + (self.points[i] - self.points[i - 1]) * fraction
        return self.points[-1]


@lru_cache(maxsize=1)
def routes() -> tuple[CircuitRoute, ...]:
    # Angular branches and endpoint contacts echo the source at a much smaller size.
    upper = (
        ((20, 7), (20, 11), (27, 18), (27, 27)),
        ((32, 5), (32, 10), (35, 13), (35, 22)),
        ((42, 2.5), (42, 21)),
        ((51, 6), (51, 12), (49, 14), (49, 23)),
        ((63, 9), (63, 12), (57, 18), (57, 27)),
    )
    result = []
    for quarter in range(4):
        for branch in upper:
            points = []
            for x, y in branch:
                for _ in range(quarter):
                    x, y = 84 - y, x
                points.append(QPointF(x, y))
            path = QPainterPath(points[0])
            distances = [0.0]
            for previous, point in zip(points, points[1:]):
                path.lineTo(point)
                distances.append(
                    distances[-1] + math.hypot(point.x() - previous.x(), point.y() - previous.y())
                )
            result.append(CircuitRoute(tuple(points), tuple(distances), path))
    return tuple(result)


def pulse_progress(elapsed: float, index: int) -> float | None:
    # Stagger contacts, then accelerate inward. Each cycle includes a quiet interval.
    phase = elapsed - (index % 5) * 0.075
    if phase < 0:
        return None
    progress = (phase % 2.0) / 1.45
    return progress**0.85 if progress <= 1 else None


def paint_circuits(
    p: QPainter,
    rect: QRectF,
    palette: dict[str, str],
    *,
    busy=False,
    elapsed=0.0,
    reduce_motion=False,
):
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.translate(rect.topLeft())
    p.scale(rect.width() / ICON_SIZE, rect.height() / ICON_SIZE)
    color = QColor(palette["active_edge" if busy else "edge"])
    p.setBrush(Qt.BrushStyle.NoBrush)
    for index, route in enumerate(routes()):
        wire = QColor(color)
        wire.setAlpha(145 if busy else 88)
        p.setPen(
            QPen(
                wire,
                0.75,
                Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.RoundCap,
                Qt.PenJoinStyle.RoundJoin,
            )
        )
        p.drawPath(route.path)
        contact = QColor(color)
        contact.setAlpha(185 if busy else 115)
        p.setPen(QPen(contact, 0.8))
        p.drawEllipse(route.points[0], 1.15, 1.15)
        if not busy or reduce_motion:
            continue
        progress = pulse_progress(elapsed, index)
        if progress is None:
            continue
        point = route.point_at(progress)
        glow = QColor(color)
        glow.setAlpha(30)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(glow)
        p.drawEllipse(point, 2.5, 2.5)
        for trail in range(5):
            start = max(0, progress - 0.24 + trail * 0.048)
            end = min(progress, start + 0.048)
            glow.setAlpha(40 + trail * 38)
            p.setPen(QPen(glow, 1.15, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawLine(route.point_at(start), route.point_at(end))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(palette["ink"]))
        p.drawEllipse(point, 0.95, 0.95)
        p.setBrush(Qt.BrushStyle.NoBrush)
    p.restore()
