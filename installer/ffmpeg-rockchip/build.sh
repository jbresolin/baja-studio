#!/usr/bin/env bash
# Builds the camera node's ffmpeg for Rockchip boards (the NanoPi R3S, ADR 0024).
#
#   installer/ffmpeg-rockchip/build.sh <output.tar.xz>
#
# Run as root in a fresh arm64 `ubuntu:24.04` container (the release workflow
# does: .github/workflows/node-arm64.yml). It builds, from the commits in
# pin.json:
#
#   1. Rockchip MPP (the video codec's library: MJPEG decode, H.264 encode),
#   2. librga (the 2D engine's library: crop and scale),
#   3. ffmpeg-rockchip with the node's patch (the RGA crop can move while
#      running, which the window needs), plus zmq, SRT and x264,
#
# all installed under /opt/baja-camera-node/ffmpeg (bin/ and lib/, ffmpeg's
# RUNPATH pointing at ../lib), checks the result, and packs that folder.
# The node's Linux installer unpacks it to the same place.
set -euo pipefail

output=${1:?usage: build.sh <output.tar.xz>}
here=$(cd "$(dirname "$0")" && pwd)
pin="$here/pin.json"
prefix=/opt/baja-camera-node/ffmpeg
work=$(mktemp -d)
jobs=$(nproc)

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends \
  build-essential cmake meson ninja-build pkg-config git ca-certificates jq xz-utils \
  nasm patchelf libdrm-dev libsrt-openssl-dev libzmq3-dev libx264-dev

pinned() { jq -r "$1" "$pin"; }

# Fetch one commit of a repository, without its history.
fetch() {
  local repository=$1 commit=$2 folder=$3
  git init -q "$folder"
  git -C "$folder" remote add origin "$repository"
  git -C "$folder" fetch -q --depth 1 origin "$commit"
  git -C "$folder" checkout -q FETCH_HEAD
}

echo "== MPP"
fetch "$(pinned .mpp.repository)" "$(pinned .mpp.commit)" "$work/mpp"
cmake -S "$work/mpp" -B "$work/mpp/build" -G Ninja \
  -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX="$prefix" -DCMAKE_INSTALL_LIBDIR=lib \
  -DBUILD_TEST=OFF
ninja -C "$work/mpp/build" -j "$jobs" install

echo "== librga"
fetch "$(pinned .rga.repository)" "$(pinned .rga.commit)" "$work/rga"
meson setup "$work/rga/build" "$work/rga" \
  --prefix="$prefix" --libdir=lib --buildtype=release \
  -Dcpp_args=-fpermissive -Dlibdrm=false -Dlibrga_demo=false
ninja -C "$work/rga/build" -j "$jobs" install

echo "== ffmpeg-rockchip"
fetch "$(pinned .ffmpeg.repository)" "$(pinned .ffmpeg.commit)" "$work/ffmpeg"
for patch in $(pinned '.ffmpeg.patches[]'); do
  git -C "$work/ffmpeg" apply --verbose "$here/$patch"
done
(
  cd "$work/ffmpeg"
  PKG_CONFIG_PATH="$prefix/lib/pkgconfig" ./configure \
    --prefix="$prefix" \
    --enable-gpl --enable-version3 \
    --enable-libdrm --enable-rkmpp --enable-rkrga \
    --enable-libzmq --enable-libsrt --enable-libx264 \
    --disable-doc --disable-ffplay --disable-debug
  make -j "$jobs"
  make install
)
# ffmpeg finds MPP and librga next to it, wherever the folder is unpacked.
patchelf --set-rpath '$ORIGIN/../lib' "$prefix/bin/ffmpeg" "$prefix/bin/ffprobe"
rm -rf "$prefix/include" "$prefix/share" "$prefix/lib/pkgconfig" "$prefix"/lib/*.a
mkdir -p "$prefix/licenses"
cp "$work/ffmpeg/COPYING.GPLv3" "$prefix/licenses/ffmpeg-COPYING.GPLv3"
cp -r "$work/mpp/LICENSES" "$prefix/licenses/mpp" 2>/dev/null || true
cp "$work/rga/COPYING" "$prefix/licenses/librga-COPYING"
cp "$pin" "$prefix/pin.json"

echo "== checks"
ffmpeg="$prefix/bin/ffmpeg"
"$ffmpeg" -hide_banner -version | head -1
# The features the node uses; none of these needs the board's hardware to be listed.
"$ffmpeg" -hide_banner -decoders | grep -q ' mjpeg_rkmpp '
"$ffmpeg" -hide_banner -encoders | grep -q ' h264_rkmpp '
"$ffmpeg" -hide_banner -encoders | grep -q ' libx264 '
"$ffmpeg" -hide_banner -filters | grep -q ' vpp_rkrga '
"$ffmpeg" -hide_banner -filters | grep -q ' zmq '
"$ffmpeg" -hide_banner -protocols | grep -qw 'srt'
# The patch: cw/cx/cy can change while running (a `T` in their flags).
"$ffmpeg" -hide_banner -h filter=vpp_rkrga | grep -E '^\s+cw\s' | grep -q 'T'
"$ffmpeg" -hide_banner -h filter=vpp_rkrga | grep -E '^\s+cx\s' | grep -q 'T'

tar -C "$(dirname "$prefix")" -cJf "$output" "$(basename "$prefix")"
sha256sum "$output"
