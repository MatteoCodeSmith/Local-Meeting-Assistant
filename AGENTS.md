# Privacy delle conversazioni

- Non leggere, cercare nel contenuto, riassumere o confrontare audio, trascrizioni,
  chat e recap reali dell'utente, inclusi checkpoint e copie negli artifacts.
- Non eseguire benchmark o generazioni sulle registrazioni reali. La scelta dei
  modelli, l'avvio dei recap e il confronto dei risultati spettano all'utente nell'app.
- Per sviluppo e verifica usare esclusivamente dati fittizi e risposte AI simulate.
- Si possono ispezionare codice, test, metadati tecnici dei modelli installati e
  stato dei processi avviati dall'agente, senza accedere ai contenuti delle conversazioni.
- L'elaborazione delle registrazioni nell'app deve restare locale e partire solo
  dall'azione esplicita dell'utente per il recap. Non attivare servizi AI remoti.

# Manutenzione del repository e della guida

- A ogni nuova funzionalita rilevante o modifica dell'installazione/uso aggiornare
  il README nella stessa modifica, con istruzioni pratiche e limiti verificati.
- Mantenere allineati sorgenti, asset necessari, script di installazione/export
  e test essenziali. Non creare cartelle di prove o risultati nella radice.
- Verificare le modifiche usando solo dati fittizi e aggiungere a Git soltanto
  i file pertinenti, con percorsi espliciti; preservare le altre modifiche dell'utente.
- Non includere modelli, ambienti, registrazioni, profili vocali o dati privati.
  Non eseguire push/pubblicazioni remote senza una richiesta esplicita.
