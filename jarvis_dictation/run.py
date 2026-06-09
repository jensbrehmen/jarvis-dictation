from __future__ import annotations

import argparse
import logging
import signal
import subprocess
import sys
import time

from jarvis_dictation.model_server import get_server_info, send_shutdown
from jarvis_dictation.models import DEFAULT_MODEL_PRESET, MODEL_PRESETS, resolve_model_name


def stop_process(process: subprocess.Popen, label: str) -> int:
    if process.poll() is not None:
        return int(process.returncode)

    logging.info("Stopping %s", label)
    process.send_signal(signal.SIGINT)
    try:
        return int(process.wait(timeout=5))
    except subprocess.TimeoutExpired:
        process.terminate()
        try:
            return int(process.wait(timeout=5))
        except subprocess.TimeoutExpired:
            process.kill()
            return int(process.wait())


def wait_for_server(timeout_secs: float) -> bool:
    deadline = time.monotonic() + timeout_secs
    while time.monotonic() < deadline:
        if get_server_info() is not None:
            return True
        time.sleep(0.5)
    return False


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Start Jarvis dictation server and app together.")
    parser.add_argument("--model-preset", choices=sorted(MODEL_PRESETS), default=DEFAULT_MODEL_PRESET)
    parser.add_argument("--model-name", default=None, help="Override the preset with a Hugging Face model id or local path.")
    parser.add_argument("--server-timeout-secs", type=float, default=120.0)
    parser.add_argument("--keep-server", action="store_true", help="Leave the model server running after app exit.")
    parser.add_argument("--debug", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    started_server = False
    server_process: subprocess.Popen | None = None
    requested_model_name = resolve_model_name(args.model_preset, args.model_name)

    server_info = get_server_info()
    if server_info is not None:
        running_model_name = server_info.get("model_name")
        if running_model_name != requested_model_name:
            raise SystemExit(
                "Model server is already running with "
                f"`{running_model_name}`. Stop it first with `jarvis-dictation-server --stop`, "
                f"then restart with `{requested_model_name}`."
            )
        logging.info("Model server is already running with %s", running_model_name)
    else:
        logging.info("Starting model server")
        server_cmd = [
            sys.executable,
            "-m",
            "jarvis_dictation.model_server",
            "--model-preset",
            args.model_preset,
        ]
        if args.model_name:
            server_cmd.extend(["--model-name", args.model_name])
        server_process = subprocess.Popen(server_cmd)
        started_server = True
        try:
            server_ready = wait_for_server(args.server_timeout_secs)
        except KeyboardInterrupt:
            stop_process(server_process, "model server")
            raise SystemExit(130) from None
        if not server_ready:
            stop_process(server_process, "model server")
            raise SystemExit("Timed out waiting for model server to become ready")

    app_cmd = [sys.executable, "-m", "jarvis_dictation.app"]
    if args.debug:
        app_cmd.append("--debug")

    logging.info("Starting dictation app")
    app_process = subprocess.Popen(app_cmd)
    try:
        exit_code = app_process.wait()
    except KeyboardInterrupt:
        exit_code = stop_process(app_process, "dictation app")
    finally:
        if started_server and not args.keep_server:
            logging.info("Stopping model server")
            if not send_shutdown():
                if server_process is not None and server_process.poll() is None:
                    stop_process(server_process, "model server")

    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
