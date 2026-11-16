"""Baja Studio's tray icon (WP7b, ADR 0010).

A separate process, started at user logon by a `Run` registry entry the
installer adds. It is only a client of the service: it polls `/api/health`
every 5 s, shows a green, amber or red dot on the Baja Studio icon, and
offers shortcuts (open the page, readiness, restart the services, logs,
diagnostics bundle). It keeps no state, and nothing breaks when it is closed.

    pythonw -m baja_studio_tray                     the tray
    pythonw -m baja_studio_tray --exit-after-first-poll   CI smoke test
    python -m baja_studio_tray.restart              (elevated) restart both services

The same package is the camera node's tray on a trackside laptop (ADR 0023),
with a camera icon, one menu line per camera, and its own restart:

    pythonw -m baja_studio_tray --node
    python -m baja_studio_tray.restart --node       (elevated) restart the node

Everything but `app.py` is plain Python, testable without a desktop.
"""
