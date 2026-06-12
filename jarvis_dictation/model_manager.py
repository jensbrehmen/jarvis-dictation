from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable

from jarvis_dictation.model_server import get_server_info, send_shutdown
from jarvis_dictation.models import resolve_model_name
from jarvis_dictation.prepare import APP_SUPPORT_DIR


MODEL_LOG_PATH = APP_SUPPORT_DIR / "mac-app-model-server.log"
StatusCallback = Callable[[str, str, dict | None], None]


class ModelManager:
    def __init__(self, status_callback: StatusCallback | None = None) -> None:
        self.status_callback = status_callback
        self.lock = threading.Lock()
        self.process: subprocess.Popen | None = None
        self.log_file = None
        self.operation: threading.Thread | None = None
        self.stopping = False
        self.owns_server = False
        self.current_preset: str | None = None

    def start_async(self, model_preset: str) -> None:
        self._run_async(self._ensure_running, model_preset)

    def restart_async(self, model_preset: str) -> None:
        self._run_async(self._restart, model_preset)

    def stop_async(self) -> None:
        self._run_async(self._stop_server, True)

    def stop(self, timeout: float = 8.0) -> None:
        self.stopping = True
        self._stop_server(False)
        operation = self.operation
        if operation is not None and operation is not threading.current_thread():
            operation.join(timeout=timeout)

    def _run_async(self, target: Callable, *args) -> None:
        with self.lock:
            if self.operation is not None and self.operation.is_alive():
                self._notify("busy", "Model operation already in progress", None)
                return
            self.operation = threading.Thread(target=target, args=args, daemon=True)
            self.operation.start()

    def _ensure_running(self, model_preset: str) -> None:
        requested_name = resolve_model_name(model_preset)
        info = get_server_info()
        if info is not None and info.get("model_name") == requested_name:
            self.owns_server = False
            self.current_preset = model_preset
            self._notify("ready", self._ready_message(model_preset), info)
            return

        if info is not None:
            self._notify("switching", "Switching speech model...", info)
            self._stop_server(True)

        self._start_server(model_preset)

    def _restart(self, model_preset: str) -> None:
        self._notify("switching", "Switching speech model...", get_server_info())
        self._stop_server(True)
        self._start_server(model_preset)

    def _start_server(self, model_preset: str) -> None:
        if self.stopping:
            return

        APP_SUPPORT_DIR.mkdir(parents=True, exist_ok=True)
        self._notify("loading", f"Loading {self._display_name(model_preset)}...", None)

        command = [
            str(self._worker_python()),
            "-m",
            "jarvis_dictation.model_server",
            "--model-preset",
            model_preset,
        ]
        try:
            self.log_file = Path(MODEL_LOG_PATH).open("ab", buffering=0)
            self.process = subprocess.Popen(
                command,
                stdout=self.log_file,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            self.owns_server = True
        except Exception as exc:
            self._close_log()
            logging.exception("Could not start model server")
            self._notify("error", f"Could not start model: {exc}", None)
            return

        deadline = time.monotonic() + 180.0
        requested_name = resolve_model_name(model_preset)
        while time.monotonic() < deadline and not self.stopping:
            if self.process.poll() is not None:
                code = self.process.returncode
                self._notify("error", f"Model server exited with code {code}. Check the model log.", None)
                self._close_log()
                return

            info = get_server_info()
            if info is not None and info.get("model_name") == requested_name:
                self.current_preset = model_preset
                self._notify("ready", self._ready_message(model_preset), info)
                return
            time.sleep(0.5)

        if not self.stopping:
            self._notify("error", "Timed out while loading the speech model.", None)

    def _stop_server(self, force: bool = False) -> None:
        if not force and not self.owns_server:
            return

        info = get_server_info()
        if info is not None:
            send_shutdown()

        process = self.process
        if process is not None and process.poll() is None:
            try:
                process.wait(timeout=6)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()

        self.process = None
        self.owns_server = False
        self.current_preset = None
        self._close_log()
        if not self.stopping:
            self._notify("stopped", "Model stopped", None)

    def _close_log(self) -> None:
        if self.log_file is not None:
            self.log_file.close()
            self.log_file = None

    def _notify(self, state: str, message: str, info: dict | None) -> None:
        logging.info("Model manager: %s - %s", state, message)
        if self.status_callback is not None:
            self.status_callback(state, message, info)

    @staticmethod
    def _display_name(model_preset: str) -> str:
        return {
            "default": "Parakeet 0.6B",
            "small-en": "Parakeet 110M English",
            "nemotron": "Nemotron 3.5",
        }.get(model_preset, model_preset)

    @classmethod
    def _ready_message(cls, model_preset: str) -> str:
        return f"{cls._display_name(model_preset)} ready"

    @staticmethod
    def _worker_python() -> Path:
        override = os.environ.get("JARVIS_DICTATION_PYTHON")
        if override:
            return Path(override).expanduser()

        project_venv = Path(__file__).resolve().parent.parent / ".venv" / "bin" / "python"
        if project_venv.exists():
            return project_venv

        return Path(sys.executable)
