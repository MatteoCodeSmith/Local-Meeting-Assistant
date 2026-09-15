"""Isolated offline process, invoked exclusively by an explicit archive action."""
import argparse
import json
import os
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--threshold", type=float, default=0.7)
    parser.add_argument("--expected", type=int, default=0)
    args = parser.parse_args()
    if os.name == "nt":
        import ctypes
        ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x4000)
    os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", CUDA_VISIBLE_DEVICES="",
                      OMP_NUM_THREADS="2", MKL_NUM_THREADS="2", HF_HUB_DISABLE_TELEMETRY="1")
    # Defence in depth: inference cannot open any network socket, even through a dependency.
    import socket
    def offline(*_args, **_kwargs):
        raise RuntimeError("Rete disabilitata durante l'analisi vocale locale.")
    socket.socket.connect = offline
    socket.socket.connect_ex = offline
    socket.socket.sendto = offline
    socket.create_connection = offline
    try:
        from .voice_engine import analyse
        result = analyse(args.directory, args.checkpoint, threshold=args.threshold, expected=args.expected,
                         progress=lambda value: print(json.dumps({"progress": value}), flush=True))
        temp = args.output.with_suffix(".tmp")
        temp.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
        temp.replace(args.output)
        return 0
    except Exception as exc:
        # Do not echo paths, audio, transcription text, or dependency request details.
        print(json.dumps({"error": type(exc).__name__}), flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
