"""Command line: `python -m baja_studio.rig [--no-studio] [--reset] [--scenario NAME] [--seed N]`.

Brings up every fake and a studio pointed at them, prints what runs where,
plays the scenario, and stops everything on Ctrl-C.
"""

import argparse
import sys
import time
from pathlib import Path

import httpx2

from baja_studio.rig.fakes import STUDIO_ENVIRONMENT
from baja_studio.rig.layout import LOOPBACK, RigLayout, default_rig_directory
from baja_studio.rig.processes import ProcessStartError
from baja_studio.rig.scenarios import DEFAULT_SCENARIO, load_scenario, scenario_names
from baja_studio.rig.session import RigSession
from baja_studio.rig.studio import STUDIO_PORT, StudioApi, StudioSetupError, studio_process

# The fake OBS's timing seed unless `--seed` says otherwise; printed, so a run can be replayed.
DEFAULT_SEED = 2026
STUDIO_REQUEST_TIMEOUT_SECONDS = 15.0
WATCH_INTERVAL_SECONDS = 0.5


def say(message: str) -> None:
    """One line on the terminal, at once."""
    print(message, flush=True)


def parse_arguments(argv: list[str] | None) -> argparse.Namespace:
    """The rig's options."""
    parser = argparse.ArgumentParser(
        prog="python -m baja_studio.rig",
        description="Start every fake and a studio pointed at them (the rehearsal rig).",
    )
    parser.add_argument(
        "--studio",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=f"also start a studio on port {STUDIO_PORT} and import the bundle (default: on)",
    )
    parser.add_argument("--reset", action="store_true", help="wipe the rig's folder first")
    parser.add_argument(
        "--scenario", default=DEFAULT_SCENARIO, choices=scenario_names(), help="failures to play"
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=DEFAULT_SEED,
        help=f"the fake OBS's timing seed and the scenarios' (default {DEFAULT_SEED})",
    )
    parser.add_argument(
        "--fast-obs", action="store_true", help="the fake OBS answers at once (no timing)"
    )
    parser.add_argument("--port", type=int, default=STUDIO_PORT, help="the studio's port")
    parser.add_argument(
        "--rig-dir",
        type=Path,
        default=default_rig_directory(),
        help=f"the rig's folder (default {default_rig_directory()})",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run the rig until Ctrl-C; the exit code says whether it came up."""
    options = parse_arguments(argv)
    layout = RigLayout(options.rig_dir)
    if options.reset:
        say(f"wiping {layout.root}")
        layout.reset()
    layout.create()
    scenario = load_scenario(options.scenario)
    session = RigSession(layout, say, None if options.fast_obs else options.seed)
    try:
        session.make_clips()
        session.start_fakes()
        session.write_bundle()
        session.play(scenario, options.seed)
        if options.studio:
            start_studio(session, options.port)
        print_summary(session, options, scenario.description)
        while True:
            time.sleep(WATCH_INTERVAL_SECONDS)
    except KeyboardInterrupt:
        say("stopping ...")
    except (ProcessStartError, StudioSetupError) as problem:
        say(f"the rig could not start: {problem}")
        return 1
    finally:
        session.stop()
        say("stopped: no fake or studio left running")
    return 0


def start_studio(session: RigSession, port: int) -> None:
    """Start the studio, import the bundle, restore the audio defaults, set the return scene."""
    data = session.layout.studio_data
    session.studio = studio_process(data, session.log_directory, port)
    session.studio.start()
    say(f"studio starting on port {port} (data in {data}) ...")
    with httpx2.Client(
        base_url=f"http://{LOOPBACK}:{port}", timeout=STUDIO_REQUEST_TIMEOUT_SECONDS
    ) as client:
        api = StudioApi(client)
        api.wait_until_up()
        results = api.import_bundle(session.layout.bundle.read_bytes())
        applied = [item["section"] for item in results if item["result"] == "applied"]
        say(f"settings imported: {', '.join(applied) or 'nothing changed'}")
        if not api.wait_for_module("audio"):
            say("the fake XR18 is not answering the studio yet; restoring defaults anyway")
        restored = api.restore_audio_defaults()
        say(f"audio defaults restored: {', '.join(restored) or 'already the defaults'}")
        api.set_return_scene()


def print_summary(session: RigSession, options: argparse.Namespace, description: str) -> None:
    """What runs where, and what to open."""
    say("")
    say("running:")
    for name, fake in session.fakes.items():
        say(f"  {name:<13} {fake.describe}")
    say("  m-vave        the MIDI child's fake backend (in the studio's environment)")
    say(f"  logs          {session.log_directory}")
    say(f"  bundle        {session.layout.bundle}")
    if session.fakes["obs"].process.running and not options.fast_obs:
        say(f"fake OBS timing seed: {options.seed} (replay with --seed {options.seed})")
    say(f"scenario {options.scenario}: {description}")
    if options.studio:
        say(f"Open http://{LOOPBACK}:{options.port}")
    else:
        variables = " ".join(f"{key}={value}" for key, value in STUDIO_ENVIRONMENT.items())
        say("no studio started: import the bundle above on Settings → Export / import,")
        say(f"and start the studio with {variables} for the fake M-VAVE.")
    say("Ctrl-C stops everything.")


if __name__ == "__main__":
    sys.exit(main())
