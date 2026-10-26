"""CPU-only ECAPA + Silero voice grouping. Encoders are imported ONLY in the isolated worker."""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np

from .voice_corrections import allowed_spans


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


UNCERTAIN = "Incerti"
# Recommended analysis settings. Thresholds are ECAPA cosine similarities between
# group centres; none of them was tuned on real conversations.
DEFAULT_OPTIONS = {
    "window": 2.0,              # Length of each fingerprint window ("taglio"), seconds.
    "vad_threshold": 0.5,       # Silero speech probability: higher drops laughs/noise.
    "min_group_seconds": 8.0,   # Shorter groups are fragments, never a person slot.
    "other_voices": True,       # Keep voices beyond the declared count as extra groups.
    "drop_echo": True,          # Drop PC audio picked up again by the microphone.
    "use_profiles": True,       # Name and merge groups that match the local voice book.
    "profile_threshold": 0.55,
}
QUIET_RMS = 10 ** (-55 / 20)


def _centre(matrix, weights, members):
    return unit((matrix[members] * weights[members, None]).sum(axis=0))


def _split(matrix, weights, members):
    from scipy.cluster.hierarchy import fcluster, linkage
    parts = fcluster(linkage(matrix[members], method="average", metric="cosine"), 2,
                     criterion="maxclust")
    return [members[parts == label] for label in np.unique(parts)]


def cluster_voices(vectors, threshold=0.55, expected=0, durations=None,
                   min_group_seconds=0.0, other_voices=True):
    """Return (labels, persons): 0..persons-1 are people ordered by talk time, higher labels
    are other voices grouped by similarity, -1 is uncertain (laughs, noise, fragments).

    Over-segment first, then merge group centres: average-linkage cut to an exact count
    lets isolated laughs or noise take a person's slot while real voices are merged.
    """
    if not vectors:
        return [], 0
    from scipy.cluster.hierarchy import fcluster, linkage

    matrix = np.stack([unit(vector) for vector in vectors])
    weights = (np.ones(len(matrix), dtype=np.float32) if durations is None
               else np.asarray(durations, dtype=np.float32))
    # Bound the quadratic agglomerative pass even for all-day recordings.
    indices = np.unique(np.linspace(0, len(matrix) - 1, min(1500, len(matrix)), dtype=int))
    sample, sample_weights = matrix[indices], weights[indices]
    scale = float(weights.sum() / max(float(sample_weights.sum()), 1e-6))
    if len(sample) == 1:
        initial = np.zeros(1, dtype=int)
    else:
        tree = linkage(sample, method="average", metric="cosine")
        initial = fcluster(tree, max(30, 5 * expected), criterion="maxclust") - 1

    def make(members):
        return {"members": members, "seconds": float(sample_weights[members].sum() * scale),
                "centre": _centre(sample, sample_weights, members)}

    def merge(target, source):
        target.update(make(np.concatenate([target["members"], source["members"]])))

    def merge_closest(groups, floor):
        pairs = [(float(groups[i]["centre"] @ groups[j]["centre"]), i, j)
                 for i in range(len(groups)) for j in range(i + 1, len(groups))]
        if not pairs or max(pairs)[0] < floor:
            return False
        _, i, j = max(pairs)
        merge(groups[i], groups.pop(j))
        return True

    clusters = [make(np.flatnonzero(initial == label)) for label in np.unique(initial)]
    big = [c for c in clusters if c["seconds"] >= min_group_seconds] or \
        [max(clusters, key=lambda c: c["seconds"])]
    small = [c for c in clusters if all(c is not b for b in big)]
    while merge_closest(big, threshold):
        pass
    if expected and not other_voices:
        while len(big) > expected and merge_closest(big, -1.0):
            pass
    if expected and len(big) < expected:
        # Split only borderline merges. Fragments below the minimum (often laughs) never
        # take a person slot: the caller warns and the user can lower the minimum.
        while len(big) < expected:
            for c in sorted(big, key=lambda c: c["seconds"], reverse=True):
                if len(c["members"]) < 4:
                    continue
                halves = [make(part) for part in _split(sample, sample_weights, c["members"])]
                if (len(halves) == 2
                        and min(h["seconds"] for h in halves) >= max(2.0, min_group_seconds / 2)
                        and float(halves[0]["centre"] @ halves[1]["centre"]) < threshold + 0.1):
                    big = [item for item in big if item is not c] + halves
                    break
            else:
                break
    big.sort(key=lambda c: c["seconds"], reverse=True)
    persons, others = (big[:expected], big[expected:]) if expected else (big, [])
    # Dicts hold arrays: compare by identity, never with list.remove/==.
    kept = []
    for c in others:
        best = max(persons, key=lambda p: float(p["centre"] @ c["centre"]))
        if float(best["centre"] @ c["centre"]) >= threshold - 0.1:
            merge(best, c)
        else:
            kept.append(c)
    others = kept
    uncertain = []
    for c in small:
        target = max(persons + others, key=lambda p: float(p["centre"] @ c["centre"]))
        if float(target["centre"] @ c["centre"]) >= threshold - 0.15:
            merge(target, c)
        else:
            uncertain.append(c)
    persons.sort(key=lambda c: c["seconds"], reverse=True)
    others.sort(key=lambda c: c["seconds"], reverse=True)
    final = np.stack([c["centre"] for c in persons + others])
    rejected = np.stack([c["centre"] for c in uncertain]) if uncertain else None
    labels = []
    # Blocks avoid a huge windows-by-speakers matrix for long recordings.
    for offset in range(0, len(matrix), 256):
        block = matrix[offset:offset + 256]
        scores = block @ final.T
        best, best_score = np.argmax(scores, axis=1), scores.max(axis=1)
        rejected_score = (block @ rejected.T).max(axis=1) if rejected is not None else None
        for row, (label, score) in enumerate(zip(best.tolist(), best_score.tolist())):
            if score < threshold - 0.25 or (rejected_score is not None
                                            and rejected_score[row] > score):
                labels.append(-1)
            else:
                labels.append(label)
    # Compact labels in group order: a group can lose every window in reassignment.
    used = sorted({label for label in labels if label >= 0})
    remap = {label: index for index, label in enumerate(used)}
    return ([remap.get(label, -1) for label in labels],
            sum(1 for label in used if label < len(persons)))


def cluster_vectors(vectors, threshold=0.55, expected=0):
    """Labels only; bounded-memory clustering. Count is a heuristic, never attendance truth."""
    return cluster_voices(vectors, threshold, expected)[0]


def drop_echo(windows, vectors, similarity=0.5):
    """Indices to keep: a mic window concurrent with a similar PC window is loudspeaker echo."""
    import bisect
    system = sorted((w["start"], w["end"], i) for i, w in enumerate(windows) if w["track"] == "system")
    starts = [item[0] for item in system]
    keep = []
    for index, window in enumerate(windows):
        if window["track"] == "mic":
            length = window["end"] - window["start"]
            first = bisect.bisect_left(starts, window["start"] - 10)
            last = bisect.bisect_left(starts, window["end"])
            if any(min(window["end"], end) - max(window["start"], start) >= length / 2
                   and float(vectors[index] @ vectors[other]) >= similarity
                   for start, end, other in system[first:last]):
                continue
        keep.append(index)
    return keep


def merge_turns(windows, longest=15.0):
    """Join contiguous windows of the same group; timestamps are never shifted."""
    turns = []
    for window in sorted(windows, key=lambda w: (w["track"], w["start"])):
        last = turns[-1] if turns else None
        if (last and last["track"] == window["track"] and last["speaker"] == window["speaker"]
                and abs(last["end"] - window["start"]) < 0.05
                and window["end"] - last["start"] <= longest):
            last["end"] = window["end"]
        else:
            turns.append(dict(window))
    return turns


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


def analysis_options(options=None):
    settings = {**DEFAULT_OPTIONS, **{key: value for key, value in (options or {}).items()
                                      if key in DEFAULT_OPTIONS}}
    if (not 1.0 <= float(settings["window"]) <= 4.0
            or not 0.2 <= float(settings["vad_threshold"]) <= 0.9
            or not 0 <= float(settings["min_group_seconds"]) <= 120
            or not 0.3 <= float(settings["profile_threshold"]) <= 0.9):
        raise ValueError("Opzioni di analisi vocale non valide.")
    return settings


def analyse(directory: Path, checkpoint: Path, *, threshold=0.55, expected=0, options=None,
            progress=lambda value: None, encoder=None, speech_detector=None, exclusions=()):
    """Stream original tracks; never changes or decodes transcript content."""
    import soundfile as sf
    if not 0.3 <= threshold <= 0.9 or not 0 <= expected <= 30:
        raise ValueError("Parametri di raggruppamento non validi.")
    settings = analysis_options(options)
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
                                         threshold=float(settings["vad_threshold"]),
                                         min_speech_duration_ms=500, min_silence_duration_ms=300,
                                         speech_pad_ms=30)

    window_seconds = float(settings["window"])
    shortest = min(1.0, window_seconds)
    duration = sum(sf.info(path).duration for _, path in tracks)
    processed, windows, vectors = 0.0, [], []
    short_seconds = quiet_seconds = 0.0
    for track, path in tracks:
        for offset, pcm in audio_blocks(path):
            for span in speech_detector(pcm):
                start, end = int(span["start"]), int(span["end"])
                for begin, finish in allowed_spans(track, offset + start / 16000,
                                                   offset + end / 16000, exclusions):
                    left_bound = max(start, round((begin - offset) * 16000))
                    right_bound = min(end, round((finish - offset) * 16000))
                    length = (right_bound - left_bound) / 16000
                    if length < shortest:
                        short_seconds += max(0.0, length)
                        continue
                    # Never feed rejected audio to the encoder/clusterer. Preserve timestamps.
                    pieces = max(1, round(length / window_seconds))
                    bounds = np.linspace(left_bound, right_bound, pieces + 1, dtype=int)
                    for left, right in zip(bounds[:-1], bounds[1:]):
                        piece = pcm[left:right]
                        if float(np.sqrt(np.mean(piece ** 2))) < QUIET_RMS:
                            quiet_seconds += (right - left) / 16000
                            continue
                        vectors.append(encoder.encode(piece))
                        windows.append({"track": track, "start": offset + left / 16000,
                                        "end": offset + right / 16000})
            processed += len(pcm) / 16000
            progress(min(95, int(95 * processed / max(duration, 1))))
    echo_seconds = 0.0
    if settings["drop_echo"] and windows:
        keep = drop_echo(windows, vectors)
        echo_seconds = sum(w["end"] - w["start"] for w in windows) - \
            sum(windows[i]["end"] - windows[i]["start"] for i in keep)
        windows, vectors = [windows[i] for i in keep], [vectors[i] for i in keep]
    labels, persons = cluster_voices(
        vectors, threshold, expected, [w["end"] - w["start"] for w in windows],
        float(settings["min_group_seconds"]), bool(settings["other_voices"]))
    for window, label in zip(windows, labels):
        window["speaker"] = f"Voce {label + 1}" if label >= 0 else UNCERTAIN
    groups = []
    for label in sorted(set(labels), key=lambda value: (value < 0, value)):
        key = f"Voce {label + 1}" if label >= 0 else UNCERTAIN
        candidates = [i for i, value in enumerate(labels) if value == label]
        center = unit(np.mean([vectors[i] for i in candidates], axis=0))
        ranked = sorted(candidates, key=lambda i: float(np.dot(vectors[i], center)), reverse=True)
        if label < 0:
            # A mix of fragments: listen from the longest ones, never enrol a "centre".
            ranked = sorted(candidates, key=lambda i: windows[i]["start"] - windows[i]["end"])
        samples = []
        references = []
        for index in ranked:
            window = windows[index]
            if all(s["track"] != window["track"] or abs(s["start"] - window["start"]) >= 6 for s in samples):
                samples.append({field: window[field] for field in ("track", "start", "end")})
                references.append(vectors[index].tolist())
            if len(samples) == 3:
                break
        group = {"speaker": key, "kind": "incerto" if label < 0 else
                 "persona" if label < persons else "altra",
                 "seconds": sum(windows[i]["end"] - windows[i]["start"] for i in candidates),
                 "samples": samples, "tracks": sorted({windows[i]["track"] for i in candidates})}
        if label >= 0:
            group.update(embedding=center.tolist(), reference_vectors=references)
        groups.append(group)
    found = sum(1 for group in groups if group["kind"] == "persona")
    warning = ("Stima delle voci, non numero certo dei presenti. Verificare i campioni: "
               "sovrapposizioni, eco, voci brevi e cambi di microfono possono confondere i gruppi.")
    if expected and found < expected:
        warning = (f"Trovate solo {found} voci distinte su {expected} indicate: prova un taglio più "
                   "corto, il filtro parlato Ampio o un gruppo minimo più basso. " + warning)
    progress(100)
    return {"version": 2, "engine": "ECAPA SpeechBrain / Silero · CPU",
            "threshold": threshold, "expected": expected, "options": settings,
            "groups": groups, "turns": merge_turns(windows), "names": {},
            "short_speech_seconds": round(short_seconds, 2),
            "quiet_seconds": round(quiet_seconds, 2), "echo_seconds": round(echo_seconds, 2),
            "excluded_intervals": len(exclusions), "warning": warning}
