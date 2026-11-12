"""The rehearsal rig: a whole fake studio, and a studio pointed at it, from one command.

    python -m baja_studio.rig [--no-studio] [--reset] [--scenario NAME] [--seed N]

Starts every runnable fake on free local ports (127.0.0.1 only): OBS, the
XR18, two VISCA cameras, two camera-laptop agents and the orchestrator; the
M-VAVE is the MIDI child's fake backend. It makes a demo clip library the
first time, writes a settings bundle in the export format (ADR 0008) that
points every module at the fakes, and starts a studio on port 8889 with its
own data folder, which imports the bundle through the import API. A
scenario (`scenarios/*.json`) then plays failures against the fakes on a
timeline. Ctrl-C stops everything. Decisions: docs/adr/0016-rehearsal-rig.md.

The rig only launches the fakes and talks to them through their own control
surfaces (CLI flags, the XR18 console, the agent's `/_fake` switches); it
never changes how a fake behaves.
"""
