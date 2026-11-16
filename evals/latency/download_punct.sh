#!/usr/bin/env bash
# CT-Transformer punctuation model used as the semantic-endpoint candidate.
# Not vendored: the archive is about 266MB.
set -euo pipefail
root="$(cd "$(dirname "$0")" && pwd)"
dest="$root/models/punct/model.onnx"
if [[ -f "$dest" ]]; then
  echo "already present: $dest"
  exit 0
fi
url="https://github.com/k2-fsa/sherpa-onnx/releases/download/punctuation-models/sherpa-onnx-punct-ct-transformer-zh-en-vocab272727-2024-04-12.tar.bz2"
tmp="$(mktemp -d)"
curl -fL --retry 3 -o "$tmp/punct.tar.bz2" "$url"
tar -xjf "$tmp/punct.tar.bz2" -C "$tmp"
found="$(find "$tmp" -name model.onnx | head -n 1)"
mkdir -p "$(dirname "$dest")"
cp "$found" "$dest"
rm -rf "$tmp"
echo "wrote $dest"
