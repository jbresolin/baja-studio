"""Command-line entry point: `python -m baja_studio [--port 8888] [--data-dir PATH]`."""

import argparse
from pathlib import Path

import uvicorn

from baja_studio.main import DEFAULT_PORT, create_app, default_data_directory


def main() -> None:
    """Parse arguments and serve the app until Ctrl+C or the service manager stops it."""
    parser = argparse.ArgumentParser(prog="baja_studio", description="Run the Baja Studio service.")
    parser.add_argument(
        "--host", default="0.0.0.0", help="address to listen on (default: all interfaces)"
    )
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help=f"port to listen on (default: {DEFAULT_PORT})",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        help=f"folder for the database and logs (default: {default_data_directory()})",
    )
    arguments = parser.parse_args()
    app = create_app(data_directory=arguments.data_dir)
    # log_config=None keeps uvicorn from replacing our logging setup.
    uvicorn.run(app, host=arguments.host, port=arguments.port, log_config=None)


if __name__ == "__main__":
    main()
