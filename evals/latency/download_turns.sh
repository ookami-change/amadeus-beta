#!/usr/bin/env bash
# Models for the turn-detection comparison. Weights stay out of git.
set -euo pipefail
root="$(cd "$(dirname "$0")" && pwd)"
models="$root/models"
mkdir -p "$models/paraformer" "$models/livekit-turn"

if [[ ! -f "$models/silero_vad.onnx" ]]; then
  curl -fL --retry 3 -o "$models/silero_vad.onnx" \
    "https://github.com/snakers4/silero-vad/raw/v5.1.2/src/silero_vad/data/silero_vad.onnx"
fi

if [[ ! -f "$models/smart-turn-v3.2-cpu.onnx" ]]; then
  curl -fL --retry 3 -o "$models/smart-turn-v3.2-cpu.onnx" \
    "https://huggingface.co/pipecat-ai/smart-turn-v3/resolve/main/smart-turn-v3.2-cpu.onnx"
fi

uv run --directory "$root/.." --group turns python - "$models/paraformer" << 'PY'
import shutil
import sys
from pathlib import Path
from huggingface_hub import hf_hub_download

dest = Path(sys.argv[1])
repo = "csukuangfj/sherpa-onnx-streaming-paraformer-bilingual-zh-en"
for name in ("encoder.int8.onnx", "decoder.int8.onnx", "tokens.txt"):
    path = Path(hf_hub_download(repo, name))
    target = dest / name
    if not target.exists():
        shutil.copy(path, target)
print("paraformer", sorted(p.name for p in dest.iterdir()))
PY

uv run --directory "$root/.." --group turns python - "$models/livekit-turn" << 'PY'
import shutil
import sys
from pathlib import Path
from huggingface_hub import hf_hub_download

dest = Path(sys.argv[1])
revision = "v0.4.1-intl"
repo = "livekit/turn-detector"
files = [
    "onnx/model_q8.onnx",
    "languages.json",
    "tokenizer.json",
    "tokenizer_config.json",
    "config.json",
    "special_tokens_map.json",
]
for name in files:
    path = Path(hf_hub_download(repo, name, revision=revision))
    target = dest / path.name
    if not target.exists():
        shutil.copy(path, target)
print("livekit files", sorted(p.name for p in dest.iterdir()))
PY
echo "turn models ready"
