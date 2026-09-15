from __future__ import annotations

from pathlib import Path
from html import escape
from textwrap import wrap
import math
import time
from copy import deepcopy

from PySide6.QtCore import QObject, QPoint, QRect, QRectF, Qt, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import (
    QColor,
    QCursor,
    QDesktopServices,
    QEnterEvent,
    QIcon,
    QMouseEvent,
    QPainter,
    QPixmap,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QColorDialog,
    QDialog,
    QFrame,
    QGridLayout,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QLineEdit,
    QInputDialog,
    QScrollArea,
    QSizePolicy,
    QMenu,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QSystemTrayIcon,
    QTableWidget,
    QTableWidgetItem,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from .config import AppConfig
from .controller import AssistantController
from .domain import SessionRecord, SessionStatus
from .summarizer import LMStudioSummarizer, LMStudioError
from .model_catalog import model_details, text_models
from .participants import Participants, clean_name, read_segments, segment_key
from .circuits import ICON_SIZE, core_rect, paint_circuits
from .appearance import (
    STYLES,
    ICON_COLOR_LABELS,
    ICON_PRESETS,
    AnimatedButton,
    AudioBars,
    ElidedLabel,
    Surface,
    dialog_css,
    get_style,
    icon_palette,
    paint_cyber_icon,
    paint_orb,
    panel_style,
)


def _icon_pixmap(
    size: int, ring_color: str = "#35e7ef", style_key="obsidian", colors=None
) -> QPixmap:
    result = QPixmap(size, size)
    result.fill(Qt.GlobalColor.transparent)
    painter = QPainter(result)
    painter.scale(size / 64, size / 64)
    paint_orb(
        painter,
        QRectF(0, 0, 64, 64),
        get_style(style_key),
        recording=ring_color == "#ff4057",
        colors=colors,
    )
    painter.end()
    return result


def make_tray_icon(color: str = "#35e7ef", style_key="obsidian", colors=None) -> QIcon:
    return QIcon(_icon_pixmap(64, color, style_key, colors))


class HoverIcon(QWidget):
    def __init__(self, overlay: OverlayWindow) -> None:
        super().__init__(overlay)
        self.overlay = overlay
        self.ring_color = "#35e7ef"
        self._glitch_mode = None
        self._glitch_origin = 0.0
        self.setFixedSize(ICON_SIZE, ICON_SIZE)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setToolTip("Passa sopra per aprire — trascina per spostare")

    def set_ring_color(self, color: str) -> None:
        if color != self.ring_color:
            self.ring_color = color
            self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        bounds = QRectF(self.rect())
        palette = icon_palette(
            self.overlay.style, self.overlay.config.icon_colors.get(self.overlay.style.key)
        )
        paint_circuits(
            painter,
            bounds,
            palette,
            busy=self.overlay._ai_active,
            elapsed=self.overlay._ai_phase,
            reduce_motion=self.overlay.config.reduce_motion,
        )
        # The original icon stays 64 px; the fine circuit traces add a 10 px border.
        bounds = core_rect(bounds)
        if self.overlay.style.key == "cyberpunk":
            hovered = self.underMouse() or self.overlay._dragging
            active = self.overlay._recording or self.overlay._ai_active
            mode = "active" if active else "hover" if hovered else "idle"
            if mode != self._glitch_mode:
                self._glitch_origin = self.overlay._phase
                self._glitch_mode = mode
            paint_cyber_icon(
                painter,
                bounds,
                level=self.overlay._display_level,
                hovered=hovered,
                elapsed=self.overlay._phase - self._glitch_origin,
                recording=self.overlay._recording,
                busy=self.overlay._ai_active,
                reduce_motion=self.overlay.config.reduce_motion,
                colors=self.overlay.config.icon_colors.get(self.overlay.style.key),
            )
            painter.end()
            return
        painter.save()
        painter.translate(bounds.topLeft())
        painter.scale(bounds.width() / 64, bounds.height() / 64)
        paint_orb(
            painter,
            QRectF(0, 0, 64, 64),
            self.overlay.style,
            level=self.overlay._display_level,
            hover=self.overlay._hover_amount,
            phase=self.overlay._phase,
            recording=self.overlay._recording,
            busy=self.overlay._ai_active,
            colors=self.overlay.config.icon_colors.get(self.overlay.style.key),
        )
        painter.restore()
        painter.end()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if self.overlay.preview:
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton:
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            self.overlay.begin_drag(event.globalPosition().toPoint())
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self.overlay.preview:
            event.accept()
            return
        if event.buttons() & Qt.MouseButton.LeftButton:
            self.overlay.continue_drag(event.globalPosition().toPoint())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self.overlay.preview:
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton:
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            self.overlay.end_drag()
            event.accept()
            return
        super().mouseReleaseEvent(event)


class StatusLamp(QWidget):
    def __init__(self, label: str, active_color: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.active_color = active_color
        self.dot = QLabel("●")
        self.dot.setFixedWidth(13)
        self.text = QLabel(label)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        layout.addWidget(self.dot)
        layout.addWidget(self.text)
        self.set_active(False)

    def set_active(self, active: bool, text: str | None = None) -> None:
        self.dot.setStyleSheet(f"color: {self.active_color if active else '#606773'};")
        if text is not None:
            self.text.setText(text)


class ArchiveDialog(QDialog):
    """Browse saved sessions and rerun either the whole pipeline or only Gemma."""

    def __init__(self, controller: AssistantController, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.controller = controller
        self._records: list[SessionRecord] = []
        self.setWindowTitle("Archivio registrazioni")
        self.setWindowIcon(make_tray_icon())
        self.resize(960, 520)
        self.setStyleSheet(
            """
            QDialog { background: #171a20; }
            QLabel { color: #dce2ea; }
            QTableWidget {
                background: #20242c; alternate-background-color: #262b35;
                color: #eef1f5; border: 1px solid #3b414d; gridline-color: #343a46;
            }
            QHeaderView::section {
                background: #2c323d; color: #eef1f5; border: none;
                border-right: 1px solid #454c59; padding: 7px;
            }
            QPushButton {
                color: white; background: #343a46; border: 1px solid #4a5261;
                border-radius: 7px; padding: 7px 12px; font-weight: 600;
            }
            QPushButton:hover { background: #424a59; }
            QPushButton:disabled { color: #777d87; background: #272b33; }
            """
        )
        layout = QVBoxLayout(self)
        intro = QLabel(
            "Le operazioni sono locali. La trascrizione usa gli audio FLAC; "
            "il recap parte solo su richiesta, a registrazione ferma, con LM Studio avviato."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["Data", "Titolo", "Origine", "Audio", "Trascrizione", "Recap"]
        )
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for column in range(2, 6):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        self.table.cellDoubleClicked.connect(lambda _row, _column: self._open_selected())
        layout.addWidget(self.table)

        controls = QHBoxLayout()
        self.refresh_button = QPushButton("Aggiorna")
        self.transcribe_button = QPushButton("Trascrivi audio")
        self.recap_button = QPushButton("Genera solo recap")
        self.names_button = QPushButton("Nomi parlanti")
        self.versions_button = QPushButton("Recap salvati")
        self.open_button = QPushButton("Apri cartella")
        self.close_button = QPushButton("Chiudi")
        self.rename_button = QPushButton("Rinomina")
        self.delete_button = QPushButton("Elimina")
        self.voices_button = QPushButton("Analizza voci")
        controls.addWidget(self.refresh_button)
        controls.addStretch(1)
        controls.addWidget(self.transcribe_button)
        controls.addWidget(self.recap_button)
        controls.addWidget(self.names_button)
        controls.addWidget(self.versions_button)
        layout.addLayout(controls)
        management = QHBoxLayout()
        management.addWidget(self.rename_button)
        management.addWidget(self.delete_button)
        management.addWidget(self.open_button)
        management.addWidget(self.voices_button)
        management.addStretch(1)
        management.addWidget(self.close_button)
        layout.addLayout(management)
        self.status_label = QLabel("Seleziona una registrazione.")
        self.status_label.setStyleSheet("color: #aeb6c3;")
        layout.addWidget(self.status_label)

        self.refresh_button.clicked.connect(self.refresh)
        self.transcribe_button.clicked.connect(self._retranscribe_selected)
        self.recap_button.clicked.connect(self._recap_selected)
        self.names_button.clicked.connect(self._names_selected)
        self.versions_button.clicked.connect(self._recap_versions)
        self.open_button.clicked.connect(self._open_selected)
        self.close_button.clicked.connect(self.close)
        self.rename_button.clicked.connect(self._rename_selected)
        self.delete_button.clicked.connect(self._delete_selected)
        self.voices_button.clicked.connect(self._voices_selected)
        self.controller.session_finished.connect(self._session_finished)
        if hasattr(self.controller, "voices_finished"):
            self.controller.voices_finished.connect(lambda _session_id: self.refresh())
        self.controller.message.connect(self.status_label.setText)
        self.refresh()

    def refresh(self) -> None:
        previous_id = self._selected_record().session_id if self._selected_record() else None
        self._records = self.controller.list_sessions()
        self.table.setRowCount(len(self._records))
        selected_row = -1
        for row, record in enumerate(self._records):
            mic = record.directory / "mic.flac"
            system = record.directory / "system.flac"
            audio_parts = []
            if mic.is_file() and mic.stat().st_size > 0:
                audio_parts.append("MIC")
            if system.is_file() and system.stat().st_size > 0:
                audio_parts.append("PC")
            transcript = (record.directory / "transcript.md").is_file()
            recap = (record.directory / "recap.md").is_file()
            values = (
                record.started_at.astimezone().strftime("%d/%m/%Y %H:%M"),
                record.title,
                "Teams" if record.source.value == "teams" else "Manuale",
                " + ".join(audio_parts) if audio_parts else "Mancante",
                "Presente" if transcript else "Mancante",
                "Presente" if recap else "Mancante",
            )
            for column, value in enumerate(values):
                self.table.setItem(row, column, QTableWidgetItem(value))
            if previous_id == record.session_id:
                selected_row = row
        if selected_row >= 0:
            self.table.selectRow(selected_row)
        elif self._records:
            self.table.selectRow(0)
        self._selection_changed()

    def _selected_record(self) -> SessionRecord | None:
        row = self.table.currentRow()
        return self._records[row] if 0 <= row < len(self._records) else None

    def _selection_changed(self) -> None:
        record = self._selected_record()
        has_audio = bool(
            record
            and any(
                path.is_file() and path.stat().st_size > 0
                for path in (record.directory / "mic.flac", record.directory / "system.flac")
            )
        )
        has_transcript = bool(record and (record.directory / "transcript.md").is_file())
        busy = bool(
            record
            and record.status
            in (SessionStatus.RECORDING, SessionStatus.TRANSCRIBING, SessionStatus.SUMMARIZING)
        )
        busy = busy or (bool(record) and bool(getattr(self.controller, "is_session_busy", lambda r: False)(record)))
        self.transcribe_button.setEnabled(has_audio and not busy)
        self.recap_button.setEnabled(has_transcript and not busy)
        self.names_button.setEnabled(record is not None and not busy)
        self.versions_button.setEnabled(record is not None)
        self.open_button.setEnabled(record is not None)
        self.rename_button.setEnabled(record is not None and not busy)
        self.delete_button.setEnabled(record is not None and not busy)
        self.voices_button.setEnabled(has_audio)
        if record:
            self.status_label.setText(record.error or f"Stato: {record.status.value}")
        else:
            self.status_label.setText("Nessuna registrazione selezionata.")

    def _rename_selected(self):
        record = self._selected_record()
        if not record or not self.rename_button.isEnabled():
            return
        title, accepted = QInputDialog.getText(self, "Rinomina meeting", "Nome del meeting:", text=record.title)
        if accepted and self.controller.rename_session(record, title):
            self.refresh()
            self.status_label.setText("Nome aggiornato nell'archivio. I file già generati restano invariati.")

    def _delete_selected(self):
        record = self._selected_record()
        if not record or not self.delete_button.isEnabled():
            return
        confirmation = QMessageBox(self)
        confirmation.setWindowTitle("Elimina registrazione")
        confirmation.setIcon(QMessageBox.Icon.Warning)
        confirmation.setTextFormat(Qt.TextFormat.PlainText)
        confirmation.setText(f"Spostare nel Cestino il meeting «{record.title}»?\n\n"
                             "Include audio, trascrizioni, tutti i recap e le copie della sessione. "
                             "Potrai ripristinare la cartella dal Cestino di Windows.")
        confirmation.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        confirmation.setDefaultButton(QMessageBox.StandardButton.No)
        if confirmation.exec() == QMessageBox.StandardButton.Yes:
            if self.controller.delete_session(record):
                self.refresh()
                self.status_label.setText("Registrazione spostata nel Cestino, da cui puoi recuperarla.")

    def _retranscribe_selected(self) -> None:
        record = self._selected_record()
        if not record:
            return
        if self.controller.retranscribe_session(record):
            record.status = SessionStatus.TRANSCRIBING
            self.status_label.setText("Trascrizione completa messa in coda…")
            self._selection_changed()

    def _recap_selected(self) -> None:
        record = self._selected_record()
        if not record:
            return
        if self.controller.is_recording:
            self.status_label.setText("Registrazione in corso: Whisper ha priorità.")
            return
        if RecapDialog(self.controller.config, self).exec() != QDialog.DialogCode.Accepted:
            return
        if self.controller.generate_recap(record):
            record.status = SessionStatus.SUMMARIZING
            self.status_label.setText("Generazione del recap messa in coda…")
            self._selection_changed()

    def _open_selected(self) -> None:
        record = self._selected_record()
        if record:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(record.directory)))

    def _names_selected(self):
        record = self._selected_record()
        if record:
            ParticipantsDialog(self.controller, record, self).exec()
            self.refresh()

    def _voices_selected(self):
        record = self._selected_record()
        if record:
            from .voice_ui import VoiceDialog
            dialog = VoiceDialog(self.controller, record, self)
            dialog.exec()
            dialog.deleteLater()
            self.refresh()

    def _recap_versions(self):
        record = self._selected_record()
        if record:
            RecapHistoryDialog(self.controller.repository, record, self).exec()

    @Slot(object, object)
    def _session_finished(self, _record: object, _recap_path: object) -> None:
        self.refresh()


class OverlayWindow(QWidget):
    appearance_changed = Signal(str)
    COLLAPSED_SIZE = ICON_SIZE + 4
    EXPANDED_WIDTH = 700
    EXPANDED_HEIGHT = 84

    def __init__(
        self, controller: AssistantController, config: AppConfig, *, preview=False
    ) -> None:
        super().__init__()
        self.controller = controller
        self.config = config
        self.preview = preview
        self.style = get_style(config.appearance_style)
        self._levels = {"mic": (0.0, 0.0), "system": (0.0, 0.0)}
        self._display_level = 0.0
        self._hover_amount = 0.0
        self._phase = 0.0
        self._ai_phase = 0.0
        self._appearance_dialog = None
        self._drag_position: QPoint | None = None
        self._dragging = False
        self._expanded = True
        self._recording = False
        self._mic_active = False
        self._ai_active = False
        self._teams_active = False
        self._archive_dialog: ArchiveDialog | None = None
        self._live_dialog = None
        self._active_voice_names = {}
        self._status_detail = "Pronta · REC funziona anche senza Teams"
        self.setWindowTitle("Local Meeting Assistant")
        self.setWindowIcon(make_tray_icon())
        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        if preview:
            self.setWindowFlags(Qt.WindowType.Widget)
        self.setMouseTracking(True)
        self._collapse_timer = QTimer(self)
        self._collapse_timer.setSingleShot(True)
        self._collapse_timer.setInterval(420)
        self._collapse_timer.timeout.connect(self._collapse_if_outside)
        self._build_ui()
        self._connect()
        self.apply_style(self.style.key)
        self._set_expanded(False)
        self._animation_timer = QTimer(self)
        self._animation_timer.setInterval(40)
        self._animation_timer.timeout.connect(self._animate)
        self._animation_timer.start()

    def _build_ui(self) -> None:
        outer = QHBoxLayout(self)
        outer.setContentsMargins(2, 2, 2, 2)
        outer.setSpacing(6)
        self.panel = Surface(self.style)
        self.panel.setObjectName("panel")
        self.panel_grid = QGridLayout(self.panel)
        self.panel_grid.setContentsMargins(18, 12, 18, 12)
        self.panel_grid.setSpacing(8)

        self.heading = QWidget()
        heading_layout = QHBoxLayout(self.heading)
        heading_layout.setContentsMargins(0, 0, 0, 0)
        self.brand = QLabel("BIONIC")
        self.style_caption = QLabel("OBSIDIAN / 01")
        heading_layout.addWidget(self.brand)
        heading_layout.addStretch(1)
        heading_layout.addWidget(self.style_caption)

        self.indicators = QWidget()
        top = QHBoxLayout(self.indicators)
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(12)
        self.rec_lamp = StatusLamp("REC", "#ff4057")
        self.mic_lamp = StatusLamp("MIC", "#45d483")
        self.ai_lamp = StatusLamp("STT", "#50b8ff")
        self.voice_lamp = StatusLamp("VOCI", "#62daca")
        self.teams_lamp = StatusLamp("TEAMS", "#8f7cff")
        self.mic_meter = AudioBars()
        top.addWidget(self.rec_lamp)
        top.addWidget(self.mic_lamp)
        top.addWidget(self.ai_lamp)
        top.addWidget(self.voice_lamp)
        top.addWidget(self.teams_lamp)
        top.addStretch(1)
        self.controls = QWidget()
        buttons = QHBoxLayout(self.controls)
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.setSpacing(6)
        self.record_button = AnimatedButton("● REC", "record")
        self.record_button.setObjectName("record")
        self.stop_button = AnimatedButton("■ STOP")
        self.cancel_button = AnimatedButton("Annulla", "cancel")
        self.cancel_button.setObjectName("cancel")
        self.archive_button = AnimatedButton("Archivio")
        self.stop_button.setEnabled(False)
        self.cancel_button.setEnabled(False)
        for button in (
            self.record_button,
            self.stop_button,
            self.cancel_button,
            self.archive_button,
        ):
            buttons.addWidget(button)
        self.footer = QWidget()
        footer = QHBoxLayout(self.footer)
        footer.setContentsMargins(0, 0, 0, 0)
        self.status_label = ElidedLabel("Pronta · REC funziona anche senza Teams")
        self.appearance_button = AnimatedButton("Aspetto")
        self.appearance_button.setFixedWidth(76)
        self.appearance_button.setToolTip("Impostazioni · scegli uno dei cinque stili")
        self.live_button = AnimatedButton("Live")
        self.live_button.setFixedWidth(56)
        self.live_button.setToolTip("Trascrizione in diretta con i nomi della rubrica")
        self.live_button.setEnabled(hasattr(self.controller, "live_transcript_snapshot"))
        self.live_button.clicked.connect(self.show_live_transcript)
        footer.addWidget(self.status_label, 1)
        footer.addWidget(self.live_button)
        footer.addWidget(self.appearance_button)
        self.icon = HoverIcon(self)
        outer.addWidget(self.panel)
        outer.addWidget(self.icon, 0, Qt.AlignmentFlag.AlignTop)

    def apply_style(self, key: str) -> None:
        self.style = get_style(key)
        self.panel_style = s = panel_style(self.style, self.config.icon_colors.get(self.style.key))
        self.panel.style = s
        self.mic_meter.style = s
        self.setStyleSheet(
            f"QLabel {{ color: {s.text}; font-family: '{s.font}'; font-size: 10px; }}"
        )
        self.brand.setStyleSheet(
            f"font-family: '{s.font}'; font-weight: 700; font-size: 13px; letter-spacing: 3px;"
        )
        self.style_caption.setText(f"{s.name.upper()} / 0{list(STYLES).index(s.key) + 1}")
        self.style_caption.setStyleSheet(f"color: {s.muted}; font-size: 9px; letter-spacing: 1px;")
        self.status_label.setStyleSheet(f"color: {s.muted}; font-size: 10px;")
        for button in (
            self.record_button,
            self.stop_button,
            self.cancel_button,
            self.archive_button,
            self.appearance_button,
            self.live_button,
        ):
            button.set_style(s, self.config.reduce_motion)
        grid = self.panel_grid
        while grid.count():
            grid.takeAt(0)
        for row in range(6):
            grid.setRowStretch(row, 0)
        for column in range(3):
            grid.setColumnStretch(column, 0)
        if s.key == "obsidian":
            grid.addWidget(self.heading, 0, 0)
            grid.addWidget(self.indicators, 1, 0)
            grid.addWidget(self.mic_meter, 0, 1, 2, 1)
            grid.addWidget(self.controls, 0, 2, 2, 1)
            grid.addWidget(self.footer, 2, 0, 1, 3)
            grid.setColumnStretch(1, 1)
        elif s.key == "aurora":
            grid.addWidget(self.heading, 0, 0)
            grid.addWidget(self.indicators, 1, 0)
            grid.addWidget(self.mic_meter, 0, 1, 2, 1)
            grid.addWidget(self.controls, 2, 0, 1, 2)
            grid.addWidget(self.footer, 3, 0, 1, 2)
            grid.setColumnStretch(1, 1)
        elif s.key == "cyberpunk":
            grid.addWidget(self.heading, 0, 0, 1, 2)
            grid.addWidget(self.mic_meter, 1, 0, 2, 1)
            grid.addWidget(self.indicators, 1, 1)
            grid.addWidget(self.controls, 2, 1)
            grid.addWidget(self.footer, 3, 0, 1, 2)
            grid.setColumnStretch(0, 1)
        else:
            order = (self.heading, self.mic_meter, self.indicators, self.controls, self.footer)
            if s.key == "porcelain":
                order = (self.heading, self.indicators, self.mic_meter, self.controls, self.footer)
            for row, widget in enumerate(order):
                grid.addWidget(widget, row, 0)
            grid.setRowStretch(1 if s.key == "studio" else 2, 1)
        self.EXPANDED_WIDTH, self.EXPANDED_HEIGHT = s.width, s.height
        expanded = self._expanded
        self._expanded = not expanded
        self._set_expanded(expanded)
        self.panel.update()
        self.icon.update()
        self._refresh_icon()
        if self._archive_dialog:
            self._archive_dialog.setStyleSheet(dialog_css(s))

    def _animate(self):
        if not self.isVisible():
            return
        now = time.monotonic()
        target = max(
            value if now - timestamp < 0.7 else 0.0 for value, timestamp in self._levels.values()
        )
        self._display_level += (target - self._display_level) * (
            0.55 if target > self._display_level else 0.2
        )
        hover = float(self._expanded or self._dragging)
        if self.config.reduce_motion:
            self._hover_amount = hover
        else:
            self._hover_amount += (hover - self._hover_amount) * 0.22
        if not self.config.reduce_motion:
            self._phase += 0.04
            if self._ai_active:
                self._ai_phase += 0.04
        self.mic_meter.push(self._display_level)
        self.icon.update()

    def _connect(self) -> None:
        if not self.preview:
            self.record_button.clicked.connect(self.controller.start_manual)
            self.stop_button.clicked.connect(self.controller.stop_and_process)
            self.cancel_button.clicked.connect(self._confirm_cancel)
            self.archive_button.clicked.connect(self.show_archive)
            self.appearance_button.clicked.connect(self.show_appearance)
        self.controller.recording_changed.connect(self._recording_changed)
        self.controller.track_changed.connect(self._track_changed)
        self.controller.level_changed.connect(self._level_changed)
        self.controller.ai_changed.connect(self._ai_changed)
        if hasattr(self.controller, "voice_changed"):
            self.controller.voice_changed.connect(self._voice_changed)
        if hasattr(self.controller, "live_speaker_changed"):
            self.controller.live_speaker_changed.connect(self._live_speaker_changed)
        self.controller.teams_changed.connect(self._teams_changed)
        self.controller.message.connect(self._set_status_message)
        self.controller.error.connect(self._show_error)

    def place_top_right(self) -> None:
        if self.config.overlay_x is not None and self.config.overlay_y is not None:
            saved = QPoint(self.config.overlay_x, self.config.overlay_y)
            for screen in QApplication.screens():
                if screen.availableGeometry().contains(saved):
                    self.move(saved)
                    return
        screen = QApplication.primaryScreen()
        if screen:
            area = screen.availableGeometry()
            self.move(area.right() - self.width() - 18, area.top() + 18)

    def enterEvent(self, event: QEnterEvent) -> None:
        self._collapse_timer.stop()
        self._set_expanded(True)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        if not self.preview:
            self._collapse_timer.start()
        super().leaveEvent(event)

    def _collapse_if_outside(self) -> None:
        local_cursor = self.mapFromGlobal(QCursor.pos())
        if not self._dragging and not self.rect().contains(local_cursor):
            self._set_expanded(False)

    def _set_expanded(self, expanded: bool) -> None:
        if expanded == self._expanded and self.width() > 0:
            return
        anchor = self.geometry().topRight()
        self._expanded = expanded
        self.panel.setVisible(expanded)
        if expanded:
            self.setFixedSize(self.EXPANDED_WIDTH, self.EXPANDED_HEIGHT)
        else:
            self.setFixedSize(self.COLLAPSED_SIZE, self.COLLAPSED_SIZE)
        if anchor.x() or anchor.y():
            self.move(anchor.x() - self.width() + 1, anchor.y())
        if not self.preview and self.isVisible():
            screen = QApplication.screenAt(anchor) or QApplication.primaryScreen()
            if screen:
                area = screen.availableGeometry()
                self.move(
                    max(area.left(), min(self.x(), area.right() - self.width() + 1)),
                    max(area.top(), min(self.y(), area.bottom() - self.height() + 1)),
                )

    def begin_drag(self, global_position: QPoint) -> None:
        self._dragging = True
        self._drag_position = global_position - self.frameGeometry().topLeft()

    def continue_drag(self, global_position: QPoint) -> None:
        if self._drag_position is not None:
            self.move(global_position - self._drag_position)

    def end_drag(self) -> None:
        self._dragging = False
        self._drag_position = None
        self.config.overlay_x = self.x()
        self.config.overlay_y = self.y()
        self.config.save()

    def _refresh_icon(self) -> None:
        color = (
            "#ff4057"
            if self._recording
            else "#50b8ff"
            if self._ai_active
            else "#45d483"
            if self._mic_active
            else "#8f7cff"
            if self._teams_active
            else "#35e7ef"
        )
        self.icon.set_ring_color(color)
        self.setWindowIcon(
            make_tray_icon(color, self.style.key, self.config.icon_colors.get(self.style.key))
        )

    @Slot(bool, str)
    def _recording_changed(self, active: bool, source: str) -> None:
        self._recording = active
        self._active_voice_names.clear()
        self._set_status_message(self._status_detail)
        label = "REC TEAMS" if source == "teams" and active else "REC"
        self.rec_lamp.set_active(active, label)
        self.record_button.setEnabled(not active)
        self.stop_button.setEnabled(active)
        self.cancel_button.setEnabled(active)
        self._refresh_icon()

    @Slot(str, bool)
    def _track_changed(self, track: str, active: bool) -> None:
        if track == "mic":
            self._mic_active = active
            self.mic_lamp.set_active(active, "MIC" if active else "MIC OFF")
            self._refresh_icon()

    @Slot(str, float)
    def _level_changed(self, track: str, level: float) -> None:
        if track in self._levels:
            self._levels[track] = (max(0.0, min(1.0, level)), time.monotonic())

    @Slot(bool, str)
    def _ai_changed(self, active: bool, status: str) -> None:
        if not active or not self._ai_active:
            self._ai_phase = 0.0
        self._ai_active = active
        self.ai_lamp.set_active(active, "AI" if active else "STT")
        self.ai_lamp.setToolTip(status)
        self._refresh_icon()

    @Slot(bool)
    def _teams_changed(self, active: bool) -> None:
        self._teams_active = active
        self.teams_lamp.set_active(active)
        self._refresh_icon()

    @Slot(str)
    def _show_error(self, detail: str) -> None:
        self.status_label.setText(detail)

    def _voice_changed(self, active, detail):
        self.voice_lamp.set_active(active)
        self.voice_lamp.setToolTip(detail)
        if not active:
            self._active_voice_names.clear()
            self._set_status_message(self._status_detail)

    def _set_status_message(self, detail):
        self._status_detail = detail
        names = " · ".join(f"{'MIC' if track == 'mic' else 'PC'}: {self._active_voice_names[track]}"
                           for track in ("mic", "system") if self._active_voice_names.get(track))
        self.status_label.setText(names if self._recording and names else detail)

    def _live_speaker_changed(self, track, name):
        self._active_voice_names[track] = name
        self._set_status_message(self._status_detail)

    def show_live_transcript(self):
        if not hasattr(self.controller, "live_transcript_snapshot"):
            return
        if self._live_dialog is None:
            from .live_transcript_ui import LiveTranscriptDialog
            self._live_dialog = LiveTranscriptDialog(self.controller, self)
        self._live_dialog.setStyleSheet(dialog_css(self.panel_style))
        self._live_dialog.show()
        self._live_dialog.raise_()
        self._live_dialog.activateWindow()

    def _confirm_cancel(self) -> None:
        answer = QMessageBox.question(
            self,
            "Annullare la sessione?",
            "La registrazione corrente verra interrotta e spostata nel Cestino.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.controller.cancel_and_delete()

    def show_archive(self) -> None:
        if self._archive_dialog is None:
            self._archive_dialog = ArchiveDialog(self.controller, self)
        else:
            self._archive_dialog.refresh()
        self._archive_dialog.setStyleSheet(dialog_css(self.panel_style))
        self._archive_dialog.show()
        self._archive_dialog.raise_()
        self._archive_dialog.activateWindow()

    def show_appearance(self) -> None:
        if self._appearance_dialog is None:
            self._appearance_dialog = AppearanceDialog(self)
        self._appearance_dialog.sync_selection()
        self._appearance_dialog.show()
        self._appearance_dialog.raise_()
        self._appearance_dialog.activateWindow()

    def show_recognition(self) -> None:
        dialog = RecognitionDialog(self.controller, self.config, self)
        dialog.setStyleSheet(dialog_css(self.panel_style))
        dialog.exec()

    def show_participants(self):
        record = self.controller.current_session
        if record is None:
            QMessageBox.information(self, "Nomi parlanti", "Per una registrazione salvata apri "
                "Archivio → Nomi parlanti. Per impostare il tuo nome predefinito apri "
                "Impostazioni → Trascrizione e nomi Teams.")
            return
        dialog = ParticipantsDialog(self.controller, record, self)
        dialog.setStyleSheet(dialog_css(self.panel_style))
        dialog.exec()


class ParticipantsDialog(QDialog):
    def __init__(self, controller, record, parent=None):
        super().__init__(parent)
        self.controller, self.record = controller, record
        self.setWindowTitle("Nomi dei parlanti · " + record.title)
        self.resize(900, 640)
        self.participants = Participants.load(record.directory)
        self.segments = read_segments(record.directory)
        layout = QVBoxLayout(self)
        help_text = QLabel("Questi nomi valgono SOLO per questa registrazione. Non sono "
            "riconoscimento vocale. Con più persone puoi assegnare un nome ai singoli "
            "passaggi nella tabella, anche senza Teams. I nomi saranno usati dal prossimo recap.")
        help_text.setWordWrap(True)
        layout.addWidget(help_text)
        self.local_name = QLineEdit(self.participants.local_name)
        self.local_name.setPlaceholderText("Il tuo nome / persona al microfono")
        self.local_name.setMaxLength(100)
        self.only_me = QCheckBox("Al microfono di questa registrazione parlo soltanto io")
        self.only_me.setChecked(self.participants.microphone_is_me)
        layout.addWidget(self.local_name)
        layout.addWidget(self.only_me)
        self.remote_name = QLineEdit(self.participants.remote_name)
        self.remote_name.setMaxLength(100)
        self.remote_name.setPlaceholderText("Nome interlocutore remoto, solo per chiamata con una persona")
        self.single_remote = QCheckBox("Confermo: tutto l'audio PC appartiene a questo unico interlocutore")
        self.single_remote.setChecked(self.participants.single_remote)
        layout.addWidget(self.remote_name)
        layout.addWidget(self.single_remote)
        self.table = QTableWidget(len(self.segments), 4)
        self.table.setHorizontalHeaderLabels(["Tempo", "Traccia / nome attuale", "Passaggio", "Nome manuale"])
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(1, 180)
        self.table.setColumnWidth(3, 175)
        for row, segment in enumerate(self.segments):
            minutes, seconds = divmod(int(segment.start), 60)
            values = [f"{minutes:02d}:{seconds:02d}", f"{segment.track} · {segment.speaker}",
                      segment.text, self.participants.overrides.get(segment_key(segment), "")]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column != 3:
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.table.setItem(row, column, item)
        layout.addWidget(self.table)
        note = QLabel("Lascia il nome manuale vuoto per usare l'attribuzione originale o i campi sopra. "
            "Durante REC la tabella sarà disponibile a trascrizione finita. "
            "Il vecchio recap resta invariato: usa Genera solo recap dopo aver salvato.")
        note.setWordWrap(True)
        layout.addWidget(note)
        buttons = QHBoxLayout()
        close = QPushButton("Annulla")
        close.clicked.connect(self.reject)
        save = QPushButton("Salva nomi")
        save.clicked.connect(self.save)
        buttons.addWidget(close)
        buttons.addWidget(save)
        layout.addLayout(buttons)

    def save(self):
        try:
            overrides = dict(self.participants.overrides)
            for row, segment in enumerate(self.segments):
                name = clean_name(self.table.item(row, 3).text())
                key = segment_key(segment)
                if name:
                    overrides[key] = name
                else:
                    overrides.pop(key, None)
            participants = Participants(clean_name(self.local_name.text()), self.only_me.isChecked(),
                clean_name(self.remote_name.text()), self.single_remote.isChecked(), overrides)
            if self.controller.save_participants(self.record, participants):
                self.accept()
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Nomi non salvati", str(exc))


class RecapHistoryDialog(QDialog):
    """List metadata and open user-selected files; no AI evaluation or text preview."""

    def __init__(self, repository, record, parent=None):
        super().__init__(parent)
        self.setWindowTitle("BIONIC · I tuoi recap")
        self.resize(780, 400)
        layout = QVBoxLayout(self)
        label = QLabel("Scegli un risultato e aprilo per confrontarlo tu. Nessuna valutazione automatica.")
        label.setWordWrap(True)
        layout.addWidget(label)
        self.versions = repository.list_recaps(record)
        self.table = QTableWidget(len(self.versions), 4)
        self.table.setHorizontalHeaderLabels(["Data (UTC)", "Modello", "Contesto", "Cache GPU"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for row, version in enumerate(self.versions):
            values = (str(version.get("created_at", ""))[:19].replace("T", " "),
                      version.get("model", "Non indicato"), version.get("context_length", "—"),
                      "Sì" if version.get("gpu_kv_cache") else "No")
            for column, value in enumerate(values):
                self.table.setItem(row, column, QTableWidgetItem(str(value)))
        layout.addWidget(self.table)
        buttons = QHBoxLayout()
        open_selected = QPushButton("Apri selezionato")
        open_selected.setEnabled(bool(self.versions))
        open_selected.clicked.connect(self.open_selected)
        self.table.cellDoubleClicked.connect(lambda *_: self.open_selected())
        latest = QPushButton("Apri ultimo recap / precedente versione")
        path = record.directory / "recap.md"
        latest.setEnabled(path.is_file())
        latest.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))))
        close = QPushButton("Chiudi")
        close.clicked.connect(self.close)
        buttons.addWidget(open_selected)
        buttons.addWidget(latest)
        buttons.addWidget(close)
        layout.addLayout(buttons)
        if self.versions:
            self.table.selectRow(0)

    def open_selected(self):
        row = self.table.currentRow()
        if 0 <= row < len(self.versions):
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.versions[row]["path"])))


class RecapDialog(QDialog):
    """Explicit LLM selection and confirmation, never loads an AI model on opening."""

    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.config = config
        self.setWindowTitle("BIONIC · Recap locale · selezione modello")
        self.setMinimumSize(380, 320)
        screen = self.screen().availableGeometry()
        self.resize(min(700, screen.width() - 40), min(570, screen.height() - 80))
        self._catalog = {}
        root = QVBoxLayout(self)
        self.form_scroll = QScrollArea()
        self.form_scroll.setObjectName("recapScroll")
        self.form_scroll.setWidgetResizable(True)
        self.form_scroll.setFrameShape(QFrame.Shape.NoFrame)
        form = QWidget()
        form.setObjectName("recapForm")
        layout = QVBoxLayout(form)
        self.form_scroll.setWidget(form)
        root.addWidget(self.form_scroll)
        text = QLabel(
            "Il recap parte solo premendo Genera ed è sempre in italiano. "
            "Whisper viene scaricato e poi ricaricato al termine. "
            "Una nuova registrazione interrompe il recap; il ritorno a Whisper richiede "
            "il tempo tecnico di cambio modello."
        )
        text.setWordWrap(True)
        layout.addWidget(text)
        layout.addWidget(QLabel("Modello locale per il recap:"))
        self.model = QComboBox()
        self.model.setEditable(True)
        self.model.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.model.setMinimumContentsLength(20)
        self.model.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.model.addItem(config.lm_model)
        model_row = QHBoxLayout()
        model_row.addWidget(self.model, 1)
        self.model_info_button = QPushButton("Info")
        self.model_info_button.setFixedWidth(64)
        self.model_info_button.setAccessibleName("Informazioni sul modello selezionato")
        self.model_info_button.clicked.connect(self.show_model_tooltip)
        model_row.addWidget(self.model_info_button)
        layout.addLayout(model_row)
        self.model.currentTextChanged.connect(self.update_model_info)
        self.model.activated.connect(self.show_model_tooltip)
        self.update_model_info()
        refresh = QPushButton("Leggi modelli installati in LM Studio")
        refresh.clicked.connect(self.refresh_models)
        layout.addWidget(refresh)
        self.status = QLabel("Il server LM Studio deve essere avviato. Nessun download automatico.")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        layout.addWidget(QLabel("Contesto (più contesto richiede più memoria):"))
        self.context = QSpinBox()
        self.context.setRange(8192, 32768)
        self.context.setSingleStep(8192)
        self.context.setValue(config.lm_context_length)
        layout.addWidget(self.context)
        self.gpu_cache = QCheckBox("Cache del recap sulla GPU · più veloce, usa più VRAM")
        self.gpu_cache.setChecked(config.lm_gpu_kv_cache)
        layout.addWidget(self.gpu_cache)
        layout.addWidget(QLabel("Tempo massimo per ogni fase del recap (secondi):"))
        self.timeout = QSpinBox()
        self.timeout.setRange(60, 1800)
        self.timeout.setSingleStep(60)
        self.timeout.setValue(max(600, config.lm_timeout_seconds))
        layout.addWidget(self.timeout)
        privacy = QLabel("Nessun confronto automatico. Audio e trascrizioni sono elaborati dai modelli locali. "
                         "Ogni recap viene conservato con modello, data e impostazioni, per il tuo confronto.")
        privacy.setWordWrap(True)
        layout.addWidget(privacy)
        buttons = QHBoxLayout()
        cancel, generate = QPushButton("Annulla"), QPushButton("Genera recap")
        cancel.clicked.connect(self.reject)
        generate.clicked.connect(self.save)
        buttons.addWidget(cancel)
        buttons.addWidget(generate)
        root.addLayout(buttons)

    def refresh_models(self):
        try:
            client = LMStudioSummarizer(self.config.lm_base_url, "", 3, 12000, "Italiano")
            models = client._native_request("GET", "/models").get("models", [])
            self._catalog = {str(item["key"]): item for item in text_models(models)}
            keys = list(self._catalog)
            selected = self.model.currentText()
            self.model.clear()
            self.model.addItems(keys)
            self.model.setCurrentText(selected)
            self.update_model_info()
            self.status.setText(f"{len(keys)} modelli testuali locali disponibili. Modelli vocali esclusi.")
        except (LMStudioError, OSError) as exc:
            self.status.setText(str(exc))

    def update_model_info(self, _text=None):
        # Information never participates in the dialog's size calculation.
        details = model_details(self._catalog.get(self.model.currentText().strip()))
        lines = [escape(part) for line in details.splitlines()
                 for part in (wrap(line, width=64) or [""])]
        self._model_tooltip = '<qt><p style="white-space: pre-wrap">' + '<br>'.join(lines) + '</p></qt>'
        self.model.setToolTip(self._model_tooltip)
        self.model.lineEdit().setToolTip(self._model_tooltip)
        self.model_info_button.setToolTip(self._model_tooltip)
        QToolTip.hideText()

    def show_model_tooltip(self, _index=None):
        # Qt keeps native tooltips within the available screen, including at its edges.
        QToolTip.showText(self.model.mapToGlobal(QPoint(self.model.width() + 8, 0)),
                          self._model_tooltip, self.model, QRect(), 15000)

    def done(self, result):
        QToolTip.hideText()
        super().done(result)

    def save(self):
        model = self.model.currentText().strip()
        if not model or any(word in model.lower() for word in ("whisper", "speech", "ecapa", "embed", "tts")):
            self.status.setText("Seleziona un modello linguistico, non un modello vocale o embedding.")
            return
        maximum = self._catalog.get(model, {}).get("max_context_length")
        if isinstance(maximum, int) and maximum > 0 and self.context.value() > maximum:
            self.status.setText(f"Questo modello dichiara un contesto massimo di {maximum} token. Riduci il contesto.")
            return
        previous = (self.config.lm_model, self.config.lm_context_length,
                    self.config.summary_chunk_characters, self.config.recap_language,
                    self.config.lm_gpu_kv_cache, self.config.lm_timeout_seconds)
        self.config.lm_model = model
        self.config.lm_context_length = self.context.value()
        self.config.summary_chunk_characters = min(48000, (self.context.value() - 6500) * 2)
        self.config.recap_language = "Italiano"
        self.config.lm_gpu_kv_cache = self.gpu_cache.isChecked()
        self.config.lm_timeout_seconds = self.timeout.value()
        try:
            self.config.save()
        except OSError as exc:
            (self.config.lm_model, self.config.lm_context_length,
             self.config.summary_chunk_characters, self.config.recap_language,
             self.config.lm_gpu_kv_cache, self.config.lm_timeout_seconds) = previous
            self.status.setText(f"Impostazioni non salvate: {exc}")
            return
        self.accept()


class RecognitionDialog(QDialog):
    def __init__(self, controller, config, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.config = config
        self.setWindowTitle("BIONIC · Trascrizione e nomi Teams")
        self.setMinimumWidth(580)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Il tuo nome predefinito (dalle prossime registrazioni Teams):"))
        self.local_name = QLineEdit(config.local_display_name)
        self.local_name.setPlaceholderText("Scrivi il tuo nome")
        self.local_name.setMaxLength(100)
        layout.addWidget(self.local_name)
        layout.addWidget(QLabel("Lingua del parlato (dalla prossima porzione audio):"))
        self.language = QComboBox()
        for label, value in (("Automatica · italiano / inglese", "auto"),
                             ("Italiano", "it"), ("English", "en")):
            self.language.addItem(label, value)
        self.language.setCurrentIndex(max(0, self.language.findData(config.transcription_language)))
        layout.addWidget(self.language)
        self.names = QCheckBox("Leggi gli indicatori dei parlanti Teams · sperimentale")
        self.names.setChecked(config.teams_speaker_names)
        layout.addWidget(self.names)
        self.live_voice_setting = QCheckBox("Riconoscimento vocale locale insieme a Whisper · dal prossimo riavvio")
        self.live_voice_setting.setChecked(config.live_voice_recognition)
        layout.addWidget(self.live_voice_setting)
        voice_help = QLabel("La spia VOCI indica il motore locale pronto. Per insegnargli un nome: "
                            "Archivio → Analizza voci → ascolta i campioni → Memorizza voce. "
                            "Le corrispondenze future restano stime da verificare.")
        voice_help.setWordWrap(True)
        layout.addWidget(voice_help)
        explanation = QLabel(
            "Il nome viene associato solo se Teams espone un indicatore esplicito di chi parla "
            "e copre almeno il 90% del segmento senza altri parlanti rilevati. "
            "È un'associazione indicativa, non un riconoscimento della voce.\n\n"
            "Tieni visibile la finestra della chiamata Teams. Se la versione di Teams non "
            "espone l'indicatore, il nome rimane non identificato. Non vengono attivati "
            "sottotitoli, registrazioni o servizi Microsoft. Nessun nome può essere recuperato "
            "automaticamente dalle vecchie registrazioni prive di questa cronologia.\n\n"
            "L'abilitazione dei nomi vale dalla prossima registrazione Teams; "
            "la disabilitazione è immediata.\n\n"
            "Il recap analizza gli argomenti nel contesto del dialogo, con riferimenti finali. "
            "Per correggere i nomi: Archivio → Nomi parlanti, oppure il menu Partecipanti "
            "durante REC. Passaggi sospetti vengono segnalati; "
            "il recap usa le parti comprensibili e segnala le lacune. L'audio non viene modificato. "
            "Il recap non parte allo STOP: richiedilo dall'archivio quando non hai altre riunioni."
        )
        explanation.setWordWrap(True)
        layout.addWidget(explanation)
        self.probe_status = QLabel()
        self.probe_status.setWordWrap(True)
        layout.addWidget(self.probe_status)
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.refresh_status)
        self.timer.start()
        self.refresh_status()
        buttons = QHBoxLayout()
        cancel = QPushButton("Chiudi")
        cancel.clicked.connect(self.reject)
        apply = QPushButton("Salva")
        apply.clicked.connect(self.save)
        buttons.addWidget(cancel)
        buttons.addWidget(apply)
        layout.addLayout(buttons)

    def refresh_status(self):
        monitor = self.controller.speaker_monitor
        self.probe_status.setText("Stato nomi Teams: " + monitor.status)

    def save(self):
        old = (self.config.transcription_language, self.config.teams_speaker_names, self.config.local_display_name,
               self.config.live_voice_recognition)
        self.config.transcription_language = self.language.currentData()
        self.config.teams_speaker_names = self.names.isChecked()
        self.config.live_voice_recognition = self.live_voice_setting.isChecked()
        try:
            self.config.local_display_name = clean_name(self.local_name.text())
            self.config.save()
        except (OSError, ValueError) as exc:
            (self.config.transcription_language, self.config.teams_speaker_names, self.config.local_display_name,
             self.config.live_voice_recognition) = old
            QMessageBox.warning(self, "Impostazioni non salvate", str(exc))
            return
        # Disabling is immediate; enabling starts only with the next recording.
        if not self.config.teams_speaker_names:
            self.controller.speaker_monitor.set_recording(False)
        self.accept()


class PreviewController(QObject):
    """Signals only: the appearance preview never starts audio capture or loads AI."""

    recording_changed = Signal(bool, str)
    track_changed = Signal(str, bool)
    level_changed = Signal(str, float)
    ai_changed = Signal(bool, str)
    teams_changed = Signal(bool)
    message = Signal(str)
    error = Signal(str)

    def start_manual(self):
        pass

    def stop_and_process(self):
        pass

    def cancel_and_delete(self):
        pass


class AppearanceDialog(QDialog):
    def __init__(self, overlay: OverlayWindow):
        super().__init__(overlay)
        self.overlay = overlay
        self.setWindowTitle("Impostazioni · Aspetto")
        self.setMinimumSize(1060, 740)
        self.setStyleSheet(dialog_css(get_style("obsidian")))
        self.preview_controller = PreviewController(self)
        self.preview_config = AppConfig()
        self.preview = OverlayWindow(self.preview_controller, self.preview_config, preview=True)
        root = QVBoxLayout(self)
        root.setContentsMargins(26, 22, 26, 22)
        root.setSpacing(10)
        title = QLabel("Un assistente. Cinque personalità.")
        title.setStyleSheet("font-size: 23px; font-weight: 600;")
        root.addWidget(title)
        subtitle = QLabel("Scegli la forma, prova il movimento e rendilo tuo.")
        subtitle.setStyleSheet("color: #a5aab5; font-size: 12px;")
        root.addWidget(subtitle)
        content = QHBoxLayout()
        content.setSpacing(20)
        self.styles_list = QListWidget()
        self.styles_list.setFixedWidth(180)
        for i, s in enumerate(STYLES.values(), 1):
            self.styles_list.addItem(f"0{i}   {s.name}")
        content.addWidget(self.styles_list)
        right = QVBoxLayout()
        right.setSpacing(8)
        self.description = QLabel()
        self.description.setStyleSheet("font-size: 13px; color: #c7ccd7;")
        right.addWidget(self.description)
        self.stage = QFrame()
        self.stage.setObjectName("previewStage")
        self.stage.setStyleSheet(
            "QFrame#previewStage { background: #101216; border: 1px solid #2f343d; border-radius: 18px; }"
        )
        self.stage.setMinimumSize(790, 280)
        stage_layout = QVBoxLayout(self.stage)
        stage_layout.setContentsMargins(8, 12, 8, 12)
        stage_layout.addWidget(self.preview, 0, Qt.AlignmentFlag.AlignCenter)
        right.addWidget(self.stage, 1)
        demo = QHBoxLayout()
        demo.addWidget(QLabel("Anteprima · segnale dimostrativo"))
        demo.addStretch()
        self.demo_state = QComboBox()
        self.demo_state.addItems(["Registrazione", "In attesa", "Elaborazione AI"])
        demo.addWidget(self.demo_state)
        right.addLayout(demo)
        color_heading = QHBoxLayout()
        color_heading.addWidget(QLabel("Colori icona e pannello · per ogni stile"))
        color_heading.addStretch()
        self.palette_combo = QComboBox()
        self.palette_combo.addItems(["Palette pronte…", *ICON_PRESETS])
        color_heading.addWidget(self.palette_combo)
        self.reset_colors_button = QPushButton("Ripristina colori")
        color_heading.addWidget(self.reset_colors_button)
        right.addLayout(color_heading)
        color_grid = QGridLayout()
        color_grid.setSpacing(6)
        self.color_buttons = {}
        for index, (key, label) in enumerate(ICON_COLOR_LABELS.items()):
            button = QPushButton(label)
            button.setToolTip("Scegli qualsiasi colore, anche tramite codice HEX")
            button.clicked.connect(lambda checked=False, field=key: self._choose_color(field))
            color_grid.addWidget(button, index // 4, index % 4)
            self.color_buttons[key] = button
        right.addLayout(color_grid)
        hint = QLabel(
            "Palette e colori aggiornano anche REC, STOP e Archivio. Glitch Cyberpunk solo in hover."
        )
        hint.setStyleSheet("font-size: 10px; color: #a5aab5;")
        right.addWidget(hint)
        self.motion_check = QCheckBox("Riduci le animazioni decorative")
        right.addWidget(self.motion_check)
        content.addLayout(right, 1)
        root.addLayout(content, 1)
        footer = QHBoxLayout()
        self.result_label = QLabel("La scelta viene ricordata al prossimo avvio.")
        self.result_label.setStyleSheet("color: #a5aab5;")
        footer.addWidget(self.result_label, 1)
        cancel = QPushButton("Chiudi")
        self.apply_button = QPushButton("Applica stile")
        self.apply_button.setStyleSheet("background: #e9edf4; color: #16191f; font-weight: 600;")
        footer.addWidget(cancel)
        footer.addWidget(self.apply_button)
        root.addLayout(footer)
        cancel.clicked.connect(self.close)
        self.apply_button.clicked.connect(self._apply)
        self.styles_list.currentRowChanged.connect(self._selection_changed)
        self.motion_check.toggled.connect(self._motion_changed)
        self.palette_combo.activated.connect(self._preset_selected)
        self.reset_colors_button.clicked.connect(self._reset_colors)
        self.demo_state.currentIndexChanged.connect(self._demo_changed)
        self.demo_timer = QTimer(self)
        self.demo_timer.setInterval(100)
        self.demo_timer.timeout.connect(self._demo_tick)
        self.sync_selection()

    def sync_selection(self):
        self.preview_config.icon_colors = deepcopy(self.overlay.config.icon_colors)
        self.styles_list.setCurrentRow(list(STYLES).index(self.overlay.style.key))
        self.motion_check.setChecked(self.overlay.config.reduce_motion)
        self._selection_changed(self.styles_list.currentRow())

    def _selection_changed(self, row):
        if row < 0:
            return
        s = list(STYLES.values())[row]
        self.preview_config.appearance_style = s.key
        self.preview.apply_style(s.key)
        self.preview._set_expanded(True)
        self.description.setText(s.description)
        self._refresh_colors()
        self.result_label.setText(f"Selezionato: {s.name} · premi Applica stile per usarlo.")
        self._demo_changed(self.demo_state.currentIndex())

    def _refresh_colors(self):
        self.preview.apply_style(self.preview.style.key)
        s = self.preview.style
        palette = icon_palette(s, self.preview_config.icon_colors.get(s.key))
        for key, button in self.color_buttons.items():
            color = palette[key]
            c = QColor(color)
            luminance = 0.2126 * c.redF() + 0.7152 * c.greenF() + 0.0722 * c.blueF()
            ink = "#14171a" if luminance > 0.55 else "#ffffff"
            button.setText(f"{ICON_COLOR_LABELS[key]}\n{color.upper()}")
            button.setStyleSheet(
                f"background: {color}; color: {ink}; border: 1px solid #747b88; padding: 5px; border-radius: 6px;"
            )
            button.setEnabled(key != "glitch" or s.key == "cyberpunk")
        self.palette_combo.setCurrentIndex(0)
        self.preview.icon.update()

    def _set_icon_color(self, key, color):
        if key not in ICON_COLOR_LABELS or not QColor(color).isValid():
            return
        s = self.preview.style
        self.preview_config.icon_colors.setdefault(s.key, {})[key] = QColor(color).name()
        if key in ("active", "active_edge"):
            self.demo_state.setCurrentIndex(0)
        elif key in ("body", "edge"):
            self.demo_state.setCurrentIndex(1)
        self._refresh_colors()

    def _choose_color(self, key):
        s = self.preview.style
        current = icon_palette(s, self.preview_config.icon_colors.get(s.key))[key]
        color = QColorDialog.getColor(
            QColor(current),
            self,
            ICON_COLOR_LABELS[key],
            QColorDialog.ColorDialogOption.DontUseNativeDialog,
        )
        if color.isValid():
            self._set_icon_color(key, color.name())

    def _preset_selected(self, index):
        if index <= 0:
            return
        values = list(ICON_PRESETS.values())[index - 1]
        self.preview_config.icon_colors[self.preview.style.key] = dict(
            zip(ICON_COLOR_LABELS, values)
        )
        self._refresh_colors()

    def _reset_colors(self):
        self.preview_config.icon_colors.pop(self.preview.style.key, None)
        self._refresh_colors()

    def _motion_changed(self, checked):
        self.preview_config.reduce_motion = checked
        self.preview.apply_style(self.preview.style.key)

    def _demo_changed(self, index):
        self.preview_controller.recording_changed.emit(index == 0, "manual")
        self.preview_controller.track_changed.emit("mic", index == 0)
        self.preview_controller.ai_changed.emit(index == 2, "Elaborazione locale")
        self.preview_controller.message.emit(
            (
                "Registrazione · microfono + audio PC",
                "Pronta · in attesa di una conversazione",
                "Elaborazione locale · preparo il recap",
            )[index]
        )
        if index != 0:
            self.preview._levels = {"mic": (0, 0), "system": (0, 0)}

    def _demo_tick(self):
        if self.demo_state.currentIndex() == 0:
            t = time.monotonic()
            level = max(0, 0.36 + 0.26 * math.sin(t * 4.8) + 0.19 * math.sin(t * 11))
            self.preview_controller.level_changed.emit("mic", level)

    def _apply(self):
        config = self.overlay.config
        old_style, old_motion = config.appearance_style, config.reduce_motion
        old_colors = config.icon_colors
        config.appearance_style = self.preview.style.key
        config.reduce_motion = self.motion_check.isChecked()
        config.icon_colors = deepcopy(self.preview_config.icon_colors)
        try:
            config.save()
        except OSError as exc:
            config.appearance_style, config.reduce_motion = old_style, old_motion
            config.icon_colors = old_colors
            self.result_label.setText(f"Impossibile salvare: {exc}")
            return
        self.overlay.apply_style(config.appearance_style)
        self.overlay.appearance_changed.emit(config.appearance_style)
        self.result_label.setText(f"{self.overlay.style.name} applicato e salvato.")

    def showEvent(self, event):
        self.demo_timer.start()
        super().showEvent(event)

    def hideEvent(self, event):
        self.demo_timer.stop()
        super().hideEvent(event)


class TrayController(QSystemTrayIcon):
    def __init__(
        self,
        controller: AssistantController,
        overlay: OverlayWindow,
        config: AppConfig,
        config_path: Path,
    ) -> None:
        super().__init__(
            make_tray_icon(
                style_key=overlay.style.key, colors=config.icon_colors.get(overlay.style.key)
            ),
            overlay,
        )
        self.controller = controller
        self.overlay = overlay
        self.config = config
        menu = QMenu()
        menu.addAction("Mostra controllo", self._show_overlay)
        menu.addAction("Archivio registrazioni", overlay.show_archive)
        menu.addAction("Trascrizione in diretta", overlay.show_live_transcript)
        if hasattr(controller, "voice_database"):
            menu.addAction("Rubrica vocale locale", self._show_voice_database)
        menu.addAction("Impostazioni · Aspetto", overlay.show_appearance)
        menu.addAction("Impostazioni · Trascrizione e nomi Teams", overlay.show_recognition)
        menu.addAction("Partecipanti · Registrazione corrente", overlay.show_participants)
        menu.addSeparator()
        self.record_action = menu.addAction("Avvia registrazione", controller.start_manual)
        self.stop_action = menu.addAction("Stop e trascrivi", controller.stop_and_process)
        self.cancel_action = menu.addAction("Annulla e sposta nel Cestino", self._cancel)
        self.stop_action.setEnabled(False)
        self.cancel_action.setEnabled(False)
        menu.addSeparator()
        menu.addAction(
            "Apri cartella registrazioni", lambda: self._open_path(config.recordings_path)
        )
        menu.addAction("Apri configurazione", lambda: self._open_path(config_path))
        menu.addSeparator()
        menu.addAction("Esci", QApplication.instance().quit)
        self.setContextMenu(menu)
        self.setToolTip("Local Meeting Assistant")
        self.activated.connect(self._activated)
        controller.recording_changed.connect(self._recording_changed)
        controller.session_finished.connect(self._session_finished)
        controller.error.connect(self._error)
        overlay.appearance_changed.connect(self._appearance_changed)

    def _appearance_changed(self, key):
        self.setIcon(
            make_tray_icon(
                "#ff4057" if self.overlay._recording else "#35e7ef",
                key,
                self.config.icon_colors.get(key),
            )
        )

    def _show_voice_database(self):
        from .voice_ui import VoiceDatabaseDialog
        dialog = VoiceDatabaseDialog(self.controller.voice_database, self.overlay)
        dialog.setStyleSheet(dialog_css(panel_style(self.overlay.style,
                                      self.config.icon_colors.get(self.overlay.style.key))))
        dialog.exec()
        dialog.deleteLater()

    @Slot(bool, str)
    def _recording_changed(self, active: bool, source: str) -> None:
        self.record_action.setEnabled(not active)
        self.stop_action.setEnabled(active)
        self.cancel_action.setEnabled(active)
        self.setIcon(
            make_tray_icon(
                "#ff4057" if active else "#35e7ef",
                self.overlay.style.key,
                self.config.icon_colors.get(self.overlay.style.key),
            )
        )
        if active:
            self.showMessage(
                "Registrazione avviata",
                "Chiamata Teams" if source == "teams" else "Conversazione manuale",
                QSystemTrayIcon.MessageIcon.Information,
                3500,
            )

    @Slot(object, object)
    def _session_finished(self, record: object, recap_path: object) -> None:
        detail = "Trascrizione salvata."
        if recap_path:
            detail = "Trascrizione e recap locale completati."
        self.showMessage(
            "Sessione completata", detail, QSystemTrayIcon.MessageIcon.Information, 6000
        )

    @Slot(str)
    def _error(self, detail: str) -> None:
        self.showMessage(
            "Local Meeting Assistant", detail, QSystemTrayIcon.MessageIcon.Warning, 7000
        )

    def _cancel(self) -> None:
        self.overlay._confirm_cancel()

    def _show_overlay(self) -> None:
        self.overlay.show()
        self.overlay.raise_()
        self.overlay.activateWindow()

    @Slot(QSystemTrayIcon.ActivationReason)
    def _activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self._show_overlay()

    @staticmethod
    def _open_path(path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True) if path.suffix else path.mkdir(
            parents=True, exist_ok=True
        )
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
