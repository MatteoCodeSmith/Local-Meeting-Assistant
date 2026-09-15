"""CPU-only ECAPA + Silero voice grouping. Imported ONLY in the isolated worker."""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np


class EcapaEncoder:
    def __init__(self, checkpoint: Path):
        # Explicit architecture + weights_only: no remote fetching or executing model YAML.
        import torch
        from speechbrain.lobes.features import Fbank
        from speechbrain.lobes.models.ECAPA_TDNN import ECAPA_TDNN
        from speechbrain.processing.features import InputNormalization

        torch.set_num_threads(2)
        self.torch = torch
        self.features = Fbank(n_mels=80).eval()
        self.norm = InputNormalization(norm_type="sentence", std_norm=False).eval()
        self.model = ECAPA_TDNN(input_size=80, channels=[1024, 1024, 1024, 1024, 3072],
                               kernel_sizes=[5, 3, 3, 3, 1], dilations=[1, 2, 3, 4, 1],
                               attention_channels=128, lin_neurons=192).eval()
        self.model.load_state_dict(torch.load(checkpoint, map_location="cpu", weights_only=True))

    def encode(self, pcm: np.ndarray) -> np.ndarray:
        with self.torch.inference_mode():
            audio = self.torch.from_numpy(pcm.copy()).unsqueeze(0)
            lengths = self.torch.ones(1)
            features = self.norm(self.features(audio), lengths)
            vector = self.model(features, lengths).numpy().reshape(-1)
        return unit(vector)


def unit(vector):
    vector = np.asarray(vector, dtype=np.float32)
    norm = float(np.linalg.norm(vector))
    if not np.isfinite(vector).all() or norm < 1e-8:
        raise ValueError("Impronta vocale non valida.")
    return vector / norm


def cluster_vectors(vectors, threshold=0.7, expected=0):
    """Bounded-memory two-pass clustering. Count is a heuristic, never attendance truth."""
    if not vectors:
        return []
    from scipy.cluster.hierarchy import linkage, fcluster

    matrix = np.stack([unit(vector) for vector in vectors])
    # Bound the quadratic agglomerative pass even for all-day recordings.
    indices = np.unique(np.linspace(0, len(matrix) - 1, min(1200, len(matrix)), dtype=int))
    sample = matrix[indices]
    if len(sample) == 1:
        return [0] * len(matrix)
    tree = linkage(sample, method="average", metric="cosine")
    if expected:
        from scipy.cluster.hierarchy import cut_tree
        labels = cut_tree(tree, n_clusters=min(expected, len(sample))).reshape(-1)
    else:
        labels = fcluster(tree, 1 - threshold, criterion="distance") - 1
    centers = np.stack([unit(sample[labels == label].mean(axis=0)) for label in np.unique(labels)])
    # Blocks avoid a huge windows-by-speakers matrix for long recordings.
    assigned = []
    for offset in range(0, len(matrix), 256):
        assigned.extend(np.argmax(matrix[offset:offset + 256] @ centers.T, axis=1).tolist())
    # Stable IDs in first-occurrence order, regardless of scipy cluster numbering.
    remap = {}
    return [remap.setdefault(label, len(remap)) for label in assigned]


def audio_blocks(path, seconds=30):
    import soundfile as sf
    from scipy.signal import resample_poly

    with sf.SoundFile(path) as source:
        rate = source.samplerate
        while source.tell() < len(source):
            offset = source.tell() / rate
            audio = source.read(int(seconds * rate), dtype="float32", always_2d=True).mean(axis=1)
            if rate != 16000:
                divisor = math.gcd(rate, 16000)
                audio = resample_poly(audio, 16000 // divisor, rate // divisor).astype(np.float32)
            yield offset, audio


def analyse(directory: Path, checkpoint: Path, *, threshold=0.7, expected=0,
            progress=lambda value: None, encoder=None, speech_detector=None):
    """Stream original tracks; never changes or decodes transcript content."""
    import soundfile as sf
    if not 0.4 <= threshold <= 0.9 or not 0 <= expected <= 30:
        raise ValueError("Parametri di raggruppamento non validi.")
    tracks = [(name, directory / f"{name}.flac") for name in ("mic", "system")
              if (directory / f"{name}.flac").is_file()]
    if not tracks:
        raise ValueError("Nessun audio originale disponibile.")
    encoder = encoder or EcapaEncoder(checkpoint)
    if speech_detector is None:
        from silero_vad import load_silero_vad, get_speech_timestamps
        vad = load_silero_vad(onnx=True)

        def speech_detector(pcm):
            return get_speech_timestamps(pcm, vad, sampling_rate=16000,
                                         min_speech_duration_ms=300, min_silence_duration_ms=250,
                                         speech_pad_ms=30)

    duration = sum(sf.info(path).duration for _, path in tracks)
    processed, windows, vectors, short_seconds = 0.0, [], [], 0.0
    for track, path in tracks:
        for offset, pcm in audio_blocks(path):
            for span in speech_detector(pcm):
                start, end = int(span["start"]), int(span["end"])
                if (end - start) / 16000 < 0.8:
                    short_seconds += (end - start) / 16000
                    continue
                # Adjacent, non-overlapping windows so alignment cannot double-count speech.
                pieces = max(1, math.ceil((end - start) / (3.0 * 16000)))
                bounds = np.linspace(start, end, pieces + 1, dtype=int)
                for left, right in zip(bounds[:-1], bounds[1:]):
                    vectors.append(encoder.encode(pcm[left:right]))
                    windows.append({"track": track, "start": offset + left / 16000,
                                    "end": offset + right / 16000})
            processed += len(pcm) / 16000
            progress(min(95, int(95 * processed / max(duration, 1))))
    labels = cluster_vectors(vectors, threshold, expected)
    for window, label in zip(windows, labels):
        window["speaker"] = f"Voce {label + 1}"
    groups = []
    for label in sorted(set(labels)):
        key = f"Voce {label + 1}"
        candidates = [i for i, value in enumerate(labels) if value == label]
        center = unit(np.mean([vectors[i] for i in candidates], axis=0))
        ranked = sorted(candidates, key=lambda i: float(np.dot(vectors[i], center)), reverse=True)
        samples = []
        for index in ranked:
            window = windows[index]
            if all(s["track"] != window["track"] or abs(s["start"] - window["start"]) >= 6 for s in samples):
                samples.append(dict(window))
            if len(samples) == 3:
                break
        groups.append({"speaker": key, "seconds": sum(windows[i]["end"] - windows[i]["start"] for i in candidates),
                       "samples": samples, "embedding": center.tolist(),
                       "tracks": sorted({windows[i]["track"] for i in candidates})})
    progress(100)
    return {"version": 1, "engine": "ECAPA SpeechBrain / Silero · CPU",
            "threshold": threshold, "expected": expected, "groups": groups, "turns": windows,
            "names": {}, "short_speech_seconds": round(short_seconds, 2),
            "warning": "Stima delle voci, non numero certo dei presenti. Verificare i campioni: "
                       "sovrapposizioni, eco, voci brevi e cambi di microfono possono confondere i gruppi."}
