"""Technical checks only: no audio devices, models, config or conversations opened."""
import importlib
import subprocess
import sys
from pathlib import Path


def main():
    root = Path(__file__).resolve().parents[1]
    failures = []
    print(f"Python: {sys.version.split()[0]}")
    for module in ("local_meeting_assistant", "PySide6.QtWidgets", "soundfile", "sounddevice",
                   "soundcard", "faster_whisper.vad", "transcribe_cpp", "httpx", "send2trash", "pywinauto"):
        try:
            imported = importlib.import_module(module)
            if module == "local_meeting_assistant" and Path(imported.__file__).resolve().parent != root / "src/local_meeting_assistant":
                raise RuntimeError("Il pacchetto punta a un'altra cartella: rieseguire install.ps1 qui.")
            print(f"OK: {module}")
        except Exception as exc:
            print(f"ERRORE: {module}: {type(exc).__name__}")
            failures.append(module)
    voice_python = root / ".venv-voices/Scripts/python.exe"
    checkpoint = root / "models/ecapa/embedding_model.ckpt"
    if voice_python.is_file() and checkpoint.is_file():
        result = subprocess.run([str(voice_python), "-c", "import torch,torchaudio,speechbrain,silero_vad,soundfile,scipy; print('OK: dipendenze voci CPU')"],
                                timeout=90, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if result.returncode:
            failures.append("voci")
    else:
        print("AVVISO: voci non installate. Eseguire scripts/install-voices.ps1 se desiderate.")
    print("Nessun audio acquisito, modello caricato o contenuto privato letto.")
    print("Whisper GGUF e server LM Studio vanno configurati come descritto nel README.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
