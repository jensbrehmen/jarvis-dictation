from __future__ import annotations

import argparse
import json
import logging
import platform
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import sounddevice as sd

from jarvis_dictation.app import (
    MLXTranscriber,
    SAMPLE_RATE,
)
from jarvis_dictation.models import (
    DEFAULT_MODEL_PRESET,
    MODEL_PRESETS,
    resolve_model_engine,
    resolve_model_name,
)


APP_SUPPORT_DIR = Path.home() / "Library" / "Application Support" / "JarvisDictation"
PREPARE_MARKER = APP_SUPPORT_DIR / "prepared.json"


def write_prepare_marker(payload: dict) -> None:
    APP_SUPPORT_DIR.mkdir(parents=True, exist_ok=True)
    PREPARE_MARKER.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def read_prepare_marker() -> dict | None:
    try:
        return json.loads(PREPARE_MARKER.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except Exception as exc:
        logging.debug("Could not read prepare marker: %s", exc)
        return None


def check_audio_devices() -> dict:
    devices = sd.query_devices()
    default_input = sd.query_devices(kind="input")
    input_devices = [
        {
            "name": device["name"],
            "max_input_channels": int(device["max_input_channels"]),
            "default_samplerate": float(device["default_samplerate"]),
        }
        for device in devices
        if int(device["max_input_channels"]) > 0
    ]
    return {
        "default_input": {
            "name": default_input["name"],
            "max_input_channels": int(default_input["max_input_channels"]),
            "default_samplerate": float(default_input["default_samplerate"]),
        },
        "input_device_count": len(input_devices),
    }


def prepare_model(args: argparse.Namespace) -> dict:
    model_name = resolve_model_name(args.model_preset, args.model_name)
    model_engine = resolve_model_engine(args.model_preset, args.model_name, getattr(args, "model_engine", None))
    transcriber = MLXTranscriber(model_name=model_name, engine=model_engine)
    if args.smoke_seconds > 0:
        silence = np.zeros(int(SAMPLE_RATE * args.smoke_seconds), dtype=np.float32)
        transcriber.accept_audio(silence, final=True)

    return {
        "model_preset": args.model_preset,
        "model_name": model_name,
        "model_engine": model_engine,
        "device": "mlx",
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prepare Jarvis dictation for smooth runtime startup.")
    parser.add_argument("--model-preset", choices=sorted(MODEL_PRESETS), default=DEFAULT_MODEL_PRESET)
    parser.add_argument("--model-name", default=None, help="Override the preset with a Hugging Face model id or local path.")
    parser.add_argument("--model-engine", choices=["mlx-audio", "parakeet-mlx"], default=None)
    parser.add_argument("--smoke-seconds", type=float, default=1.0)
    parser.add_argument("--debug", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    logging.info("Checking microphone devices")
    try:
        audio_info = check_audio_devices()
        logging.info("Default input: %s", audio_info["default_input"]["name"])
    except Exception as exc:
        logging.warning("Could not query microphone devices: %s", exc)
        audio_info = {"error": str(exc)}

    logging.info("Downloading/loading %s through mlx", resolve_model_name(args.model_preset, args.model_name))
    model_info = prepare_model(args)

    marker = {
        "prepared_at": datetime.now(timezone.utc).isoformat(),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "sample_rate": SAMPLE_RATE,
        "audio": audio_info,
        "model": model_info,
    }
    write_prepare_marker(marker)

    logging.info("Prepared Jarvis dictation")
    logging.info("Model device: %s", model_info["device"])
    logging.info("Preparation marker: %s", PREPARE_MARKER)


if __name__ == "__main__":
    main()
