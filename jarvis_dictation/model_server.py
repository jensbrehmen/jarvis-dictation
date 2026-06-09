from __future__ import annotations

import argparse
import logging
import os
import signal
import socket
import threading
from multiprocessing.connection import Client, Listener
from pathlib import Path
from typing import Any

import numpy as np

from jarvis_dictation.app import (
    MLXParakeetTranscriber,
)
from jarvis_dictation.models import DEFAULT_MODEL_PRESET, MODEL_PRESETS, resolve_model_name
from jarvis_dictation.prepare import APP_SUPPORT_DIR


SOCKET_PATH = APP_SUPPORT_DIR / "model-server.sock"


class ModelServerError(RuntimeError):
    pass


class RemoteParakeetTranscriber:
    def __init__(self, socket_path: Path = SOCKET_PATH, timeout_secs: float = 1.0) -> None:
        self.socket_path = socket_path
        self.timeout_secs = timeout_secs
        self.conn = self._connect()
        response = self._request({"cmd": "ping"})
        if not response.get("ready"):
            raise ModelServerError("Model server is not ready")
        self.info = response

    def reset(self) -> None:
        self._request({"cmd": "reset"})

    def accept_audio(self, samples: np.ndarray, final: bool = False) -> str:
        response = self._request(
            {
                "cmd": "audio",
                "samples": samples.astype(np.float32, copy=False),
                "final": bool(final),
            }
        )
        return str(response.get("text", ""))

    def close(self) -> None:
        try:
            self.conn.close()
        except Exception:
            pass

    def _connect(self):
        if not self.socket_path.exists():
            raise ModelServerError(f"Model server is not running at {self.socket_path}")
        try:
            return Client(str(self.socket_path), family="AF_UNIX")
        except Exception as exc:
            raise ModelServerError(f"Could not connect to model server: {exc}") from exc

    def _request(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            self.conn.send(payload)
            response = self.conn.recv()
        except Exception as exc:
            raise ModelServerError(f"Model server request failed: {exc}") from exc

        if not isinstance(response, dict):
            raise ModelServerError("Model server returned an invalid response")
        if not response.get("ok", False):
            raise ModelServerError(str(response.get("error", "Unknown model server error")))
        return response


def ping_server(socket_path: Path = SOCKET_PATH) -> bool:
    return get_server_info(socket_path=socket_path) is not None


def get_server_info(socket_path: Path = SOCKET_PATH) -> dict[str, Any] | None:
    try:
        remote = RemoteParakeetTranscriber(socket_path=socket_path)
        info = remote.info
        remote.close()
        return info
    except Exception:
        return None


def send_shutdown(socket_path: Path = SOCKET_PATH) -> bool:
    try:
        conn = Client(str(socket_path), family="AF_UNIX")
        conn.send({"cmd": "shutdown"})
        response = conn.recv()
        conn.close()
        return bool(isinstance(response, dict) and response.get("ok"))
    except Exception:
        return False


def serve(args: argparse.Namespace) -> None:
    APP_SUPPORT_DIR.mkdir(parents=True, exist_ok=True)
    stop_event = threading.Event()

    def request_stop(signum, frame) -> None:  # noqa: ANN001
        logging.info("Received signal %s; stopping model server", signum)
        stop_event.set()

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)

    if SOCKET_PATH.exists():
        if ping_server(SOCKET_PATH):
            raise SystemExit(f"Model server is already running at {SOCKET_PATH}")
        SOCKET_PATH.unlink()

    model_name = resolve_model_name(args.model_preset, args.model_name)
    logging.info("Loading %s into persistent MLX model server", model_name)
    transcriber = MLXParakeetTranscriber(model_name=model_name)
    logging.info("Model server ready at %s", SOCKET_PATH)

    listener = Listener(str(SOCKET_PATH), family="AF_UNIX")
    try:
        listener._listener._socket.settimeout(0.5)  # noqa: SLF001 - Listener has no public timeout API.
    except Exception:
        logging.debug("Could not set listener timeout; Ctrl-C may wait for the next client event")

    should_stop = False
    try:
        while not should_stop and not stop_event.is_set():
            try:
                conn = listener.accept()
            except socket.timeout:
                continue
            logging.info("Client connected")
            try:
                while True:
                    if stop_event.is_set():
                        break
                    try:
                        message = conn.recv()
                    except EOFError:
                        break

                    cmd = message.get("cmd") if isinstance(message, dict) else None
                    try:
                        if cmd == "ping":
                            conn.send(
                                {
                                    "ok": True,
                                    "ready": True,
                                    "model_preset": args.model_preset,
                                    "model_name": model_name,
                                }
                            )
                        elif cmd == "reset":
                            transcriber.reset()
                            conn.send({"ok": True})
                        elif cmd == "audio":
                            samples = message.get("samples")
                            if not isinstance(samples, np.ndarray):
                                samples = np.asarray(samples or [], dtype=np.float32)
                            text = transcriber.accept_audio(samples, final=bool(message.get("final", False)))
                            conn.send({"ok": True, "text": text})
                        elif cmd == "shutdown":
                            conn.send({"ok": True})
                            should_stop = True
                            break
                        else:
                            conn.send({"ok": False, "error": f"Unknown command: {cmd}"})
                    except Exception as exc:
                        logging.exception("Model server command failed")
                        conn.send({"ok": False, "error": str(exc)})
            finally:
                conn.close()
                logging.info("Client disconnected")
    finally:
        listener.close()
        try:
            os.unlink(SOCKET_PATH)
        except FileNotFoundError:
            pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the persistent local Jarvis dictation model server.")
    parser.add_argument("--model-preset", choices=sorted(MODEL_PRESETS), default=DEFAULT_MODEL_PRESET)
    parser.add_argument("--model-name", default=None, help="Override the preset with a Hugging Face model id or local path.")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--stop", action="store_true")
    parser.add_argument("--debug", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    if args.status:
        if ping_server(SOCKET_PATH):
            print(f"Model server is running at {SOCKET_PATH}")
            return
        raise SystemExit("Model server is not running")

    if args.stop:
        if send_shutdown(SOCKET_PATH):
            print("Model server stopped")
            return
        raise SystemExit("Model server is not running or did not stop cleanly")

    serve(args)


if __name__ == "__main__":
    main()
