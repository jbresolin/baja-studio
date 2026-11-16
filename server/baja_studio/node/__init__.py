"""The camera node: a slim agent that owns a trackside laptop's USB cameras (WP15, ADR 0023).

    python -m baja_studio.node [--port 8891] [--data-dir PATH]

It serves the agent contract (docs/agent-api.md) for cameras of
`kind=crop`, whose video is done either by the node's own pipeline
(`video/`) or by OBS on the same laptop (the studio's `obs_crop` driver,
reused), and a small settings and status page of its own.
"""
