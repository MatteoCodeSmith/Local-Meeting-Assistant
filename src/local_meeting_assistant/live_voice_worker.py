"""Warm CPU-only voice encoder: stdin PCM in, embeddings out. No audio files or network."""
import base64
import json
import os
import sys
from pathlib import Path


def main():
    os.environ.update(HF_HUB_OFFLINE="1", CUDA_VISIBLE_DEVICES="", OMP_NUM_THREADS="2", MKL_NUM_THREADS="2")
    if os.name == "nt":
        import ctypes
        ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x4000)
    import socket
    def offline(*_args, **_kwargs):
        raise RuntimeError("Rete disabilitata per le voci locali.")
    socket.socket.connect = offline
    socket.socket.connect_ex = offline
    socket.socket.sendto = offline
    socket.create_connection = offline
    try:
        import numpy as np
        from scipy.signal import resample_poly
        import math
        from silero_vad import load_silero_vad, get_speech_timestamps
        from .voice_engine import EcapaEncoder
        encoder = EcapaEncoder(Path(sys.argv[1]))
        vad = load_silero_vad(onnx=True)
        print(json.dumps({"ready": True}), flush=True)
        for line in sys.stdin:
            try:
                request = json.loads(line)
                pcm = np.frombuffer(base64.b64decode(request["pcm"]), dtype="float32")
                rate = int(request["rate"])
                if rate != 16000:
                    divisor = math.gcd(rate, 16000)
                    pcm = resample_poly(pcm, 16000 // divisor, rate // divisor).astype(np.float32)
                turns = []
                for span in get_speech_timestamps(pcm, vad, sampling_rate=16000,
                                                  min_speech_duration_ms=800, min_silence_duration_ms=200,
                                                  speech_pad_ms=0):
                    start, end = int(span["start"]), int(span["end"])
                    if end - start >= 12800:
                        turns.append({"start": request["offset"] + start / 16000,
                                      "end": request["offset"] + end / 16000,
                                      "embedding": encoder.encode(pcm[start:end]).tolist()})
                print(json.dumps({"session": request["session"], "track": request["track"], "turns": turns}), flush=True)
            except Exception:
                print(json.dumps({"chunk_error": True}), flush=True)
        return 0
    except Exception as exc:
        print(json.dumps({"startup_error": type(exc).__name__}), flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
