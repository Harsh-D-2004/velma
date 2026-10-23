"""Download the NLI model into models/nli/ (run at Docker build and once on the host)."""
import sys
import urllib.request
from pathlib import Path

# cross-encoder/nli-deberta-v3-base on Hugging Face, Apache-2.0; uint8-quantized ONNX export (runs on any x86 CPU)
REPO = "https://huggingface.co/cross-encoder/nli-deberta-v3-base/resolve/main/"
FILES = {"onnx/model_quint8_avx2.onnx": "model.onnx", "tokenizer.json": "tokenizer.json", "config.json": "config.json"}

dest = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent / "models" / "nli")
dest.mkdir(parents=True, exist_ok=True)
for src, name in FILES.items():
    if not (dest / name).exists():
        print("fetching", name, flush=True)
        urllib.request.urlretrieve(REPO + src, dest / (name + ".part"))
        (dest / (name + ".part")).rename(dest / name)
