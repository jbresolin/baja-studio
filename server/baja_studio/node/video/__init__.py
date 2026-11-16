"""The camera node's video pipeline (WP15 part B, ADR 0023).

One ffmpeg child process per camera: DirectShow MJPEG capture, a crop window
that moves live (ffmpeg's `crop` filter, commanded through its `zmq` filter),
a scale to 1920x1080, a hardware H.264 encode and MPEG-TS over SRT to the
studio's MediaMTX. The same process sends a small picture of the whole frame
about once a second, for the studio's preview.

What the rest of the node uses is in `pipeline.py` (`CameraPipeline`,
`PipelineSpec`, `PipelineStatus`), `capture_devices.py` (the cameras and their
modes) and `fake.py` (`FakeCameraPipeline`, for tests and the rig). The
laptop benchmark is `python -m baja_studio.node.video.bench`.
"""
