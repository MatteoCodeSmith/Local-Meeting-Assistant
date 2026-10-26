"""Explicit, reversible non-speech corrections; no playback/inference on open."""
import sqlite3

from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDoubleSpinBox, QHBoxLayout, QHeaderView,
    QLabel, QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from .voice_corrections import REASONS, VoiceErrorDatabase
from .voices import load_analysis
from .window_policy import DesktopDialog


class VoiceCorrectionsDialog(DesktopDialog):
    def __init__(self, controller, record, speaker=None, sample=None, parent=None):
        super().__init__(parent)
        self.controller, self.record, self.speaker = controller, record, speaker
        self.database = VoiceErrorDatabase.for_session(record.directory)
        self.setWindowTitle("BIONIC · Non è parlato / blacklist")
        self.resize(750, 540)
        layout = QVBoxLayout(self)
        note = QLabel("Escludi solo risate, rumori o musica, non persone sconosciute. "
                      "Testo, audio e recap restano conservati. Le correzioni valgono per questo meeting, "
                      "anche nelle rianalisi; non addestrano il modello su altri meeting.")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.turns = QComboBox()
        self.turns.addItem("Intervallo personalizzato", None)
        data = load_analysis(record.directory)
        self.group_spans = [turn for turn in data.get("turns", []) if turn["speaker"] == speaker]
        for turn in self.group_spans:
            self.turns.addItem(f"{speaker} · {turn['track']} · {turn['start']:.2f}–{turn['end']:.2f} s", turn)
        layout.addWidget(self.turns)
        row = QHBoxLayout()
        self.track = QComboBox()
        self.track.addItem("Microfono", "mic")
        self.track.addItem("Audio PC", "system")
        row.addWidget(self.track)
        self.start, self.end = QDoubleSpinBox(), QDoubleSpinBox()
        for label, widget in (("Da", self.start), ("A", self.end)):
            widget.setRange(0, 864000)
            widget.setDecimals(3)
            widget.setSuffix(" s")
            row.addWidget(QLabel(label))
            row.addWidget(widget)
        self.reason = QComboBox()
        self.reason.addItems(REASONS)
        row.addWidget(self.reason)
        layout.addLayout(row)
        self.turns.currentIndexChanged.connect(lambda: self.set_span(self.turns.currentData()))
        self.set_span(sample or (self.group_spans[0] if self.group_spans else None))
        actions = QHBoxLayout()
        self.exclude_button = QPushButton("Escludi intervallo")
        self.exclude_button.clicked.connect(self.exclude_interval)
        actions.addWidget(self.exclude_button)
        self.group_button = QPushButton("Escludi tutto il gruppo selezionato")
        self.group_button.clicked.connect(self.exclude_group)
        actions.addWidget(self.group_button)
        layout.addLayout(actions)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Traccia", "Da (s)", "A (s)", "Motivo"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        layout.addWidget(self.table, 1)
        self.status = QLabel("Dopo le correzioni chiudi e premi Avvia analisi locale per ricalcolare i gruppi.")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        footer = QHBoxLayout()
        self.restore_button = QPushButton("Ripristina intervallo selezionato")
        self.restore_button.clicked.connect(self.restore)
        footer.addWidget(self.restore_button)
        close = QPushButton("Chiudi")
        close.clicked.connect(self.accept)
        footer.addWidget(close)
        layout.addLayout(footer)
        controller.recording_changed.connect(self.update_actions)
        controller.voices_finished.connect(self.update_actions)
        controller.voices_progress.connect(self.update_actions)
        self.table.itemSelectionChanged.connect(self.update_actions)
        self.refresh()

    def set_span(self, span):
        if span:
            self.track.setCurrentIndex(self.track.findData(span["track"]))
            self.start.setValue(span["start"])
            self.end.setValue(span["end"])

    def update_actions(self, *_args):
        enabled = not self.controller.is_recording and not self.controller.is_session_busy(self.record)
        self.exclude_button.setEnabled(enabled)
        self.group_button.setEnabled(enabled and bool(self.group_spans))
        self.restore_button.setEnabled(enabled and self.table.currentRow() >= 0)

    def refresh(self):
        self.exclusions = self.database.list(self.record.directory)
        self.table.setRowCount(len(self.exclusions))
        for row, item in enumerate(self.exclusions):
            for col, value in enumerate((item["track"], f"{item['start']:.3f}", f"{item['end']:.3f}", item["reason"])):
                self.table.setItem(row, col, QTableWidgetItem(value))
        if self.exclusions:
            self.table.selectRow(0)
        self.update_actions()

    def apply(self, **kwargs):
        try:
            self.controller.correct_voice_intervals(self.record, **kwargs)
            self.status.setText("Correzione salvata. Nessun testo eliminato. Rianalizza per aggiornare i gruppi.")
            self.refresh()
        except (OSError, ValueError, RuntimeError, sqlite3.Error):
            self.status.setText("Correzione non completata: verifica intervallo, audio e che il meeting non sia in uso.")

    def exclude_interval(self):
        self.apply(spans=[{"track": self.track.currentData(), "start": self.start.value(),
                           "end": self.end.value()}], reason=self.reason.currentText())

    def exclude_group(self):
        if not self.group_spans:
            return
        if QMessageBox.question(self, "Escludi gruppo dal riconoscimento",
                "Hai verificato che TUTTI i campioni di questo gruppo siano rumori o risate? "
                "Un gruppo può contenere anche parole vere: in quel caso escludi solo gli intervalli errati. "
                "Il testo resterà comunque interamente disponibile per il recap.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes:
            self.apply(spans=self.group_spans, reason=self.reason.currentText())

    def restore(self):
        row = self.table.currentRow()
        if row >= 0:
            self.apply(restore_id=self.exclusions[row]["id"])
