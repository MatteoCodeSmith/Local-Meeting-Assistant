"""Archive voice review. Nothing is played, inferred or labelled without a user action."""
import sqlite3

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QGridLayout, QGroupBox, QHBoxLayout, QHeaderView,
    QLabel, QMessageBox, QProgressBar, QPushButton, QSpinBox, QTableWidget, QTableWidgetItem,
    QVBoxLayout,
)

from .voice_engine import DEFAULT_OPTIONS
from .voices import load_analysis
from .voice_jobs import VoiceJobs
from .voice_database import VoiceDatabase
from .window_policy import DesktopDialog


NAME_COLUMN, KIND_COLUMN, BOOK_COLUMN = 3, 4, 5
KINDS = {"persona": "Persona", "altra": "Altra voce", "incerto": "Incerti · risate/rumori/brevi"}
SEPARATION = (("Meno gruppi", 0.45), ("Bilanciata", 0.55), ("Più gruppi", 0.65))
WINDOWS = (("1,5 s · cambi di voce rapidi", 1.5), ("2 s · consigliato", 2.0),
           ("3 s · impronte più stabili", 3.0))
SPEECH_FILTERS = (("Ampio · anche voci deboli", 0.35), ("Normale", 0.5),
                  ("Rigoroso · scarta risate e rumori", 0.65))
MIN_GROUPS = (("3 s", 3.0), ("8 s · consigliato", 8.0), ("15 s", 15.0), ("30 s", 30.0))
BOOK_THRESHOLDS = (("Prudente", 0.65), ("Bilanciata", 0.55), ("Ampia", 0.45))


def _combo(choices, tooltip):
    combo = QComboBox()
    for text, value in choices:
        combo.addItem(text, value)
    combo.setToolTip(tooltip)
    return combo


def _select_nearest(combo, value):
    values = [combo.itemData(index) for index in range(combo.count())]
    combo.setCurrentIndex(min(range(len(values)), key=lambda index: abs(values[index] - float(value))))


class VoiceDialog(DesktopDialog):
    def __init__(self, controller, record, parent=None):
        super().__init__(parent)
        self.controller, self.record = controller, record
        self.database = getattr(controller, "voice_database", VoiceDatabase(controller.repository.root.parent / "voice-profiles.sqlite3"))
        self._playing = False
        self._sample_indices = {}
        self._search_scores = {}
        self.setWindowTitle("BIONIC · Analizza voci · " + record.title)
        self.resize(960, 760)
        layout = QVBoxLayout(self)
        intro = QLabel("Analisi locale degli audio MIC e PC, anche di meeting precedenti. Indica quante "
                       "persone hanno parlato: l'app crea quei gruppi e raggruppa a parte, per somiglianza, "
                       "le altre voci. Risate, rumori e frammenti finiscono in «Incerti». Le voci già in "
                       "rubrica ricevono il nome in automatico. Non viene generato alcun recap.")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        box = QGroupBox("Opzioni di analisi")
        grid = QGridLayout(box)
        self.expected = QSpinBox()
        self.expected.setRange(0, 30)
        self.expected.setSpecialValueText("Automatico")
        self.expected.setToolTip("Conta solo chi ha parlato, su MIC e PC insieme: 1, 2, 5, 6, 8… "
                                 "Non contare i partecipanti rimasti in silenzio. 0 = stima automatica, non garantita.")
        self.other_voices = QCheckBox("Raggruppa anche le altre voci per somiglianza")
        self.other_voices.setToolTip("Le voci oltre il numero indicato restano gruppi «Altra voce» invece di "
                                     "essere forzate dentro le persone.")
        self.threshold = _combo(SEPARATION, "Quanto devono somigliarsi due gruppi per essere uniti. "
                                "«Più gruppi» separa voci simili, «Meno gruppi» unisce la stessa persona su MIC e PC.")
        self.window_length = _combo(WINDOWS, "Lunghezza dei pezzi di audio da cui si calcola ogni impronta. "
                             "Più corto segue meglio i cambi rapidi, più lungo dà impronte più affidabili.")
        self.speech_filter = _combo(SPEECH_FILTERS, "Quanto deve essere sicuro il rilevatore che si tratti di "
                                    "parlato. «Rigoroso» scarta più risate e rumori ma può perdere voci deboli.")
        self.min_group = _combo(MIN_GROUPS, "Sotto questa durata un gruppo non occupa il posto di una persona: "
                                "viene unito alla voce più simile oppure finisce in «Incerti».")
        self.drop_echo = QCheckBox("Scarta l'eco dell'audio PC nel microfono")
        self.drop_echo.setToolTip("Se usi le casse invece delle cuffie, il microfono riprende le voci remote: "
                                  "quei passaggi vengono ignorati sul MIC e restano sul PC.")
        self.use_profiles = QCheckBox("Nomi automatici dalla rubrica e unione della stessa persona")
        self.use_profiles.setToolTip("Dopo l'analisi i gruppi che somigliano a una voce memorizzata ricevono il "
                                     "nome e, se sono più gruppi della stessa persona, vengono uniti. "
                                     "Restano da verificare: nessuna voce viene memorizzata in automatico.")
        self.book_threshold = _combo(BOOK_THRESHOLDS, "Somiglianza minima con la rubrica per assegnare il nome.")
        recommended = QPushButton("Ripristina consigliati")
        recommended.clicked.connect(self.recommended)
        rows = (("Persone che hanno parlato:", self.expected), ("Separazione voci:", self.threshold),
                ("Taglio finestre:", self.window_length), ("Filtro parlato:", self.speech_filter),
                ("Gruppo minimo:", self.min_group), ("Rubrica:", self.book_threshold))
        for index, (text, widget) in enumerate(rows):
            grid.addWidget(QLabel(text), index // 2, (index % 2) * 2)
            grid.addWidget(widget, index // 2, (index % 2) * 2 + 1)
        grid.addWidget(self.other_voices, 3, 0, 1, 2)
        grid.addWidget(self.drop_echo, 3, 2, 1, 2)
        grid.addWidget(self.use_profiles, 4, 0, 1, 2)
        grid.addWidget(recommended, 4, 3)
        layout.addWidget(box)
        options = QHBoxLayout()
        self.analyse_button = QPushButton("Avvia analisi locale")
        self.analyse_button.clicked.connect(self.analyse)
        options.addWidget(self.analyse_button)
        self.cancel_button = QPushButton("Interrompi analisi")
        self.cancel_button.clicked.connect(controller.voices.cancel)
        options.addWidget(self.cancel_button)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        options.addWidget(self.progress, 1)
        layout.addLayout(options)
        self.info = QLabel()
        self.info.setWordWrap(True)
        layout.addWidget(self.info)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["Gruppo stimato", "Tracce", "Secondi", "Nome da confermare",
                                              "Tipo", "Rubrica"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.horizontalHeader().setSectionResizeMode(NAME_COLUMN, QHeaderView.ResizeMode.Stretch)
        for column in (0, 1, 2, KIND_COLUMN, BOOK_COLUMN):
            self.table.horizontalHeader().setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        self.table.verticalHeader().setVisible(False)
        self.table.currentCellChanged.connect(lambda *_: self.show_candidates())
        layout.addWidget(self.table, 1)
        search = QHBoxLayout()
        search.addWidget(QLabel("Cerca in rubrica:"))
        self.search_profile = QComboBox()
        self.search_profile.setEditable(True)
        self.search_profile.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.search_profile.setToolTip("Scrivi o scegli una persona memorizzata: «Trova nel meeting» indica "
                                       "quale gruppo le somiglia di più.")
        search.addWidget(self.search_profile, 1)
        self.find_button = QPushButton("Trova nel meeting")
        self.find_button.clicked.connect(self.find_profile)
        search.addWidget(self.find_button)
        self.assign_button = QPushButton("Assegna al gruppo selezionato")
        self.assign_button.clicked.connect(self.assign_profile)
        search.addWidget(self.assign_button)
        layout.addLayout(search)
        self.candidates_label = QLabel()
        self.candidates_label.setWordWrap(True)
        layout.addWidget(self.candidates_label)
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
        database_button.clicked.connect(self.open_database)
        profiles.addWidget(database_button)
        self.corrections_button = QPushButton("Non è parlato / blacklist…")
        self.corrections_button.clicked.connect(self.correct_non_speech)
        profiles.addWidget(self.corrections_button)
        layout.addLayout(profiles)
        note = QLabel("Per unire gruppi della stessa persona assegna lo stesso nome. "
                      "Se un gruppo contiene persone diverse, scegli «Più gruppi» o un taglio più corto e "
                      "rianalizza, oppure correggi i singoli passaggi da Nomi parlanti. "
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
        self.recommended()
        self.load_profiles()
        self.refresh()
        self.restore_options()

    def recommended(self):
        self.other_voices.setChecked(DEFAULT_OPTIONS["other_voices"])
        self.drop_echo.setChecked(DEFAULT_OPTIONS["drop_echo"])
        self.use_profiles.setChecked(DEFAULT_OPTIONS["use_profiles"])
        _select_nearest(self.threshold, 0.55)
        _select_nearest(self.window_length, DEFAULT_OPTIONS["window"])
        _select_nearest(self.speech_filter, DEFAULT_OPTIONS["vad_threshold"])
        _select_nearest(self.min_group, DEFAULT_OPTIONS["min_group_seconds"])
        _select_nearest(self.book_threshold, DEFAULT_OPTIONS["profile_threshold"])

    def restore_options(self):
        """Reopen with the settings of the last analysis; legacy runs keep recommended values."""
        self.expected.setValue(int(self.data.get("expected", 0)))
        if "threshold" in self.data:
            _select_nearest(self.threshold, self.data["threshold"])
        saved = self.data.get("options", {})
        for key, box in (("other_voices", self.other_voices), ("drop_echo", self.drop_echo),
                         ("use_profiles", self.use_profiles)):
            if key in saved:
                box.setChecked(bool(saved[key]))
        for key, combo in (("window", self.window_length), ("vad_threshold", self.speech_filter),
                           ("min_group_seconds", self.min_group), ("profile_threshold", self.book_threshold)):
            if key in saved:
                _select_nearest(combo, saved[key])

    def current_options(self):
        return {"window": self.window_length.currentData(), "vad_threshold": self.speech_filter.currentData(),
                "min_group_seconds": self.min_group.currentData(),
                "other_voices": self.other_voices.isChecked(), "drop_echo": self.drop_echo.isChecked(),
                "use_profiles": self.use_profiles.isChecked(),
                "profile_threshold": self.book_threshold.currentData()}

    def load_profiles(self):
        try:
            self.profiles = self.database.list_profiles()
        except (OSError, sqlite3.Error):
            self.profiles = []
        current = self.search_profile.currentText()
        self.search_profile.clear()
        self.search_profile.addItems([profile["name"] for profile in self.profiles])
        self.search_profile.setCurrentText(current)

    def open_database(self):
        VoiceDatabaseDialog(self.database, self).exec()
        self.load_profiles()
        self.refresh()

    def refresh(self):
        self.data = load_analysis(self.record.directory)
        self.groups = self.data.get("groups", [])
        self._sample_indices.clear()
        self._search_scores.clear()
        automatic = self.data.get("auto_names", {})
        self.table.setRowCount(len(self.groups))
        for row, group in enumerate(self.groups):
            kind = group.get("kind", "persona")
            values = (group["speaker"], " + ".join(group["tracks"]), str(round(group["seconds"])),
                      self.data.get("names", {}).get(group["speaker"], ""), KINDS.get(kind, kind), "")
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column != NAME_COLUMN or kind == "incerto":
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.table.setItem(row, column, item)
            if kind == "incerto":
                self.table.item(row, NAME_COLUMN).setToolTip(
                    "Frammenti di persone diverse o non parlato: ascoltali e, se serve, usa la blacklist.")
                continue
            if group["speaker"] in automatic:
                score = automatic[group["speaker"]].get("score", 0)
                self.table.item(row, BOOK_COLUMN).setText(f"automatico · {score:.2f}")
                self.table.item(row, NAME_COLUMN).setToolTip(
                    "Assegnato automaticamente dalla rubrica: ascolta i campioni e conferma.")
            elif not values[NAME_COLUMN] and group.get("embedding") and self.profiles:
                match = self.database.match_group(group, self.book_threshold.currentData(),
                                                  profiles=self.profiles)
                if match:
                    self.table.item(row, NAME_COLUMN).setText(match["name"])
                    self.table.item(row, BOOK_COLUMN).setText(f"proposta · {match['score']:.2f}")
                    self.table.item(row, NAME_COLUMN).setToolTip("Proposta della rubrica: verifica i campioni prima di confermare.")
        if self.groups:
            self.table.selectRow(0)
        warning = self.data.get("warning", "")
        if self.data:
            counts = {kind: sum(1 for group in self.groups if group.get("kind", "persona") == kind)
                      for kind in KINDS}
            summary = f"{counts['persona']} persone, {counts['altra']} altre voci"
            if counts["incerto"]:
                summary += ", un gruppo di passaggi incerti"
            removed = [f"{label} {self.data[key]:.0f} s" for key, label in
                       (("echo_seconds", "eco"), ("short_speech_seconds", "frammenti brevi"))
                       if self.data.get(key)]
            if removed:
                summary += " · scartati: " + ", ".join(removed)
            self.info.setText(f"{summary}. {warning}")
        else:
            self.info.setText("Nessuna analisi salvata: imposta le opzioni e premi Avvia analisi locale. "
                              "Non serve rigenerare la trascrizione.")
        self.show_candidates()
        self.update_actions()

    def selected_group(self):
        row = self.table.currentRow()
        return (row, self.groups[row]) if 0 <= row < len(self.groups) else (row, None)

    def show_candidates(self):
        _, group = self.selected_group()
        if not group or group.get("kind") == "incerto" or not group.get("embedding"):
            self.candidates_label.setText("")
            return
        ranked = group.get("candidates") or (self.database.rank(group["embedding"], self.profiles)[:3]
                                             if self.profiles else [])
        self.candidates_label.setText(
            "Somiglianza con la rubrica: " + " · ".join(f"{item['name']} {item['score']:.2f}" for item in ranked)
            if ranked else "Nessuna voce memorizzata da confrontare.")

    def find_profile(self):
        name = self.search_profile.currentText().strip().casefold()
        profile = next((item for item in self.profiles if item["name"].casefold() == name), None) or \
            next((item for item in self.profiles if name and name in item["name"].casefold()), None)
        if profile is None:
            self.status.setText("Persona non trovata nella rubrica: scegli un nome dall'elenco.")
            return
        self.search_profile.setCurrentText(profile["name"])
        best = None
        for row, group in enumerate(self.groups):
            if group.get("kind") == "incerto" or not group.get("embedding") or group.get("needs_reanalysis"):
                continue
            score = self.database.rank(group["embedding"], [profile])[0]["score"]
            self._search_scores[row] = score
            self.table.item(row, BOOK_COLUMN).setText(f"{profile['name']} {score:.2f}")
            if best is None or score > best[0]:
                best = (score, row)
        if best is None:
            self.status.setText("Nessun gruppo confrontabile: esegui prima l'analisi locale.")
            return
        self.table.selectRow(best[1])
        verdict = ("probabilmente è questa persona" if best[0] >= self.book_threshold.currentData()
                   else "somiglianza bassa: forse non ha parlato o serve rianalizzare")
        self.status.setText(f"Gruppo più simile a {profile['name']}: {self.groups[best[1]]['speaker']} "
                            f"({best[0]:.2f}, {verdict}). Ascolta i campioni, poi «Assegna al gruppo selezionato».")

    def assign_profile(self):
        row, group = self.selected_group()
        name = self.search_profile.currentText().strip()
        if group is None or group.get("kind") == "incerto" or not name:
            self.status.setText("Seleziona un gruppo di voce e una persona della rubrica.")
            return
        self.table.item(row, NAME_COLUMN).setText(name)
        self.status.setText(f"{name} assegnato a {group['speaker']}: premi «Conferma nomi e applica».")

    def update_actions(self):
        busy = self.controller.is_session_busy(self.record)
        recording = self.controller.is_recording
        self.analyse_button.setEnabled(not busy and not recording and VoiceJobs.available())
        self.apply_button.setEnabled(bool(self.groups) and not busy and not recording)
        self.enroll_button.setEnabled(bool(self.groups) and not busy and not recording)
        self.corrections_button.setEnabled(not busy and not recording)
        self.listen_button.setEnabled(bool(self.groups) and not recording)
        self.find_button.setEnabled(bool(self.groups) and bool(self.profiles) and not busy)
        self.assign_button.setEnabled(bool(self.groups) and not busy)
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
        if self.controller.analyse_voices(self.record, self.expected.value(), self.threshold.currentData(),
                                          self.current_options()):
            self.progress.setValue(0)
            self.status.setText("Caricamento ECAPA e rilevatore vocale locali…")
            self.update_actions()

    def on_progress(self, session_id, value):
        if session_id == self.record.session_id:
            self.progress.setValue(value)
            self.update_actions()

    def correct_non_speech(self):
        from .voice_corrections_ui import VoiceCorrectionsDialog
        self.stop_audio()
        row = self.table.currentRow()
        group = self.groups[row] if row >= 0 else {}
        samples = group.get("samples", [])
        sample = samples[(self._sample_indices.get(row, 1) - 1) % len(samples)] if samples else None
        try:
            VoiceCorrectionsDialog(self.controller, self.record, group.get("speaker"), sample, self).exec()
            self.refresh()
        except (OSError, sqlite3.Error):
            self.status.setText("Blacklist non disponibile. Non eliminare la rubrica; verifica il database delle correzioni.")

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
        names = {group["speaker"]: self.table.item(row, NAME_COLUMN).text()
                 for row, group in enumerate(self.groups) if group.get("kind") != "incerto"}
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
        if group.get("kind") == "incerto":
            self.status.setText("I passaggi incerti mescolano voci e rumori: non si possono memorizzare.")
            return
        if not group.get("embedding"):
            self.status.setText("Impronta mancante o invalidata dalle correzioni: esegui di nuovo l'analisi locale prima di memorizzarla.")
            return
        if group["seconds"] < 6 or len(group["samples"]) < 2:
            self.status.setText("Per memorizzare una voce servono almeno 6 secondi e due campioni verificabili. Puoi comunque assegnare il nome al meeting.")
            return
        name = self.table.item(row, NAME_COLUMN).text().strip()
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
                self.load_profiles()
                self.status.setText("Voce memorizzata nella rubrica locale; disponibile subito per i prossimi confronti.")
            except Exception:
                self.status.setText("Voce non memorizzata: verifica nome e disponibilità del database locale.")

    def done(self, result):
        self.stop_audio()
        super().done(result)


class VoiceDatabaseDialog(DesktopDialog):
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
