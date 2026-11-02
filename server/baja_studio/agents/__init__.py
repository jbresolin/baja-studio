"""Studio side of the camera-laptop agent API (contract in docs/agent-api.md).

`contract` holds the wire format, `client` talks to one agent, `scan` finds
agents on a subnet, and `registry` keeps a live link to every configured
agent. The `agents` module (modules/agents) puts this on the web page.
"""
