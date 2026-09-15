# BIONIC · Local Meeting Assistant

Un assistente desktop per Windows: registra microfono e audio del PC, trascrive italiano e inglese, riconosce le voci della tua rubrica locale e genera recap in italiano **solo quando li richiedi**.

Icona trascinabile sempre in primo piano, controlli al passaggio del mouse, cinque stili, archivio e trascrizione Live. Audio, nomi e contenuti delle conversazioni non vengono inviati a servizi AI remoti.

> Progetto sperimentale: trascrizione, raggruppamento delle voci e nomi Teams possono sbagliare. Non è un prodotto Microsoft né un'integrazione ufficiale Teams. Verifica i risultati importanti e informa i partecipanti prima di registrare.

## 1. Cosa serve

- **Windows 11 x64**: è la piattaforma di riferimento dell'app. Mac, Linux e Windows ARM non sono verificati per questa distribuzione.
- **16 GB di RAM o più**, GPU con driver aggiornati; 8 GB di VRAM sono una base ragionevole per la configurazione sotto, non una garanzia per qualsiasi modello/contesto. Su CPU la trascrizione può essere più lenta.
- Spazio per ambienti Python, modelli e registrazioni: riserva diversi GB, oltre allo spazio per i meeting.
- Internet per scaricare software, dipendenze e modelli; poi l'elaborazione prevista dall'app è locale.
- **uv**, che gestisce Python 3.12 e gli ambienti: non occorre installare Python separatamente.
- **LM Studio** per i recap. Non serve un abbonamento Copilot, una chiave OpenAI o un account cloud per elaborare le registrazioni.

I requisiti di LM Studio includono AVX2 su x64 e raccomandano almeno 16 GB di RAM. Consulta i [requisiti ufficiali](https://lmstudio.ai/docs/app/system-requirements).

## 2. Scarica il progetto e installa uv

1. Scarica/clona il repository o estrai lo ZIP sorgente in una cartella definitiva, per esempio `C:\Apps\BIONIC`.
2. Non eseguire l'app dentro lo ZIP. Evita cartelle di sistema protette e cartelle sincronizzate se non vuoi sincronizzare i tuoi dati.
3. Installa uv dalla [guida ufficiale](https://docs.astral.sh/uv/getting-started/installation/). Con WinGet puoi usare:

```powershell
winget install --id astral-sh.uv -e
```

4. Chiudi e riapri PowerShell, poi controlla:

```powershell
uv --version
```

5. Apri PowerShell nella cartella che contiene questo README:

```powershell
Set-Location "C:\Apps\BIONIC"
```

Tutti i comandi successivi partono da questa cartella. Non servono Codex, percorsi legati a un particolare utente o l'attivazione manuale del virtualenv.

## 3. Installa l'app

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\install.ps1
```

Lo script trova/scarica Python 3.12, crea `.venv`, installa l'app in modalità modificabile e verifica le dipendenze. Il parametro Bypass vale per questo processo: non cambia permanentemente la policy di Windows. Su PC aziendali rispetta le restrizioni dell'amministratore.

**Se l'app è già installata, prima di aggiornarla fai Esci dal menu vicino all'orologio, quando non stai registrando/elaborando.** Chiudere una finestra non termina BIONIC.

## 4. Scarica i modelli giusti

Servono modelli distinti per tre compiti:

| Funzione | Modello | Dove viene usato |
| --- | --- | --- |
| Trascrizione IT/EN | Whisper large-v3-turbo, GGUF Q8_0 compatibile con transcribe.cpp | Direttamente da BIONIC, GPU/CPU |
| Recap in italiano | Un LLM istruito per conversazioni, ad esempio Gemma 3 4B IT Q4_K_M | Server locale di LM Studio |
| Riconoscimento voci | ECAPA SpeechBrain + rilevatore Silero | Processo CPU separato, installazione al punto 5 |

### Whisper: trascrizione, non chat

Scarica **`whisper-large-v3-turbo-Q8_0.gguf`** dalla raccolta [handy-computer/whisper-large-v3-turbo-gguf](https://huggingface.co/handy-computer/whisper-large-v3-turbo-gguf/tree/main). È la conversione per transcribe.cpp; un file GGML `.bin` o un GGUF per un runtime differente non è intercambiabile solo perché si chiama Whisper.

Puoi scaricarlo usando la ricerca modelli di LM Studio, se disponibile, oppure dal collegamento sopra. Per il rilevamento automatico conserva il file in:

```text
%USERPROFILE%\.lmstudio\models\handy-computer\whisper-large-v3-turbo-gguf\whisper-large-v3-turbo-Q8_0.gguf
```

Non devi caricarlo nella chat di LM Studio: BIONIC lo apre direttamente. Se lo tieni altrove, imposta `whisper_gguf_path` nel file di configurazione, come spiegato al punto 7.

### Modello per il recap

1. Installa [LM Studio per Windows](https://lmstudio.ai/download).
2. Dalla ricerca modelli scarica, per esempio, [lmstudio-community/gemma-3-4b-it-GGUF](https://huggingface.co/lmstudio-community/gemma-3-4b-it-GGUF), variante **Q4_K_M**. È un punto di partenza compatto, non una promessa di qualità superiore.
3. Puoi scegliere altri modelli testuali installati dal selettore di BIONIC. I risultati li valuti tu: l'app conserva i recap per modello e non esegue confronti automatici.
4. Nella sezione **Developer** di LM Studio avvia il server locale sulla porta **1234**.
5. Per questa app usa il server sul solo PC locale, senza esposizione alla rete. Il client attuale non configura token di autenticazione: se il server ne richiede uno, non funzionerà con la configurazione standard.
6. Non è necessario tenere un LLM caricato durante le registrazioni: BIONIC ne gestisce il caricamento quando richiedi il recap.

Il client usa sia le API compatibili chat sia le API native di gestione modelli. Serve una versione di LM Studio che esponga `/api/v1/models` e i relativi endpoint di caricamento/scaricamento. Riferimenti: [server locale](https://lmstudio.ai/docs/developer/core/server), [API native](https://lmstudio.ai/docs/developer/rest).

## 5. Installa le voci, se le desideri

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\install-voices.ps1
```

Lo script crea `.venv-voices`, installa PyTorch CPU/SpeechBrain/Silero e scarica il checkpoint originale ECAPA in `models/ecapa`, verificandone il checksum. Non modifica l'ambiente di Whisper.

**Il GGUF ECAPA di Vokra presente in LM Studio non sostituisce questo checkpoint.** Il modello originale è [speechbrain/spkrec-ecapa-voxceleb](https://huggingface.co/speechbrain/spkrec-ecapa-voxceleb). Non serve caricarlo in LM Studio.

Se non vuoi questa funzione puoi saltare il passaggio e disabilitare il riconoscimento vocale nelle impostazioni dell'app; la modifica vale dal successivo riavvio. Registrazione e trascrizione non richiedono la rubrica vocale.

## 6. Verifica l'installazione

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\check-install.ps1
```

Il controllo verifica l'importazione dei componenti, senza aprire microfono, meeting, configurazione personale o contenuti privati. Non carica i modelli e non garantisce la qualità dei risultati: controlla separatamente il file Whisper e il server LM Studio.

Se compare un errore DLL su un PC nuovo, verifica driver GPU e [Microsoft Visual C++ Redistributable x64](https://learn.microsoft.com/en-us/cpp/windows/latest-supported-vc-redist). Consulta anche la sezione Problemi comuni sotto.

## 7. Primo avvio e configurazione

Prima di avviare BIONIC, in **Windows → Impostazioni → Privacy e sicurezza → Microfono** consenti l'accesso alle app desktop; in **Sistema → Audio** scegli il microfono e l'uscita corretti.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\run.ps1
```

Compare il widget e un'icona nell'area vicino all'orologio (anche sotto la freccia delle icone nascoste). Il pannello si apre al passaggio del mouse. Trascina l'icona per spostarlo.

Al primo avvio viene creata la configurazione. Usa **menu vicino all'orologio → Apri configurazione** per trovare il file effettivo: il percorso standard è sotto `%LOCALAPPDATA%\LocalMeetingAssistant` con platformdirs; il fallback è sotto `%APPDATA%`.

Per modificare il JSON, fai prima **Esci**, modifica solo i campi necessari e riapri BIONIC. Per esempio:

```json
{
  "whisper_gguf_path": "C:/Modelli/whisper-large-v3-turbo-Q8_0.gguf",
  "follow_default_microphone": true,
  "microphone_name": "",
  "transcription_language": "auto",
  "lm_base_url": "http://127.0.0.1:1234/v1",
  "lm_context_length": 8192,
  "lm_gpu_kv_cache": false,
  "live_voice_recognition": true,
  "auto_detect_teams": true,
  "auto_record_teams": true
}
```

Questo è un estratto: **non sostituire tutta la configurazione** e non lasciare il percorso d'esempio se il file non si trova lì. In JSON usa `/` o raddoppia i backslash.

Per iniziare con 8 GB di VRAM usa un LLM compatto, contesto **8192** e cache GPU disattivata nel dialogo recap. Aumenta il contesto solo se la memoria lo consente. Il peso del file del modello non equivale alla VRAM necessaria.

### Prima prova, senza dati riservati

- Fai una breve registrazione manuale dicendo una frase inventata; verifica MIC, livelli e poi **Live**.
- Premi STOP e controlla la sessione nell'archivio.
- Per il recap: **Genera solo recap → Leggi modelli installati → scegli il modello → Genera recap**. Le informazioni del modello sono nel tooltip/nel pulsante Info.
- Verifica il cambio del microfono predefinito di Windows con una registrazione di prova.
- Verifica il rilevamento Teams in una chiamata di prova: dipende dalla versione del client e non è garantito. Puoi sempre usare REC/STOP manualmente.

## 8. Collegamento sul desktop e avvio automatico

Crea o aggiorna il collegamento **Local Meeting Assistant**, con l'icona BIONIC:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\create-desktop-shortcut.ps1
```

Il collegamento punta alla `.venv` di questa cartella. Se sposti il progetto, reinstalla nella nuova posizione e ricrea il collegamento: non trasferire i virtualenv tra PC.

Lo script usa l'icona Windows `.ico` inclusa nei sorgenti, con risoluzioni da 16 a 256 pixel. Puoi rieseguirlo per aggiornare anche un collegamento esistente, senza avviare l'app o cambiare le impostazioni. Se Windows mostra ancora l'icona precedente, aggiorna il desktop con F5. L'icona del collegamento è fissa: i temi selezionati nell'app cambiano il widget, non il collegamento desktop.

Avvio automatico al login, facoltativo:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\enable-autostart.ps1
```

Per disabilitarlo:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\disable-autostart.ps1
```

Non abilitare l'avvio automatico prima di aver verificato microfono, Whisper e impostazioni di registrazione automatica. Si tratta dell'avvio di BIONIC, non di LM Studio.

## Uso quotidiano

| Controllo | Cosa fa |
| --- | --- |
| REC / STOP | Avvia la registrazione / salva e completa la trascrizione. STOP **non genera recap**. |
| Live | Mostra testo e nomi durante REC. Se il nome arriva dopo il testo, aggiorna la stessa riga. |
| Archivio | Elenca le sessioni; Trascrivi audio ricostruisce la trascrizione dagli originali. |
| Genera solo recap | Avvia il modello locale selezionato, su tua richiesta. Risultato sempre in italiano. |
| Rinomina / Elimina | Cambia il titolo / sposta la sessione nel Cestino dopo conferma. |
| Analizza voci | Rianalizza gli audio vecchi, raggruppa le voci e permette di ascoltare campioni. |
| Nomi parlanti | Corregge manualmente le attribuzioni. I nomi manuali hanno precedenza. |
| Aspetto | Cinque temi, colori, animazioni ed effetti del widget. |
| Esci, dal menu dell'icona | Termina l'app. Necessario per applicare gli aggiornamenti. |

Durante il recap richiesto, Whisper lascia spazio al LLM; al termine il LLM viene scaricato e Whisper ripristinato. Una nuova registrazione interrompe il recap, ma il cambio di modello richiede tempo tecnico. Le voci usano CPU/RAM, non VRAM.

### Insegnare i nomi alla rubrica vocale

1. Apri **Archivio → Analizza voci**.
2. Se necessario avvia l'analisi; ascolta più campioni dello stesso gruppo.
3. Inserisci il nome corretto. Per attribuire più gruppi alla stessa persona, usa lo stesso nome.
4. **Conferma nomi e applica** aggiorna quella trascrizione, non il recap già generato.
5. **Memorizza voce selezionata nella rubrica** salva il profilo per i meeting successivi, dopo conferma. Servono almeno sei secondi e due campioni.
6. Gestisci/rimuovi i profili da **Rubrica vocale locale**, anche dal menu vicino all'orologio.

Il motore resta pronto e durante REC analizza finestre di circa tre secondi. Se la corrispondenza con la rubrica è sufficientemente netta, il nome compare subito nel widget e viene associato ai passaggi disponibili in Live. Whisper mantiene i propri tempi: non è una trascrizione istantanea parola per parola. Le corrispondenze incerte restano senza nome.

Il numero di voci è una stima: eco, sovrapposizioni, interventi brevi e cambio microfono possono dividere o mescolare gruppi. Nessun apprendimento automatico dai nomi ipotizzati; verifica i campioni prima di memorizzare un profilo.

## Dove sono i dati e cosa non condividere

Per impostazione predefinita:

- registrazioni: `%USERPROFILE%\Documents\Local Meeting Assistant\recordings`;
- rubrica: `voice-profiles.sqlite3`, accanto alla cartella registrazioni configurata;
- modelli vocali installati dall'app: `models/ecapa`;
- modelli LM Studio: normalmente `%USERPROFILE%\.lmstudio\models`.

Ogni sessione può contenere FLAC, trascrizioni, recap, impronte vocali e revisioni. La rubrica è separata: eliminare un meeting non cancella il profilo memorizzato; rimuovere un profilo non riscrive i vecchi meeting.

**Questi dati non sono cifrati dall'app.** Proteggi account Windows, disco e backup. L'audio PC comprende l'uscita di altre applicazioni, non soltanto Teams. Un'eventuale sincronizzazione OneDrive/backup è esterna a BIONIC: “AI locale” non disabilita quella sincronizzazione.

Il repository esclude ambienti Python, modelli, cache, registrazioni, configurazioni personali e artefatti. Non condividere l'intera cartella di lavoro con “Comprimi tutto”: usa lo script sotto o i soli file tracciati da Git.

## Condividere il codice

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\export-source.ps1
```

Crea uno ZIP sorgente in `.local`, usando una lista esplicita di codice, script e documentazione: **non legge né include i dati dei meeting, gli artefatti o i modelli**. L'export normale esclude i test; aggiungi `-IncludeTests` se vuoi condividere anche le verifiche di sviluppo.

La struttura da versionare è:

```text
src/local_meeting_assistant/   App e icone PNG/ICO
scripts/                       Installazione, avvio, controllo ed export
tests/                         Sole verifiche automatiche utili, dati fittizi
README.md                      Questa guida
pyproject.toml                 Dipendenze e configurazione del pacchetto
create-desktop-shortcut.ps1    Collegamento desktop
LICENSE / THIRD_PARTY_NOTICES.md
AGENTS.md / .gitignore
```

`.venv`, `.venv-voices` e `models` sono dati locali ricreabili dagli installer, non sorgenti da pubblicare. Nessun caricamento su GitHub avviene automaticamente. Rispetta separatamente le licenze dei modelli e delle risorse di terze parti.

## Problemi comuni

| Problema | Controllo |
| --- | --- |
| Il collegamento non apre l'app | Avvia `scripts/run-debug.ps1` da PowerShell e controlla l'errore; non pubblicare log contenenti dati privati. |
| “App già in esecuzione” o grafica vecchia | Fai Esci dall'icona vicino all'orologio, poi riapri. |
| Whisper non trovato | Controlla formato transcribe.cpp, nome del file e `whisper_gguf_path`. |
| Nessun microfono / dispositivo cambiato | Permessi Windows, dispositivo predefinito; lascia vuoto `microphone_name` e attivo `follow_default_microphone`. |
| Modelli recap assenti / errore API | LM Studio avviato, server porta 1234, endpoint nativi supportati, server solo locale e autenticazione compatibile. |
| Memoria insufficiente o timeout recap | Modello più piccolo, contesto 8192, cache GPU disattivata; non aumentare il contesto alla cieca. Il timeout è modificabile nel dialogo. |
| Spia VOCI non pronta | Installa `scripts/install-voices.ps1`, esegui il controllo tecnico e riavvia. Whisper funziona indipendentemente. |
| Nome errato o non riconosciuto | Verifica/rimuovi il profilo; conferma campioni puliti. Per il singolo meeting usa Nomi parlanti. |
| Teams non avvia/ferma la registrazione | Usa REC/STOP e verifica il rilevamento sulla tua versione di Teams. |

## Sviluppo e test essenziali

Non servono per usare l'app. Manteniamo solo la cartella `tests/`, senza risultati, screenshot, benchmark o registrazioni di prova versionati.

Le funzionalità rilevanti e le modifiche di installazione/uso devono essere documentate qui insieme al codice: la regola è salvata anche in `AGENTS.md`. Aggiorna inoltre gli asset e lo script di export quando necessario; i dati privati restano sempre esclusi. Per rigenerare l'ICO dopo un cambiamento del PNG originale, usa `.\.venv\Scripts\python.exe .\scripts\build_desktop_icon.py` e ricrea il collegamento.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\install.ps1 -Dev
.\.venv\Scripts\python.exe -m pytest
```

Pytest usa la directory temporanea di sistema e non crea cache nel progetto. Non impostare `--basetemp .test-...` nel repository. I test devono usare esclusivamente dati inventati e risposte simulate; non usare meeting reali per benchmark o confronti automatici.
