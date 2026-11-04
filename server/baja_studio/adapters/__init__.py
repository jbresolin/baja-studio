"""Clients for external systems shared by several modules (OBS, ...).

Adapters speak a protocol and nothing else: which scene to cut to, or when,
is the business of the module using them ("adapters are shared, modules own
policy").
"""
