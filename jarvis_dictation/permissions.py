from __future__ import annotations

import argparse
import logging
import threading
import time

import sounddevice as sd

from jarvis_dictation.app import CHANNELS, SAMPLE_RATE


MIC_STATUS = {
    0: "not_determined",
    1: "restricted",
    2: "denied",
    3: "authorized",
}


def request_microphone_permission(timeout_secs: float = 30.0) -> bool | None:
    try:
        from AVFoundation import AVCaptureDevice, AVMediaTypeAudio
    except Exception as exc:  # pragma: no cover - depends on macOS frameworks
        logging.debug("AVFoundation unavailable: %s", exc)
        return None

    status = int(AVCaptureDevice.authorizationStatusForMediaType_(AVMediaTypeAudio))
    logging.info("Microphone permission status: %s", MIC_STATUS.get(status, status))

    if status == 3:
        return True
    if status in (1, 2):
        return False

    done = threading.Event()
    result = {"granted": False}

    def callback(granted: bool) -> None:
        result["granted"] = bool(granted)
        done.set()

    AVCaptureDevice.requestAccessForMediaType_completionHandler_(AVMediaTypeAudio, callback)
    done.wait(timeout_secs)
    logging.info("Microphone permission granted: %s", result["granted"])
    return result["granted"] if done.is_set() else None


def request_accessibility_permission() -> bool | None:
    try:
        from ApplicationServices import AXIsProcessTrustedWithOptions, kAXTrustedCheckOptionPrompt
    except Exception as exc:  # pragma: no cover - depends on macOS frameworks
        logging.debug("Quartz accessibility API unavailable: %s", exc)
        return None

    trusted = bool(AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: True}))
    logging.info("Accessibility permission trusted: %s", trusted)
    return trusted


def force_microphone_prompt_via_sounddevice(duration_secs: float = 1.0) -> bool:
    try:
        with sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=CHANNELS,
            dtype="float32",
            blocksize=int(SAMPLE_RATE * 0.05),
        ):
            time.sleep(duration_secs)
        logging.info("Microphone stream opened successfully")
        return True
    except Exception as exc:
        logging.warning("Could not open microphone stream: %s", exc)
        return False


def request_macos_permissions(include_accessibility: bool = True) -> None:
    mic = request_microphone_permission()
    if include_accessibility:
        request_accessibility_permission()

    if mic is not True:
        logging.info("Opening microphone briefly to trigger the system prompt if macOS allows it")
        force_microphone_prompt_via_sounddevice()

    if mic is False:
        logging.warning(
            "macOS reports microphone access is denied. To make macOS ask again, quit this terminal app and run: "
            "tccutil reset Microphone"
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ask macOS for Jarvis dictation permissions.")
    parser.add_argument("--debug", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    request_macos_permissions()
    logging.info("Permission request finished")


if __name__ == "__main__":
    main()
