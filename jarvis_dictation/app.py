from __future__ import annotations

import argparse
import logging
import math
import queue
import signal
import tempfile
import threading
import time
import wave
from pathlib import Path
from typing import Callable, Optional

import numpy as np
import pyperclip
import sounddevice as sd
from pynput import keyboard

from jarvis_dictation.models import MLX_MODEL_NAME

SAMPLE_RATE = 16_000
CHANNELS = 1


class FloatingOverlay:
    def __init__(self) -> None:
        from AppKit import (
            NSApplication,
            NSApplicationActivationPolicyAccessory,
            NSBackingStoreBuffered,
            NSColor,
            NSFloatingWindowLevel,
            NSFont,
            NSFontWeightMedium,
            NSShadow,
            NSScreen,
            NSTextField,
            NSVisualEffectBlendingModeBehindWindow,
            NSVisualEffectMaterialPopover,
            NSVisualEffectStateActive,
            NSVisualEffectView,
            NSView,
            NSWindow,
            NSWindowStyleMaskBorderless,
        )
        from Foundation import NSMakeRect
        from Quartz import CALayer, CGPathCreateWithRoundedRect

        self.app = NSApplication.sharedApplication()
        self.app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
        self.NSMakeRect = NSMakeRect
        self.NSColor = NSColor

        screen = NSScreen.mainScreen().visibleFrame()
        self.width = 328
        self.height = 48
        shadow_pad = 28
        window_width = self.width + shadow_pad * 2
        window_height = self.height + shadow_pad * 2
        x = screen.origin.x + (screen.size.width - window_width) / 2
        y = screen.origin.y + 28 - shadow_pad
        frame = NSMakeRect(x, y, window_width, window_height)

        self.window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            frame, NSWindowStyleMaskBorderless, NSBackingStoreBuffered, False
        )
        self.window.setOpaque_(False)
        self.window.setBackgroundColor_(NSColor.clearColor())
        self.window.setLevel_(NSFloatingWindowLevel)
        self.window.setIgnoresMouseEvents_(True)
        self.window.setHasShadow_(False)

        root = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, window_width, window_height))
        root.setWantsLayer_(True)
        root.layer().setMasksToBounds_(False)
        root.layer().setBackgroundColor_(NSColor.clearColor().CGColor())
        self.window.setContentView_(root)

        shadow_layer = CALayer.layer()
        shadow_layer.setFrame_(NSMakeRect(shadow_pad, shadow_pad, self.width, self.height))
        shadow_layer.setMasksToBounds_(False)
        shadow_layer.setBackgroundColor_(NSColor.clearColor().CGColor())
        shadow_layer.setShadowColor_(NSColor.blackColor().CGColor())
        shadow_layer.setShadowOpacity_(0.28)
        shadow_layer.setShadowRadius_(12)
        shadow_layer.setShadowOffset_((0, -10))
        shadow_layer.setShadowPath_(CGPathCreateWithRoundedRect(NSMakeRect(0, 0, self.width, self.height), 20, 20, None))
        root.layer().addSublayer_(shadow_layer)

        shadow_host = NSView.alloc().initWithFrame_(NSMakeRect(shadow_pad, shadow_pad, self.width, self.height))
        shadow_host.setWantsLayer_(True)
        shadow_host.layer().setCornerRadius_(20)
        shadow_host.layer().setMasksToBounds_(False)
        shadow_host.layer().setBackgroundColor_(NSColor.clearColor().CGColor())
        root.addSubview_(shadow_host)

        container = NSVisualEffectView.alloc().initWithFrame_(NSMakeRect(0, 0, self.width, self.height))
        container.setMaterial_(NSVisualEffectMaterialPopover)
        container.setBlendingMode_(NSVisualEffectBlendingModeBehindWindow)
        container.setState_(NSVisualEffectStateActive)
        container.setWantsLayer_(True)
        container.layer().setCornerRadius_(20)
        container.layer().setMasksToBounds_(True)
        container.layer().setBorderWidth_(0.35)
        container.layer().setBorderColor_(NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.18).CGColor())
        shadow_host.addSubview_(container)

        glass_tint = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, self.width, self.height))
        glass_tint.setWantsLayer_(True)
        glass_tint.layer().setCornerRadius_(20)
        glass_tint.layer().setMasksToBounds_(True)
        glass_tint.layer().setBackgroundColor_(
            NSColor.colorWithCalibratedRed_green_blue_alpha_(0.96, 0.98, 1.0, 0.045).CGColor()
        )
        container.addSubview_(glass_tint)

        top_highlight = NSView.alloc().initWithFrame_(NSMakeRect(20, self.height - 2, self.width - 40, 1))
        top_highlight.setWantsLayer_(True)
        top_highlight.layer().setBackgroundColor_(NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.52).CGColor())
        glass_tint.addSubview_(top_highlight)

        self.status_dot = NSView.alloc().initWithFrame_(NSMakeRect(22, 19, 10, 10))
        self.status_dot.setWantsLayer_(True)
        self.status_dot.layer().setCornerRadius_(5)
        self.status_dot.layer().setBackgroundColor_(
            NSColor.colorWithCalibratedRed_green_blue_alpha_(0.05, 0.08, 0.10, 0.78).CGColor()
        )
        glass_tint.addSubview_(self.status_dot)

        self.title = NSTextField.alloc().initWithFrame_(NSMakeRect(42, 10, 96, 24))
        self.title.setEditable_(False)
        self.title.setBordered_(False)
        self.title.setDrawsBackground_(False)
        self.title.setSelectable_(False)
        self.title.setTextColor_(NSColor.colorWithCalibratedRed_green_blue_alpha_(0.04, 0.055, 0.065, 0.86))
        self.title.setFont_(NSFont.systemFontOfSize_weight_(15, NSFontWeightMedium))
        text_shadow = NSShadow.alloc().init()
        text_shadow.setShadowBlurRadius_(4)
        text_shadow.setShadowOffset_((0, -1))
        text_shadow.setShadowColor_(NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.18))
        self.title.setShadow_(text_shadow)
        self.title.setStringValue_("Listening")
        glass_tint.addSubview_(self.title)

        self.wave_bars = []
        self.wave_level = 0.0
        self.wave_phase = 0.0
        bar_count = 18
        bar_width = 3
        gap = 4
        wave_x = 176
        wave_y = 14
        for index in range(bar_count):
            bar = NSView.alloc().initWithFrame_(NSMakeRect(wave_x + index * (bar_width + gap), wave_y + 8, bar_width, 4))
            bar.setWantsLayer_(True)
            bar.layer().setCornerRadius_(1.5)
            bar.layer().setBackgroundColor_(
                NSColor.colorWithCalibratedRed_green_blue_alpha_(0.03, 0.06, 0.08, 0.30).CGColor()
            )
            glass_tint.addSubview_(bar)
            self.wave_bars.append(bar)

    def show(self, text: str) -> None:
        from PyObjCTools import AppHelper

        AppHelper.callAfter(self._show, text)

    def update(self, text: str) -> None:
        from PyObjCTools import AppHelper

        AppHelper.callAfter(self._update, text)

    def update_level(self, level: float) -> None:
        from PyObjCTools import AppHelper

        AppHelper.callAfter(self._update_level, level)

    def hide(self) -> None:
        from PyObjCTools import AppHelper

        AppHelper.callAfter(self.window.orderOut_, None)

    def run(self) -> None:
        from PyObjCTools import AppHelper

        AppHelper.runEventLoop(installInterrupt=True)

    def stop(self) -> None:
        from PyObjCTools import AppHelper

        AppHelper.callAfter(AppHelper.stopEventLoop)

    def _show(self, text: str) -> None:
        self._update(text)
        self._update_level(0.0)
        self.window.orderFrontRegardless()

    def _update(self, text: str) -> None:
        clean = " ".join(text.split())
        if clean.lower().startswith("connecting") or clean.lower().startswith("loading"):
            self.title.setStringValue_("Warming")
        elif clean.lower().startswith("finalizing"):
            self.title.setStringValue_("Finalizing")
        elif clean.lower().startswith("start "):
            self.title.setStringValue_("Offline")
        else:
            self.title.setStringValue_("Listening")

    def _update_level(self, level: float) -> None:
        level = max(0.0, min(1.0, float(level)))
        self.wave_level = max(level, self.wave_level * 0.72)
        self.wave_phase += 0.45

        active_color = self.NSColor.colorWithCalibratedRed_green_blue_alpha_(
            0.02, 0.055 + 0.04 * self.wave_level, 0.075 + 0.06 * self.wave_level, 0.54 + 0.26 * self.wave_level
        ).CGColor()
        quiet_color = self.NSColor.colorWithCalibratedRed_green_blue_alpha_(0.04, 0.07, 0.09, 0.26).CGColor()

        base_y = 14
        max_height = 20
        min_height = 4
        for index, bar in enumerate(self.wave_bars):
            wave = (math.sin(self.wave_phase + index * 0.62) + 1.0) / 2.0
            taper = 1.0 - abs(index - (len(self.wave_bars) - 1) / 2.0) / len(self.wave_bars)
            height = min_height + (max_height - min_height) * self.wave_level * (0.35 + 0.65 * wave) * taper
            frame = bar.frame()
            bar.setFrame_(self.NSMakeRect(frame.origin.x, base_y + (max_height - height) / 2.0, frame.size.width, height))
            bar.layer().setBackgroundColor_(active_color if self.wave_level > 0.05 else quiet_color)

        if self.wave_level > 0.05:
            self.status_dot.layer().setBackgroundColor_(
                self.NSColor.colorWithCalibratedRed_green_blue_alpha_(0.02, 0.09, 0.12, 0.86).CGColor()
            )
        else:
            self.status_dot.layer().setBackgroundColor_(
                self.NSColor.colorWithCalibratedRed_green_blue_alpha_(0.05, 0.08, 0.10, 0.58).CGColor()
            )


class ClipboardPaster:
    def __init__(self) -> None:
        self.keyboard = keyboard.Controller()

    def paste_text_preserving_clipboard(self, text: str) -> None:
        if not text.strip():
            return

        previous = None
        try:
            previous = pyperclip.paste()
        except Exception as exc:
            logging.warning("Could not read clipboard before paste: %s", exc)

        pyperclip.copy(text)
        time.sleep(0.08)
        self.keyboard.press(keyboard.Key.cmd)
        self.keyboard.press("v")
        self.keyboard.release("v")
        self.keyboard.release(keyboard.Key.cmd)
        time.sleep(0.35)

        if previous is not None:
            try:
                pyperclip.copy(previous)
            except Exception as exc:
                logging.warning("Could not restore clipboard after paste: %s", exc)


class SoundCues:
    def __init__(self) -> None:
        self.enabled = True
        self.start_sound = self._load_sound(("Pop", "Tink", "Glass"))
        self.stop_sound = self.start_sound

    def play_start(self) -> None:
        self._play(self.start_sound)

    def play_stop(self) -> None:
        self._play(self.stop_sound)

    def _load_sound(self, names: tuple[str, ...]):
        try:
            from AppKit import NSSound
        except Exception as exc:  # pragma: no cover - depends on macOS frameworks
            logging.debug("NSSound unavailable: %s", exc)
            return None

        for name in names:
            sound = NSSound.soundNamed_(name)
            if sound is not None:
                sound.setVolume_(0.32)
                return sound
        return None

    def _play(self, sound) -> None:  # noqa: ANN001
        if not self.enabled or sound is None:
            return
        try:
            sound.stop()
            sound.play()
        except Exception as exc:
            logging.debug("Could not play sound cue: %s", exc)


class AudioRecorder:
    def __init__(
        self,
        audio_queue: queue.Queue[np.ndarray],
        sample_rate: int = SAMPLE_RATE,
        on_level: Callable[[float], None] | None = None,
    ) -> None:
        self.audio_queue = audio_queue
        self.sample_rate = sample_rate
        self.on_level = on_level
        self.stream: Optional[sd.InputStream] = None

    def start(self) -> None:
        self.stream = sd.InputStream(
            samplerate=self.sample_rate,
            channels=CHANNELS,
            dtype="float32",
            blocksize=int(self.sample_rate * 0.05),
            callback=self._callback,
        )
        self.stream.start()

    def stop(self) -> None:
        if self.stream is None:
            return
        self.stream.stop()
        self.stream.close()
        self.stream = None

    def _callback(self, indata, frames, time_info, status) -> None:  # noqa: ANN001
        if status:
            logging.debug("Audio input status: %s", status)
        samples = indata[:, 0].copy()
        self.audio_queue.put(samples)
        if self.on_level is not None:
            rms = float(np.sqrt(np.mean(samples * samples))) if samples.size else 0.0
            self.on_level(min(1.0, rms * 18.0))


def _normalize_transcribe_result(result) -> str:  # noqa: ANN001
    if result is None:
        return ""
    if isinstance(result, tuple):
        result = result[0]
    if isinstance(result, list):
        if not result:
            return ""
        result = result[0]
    if hasattr(result, "text"):
        return str(result.text)
    return str(result)


def _write_temp_wav(samples: np.ndarray, sample_rate: int = SAMPLE_RATE) -> Path:
    audio = np.asarray(samples, dtype=np.float32)
    audio = np.clip(audio, -1.0, 1.0)
    pcm16 = (audio * 32767.0).astype("<i2")
    temp = tempfile.NamedTemporaryFile(prefix="jarvis-dictation-", suffix=".wav", delete=False)
    path = Path(temp.name)
    temp.close()
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm16.tobytes())
    return path


class MLXTranscriber:
    def __init__(self, model_name: str = MLX_MODEL_NAME, engine: str = "parakeet-mlx") -> None:
        self.model_name = model_name
        self.engine = engine
        self.model = self._load_model()

    def reset(self) -> None:
        pass

    def accept_audio(self, samples: np.ndarray, final: bool = False) -> str:
        if not final or samples.size == 0:
            return ""
        path = _write_temp_wav(samples)
        try:
            if self.engine == "mlx-audio":
                result = self.model.generate(str(path))
            else:
                result = self.model.transcribe(str(path))
        finally:
            try:
                path.unlink()
            except FileNotFoundError:
                pass
        return _normalize_transcribe_result(result).strip()

    def _load_model(self):  # noqa: ANN202
        if self.engine == "mlx-audio":
            try:
                from mlx_audio.stt import load
            except ImportError as exc:
                raise RuntimeError(
                    "The Nemotron preset requires the optional MLX Audio dependency. Install it with: "
                    "python -m pip install -e '.[nemotron]'"
                ) from exc

            logging.info("Loading %s with MLX Audio", self.model_name)
            return load(self.model_name)

        if self.engine != "parakeet-mlx":
            raise ValueError(f"Unknown MLX transcription engine: {self.engine}")

        try:
            from parakeet_mlx import from_pretrained
        except ImportError:
            try:
                from parakeet import from_pretrained
            except ImportError as exc:
                raise RuntimeError(
                    "MLX support requires `parakeet-mlx`. Install dependencies with: "
                    "python -m pip install -e ."
                ) from exc

        try:
            import mlx.core as mx

            logging.info("Loading %s with MLX bfloat16", self.model_name)
            return from_pretrained(self.model_name, dtype=mx.bfloat16)
        except ValueError as exc:
            if "not in model" in str(exc) and ("scales" in str(exc) or "biases" in str(exc)):
                raise RuntimeError(
                    f"MLX model `{self.model_name}` looks like a quantized checkpoint that the installed "
                    "`parakeet-mlx` loader cannot read. Use the supported default "
                    f"`{MLX_MODEL_NAME}`, or install a loader version that explicitly supports that quantized model."
                ) from exc
            raise
        except TypeError:
            logging.info("Loading %s with MLX", self.model_name)
            return from_pretrained(self.model_name)


class DictationSession(threading.Thread):
    def __init__(
        self,
        transcriber,
        audio_queue: queue.Queue[np.ndarray],
        on_final: Callable[[str], None],
    ) -> None:
        super().__init__(daemon=True)
        self.transcriber = transcriber
        self.audio_queue = audio_queue
        self.on_final = on_final
        self.stop_event = threading.Event()

    def stop(self) -> None:
        self.stop_event.set()

    def run(self) -> None:
        self.transcriber.reset()
        buffered_audio: list[np.ndarray] = []

        while not self.stop_event.is_set() or not self.audio_queue.empty():
            try:
                samples = self.audio_queue.get(timeout=0.1)
            except queue.Empty:
                continue

            buffered_audio.append(samples)

        try:
            audio = np.concatenate(buffered_audio) if buffered_audio else np.zeros(0, dtype=np.float32)
            final_text = self.transcriber.accept_audio(audio, final=True)
        except Exception:
            logging.exception("Final transcription failed")
            final_text = ""
        self.on_final(final_text.strip())


class DictationController:
    def __init__(
        self,
        overlay: FloatingOverlay,
    ) -> None:
        self.overlay = overlay
        self.lock = threading.Lock()
        self.model_lock = threading.Lock()
        self.loading = False
        self.recording = False
        self.cancel_start = False
        self.transcriber = None
        self.audio_queue: Optional[queue.Queue[np.ndarray]] = None
        self.recorder: Optional[AudioRecorder] = None
        self.session: Optional[DictationSession] = None
        self.paster = ClipboardPaster()
        self.sounds = SoundCues()

    def preload(self) -> None:
        threading.Thread(target=self._preload_model, daemon=True).start()

    def _preload_model(self) -> None:
        try:
            self._ensure_transcriber()
            logging.info("Parakeet transcriber is ready")
        except Exception as exc:
            logging.warning("Background transcriber connection failed: %s", exc)

    def _ensure_transcriber(self):
        with self.model_lock:
            if self.transcriber is None:
                from jarvis_dictation.model_server import RemoteParakeetTranscriber

                self.transcriber = RemoteParakeetTranscriber()
            return self.transcriber

    def toggle(self) -> None:
        with self.lock:
            should_stop = self.recording or self.loading
        if should_stop:
            threading.Thread(target=self.stop, daemon=True).start()
        else:
            threading.Thread(target=self.start, daemon=True).start()

    def start(self) -> None:
        with self.lock:
            if self.loading or self.recording:
                return
            self.loading = True
            self.cancel_start = False

        self.overlay.show("Connecting to local Parakeet...")
        self.sounds.play_start()
        try:
            transcriber = self._ensure_transcriber()
        except Exception:
            logging.exception("Could not connect to ASR model")
            self.overlay.show("Start jarvis-dictation-server first.")
            with self.lock:
                self.loading = False
            return

        with self.lock:
            if self.cancel_start:
                self.loading = False
                self.cancel_start = False
                self.overlay.hide()
                return

            self.audio_queue = queue.Queue()
            self.recorder = AudioRecorder(self.audio_queue, on_level=self.overlay.update_level)
            self.session = DictationSession(
                transcriber,
                self.audio_queue,
                on_final=self._handle_final_text,
            )

        try:
            self.recorder.start()
            self.session.start()
        except Exception:
            logging.exception("Could not start microphone recording")
            self.overlay.show("Could not start microphone. Check permissions.")
            with self.lock:
                self.loading = False
            return

        with self.lock:
            self.loading = False
            self.recording = True
        self.overlay.show("Listening...")

    def stop(self) -> None:
        with self.lock:
            if self.loading and not self.recording:
                self.cancel_start = True
                return
            if not self.recording:
                return
            self.recording = False
            recorder = self.recorder
            session = self.session

        self.overlay.update("Finalizing...")
        self.sounds.play_stop()
        if recorder is not None:
            recorder.stop()
        if session is not None:
            session.stop()
            session.join()

    def _handle_final_text(self, text: str) -> None:
        self.overlay.update_level(0.0)
        self.overlay.hide()
        if text:
            self.paster.paste_text_preserving_clipboard(text)

    def shutdown(self) -> None:
        with self.lock:
            recorder = self.recorder
            session = self.session
            self.recording = False
            self.loading = False
        if recorder is not None:
            recorder.stop()
        if session is not None:
            session.stop()
        transcriber = self.transcriber
        if transcriber is not None and hasattr(transcriber, "close"):
            transcriber.close()


class RightCommandHotkey:
    def __init__(self, on_toggle: Callable[[], None]) -> None:
        self.on_toggle = on_toggle
        self.down = False
        self.listener: Optional[keyboard.Listener] = None

    def start(self) -> None:
        self.listener = keyboard.Listener(on_press=self._on_press, on_release=self._on_release)
        self.listener.start()

    def stop(self) -> None:
        if self.listener is not None:
            self.listener.stop()

    def _on_press(self, key) -> None:  # noqa: ANN001
        if key == keyboard.Key.cmd_r and not self.down:
            self.down = True
            self.on_toggle()

    def _on_release(self, key) -> None:  # noqa: ANN001
        if key == keyboard.Key.cmd_r:
            self.down = False


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Local macOS background dictation MVP.")
    parser.add_argument("--debug", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    from jarvis_dictation.permissions import request_macos_permissions

    request_macos_permissions()
    try:
        from jarvis_dictation.prepare import read_prepare_marker

        marker = read_prepare_marker()
        if marker is None:
            logging.warning("No preparation marker found. Run `jarvis-dictation-prepare` before regular use.")
        else:
            logging.info("Prepared model marker found from %s", marker.get("prepared_at", "unknown time"))
    except Exception as exc:
        logging.debug("Could not check preparation marker: %s", exc)

    overlay = FloatingOverlay()
    controller = DictationController(overlay)
    hotkey = RightCommandHotkey(controller.toggle)
    hotkey.start()
    controller.preload()

    shutdown_once = threading.Event()

    def request_shutdown(signum, frame) -> None:  # noqa: ANN001
        if shutdown_once.is_set():
            return
        shutdown_once.set()
        logging.info("Received signal %s; stopping dictation app", signum)
        hotkey.stop()
        controller.shutdown()
        overlay.stop()

    signal.signal(signal.SIGINT, request_shutdown)
    signal.signal(signal.SIGTERM, request_shutdown)

    logging.info("Jarvis dictation is running. Press Right Command to toggle.")
    try:
        overlay.run()
    finally:
        request_shutdown("exit", None)


if __name__ == "__main__":
    main()
