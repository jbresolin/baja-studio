#!/usr/bin/env bash
# Installs (or upgrades, or removes) the Baja camera node on a Linux board: the NanoPi R3S
# running FriendlyElec's Ubuntu 24.04 (Noble) Core image with its 6.1 vendor kernel (ADR 0024).
#
#   sudo ./install.sh              install, or upgrade in place (settings are kept)
#   sudo ./install.sh --uninstall  remove the program and the service, keep the settings
#   sudo ./install.sh --purge      remove everything, settings and logs included
#
# Run it from the unpacked BajaCameraNode-<version>-linux-arm64 folder. It needs the
# internet only for the few Ubuntu packages below, and only the first time; the Python
# packages come from the folder's own wheelhouse. What it does, in order:
#
#   1. checks the board (arm64, Python 3.12) and installs the Ubuntu packages it lacks;
#   2. creates the service user `baja-node` (groups video and render);
#   3. stops the service if it runs (an upgrade), then puts the program in
#      /opt/baja-camera-node: a Python environment with the node, its page, and the
#      node's ffmpeg (ffmpeg-rockchip with the live-crop patch);
#   4. installs the udev rule that lets the service open the video hardware;
#   5. installs and starts the `baja-camera-node` systemd service (port 8891) and, if
#      ufw is on, opens 8891/tcp;
#   6. waits for the node to answer and prints its page's address.
set -euo pipefail

PROGRAM=/opt/baja-camera-node
DATA=/var/lib/baja-camera-node
SERVICE=baja-camera-node
SERVICE_USER=baja-node
PORT=8891
UNIT_FILE=/etc/systemd/system/$SERVICE.service
RULES_FILE=/etc/udev/rules.d/99-baja-camera-node.rules
UBUNTU_PACKAGES=(python3.12 python3.12-venv libdrm2 libsrt1.5-openssl libzmq5 libx264-164 curl)
HERE=$(cd "$(dirname "$0")" && pwd)

say() { printf '\n== %s\n' "$*"; }
fail() { printf '\nERROR: %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || fail "run it as root: sudo $0 $*"

remove_program() {
  systemctl disable --now "$SERVICE" 2>/dev/null || true
  rm -f "$UNIT_FILE" "$RULES_FILE"
  systemctl daemon-reload
  udevadm control --reload-rules 2>/dev/null || true
  rm -rf "$PROGRAM"
}

case "${1:-}" in
  --uninstall)
    remove_program
    echo "Removed. Settings and logs are kept in $DATA (--purge removes them)."
    exit 0
    ;;
  --purge)
    remove_program
    rm -rf "$DATA"
    userdel "$SERVICE_USER" 2>/dev/null || true
    echo "Removed, settings and logs included."
    exit 0
    ;;
  "") ;;
  *) fail "unknown option $1 (use --uninstall or --purge)" ;;
esac

say "Checking the board"
[ "$(uname -m)" = "aarch64" ] || fail "this package is for 64-bit ARM boards (this is $(uname -m))"
missing=()
for package in "${UBUNTU_PACKAGES[@]}"; do
  dpkg-query -W -f='${Status}' "$package" 2>/dev/null | grep -q "install ok installed" ||
    missing+=("$package")
done
if [ ${#missing[@]} -gt 0 ]; then
  echo "Installing Ubuntu packages: ${missing[*]}"
  apt-get update
  apt-get install -y --no-install-recommends "${missing[@]}"
fi
for node in /dev/mpp_service /dev/rga; do
  if [ ! -e "$node" ]; then
    echo "WARNING: $node is missing: this kernel has no Rockchip video hardware driver."
    echo "         Use FriendlyElec's image (vendor kernel 6.1), not a mainline one;"
    echo "         without it the node can only encode on the CPU, too slow for 4K."
  fi
done

say "Service user"
if ! id "$SERVICE_USER" >/dev/null 2>&1; then
  useradd --system --no-create-home --home-dir "$DATA" --shell /usr/sbin/nologin \
    --user-group "$SERVICE_USER"
fi
usermod -a -G video "$SERVICE_USER"
getent group render >/dev/null && usermod -a -G render "$SERVICE_USER"

say "Program ($PROGRAM)"
systemctl stop "$SERVICE" 2>/dev/null || true
version=$(cat "$HERE/VERSION")
mkdir -p "$PROGRAM"
# A fresh environment every time: an upgrade never mixes old and new packages.
rm -rf "$PROGRAM/venv.new"
python3.12 -m venv "$PROGRAM/venv.new"
"$PROGRAM/venv.new/bin/python" -m pip install --quiet --no-index --find-links "$HERE/wheels" \
  -r "$HERE/requirements-node.txt"
"$PROGRAM/venv.new/bin/python" -m pip install --quiet --no-index --no-deps \
  "$HERE"/wheels/baja_studio-*.whl
# The service runs `venv/bin/python -m ...`, which works from the moved folder (only the
# venv's own helper scripts, never used here, name the folder they were made in).
rm -rf "$PROGRAM/venv" && mv "$PROGRAM/venv.new" "$PROGRAM/venv"
rm -rf "$PROGRAM/frontend" && cp -r "$HERE/frontend" "$PROGRAM/frontend"
rm -rf "$PROGRAM/ffmpeg" && tar -C "$PROGRAM" -xJf "$HERE/ffmpeg-rockchip-node-linux-arm64.tar.xz"
cp "$HERE/VERSION" "$PROGRAM/VERSION"
cp "$HERE/install.sh" "$PROGRAM/install.sh"
"$PROGRAM/ffmpeg/bin/ffmpeg" -hide_banner -version | head -1 ||
  fail "the node's ffmpeg does not run (a missing Ubuntu library?)"

say "Video hardware permissions"
cp "$HERE/99-baja-camera-node.rules" "$RULES_FILE"
udevadm control --reload-rules
udevadm trigger --subsystem-match=video4linux --subsystem-match=dma_heap 2>/dev/null || true
udevadm trigger --sysname-match=mpp_service --sysname-match=rga 2>/dev/null || true

say "Service ($SERVICE, port $PORT)"
cp "$HERE/$SERVICE.service" "$UNIT_FILE"
systemctl daemon-reload
systemctl enable --now "$SERVICE"
if command -v ufw >/dev/null && ufw status | grep -q "Status: active"; then
  ufw allow "$PORT/tcp" comment "Baja camera node"
fi

say "Waiting for the node to answer"
for _ in $(seq 1 30); do
  if curl -fsS "http://127.0.0.1:$PORT/agent/info" >/dev/null 2>&1; then
    echo "Baja camera node $version is running."
    for address in $(hostname -I); do
      case "$address" in *:*) ;; *) echo "  its page: http://$address:$PORT/" ;; esac
    done
    echo "  logs: journalctl -u $SERVICE -f   (and $DATA/logs)"
    exit 0
  fi
  sleep 1
done
journalctl -u "$SERVICE" -n 40 --no-pager || true
fail "the node did not answer on port $PORT within 30 s (its log is above)"
