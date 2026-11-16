"""A tiny program that behaves like ffmpeg on its pipes, for `test_ffmpeg_process.py`.

Run as `python fake_ffmpeg_child.py <mode> [arguments]`:

- `talk <exit code>`: two log lines and a progress block on stderr, some
  bytes on stdout, then exit with the code;
- `flood <bytes>`: that many bytes on stdout *and* on stderr, then exit 0
  (more than a pipe holds: it only finishes if both pipes are read);
- `sleep`: wait forever (until killed), like an ffmpeg streaming;
- `ignore-term`: the same, but ignoring a polite stop (SIGTERM), like an
  ffmpeg stuck in a driver call.
"""

import signal
import sys
import time


def main() -> None:
    mode = sys.argv[1]
    if mode == "talk":
        sys.stderr.write("[dshow @ 0x1] first warning\nframe=30\nprogress=continue\n")
        sys.stderr.write("[srt @ 0x2] Connection to srt://x failed\n")
        sys.stderr.flush()
        sys.stdout.buffer.write(b"snapshot-bytes")
        sys.stdout.flush()
        sys.exit(int(sys.argv[2]))
    if mode == "flood":
        size = int(sys.argv[2])
        sys.stdout.buffer.write(b"s" * size)
        sys.stdout.flush()
        sys.stderr.write(("e" * 99 + "\n") * (size // 100))
        sys.stderr.flush()
        sys.exit(0)
    if mode == "ignore-term" and sys.platform != "win32":
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
    sys.stderr.write("ready\n")
    sys.stderr.flush()
    while True:
        time.sleep(1)


if __name__ == "__main__":
    main()
