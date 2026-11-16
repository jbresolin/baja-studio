"""The camera node: a slim service that owns a trackside laptop's USB cameras (WP15, ADR 0023).

`node/video/` is its video pipeline: one ffmpeg per camera that captures,
crops live, encodes and sends the picture to the studio's MediaMTX over SRT.
"""
