"""Archive voice review. Nothing is played, inferred or labelled without a user action."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QHBoxLayout, QHeaderView, QLabel,
    QMessageBox, QProgressBar, QPushButton, QSpinBox, QTableWidget, QTableWidgetItem,
    QVBoxLayout,
)

from .voices import load_analysis
from .voice_jobs import VoiceJobs
from .voice_database import VoiceDatabase


class VoiceDialog(QDialog):
    def __init__(self, controller, record, parent=None):
        super().__init__(parent)
        self.controller, self.record = controller, record
        self.database = getattr(controller, "voice_database", VoiceDatabase(controller.repository.root.parent / "voice-profiles.sqlite3"))
        self._playing = False
        self._sample_indices = {}
        self.setWindowTitle("BIONIC · Analizza voci · " + record.title)
        self.resize(880, 620)
        layout = QVBoxLayout(self)
        intro = QLabel("Analisi locale degli audio MIC e PC, anche di meeting precedenti. "
                       "Il numero di voci è una stima, non il numero certo dei presenti. "
                       "Ascolta più campioni e conferma i nomi. Non viene generato alcun recap.")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        options = QHBoxLayout()
        options.addWidget(QLabel("Persone attese:"))
        self.expected = QSpinBox()
        self.expected.setRange(0, 30)
        self.expected.setSpecialValueText("Automatico")
        self.expected.setToolTip("Conta le voci di entrambe le tracce. Un numero imposto non garantisce gruppi corretti.")
        options.addWidget(self.expected)
        options.addWidget(QLabel("Separazione:"))
        self.threshold = QComboBox()
        for text, value in (("Meno gruppi", 0.6), ("Bilanciata", 0.7), ("Più gruppi", 0.8)):
            self.threshold.addItem(text, value)
        self.threshold.setCurrentIndex(1)
        options.addWidget(self.threshold)
        self.analyse_button = QPushButton("Avvia analisi locale")
        self.analyse_button.clicked.connect(self.analyse)
        options.addWidget(self.analyse_button)
        self.cancel_button = QPushButton("Interrompi analisi")
        self.cancel_button.clicked.connect(controller.voices.cancel)
        options.addWidget(self.cancel_button)
        layout.addLayout(options)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        layout.addWidget(self.progress)
        self.info = QLabel()
        self.info.setWordWrap(True)
        layout.addWidget(self.info)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Gruppo stimato", "Tracce", "Secondi", "Nome da confermare"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        for column in range(3):
            self.table.horizontalHeader().setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        self.table.verticalHeader().setVisible(False)
        layout.addWidget(self.table, 1)
        sample_row = QHBoxLayout()
        self.listen_button = QPushButton("Ascolta campione / successivo")
        self.listen_button.clicked.connect(self.listen)
        sample_row.addWidget(self.listen_button)
        stop = QPushButton("Ferma ascolto")
        stop.clicked.connect(self.stop_audio)
        sample_row.addWidget(stop)
        self.sample_label = QLabel("Seleziona una voce. Nessuna riproduzione automatica.")
        self.sample_label.setWordWrap(True)
        sample_row.addWidget(self.sample_label, 1)
        layout.addLayout(sample_row)
        profiles = QHBoxLayout()
        self.enroll_button = QPushButton("Memorizza voce selezionata nella rubrica")
        self.enroll_button.clicked.connect(self.enroll)
        profiles.addWidget(self.enroll_button)
        database_button = QPushButton("Rubrica vocale")
        database_button.clicked.connect(lambda: VoiceDatabaseDialog(self.database, self).exec())
        profiles.addWidget(database_button)
        layout.addLayout(profiles)
        note = QLabel("Per unire gruppi della stessa persona assegna lo stesso nome. "
                      "Se un gruppo contiene persone diverse, aumenta la separazione e rianalizza, "
                      "oppure correggi i singoli passaggi da Nomi parlanti. "
                      "I nomi manuali esistenti hanno precedenza. I passaggi incerti restano invariati.")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.status = QLabel("Live sempre pronto su CPU durante REC. La rianalisi d'archivio si interrompe se parte un meeting.")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        footer = QHBoxLayout()
        self.apply_button = QPushButton("Conferma nomi e applica alla trascrizione")
        self.apply_button.clicked.connect(self.apply_names)
        footer.addWidget(self.apply_button)
        close = QPushButton("Chiudi")
        close.clicked.connect(self.accept)
        footer.addWidget(close)
        layout.addLayout(footer)
        controller.voices_progress.connect(self.on_progress)
        controller.voices_finished.connect(self.on_finished)
        controller.recording_changed.connect(self.on_recording)
        controller.message.connect(self.status.setText)
        self.refresh()

    def refresh(self):
        self.data = load_analysis(self.record.directory)
        self.groups = self.data.get("groups", [])
        self._sample_indices.clear()
        self.table.setRowCount(len(self.groups))
        for row, group in enumerate(self.groups):
            values = (group["speaker"], " + ".join(group["tracks"]), str(round(group["seconds"])),
                      self.data.get("names", {}).get(group["speaker"], ""))
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column != 3:
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.table.setItem(row, column, item)
            if not values[3] and group.get("embedding"):
                match = self.database.match(group["embedding"])
                if match:
                    self.table.item(row, 3).setText(match["name"])
                    self.table.item(row, 3).setToolTip("Proposta della rubrica: verifica i campioni prima di confermare.")
        if self.groups:
            self.table.selectRow(0)
        warning = self.data.get("warning", "")
        self.info.setText(f"{len(self.groups)} gruppi vocali stimati. {warning}" if self.data else
                          "Nessuna analisi salvata: premi Avvia analisi locale. Non serve rigenerare la trascrizione.")
        self.update_actions()

    def update_actions(self):
        busy = self.controller.is_session_busy(self.record)
        recording = self.controller.is_recording
        self.analyse_button.setEnabled(not busy and not recording and VoiceJobs.available())
        self.apply_button.setEnabled(bool(self.groups) and not busy and not recording)
        self.enroll_button.setEnabled(bool(self.groups) and not busy and not recording)
        self.listen_button.setEnabled(bool(self.groups) and not recording)
        self.table.setEnabled(not busy)
        self.cancel_button.setEnabled(self.controller.voices.session_id == self.record.session_id)
        if not VoiceJobs.available():
            self.status.setText("Componente vocale non installato. Esegui scripts/install-voices.ps1, poi riapri questa finestra.")

    def analyse(self):
        if self.data:
            answer = QMessageBox.question(self, "Rianalizza voci",
                "La nuova analisi ricrea i gruppi: dovrai confermare di nuovo i nomi. "
                "I risultati precedenti saranno conservati in voice-runs. Proseguire?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                return
        self.stop_audio()
        if self.controller.analyse_voices(self.record, self.expected.value(), self.threshold.currentData()):
            self.progress.setValue(0)
            self.status.setText("Caricamento ECAPA e rilevatore vocale locali…")
            self.update_actions()

    def on_progress(self, session_id, value):
        if session_id == self.record.session_id:
            self.progress.setValue(value)

    def on_finished(self, session_id):
        if session_id == self.record.session_id:
            self.refresh()

    def on_recording(self, active, _source):
        if active:
            self.stop_audio()
        self.update_actions()

    def listen(self):
        row = self.table.currentRow()
        if row < 0 or self.controller.is_recording:
            return
        group = self.groups[row]
        samples = group["samples"]
        if not samples:
            return
        index = self._sample_indices.get(row, 0) % len(samples)
        sample = samples[index]
        if sample["track"] not in ("mic", "system"):
            return
        self.stop_audio()
        try:
            import soundfile as sf
            import sounddevice as sd
            with sf.SoundFile(self.record.directory / f"{sample['track']}.flac") as audio:
                audio.seek(max(0, int(sample["start"] * audio.samplerate)))
                pcm = audio.read(int(min(6, sample["end"] - sample["start"]) * audio.samplerate), dtype="float32")
                rate = audio.samplerate
            sd.play(pcm, rate, blocking=False)
            self._playing = True
            self._sample_indices[row] = index + 1
            self.sample_label.setText(f"{group['speaker']} · campione {index + 1}/{len(samples)} · "
                                      f"{sample['track']} · {sample['start']:.1f} s")
        except Exception:
            self.status.setText("Campione non riproducibile: verifica audio originale e dispositivo di uscita Windows.")

    def stop_audio(self):
        if self._playing:
            import sounddevice as sd
            sd.stop()
            self._playing = False

    def apply_names(self):
        names = {group["speaker"]: self.table.item(row, 3).text()
                 for row, group in enumerate(self.groups)}
        try:
            assigned, uncertain = self.controller.save_voice_names(self.record, names)
            self.status.setText(f"Nomi salvati: {assigned} passaggi attribuiti, {uncertain} non attribuiti "
                                "automaticamente. Testo originale e recap conservati. "
                                "Se manca la trascrizione, genera Trascrivi audio dall'archivio.")
            self.data = load_analysis(self.record.directory)
        except (OSError, ValueError) as exc:
            self.status.setText(str(exc))

    def enroll(self):
        row = self.table.currentRow()
        if row < 0:
            return
        group = self.groups[row]
        if not group.get("embedding"):
            self.status.setText("Questa vecchia analisi non contiene impronte: esegui di nuovo l'analisi locale.")
            return
        if group["seconds"] < 6 or len(group["samples"]) < 2:
            self.status.setText("Per memorizzare una voce servono almeno 6 secondi e due campioni verificabili. Puoi comunque assegnare il nome al meeting.")
            return
        name = self.table.item(row, 3).text().strip()
        if not name:
            self.status.setText("Scrivi prima il nome della persona nella tabella.")
            return
        box = QMessageBox(self)
        box.setWindowTitle("Memorizza voce locale")
        box.setTextFormat(Qt.TextFormat.PlainText)
        box.setText(f"Confermi di aver ascoltato i campioni e che appartengono tutti a {name}?\n\n"
                    "L'impronta vocale verrà salvata solo sul PC e confrontata nei prossimi meeting. "
                    "Non è un'identificazione certa. Puoi rimuoverla dalla Rubrica vocale.")
        box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        box.setDefaultButton(QMessageBox.StandardButton.No)
        if box.exec() == QMessageBox.StandardButton.Yes:
            try:
                self.database.enroll(name, group["embedding"])
                self.status.setText("Voce memorizzata nella rubrica locale; disponibile subito per i prossimi confronti.")
            except Exception:
                self.status.setText("Voce non memorizzata: verifica nome e disponibilità del database locale.")

    def done(self, result):
        self.stop_audio()
        super().done(result)


class VoiceDatabaseDialog(QDialog):
    def __init__(self, database, parent=None):
        super().__init__(parent)
        self.database = database
        self.setWindowTitle("BIONIC · Rubrica vocale locale")
        self.resize(570, 380)
        layout = QVBoxLayout(self)
        label = QLabel("Solo voci memorizzate da te. Nessun apprendimento automatico dai nomi ipotizzati. "
                       "Rimuovere un profilo impedisce i confronti futuri; i vecchi meeting restano invariati.")
        label.setWordWrap(True)
        layout.addWidget(label)
        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["Nome confermato", "Campioni di riferimento"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        layout.addWidget(self.table)
        buttons = QHBoxLayout()
        delete = QPushButton("Rimuovi profilo")
        delete.clicked.connect(self.delete)
        buttons.addWidget(delete)
        close = QPushButton("Chiudi")
        close.clicked.connect(self.accept)
        buttons.addWidget(close)
        layout.addLayout(buttons)
        self.refresh()

    def refresh(self):
        self.profiles = self.database.list_profiles()
        self.table.setRowCount(len(self.profiles))
        for row, profile in enumerate(self.profiles):
            self.table.setItem(row, 0, QTableWidgetItem(profile["name"]))
            self.table.setItem(row, 1, QTableWidgetItem(str(len(profile["vectors"]))))

    def delete(self):
        row = self.table.currentRow()
        if row < 0:
            return
        if QMessageBox.question(self, "Rimuovi profilo", "Rimuovere questa voce dalla rubrica? "
                                "Per usarla di nuovo dovrai memorizzarla nuovamente da un meeting.",
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes:
            try:
                self.database.delete(self.profiles[row]["id"])
                self.refresh()
            except Exception:
                QMessageBox.warning(self, "Rubrica", "Profilo non rimosso: database non disponibile.")
