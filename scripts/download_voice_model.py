"""Download the public ECAPA checkpoint; never open recordings or app configuration."""
import hashlib
import json
from pathlib import Path
from urllib.request import urlopen


def main():
    directory = Path(__file__).resolve().parents[1] / "models" / "ecapa"
    directory.mkdir(parents=True, exist_ok=True)
    repo = "speechbrain/spkrec-ecapa-voxceleb"
    with urlopen(f"https://huggingface.co/api/models/{repo}?blobs=true", timeout=60) as response:
        metadata = json.load(response)
    revision = metadata["sha"]
    info = next(item for item in metadata["siblings"] if item["rfilename"] == "embedding_model.ckpt")
    expected = info["lfs"]["sha256"]
    target = directory / "embedding_model.ckpt"
    if not target.exists() or hashlib.sha256(target.read_bytes()).hexdigest() != expected:
        temp = directory / "embedding_model.download"
        digest = hashlib.sha256()
        with urlopen(f"https://huggingface.co/{repo}/resolve/{revision}/embedding_model.ckpt", timeout=120) as response, temp.open("wb") as output:
            while data := response.read(1024 * 1024):
                digest.update(data)
                output.write(data)
        if digest.hexdigest() != expected:
            raise RuntimeError("Checksum del modello non valido. Il checkpoint non è stato installato.")
        temp.replace(target)
    (directory / "source.json").write_text(json.dumps({"repo": repo, "revision": revision, "sha256": expected}, indent=2), encoding="utf-8")
    print("Checkpoint ufficiale ECAPA installato e checksum verificato.")


if __name__ == "__main__":
    main()
