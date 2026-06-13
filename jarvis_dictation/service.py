from __future__ import annotations

import argparse
import logging
import os
import plistlib
import subprocess
import sys
from pathlib import Path

from jarvis_dictation.model_server import get_server_info, send_shutdown
from jarvis_dictation.models import DEFAULT_MODEL_PRESET, MODEL_PRESETS
from jarvis_dictation.prepare import APP_SUPPORT_DIR


LABEL = "com.jarvis.dictation.server"
LAUNCH_AGENTS_DIR = Path.home() / "Library" / "LaunchAgents"
PLIST_PATH = LAUNCH_AGENTS_DIR / f"{LABEL}.plist"
LOG_PATH = APP_SUPPORT_DIR / "server.launchd.log"
ERROR_LOG_PATH = APP_SUPPORT_DIR / "server.launchd.err.log"


def launchctl_domain() -> str:
    return f"gui/{os.getuid()}"


def build_plist(
    model_preset: str = DEFAULT_MODEL_PRESET,
    model_name: str | None = None,
    model_engine: str | None = None,
) -> dict:
    APP_SUPPORT_DIR.mkdir(parents=True, exist_ok=True)
    program_arguments = [
        sys.executable,
        "-m",
        "jarvis_dictation.model_server",
        "--model-preset",
        model_preset,
    ]
    if model_name:
        program_arguments.extend(["--model-name", model_name])
    if model_engine:
        program_arguments.extend(["--model-engine", model_engine])
    return {
        "Label": LABEL,
        "ProgramArguments": program_arguments,
        "RunAtLoad": True,
        "KeepAlive": False,
        "StandardOutPath": str(LOG_PATH),
        "StandardErrorPath": str(ERROR_LOG_PATH),
        "WorkingDirectory": str(Path.cwd()),
        "EnvironmentVariables": {
            "PATH": os.environ.get("PATH", ""),
        },
    }


def write_plist(
    model_preset: str = DEFAULT_MODEL_PRESET,
    model_name: str | None = None,
    model_engine: str | None = None,
) -> None:
    LAUNCH_AGENTS_DIR.mkdir(parents=True, exist_ok=True)
    with PLIST_PATH.open("wb") as file:
        plistlib.dump(
            build_plist(model_preset=model_preset, model_name=model_name, model_engine=model_engine),
            file,
        )


def run_launchctl(args: list[str], check: bool = True) -> subprocess.CompletedProcess:
    logging.debug("Running launchctl %s", " ".join(args))
    return subprocess.run(["launchctl", *args], check=check, text=True, capture_output=True)


def install(model_preset: str, model_name: str | None, model_engine: str | None = None) -> None:
    write_plist(model_preset=model_preset, model_name=model_name, model_engine=model_engine)
    run_launchctl(["bootout", launchctl_domain(), str(PLIST_PATH)], check=False)
    run_launchctl(["bootstrap", launchctl_domain(), str(PLIST_PATH)])
    logging.info("Installed and started %s", LABEL)
    logging.info("Logs: %s", LOG_PATH)


def uninstall() -> None:
    run_launchctl(["bootout", launchctl_domain(), str(PLIST_PATH)], check=False)
    try:
        PLIST_PATH.unlink()
    except FileNotFoundError:
        pass
    send_shutdown()
    logging.info("Uninstalled %s", LABEL)


def start(model_preset: str, model_name: str | None, model_engine: str | None = None) -> None:
    if not PLIST_PATH.exists():
        write_plist(model_preset=model_preset, model_name=model_name, model_engine=model_engine)
        run_launchctl(["bootstrap", launchctl_domain(), str(PLIST_PATH)], check=False)
    run_launchctl(["kickstart", "-k", f"{launchctl_domain()}/{LABEL}"], check=False)
    logging.info("Requested server start")


def stop() -> None:
    if send_shutdown():
        logging.info("Requested graceful server stop")
        return
    run_launchctl(["bootout", launchctl_domain(), str(PLIST_PATH)], check=False)
    logging.info("Requested launchd server stop")


def status() -> None:
    server_info = get_server_info()
    if server_info is not None:
        print(f"Model server is running: {server_info.get('model_name', 'unknown model')}")
    else:
        print("Model server is not running")
    print(f"LaunchAgent: {PLIST_PATH if PLIST_PATH.exists() else 'not installed'}")
    print(f"Log: {LOG_PATH}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Manage the Jarvis dictation model server LaunchAgent.")
    parser.add_argument("command", choices=["install", "uninstall", "start", "stop", "status"])
    parser.add_argument("--model-preset", choices=sorted(MODEL_PRESETS), default=DEFAULT_MODEL_PRESET)
    parser.add_argument("--model-name", default=None, help="Override the preset with a Hugging Face model id or local path.")
    parser.add_argument("--model-engine", choices=["mlx-audio", "parakeet-mlx"], default=None)
    parser.add_argument("--debug", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    if args.command == "install":
        install(model_preset=args.model_preset, model_name=args.model_name, model_engine=args.model_engine)
    elif args.command == "uninstall":
        uninstall()
    elif args.command == "start":
        start(model_preset=args.model_preset, model_name=args.model_name, model_engine=args.model_engine)
    elif args.command == "stop":
        stop()
    elif args.command == "status":
        status()


if __name__ == "__main__":
    main()
