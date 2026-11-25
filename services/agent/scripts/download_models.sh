#!/usr/bin/env bash
# Silero VAD v5.1.2, the same file M0 measured with. Weights stay out of git.
set -euo pipefail
models="$(cd "$(dirname "$0")/.." && pwd)/models"
mkdir -p "$models"
if [[ ! -f "$models/silero_vad.onnx" ]]; then
  curl -fL --retry 3 -o "$models/silero_vad.onnx" \
    "https://github.com/snakers4/silero-vad/raw/v5.1.2/src/silero_vad/data/silero_vad.onnx"
fi
ls -l "$models"
