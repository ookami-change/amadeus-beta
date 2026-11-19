#!/usr/bin/env bash
# Download the CC0 VRM used by the M0 render check.
# Source and license are recorded in apps/web/public/models/SOURCE.md.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
dest="$root/public/models/avatar.vrm"
mkdir -p "$(dirname "$dest")"
if [[ -f "$dest" ]]; then
  echo "already present: $dest"
  exit 0
fi

# Vita, the VRoid Studio beta sample (AvatarSample_3), redistributed as VRM 0.x
# in the community sample mirror. pixiv waived copyright (CC0). three-vrm loads it.
# A VRM 1.0 upload of the same sample lives on VRoid Hub but has no stable
# anonymous file URL; see SOURCE.md.
url="https://raw.githubusercontent.com/madjin/vrm-samples/master/vroid/beta/Vita.vrm"
echo "downloading $url"
curl -fL --retry 3 --retry-delay 2 -o "$dest.partial" "$url"
mv "$dest.partial" "$dest"
echo "wrote $dest ($(wc -c < "$dest") bytes)"
