#!/usr/bin/env bash
# Assembles the Linux camera node's package (ADR 0024):
#
#   installer/linux-node/build-package.sh <version> <ffmpeg tarball> <output.tar.gz>
#
# Run from the repository's root after `npm run build` (frontend/dist-node) and after
# stamping the version, on an arm64 or any Linux machine with Python 3.12: the
# wheelhouse is downloaded for arm64 Linux whatever machine runs this. The package:
#
#   BajaCameraNode-<version>-linux-arm64/
#     install.sh, baja-camera-node.service, 99-baja-camera-node.rules, VERSION
#     requirements-node.txt   the node's dependencies (release_tools linux-node-requirements)
#     wheels/                 the baja_studio wheel and every dependency, for arm64 Python 3.12
#     frontend/               the node's page (frontend/dist-node)
#     ffmpeg-rockchip-node-linux-arm64.tar.xz   from installer/ffmpeg-rockchip/build.sh
set -euo pipefail

version=${1:?usage: build-package.sh <version> <ffmpeg tarball> <output.tar.gz>}
ffmpeg_tarball=${2:?ffmpeg tarball}
output=${3:?output}
root=$(pwd)
name="BajaCameraNode-$version-linux-arm64"
stage="$root/dist/linux-node/$name"

[ -f frontend/dist-node/index.html ] || { echo "build the frontend first (npm run build)"; exit 1; }
rm -rf "$stage"
mkdir -p "$stage/wheels"

python -m pip wheel --quiet --no-deps --wheel-dir "$stage/wheels" ./server
(cd server && python -m baja_studio.release_tools linux-node-requirements) \
  > "$stage/requirements-node.txt"
# Wheels for the board, not for this machine; binary only (nothing is compiled on the board).
python -m pip download --quiet --dest "$stage/wheels" --only-binary=:all: \
  --implementation cp --python-version 3.12 --abi cp312 --abi abi3 --abi none \
  --platform manylinux2014_aarch64 --platform manylinux_2_28_aarch64 \
  --platform manylinux_2_17_aarch64 \
  -r "$stage/requirements-node.txt"

cp -r frontend/dist-node "$stage/frontend"
cp installer/linux-node/install.sh installer/linux-node/baja-camera-node.service \
  installer/linux-node/99-baja-camera-node.rules "$stage/"
cp "$ffmpeg_tarball" "$stage/ffmpeg-rockchip-node-linux-arm64.tar.xz"
echo "$version" > "$stage/VERSION"
(cd server && python -m baja_studio.release_tools wheelhouse-manifest \
  --wheelhouse "$stage/wheels" --output "$stage/wheelhouse-manifest.json")

tar -C "$(dirname "$stage")" -czf "$output" "$name"
sha256sum "$output"
