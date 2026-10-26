# BIONIC · Local Meeting Assistant

Un assistente desktop per Windows: registra microfono e audio del PC, trascrive italiano e inglese, riconosce le voci della tua rubrica locale e genera recap in italiano **solo quando li richiedi**.

Icona trascinabile sempre in primo piano, controlli al passaggio del mouse, cinque stili, archivio e trascrizione Live. Audio, nomi e contenuti delle conversazioni non vengono inviati a servizi AI remoti.

**Aggiornamento 23 ottobre 2026:** solo il widget resta in primo piano; archivio, Live e impostazioni sono finestre normali. Il widget è ora una finestra gestibile nei desktop virtuali Windows. All'avvio BIONIC può avviare anche il server LM Studio in background, senza richiedere un recap o caricare un LLM.

**Aggiornamento 24 ottobre 2026:** nuova analisi delle voci. Indichi quante persone hanno parlato e BIONIC raggruppa a parte, per somiglianza, anche le altre voci; risate, rumori e frammenti finiscono in «Incerti» senza occupare il posto di una persona. Le voci già in rubrica ricevono il nome in automatico e la finestra permette di cercare una persona della rubrica nel meeting. Vedi [Analisi delle voci](#analisi-delle-voci-opzioni-e-ricerca-in-rubrica).

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
4. Apri LM Studio almeno una volta per completare la prima configurazione. BIONIC tenta poi di avviarne il server locale sulla porta **1234** tramite `lms.exe`. In alternativa, dalla sezione **Developer** di LM Studio avvia il server manualmente.
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

### Finestre normali e desktop virtuali

- Solo l'icona con il pannello rapido resta sopra le altre finestre. Archivio, Live, Aspetto, rubrica e impostazioni non hanno più il widget come finestra proprietaria sempre in primo piano: puoi coprirle con altre applicazioni, ridurle a icona e ridimensionarle.
- Per spostare una finestra su un altro desktop: **Win+Tab → clic destro sulla finestra → Sposta in → desktop desiderato**. Il widget compare anch'esso fra le finestre Windows (quindi può avere una voce nella barra delle applicazioni/Alt+Tab); icona e pannello rapido si spostano insieme.
- Archivio e widget sono indipendenti: puoi tenerli su desktop diversi. Se vuoi l'intera app su un solo desktop, sposta le finestre aperte su quel desktop oppure apri lì BIONIC.
- Se una finestra continua a comparire ovunque, in Win+Tab disattiva **Mostra questa finestra su tutti i desktop** e **Mostra le finestre di questa app su tutti i desktop**, se selezionati. L'app non modifica le tue scelte di pinning di Windows.
- Il menu vicino all'orologio resta disponibile: **Mostra controllo** riapre il widget anche se lo hai minimizzato o chiuso. Chiudere una finestra non interrompe automaticamente una registrazione.

### Avvio insieme a LM Studio

L'opzione **Avvia LM Studio insieme a BIONIC**, nel menu vicino all'orologio, è abilitata per impostazione predefinita e salvata per i successivi avvii. Vale dal collegamento desktop, dagli script e dall'avvio al login.

BIONIC usa `lms server start --port <porta configurata> --bind 127.0.0.1` per avviare **il servizio locale in background**: non forza l'apertura della finestra chat di LM Studio e non esegue comandi di caricamento modelli, trascrizione o recap. Il comando è asincrono, non blocca i controlli di registrazione e scade dopo 60 secondi. Lo stato è mostrato nel menu, dove puoi scegliere **Avvia / riprova server LM Studio**. Un errore non impedisce di registrare o usare Whisper.

Il comando richiede LM Studio installato e aperto almeno una volta. BIONIC cerca `%USERPROFILE%\.lmstudio\bin\lms.exe` oppure `lms.exe` nel PATH; per un'installazione personalizzata imposta `lms_executable` nel JSON al percorso assoluto dell'eseguibile. `auto_start_lmstudio: false` disabilita l'avvio insieme. Sono ammessi solo endpoint HTTP `localhost`/`127.0.0.1`, non server remoti. Disabilitare l'opzione o uscire da BIONIC **non arresta** un server LM Studio già attivo. Le eventuali impostazioni di ripristino/caricamento automatico dei modelli in LM Studio restano sotto il tuo controllo.

Riferimenti ufficiali: [CLI LM Studio](https://lmstudio.ai/docs/cli), [avvio server locale](https://lmstudio.ai/docs/cli/serve/server-start).

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

Non abilitare l'avvio automatico prima di aver verificato microfono, Whisper e impostazioni di registrazione automatica. L'accesso a Windows avvia BIONIC; se l'opzione **Avvia LM Studio insieme a BIONIC** è attiva, BIONIC avvia anche il server locale.

## Uso quotidiano

| Controllo | Cosa fa |
| --- | --- |
| REC / STOP | Avvia la registrazione / salva e completa la trascrizione. STOP **non genera recap**. |
| Live | Mostra testo e nomi durante REC. Se il nome arriva dopo il testo, aggiorna la stessa riga. |
| Archivio | Elenca le sessioni; Trascrivi audio ricostruisce la trascrizione dagli originali. |
| Genera solo recap | Avvia il modello locale selezionato, su tua richiesta. Risultato sempre in italiano. |
| Rinomina / Elimina | Cambia il titolo / sposta la sessione nel Cestino dopo conferma. |
| Analizza voci | Rianalizza gli audio vecchi con le opzioni scelte, raggruppa le voci, assegna i nomi della rubrica e permette di ascoltare campioni. |
| Nomi parlanti | Corregge manualmente le attribuzioni. I nomi manuali hanno precedenza. |
| Aspetto | Cinque temi, colori, animazioni ed effetti del widget. |
| Esci, dal menu dell'icona | Termina l'app. Necessario per applicare gli aggiornamenti. |

Durante il recap richiesto, Whisper lascia spazio al LLM; al termine il LLM viene scaricato e Whisper ripristinato. Una nuova registrazione interrompe il recap, ma il cambio di modello richiede tempo tecnico. Le voci usano CPU/RAM, non VRAM.

### Insegnare i nomi alla rubrica vocale

1. Apri **Archivio → Analizza voci**.
2. Se necessario avvia l'analisi; ascolta più campioni dello stesso gruppo. Le voci già in rubrica hanno il nome compilato e la colonna **Rubrica** indica «automatico» con la somiglianza.
3. Inserisci o correggi il nome. Per attribuire più gruppi alla stessa persona, usa lo stesso nome.
4. **Conferma nomi e applica** aggiorna quella trascrizione, non il recap già generato.
5. **Memorizza voce selezionata nella rubrica** salva il profilo per i meeting successivi, dopo conferma. Servono almeno sei secondi e due campioni.
6. Gestisci/rimuovi i profili da **Rubrica vocale locale**, anche dal menu vicino all'orologio.

Il motore resta pronto e durante REC analizza finestre di circa tre secondi. Se la corrispondenza con una voce già in rubrica è sufficientemente netta (somiglianza almeno 0,60, 0,70 per frammenti sotto 1,5 secondi, e distacco di 0,08 dal secondo candidato), il nome compare subito nel widget e viene associato ai passaggi disponibili in Live. Whisper mantiene i propri tempi: non è una trascrizione istantanea parola per parola. Le corrispondenze incerte restano senza nome. Il riconoscimento live gira in un processo separato su CPU a bassa priorità (circa 2% della CPU e 450 MB di RAM misurati con audio sintetico), non usa la GPU e non modifica il testo di Whisper; se resta indietro salta campioni invece di rallentare registrazione o trascrizione.

Il numero di voci è una stima: eco, sovrapposizioni, interventi brevi e cambio microfono possono dividere o mescolare gruppi. Nessun apprendimento automatico dai nomi ipotizzati: un nome assegnato dalla rubrica non aggiunge mai impronte al profilo; verifica i campioni prima di memorizzare un profilo.

### Analisi delle voci: opzioni e ricerca in rubrica

In **Archivio → Analizza voci**, riquadro **Opzioni di analisi**:

| Opzione | Effetto | Quando cambiarla |
| --- | --- | --- |
| Persone che hanno parlato | Numero di gruppi «Persona», ordinati per tempo di parola. 0 = stima automatica. | Indicalo sempre: per esempio 5 o 6. Conta solo chi ha parlato. |
| Raggruppa anche le altre voci | Le voci oltre quel numero restano gruppi «Altra voce» invece di essere forzate dentro le persone. | Disattivala solo se sei certo del numero e vuoi esattamente quei gruppi. |
| Separazione voci | Somiglianza necessaria per unire due gruppi: Meno gruppi 0,45 · Bilanciata 0,55 · Più gruppi 0,65. | «Più gruppi» se due persone finiscono insieme; «Meno gruppi» se una persona è divisa in più gruppi. |
| Taglio finestre | Lunghezza dei pezzi da cui si calcola ogni impronta: 1,5 · 2 · 3 secondi. | 1,5 s per scambi rapidi; 3 s per interventi lunghi e impronte più stabili. |
| Filtro parlato | Sicurezza richiesta al rilevatore Silero: Ampio · Normale · Rigoroso. | «Rigoroso» se escono molte risate o rumori; «Ampio» se mancano voci deboli o lontane. |
| Gruppo minimo | Sotto questa durata (3, 8, 15, 30 s) un gruppo non occupa il posto di una persona: viene unito alla voce più simile o finisce in «Incerti». | Abbassalo (3 s) se una persona ha detto pochissimo e non viene trovata. |
| Scarta l'eco dell'audio PC | Ignora sul MIC i passaggi uguali a ciò che esce contemporaneamente dal PC (casse invece delle cuffie). | Disattivala se usi le cuffie e vedi parti della tua voce mancanti. |
| Nomi automatici dalla rubrica | Dopo l'analisi assegna i nomi delle voci memorizzate e unisce i gruppi riconosciuti come la stessa persona (per esempio divisa fra MIC e PC). Soglia: Prudente 0,65 · Bilanciata 0,55 · Ampia 0,45. | «Prudente» se compaiono nomi sbagliati, «Ampia» se una voce memorizzata non viene riconosciuta. |

**Ripristina consigliati** riporta i valori iniziali; riaprendo la finestra vengono riproposte le opzioni dell'ultima analisi.

Come lavora: le impronte vengono prima suddivise in molti piccoli gruppi e poi unite in base alla somiglianza dei loro centri, così una risata isolata non può prendere il posto di una persona. Se il numero indicato è più alto delle voci distinte trovate, la finestra lo segnala invece di inventare gruppi: prova un taglio più corto, il filtro Ampio o un gruppo minimo più basso. Il gruppo **Incerti** raccoglie frammenti e suoni non attribuibili: puoi ascoltarlo e usare la blacklist, ma non assegnargli un nome né memorizzarlo.

I nomi assegnati in automatico aggiornano subito la trascrizione come «stimati dalla rubrica»; **Conferma nomi e applica** li rende confermati. I nomi manuali hanno sempre precedenza. Per cercare una persona: scrivi o scegli il nome in **Cerca in rubrica**, premi **Trova nel meeting** (la colonna Rubrica mostra la somiglianza di ogni gruppo e viene selezionato il più simile), ascolta i campioni e premi **Assegna al gruppo selezionato**. Selezionando un gruppo vedi anche i tre candidati più simili della rubrica.

Le soglie sono valori di partenza per ECAPA, non tarati sulle tue conversazioni. Le analisi salvate prima di questo aggiornamento restano leggibili; per ottenere i nuovi gruppi esegui una nuova analisi.

Il file `plda.gguf` di *speaker-diarization-community-1* in LM Studio non è usato: è la calibrazione delle impronte WeSpeaker di pyannote, incompatibile con le impronte ECAPA attuali.

### Correggere risate/rumori e rianalizzare le voci

1. Apri **Archivio → Analizza voci**. In **Persone che hanno parlato** inserisci il numero effettivo di parlanti sulle due tracce insieme: ad esempio 1, 2, 5 oppure 8. Se siete cinque ma parla solo una persona, inserisci **1**. Il valore 0 lascia la stima automatica, che può sbagliare. Il conteggio dell'ultima analisi viene riproposto riaprendo la finestra.
2. Seleziona un gruppo e ascolta i campioni. Se senti una risata, rumore o musica, apri **Non è parlato / blacklist…**: viene proposto l'ultimo campione ascoltato. Puoi scegliere un altro intervallo o correggere traccia e tempi in secondi.
3. Premi **Escludi intervallo**. **Escludi tutto il gruppo selezionato** richiede una conferma: usalo solo se tutto il gruppo è davvero non parlato. Una voce sconosciuta o un nome errato non sono motivi per escludere il parlato.
4. Chiudi il dialogo e premi **Avvia analisi locale**. Le esclusioni vengono sottratte dall'audio analizzato **prima** di calcolare le impronte e raggrupparle, senza spostare i tempi. I gruppi vengono ricreati: verifica e conferma nuovamente i nomi.
5. Per annullare una correzione riapri la blacklist, seleziona la riga e premi **Ripristina intervallo selezionato**. Rianalizza per ricostruire i gruppi se li avevi già ricalcolati.

**Non viene cancellata alcuna parola:** audio originali, testo completo, tempi e recap esistente restano conservati. Vengono rimosse solo le attribuzioni vocali automatiche interessate; i nomi corretti manualmente hanno precedenza. Anche il testo senza parlante riconosciuto rimane disponibile per un futuro recap, sempre avviato da te. Le impronte contaminate da un intervallo escluso non possono essere memorizzate finché non vengono ricalcolate.

La blacklist è un database locale di intervalli associati al singolo meeting, non ai numeri dei gruppi (che possono cambiare). Evita gli stessi errori nelle rianalisi di **quel meeting**, ma **non addestra ECAPA e non riconosce automaticamente risate simili nei meeting futuri**. I profili salvati restano compatibili e non vengono modificati dalle correzioni. Per le analisi d'archivio il nome della rubrica viene assegnato se il centro del gruppo supera la soglia scelta, con un distacco dal secondo candidato, e nessun campione del gruppo somiglia di più a un'altra persona memorizzata.

Whisper e il riconoscimento live non vengono sostituiti. Il motore attualmente integrato resta **ECAPA + Silero su CPU**: nessun nuovo consumo di VRAM e nessun download necessario per queste correzioni o per le nuove opzioni di analisi. Community-1 non è ancora integrato; un modello Nemotron GGUF per LM Studio non sostituisce il motore delle impronte vocali. Non sono dichiarati miglioramenti di accuratezza misurati sulle tue conversazioni: le verifiche di sviluppo usano solo dati fittizi.

Se l'analisi restava bloccata dopo l'avvio, chiudi BIONIC dal menu vicino all'orologio con **Esci**, poi riaprila dal collegamento desktop per caricare il codice aggiornato. Non cancellare la rubrica né reinstallare Whisper. Se appare invece “Voci non installate”, usa l'installazione del punto 5.

## Dove sono i dati e cosa non condividere

Per impostazione predefinita:

- registrazioni: `%USERPROFILE%\Documents\Local Meeting Assistant\recordings`;
- rubrica: `voice-profiles.sqlite3`, accanto alla cartella registrazioni configurata;
- correzioni non parlato: `voice-errors.sqlite3`, accanto alla stessa cartella (contiene tempi, traccia e motivo; non audio o trascrizioni);
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

Lo script carica esplicitamente le librerie ZIP anche in Windows PowerShell 5.1: non è necessario installare PowerShell 7 per esportare i sorgenti.

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
| Nome errato o non riconosciuto | Cambia la soglia Rubrica (Prudente/Ampia) e rianalizza, oppure usa Cerca in rubrica; verifica/rimuovi il profilo. Per il singolo meeting usa Nomi parlanti. |
| Risate trovate come voci, persone mancanti | Indica il numero di persone, filtro parlato Rigoroso; se manca qualcuno, gruppo minimo 3 s o taglio 1,5 s. |
| Teams non avvia/ferma la registrazione | Usa REC/STOP e verifica il rilevamento sulla tua versione di Teams. |

## Sviluppo e test essenziali

Non servono per usare l'app. Manteniamo solo la cartella `tests/`, senza risultati, screenshot, benchmark o registrazioni di prova versionati.

Le funzionalità rilevanti e le modifiche di installazione/uso devono essere documentate qui insieme al codice: la regola è salvata anche in `AGENTS.md`. Aggiorna inoltre gli asset e lo script di export quando necessario; i dati privati restano sempre esclusi. Per rigenerare l'ICO dopo un cambiamento del PNG originale, usa `.\.venv\Scripts\python.exe .\scripts\build_desktop_icon.py` e ricrea il collegamento.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\install.ps1 -Dev
.\.venv\Scripts\python.exe -m pytest
```

Pytest usa la directory temporanea di sistema e non crea cache nel progetto. Non impostare `--basetemp .test-...` nel repository. I test devono usare esclusivamente dati inventati e risposte simulate; non usare meeting reali per benchmark o confronti automatici.

L'installazione `-Dev` include SciPy per verificare il raggruppamento con vettori fittizi. Non richiede di caricare modelli o aprire registrazioni reali.
