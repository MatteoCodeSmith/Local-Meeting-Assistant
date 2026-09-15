"""In-memory live transcript with stable rows when a voice name arrives after Whisper."""
from PySide6.QtWidgets import QDialog, QLabel, QTableWidget, QTableWidgetItem, QVBoxLayout, QHeaderView, QPushButton, QAbstractItemView

from .participants import segment_key


class LiveTranscriptDialog(QDialog):
    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.setWindowTitle("BIONIC · Trascrizione in diretta")
        self.resize(850, 530)
        layout = QVBoxLayout(self)
        self.info = QLabel("Il testo arriva con i normali tempi di Whisper. "
                           "I nomi della rubrica si aggiornano appena disponibili, senza attendere STOP. "
                           "Le attribuzioni vocali automatiche restano da verificare.")
        self.info.setWordWrap(True)
        layout.addWidget(self.info)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Tempo / traccia", "Nome", "Trascrizione"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setColumnWidth(1, 180)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        layout.addWidget(self.table)
        close = QPushButton("Chiudi")
        close.clicked.connect(self.hide)
        layout.addWidget(close)
        self.rows, self.order = {}, []
        self.session_id = ""
        controller.live_transcript_reset.connect(self.reset)
        controller.live_transcript_changed.connect(self.update_segment)
        record, segments = controller.live_transcript_snapshot()
        self.reset(record.session_id if record else "")
        for segment in segments:
            self.update_segment(self.session_id, segment)

    def reset(self, session_id):
        self.session_id = session_id
        self.rows, self.order = {}, []
        self.table.setRowCount(0)

    def update_segment(self, session_id, segment):
        if session_id != self.session_id:
            return
        scroll = self.table.verticalScrollBar()
        follow = scroll.value() >= scroll.maximum() - 2
        key = segment_key(segment)
        if key not in self.rows:
            import bisect
            position = (segment.start, segment.track, segment.end, key)
            row = bisect.bisect_left(self.order, position)
            self.order.insert(row, position)
            self.table.insertRow(row)
            self.rows = {item[3]: index for index, item in enumerate(self.order)}
        row = self.rows[key]
        minutes, seconds = divmod(int(segment.start), 60)
        values = (f"{minutes:02d}:{seconds:02d} · {segment.track}", segment.speaker, segment.text)
        for column, value in enumerate(values):
            item = QTableWidgetItem(value)
            if column == 1:
                item.setToolTip("Nome stimato dalla rubrica vocale locale" if segment.speaker_source == "voice_profile"
                                else "Nome manuale" if segment.speaker_source == "manual" else "Voce non identificata")
            self.table.setItem(row, column, item)
        self.table.resizeRowToContents(row)
        if follow:
            self.table.scrollToBottom()
