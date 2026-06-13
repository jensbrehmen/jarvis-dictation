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
from jarvis_dictation.models import DEFAULT_MODEL_PRESET, ModelSpec
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
        self.current_model_key: str | None = None

    def start_async(self, model: ModelSpec) -> None:
        self._run_async(self._ensure_running, model)

    def restart_async(self, model: ModelSpec) -> None:
        self._run_async(self._restart, model)

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

    def _ensure_running(self, model: ModelSpec) -> None:
        info = get_server_info()
        if self._matches(info, model):
            self.owns_server = False
            self.current_model_key = model.key
            self._notify("ready", self._ready_message(model), info)
            return

        if info is not None:
            self._notify("switching", "Switching speech model...", info)
            self._stop_server(True)

        self._start_server(model)

    def _restart(self, model: ModelSpec) -> None:
        self._notify("switching", "Switching speech model...", get_server_info())
        self._stop_server(True)
        self._start_server(model)

    def _start_server(self, model: ModelSpec) -> None:
        if self.stopping:
            return

        APP_SUPPORT_DIR.mkdir(parents=True, exist_ok=True)
        self._notify("loading", f"Loading {model.title}...", None)

        command = self._server_command(model)
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

        deadline = time.monotonic() + (600.0 if not model.built_in else 180.0)
        while time.monotonic() < deadline and not self.stopping:
            if self.process.poll() is not None:
                code = self.process.returncode
                self._notify("error", f"Model server exited with code {code}. Check the model log.", None)
                self._close_log()
                return

            info = get_server_info()
            if self._matches(info, model):
                self.current_model_key = model.key
                self._notify("ready", self._ready_message(model), info)
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
        self.current_model_key = None
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
    def _matches(info: dict | None, model: ModelSpec) -> bool:
        return bool(
            info is not None
            and info.get("model_name") == model.model_name
            and info.get("model_engine") == model.engine
        )

    @staticmethod
    def _ready_message(model: ModelSpec) -> str:
        return f"{model.title} ready"

    @classmethod
    def _server_command(cls, model: ModelSpec) -> list[str]:
        return [
            str(cls._worker_python()),
            "-m",
            "jarvis_dictation.model_server",
            "--model-preset",
            model.key if model.built_in else DEFAULT_MODEL_PRESET,
            "--model-name",
            model.model_name,
            "--model-engine",
            model.engine,
        ]

    @staticmethod
    def _worker_python() -> Path:
        override = os.environ.get("JARVIS_DICTATION_PYTHON")
        if override:
            return Path(override).expanduser()

        project_venv = Path(__file__).resolve().parent.parent / ".venv" / "bin" / "python"
        if project_venv.exists():
            return project_venv

        return Path(sys.executable)
