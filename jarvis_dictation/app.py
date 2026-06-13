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
from importlib.resources import files
from pathlib import Path
from typing import Callable, Optional

import numpy as np
import pyperclip
import sounddevice as sd
from pynput import keyboard

from jarvis_dictation.models import MLX_MODEL_NAME
from jarvis_dictation.shortcuts import (
    DEFAULT_SHORTCUT,
    normalize_shortcut,
    serialize_key,
    shortcut_display_name,
    shortcut_matches,
)

SAMPLE_RATE = 16_000
CHANNELS = 1


def available_input_devices() -> list[str]:
    try:
        devices = sd.query_devices()
    except Exception as exc:
        logging.warning("Could not enumerate microphone devices: %s", exc)
        return []

    names: list[str] = []
    for device in devices:
        if int(device.get("max_input_channels", 0)) <= 0:
            continue
        name = str(device.get("name", "")).strip()
        if name and name not in names:
            names.append(name)
    return names


def resolve_input_device(device_name: str | None) -> int | None:
    if not device_name:
        return None
    try:
        devices = sd.query_devices()
    except Exception as exc:
        logging.warning("Could not resolve microphone `%s`: %s", device_name, exc)
        return None

    for index, device in enumerate(devices):
        if int(device.get("max_input_channels", 0)) > 0 and str(device.get("name", "")).strip() == device_name:
            return index

    logging.warning("Configured microphone `%s` is unavailable; using the system default", device_name)
    return None


class FloatingOverlay:
    def __init__(self) -> None:
        from AppKit import (
            NSApplication,
            NSApplicationActivationPolicyAccessory,
            NSBackingStoreBuffered,
            NSColor,
            NSFont,
            NSFontWeightMedium,
            NSScreenSaverWindowLevel,
            NSShadow,
            NSScreen,
            NSTextField,
            NSView,
            NSWindow,
            NSWindowCollectionBehaviorCanJoinAllSpaces,
            NSWindowCollectionBehaviorFullScreenAuxiliary,
            NSWindowCollectionBehaviorStationary,
            NSWindowStyleMaskBorderless,
        )
        from Foundation import NSMakeRect
        from Quartz import (
            CABasicAnimation,
            CALayer,
            CAMediaTimingFunction,
            CGPathCreateWithRoundedRect,
            kCAMediaTimingFunctionEaseOut,
        )

        self.app = NSApplication.sharedApplication()
        self.app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
        self.NSMakeRect = NSMakeRect
        self.NSColor = NSColor
        self.CABasicAnimation = CABasicAnimation
        self.CAMediaTimingFunction = CAMediaTimingFunction
        self.animation_timing_name = kCAMediaTimingFunctionEaseOut

        self.NSScreen = NSScreen
        self.width = 318
        self.height = 70
        self.shadow_pad_x = 18
        self.shadow_pad_bottom = 28
        self.top_bleed = 18
        self.window_width = self.width + self.shadow_pad_x * 2
        self.window_height = self.height + self.shadow_pad_bottom
        frame = NSMakeRect(0, 0, self.window_width, self.window_height)

        self.window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            frame, NSWindowStyleMaskBorderless, NSBackingStoreBuffered, False
        )
        self.window.setOpaque_(False)
        self.window.setBackgroundColor_(NSColor.clearColor())
        self.window.setLevel_(NSScreenSaverWindowLevel)
        self.window.setIgnoresMouseEvents_(True)
        self.window.setHasShadow_(False)
        self.window.setCollectionBehavior_(
            NSWindowCollectionBehaviorCanJoinAllSpaces
            | NSWindowCollectionBehaviorFullScreenAuxiliary
            | NSWindowCollectionBehaviorStationary
        )
        self._position_window()

        root = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, self.window_width, self.window_height))
        root.setWantsLayer_(True)
        root.layer().setMasksToBounds_(True)
        root.layer().setBackgroundColor_(NSColor.clearColor().CGColor())
        self.window.setContentView_(root)
        self.overlay_layer = root.layer()

        shadow_layer = CALayer.layer()
        shadow_layer.setFrame_(
            NSMakeRect(
                self.shadow_pad_x,
                self.shadow_pad_bottom,
                self.width,
                self.height + self.top_bleed,
            )
        )
        shadow_layer.setMasksToBounds_(False)
        shadow_layer.setCornerRadius_(18)
        shadow_layer.setBackgroundColor_(NSColor.blackColor().CGColor())
        shadow_layer.setShadowColor_(NSColor.blackColor().CGColor())
        shadow_layer.setShadowOpacity_(0.32)
        shadow_layer.setShadowRadius_(11)
        shadow_layer.setShadowOffset_((0, -6))
        shadow_layer.setShadowPath_(
            CGPathCreateWithRoundedRect(
                NSMakeRect(0, 0, self.width, self.height + self.top_bleed),
                18,
                18,
                None,
            )
        )
        root.layer().addSublayer_(shadow_layer)

        notch_surface = NSView.alloc().initWithFrame_(
            NSMakeRect(
                self.shadow_pad_x,
                self.shadow_pad_bottom,
                self.width,
                self.height + self.top_bleed,
            )
        )
        notch_surface.setWantsLayer_(True)
        notch_surface.layer().setCornerRadius_(18)
        notch_surface.layer().setMasksToBounds_(True)
        notch_surface.layer().setBackgroundColor_(NSColor.blackColor().CGColor())
        root.addSubview_(notch_surface)

        self.status_dot = NSView.alloc().initWithFrame_(NSMakeRect(22, 19, 10, 10))
        self.status_dot.setWantsLayer_(True)
        self.status_dot.layer().setCornerRadius_(5)
        self.status_dot.layer().setBackgroundColor_(
            NSColor.colorWithCalibratedRed_green_blue_alpha_(0.25, 0.72, 0.95, 0.92).CGColor()
        )
        notch_surface.addSubview_(self.status_dot)

        self.title = NSTextField.alloc().initWithFrame_(NSMakeRect(42, 10, 96, 24))
        self.title.setEditable_(False)
        self.title.setBordered_(False)
        self.title.setDrawsBackground_(False)
        self.title.setSelectable_(False)
        self.title.setTextColor_(NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.92))
        self.title.setFont_(NSFont.systemFontOfSize_weight_(15, NSFontWeightMedium))
        text_shadow = NSShadow.alloc().init()
        text_shadow.setShadowBlurRadius_(3)
        text_shadow.setShadowOffset_((0, -1))
        text_shadow.setShadowColor_(NSColor.colorWithCalibratedWhite_alpha_(0.0, 0.65))
        self.title.setShadow_(text_shadow)
        self.title.setStringValue_("Listening")
        notch_surface.addSubview_(self.title)

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
                NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.26).CGColor()
            )
            notch_surface.addSubview_(bar)
            self.wave_bars.append(bar)

    def _window_origin(self) -> tuple[float, float]:
        screen = self.NSScreen.mainScreen().frame()
        x = screen.origin.x + (screen.size.width - self.window_width) / 2
        y = screen.origin.y + screen.size.height - self.window_height
        return x, y

    def _position_window(self) -> None:
        self.window.setFrameOrigin_(self._window_origin())

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

        AppHelper.callAfter(self._hide)

    def run(self) -> None:
        from PyObjCTools import AppHelper

        AppHelper.runEventLoop(installInterrupt=True)

    def stop(self) -> None:
        from PyObjCTools import AppHelper

        AppHelper.callAfter(AppHelper.stopEventLoop)

    def _show(self, text: str) -> None:
        self._update(text)
        self._update_level(0.0)
        if self.window.isVisible():
            return

        self._position_window()
        timing = self.CAMediaTimingFunction.functionWithName_(self.animation_timing_name)

        fade = self.CABasicAnimation.animationWithKeyPath_("opacity")
        fade.setFromValue_(0.0)
        fade.setToValue_(1.0)
        fade.setDuration_(0.14)
        fade.setTimingFunction_(timing)

        drop = self.CABasicAnimation.animationWithKeyPath_("transform.translation.y")
        drop.setFromValue_(6.0)
        drop.setToValue_(0.0)
        drop.setDuration_(0.16)
        drop.setTimingFunction_(timing)

        self.overlay_layer.addAnimation_forKey_(fade, "overlayFadeIn")
        self.overlay_layer.addAnimation_forKey_(drop, "overlayDropIn")
        self.window.orderFrontRegardless()

    def _hide(self) -> None:
        self.overlay_layer.removeAllAnimations()
        self.window.orderOut_(None)

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

        active_color = self.NSColor.colorWithCalibratedWhite_alpha_(
            1.0,
            0.58 + 0.36 * self.wave_level,
        ).CGColor()
        quiet_color = self.NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.26).CGColor()

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
                self.NSColor.colorWithCalibratedRed_green_blue_alpha_(0.28, 0.78, 1.0, 1.0).CGColor()
            )
        else:
            self.status_dot.layer().setBackgroundColor_(
                self.NSColor.colorWithCalibratedRed_green_blue_alpha_(0.20, 0.56, 0.76, 0.76).CGColor()
            )


class ClipboardPaster:
    def __init__(self, preserve_clipboard: bool = True) -> None:
        self.preserve_clipboard = preserve_clipboard

    def paste_text_preserving_clipboard(self, text: str) -> bool:
        if not text.strip():
            return False
        started_at = time.monotonic()

        from ApplicationServices import AXIsProcessTrustedWithOptions, kAXTrustedCheckOptionPrompt
        from Quartz import (
            CGEventCreateKeyboardEvent,
            CGEventPost,
            CGEventSetFlags,
            kCGEventFlagMaskCommand,
            kCGHIDEventTap,
        )

        if not AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: False}):
            logging.error("Cannot paste transcript because Accessibility permission is not granted")
            return False

        previous = None
        if self.preserve_clipboard:
            try:
                previous = pyperclip.paste()
            except Exception as exc:
                logging.warning("Could not read clipboard before paste: %s", exc)

        try:
            pyperclip.copy(text)
            time.sleep(0.12)

            key_down = CGEventCreateKeyboardEvent(None, 9, True)
            key_up = CGEventCreateKeyboardEvent(None, 9, False)
            CGEventSetFlags(key_down, kCGEventFlagMaskCommand)
            CGEventSetFlags(key_up, kCGEventFlagMaskCommand)
            CGEventPost(kCGHIDEventTap, key_down)
            CGEventPost(kCGHIDEventTap, key_up)
            logging.info(
                "Posted transcript paste event (%d characters) in %.3fs",
                len(text),
                time.monotonic() - started_at,
            )
        except Exception:
            logging.exception("Could not paste transcript")
            return False

        if previous is not None:
            threading.Thread(
                target=self._restore_clipboard,
                args=(previous, text),
                daemon=True,
            ).start()
        return True

    @staticmethod
    def _restore_clipboard(previous: str, inserted_text: str) -> None:
        time.sleep(0.65)
        try:
            if pyperclip.paste() != inserted_text:
                logging.info("Clipboard changed after dictation; preserving the newer contents")
                return
            pyperclip.copy(previous)
            logging.info("Restored clipboard after dictation")
        except Exception as exc:
            logging.warning("Could not restore clipboard after paste: %s", exc)


class SoundCues:
    def __init__(self) -> None:
        self.enabled = True
        self.primed = False
        self.start_sound = self._load_sound("listening-start.wav", volume=0.23)
        self.stop_sound = self._load_sound("listening-stop.wav", volume=0.19)

    def prime(self) -> None:
        if self.primed or not self.enabled or self.start_sound is None:
            return
        try:
            volume = self.start_sound.volume()
            self.start_sound.setVolume_(0.0)
            self.start_sound.play()
            time.sleep(0.025)
            self.start_sound.stop()
            self.start_sound.setCurrentTime_(0.0)
            self.start_sound.setVolume_(volume)
            self.primed = True
        except Exception as exc:
            logging.debug("Could not prime sound cues: %s", exc)

    def play_start(self) -> None:
        self._play(self.start_sound)

    def play_stop(self) -> None:
        self._play(self.stop_sound)

    @staticmethod
    def _load_sound(filename: str, volume: float):
        try:
            from AVFoundation import AVAudioPlayer
            from Foundation import NSData
        except Exception as exc:  # pragma: no cover - depends on macOS frameworks
            logging.debug("AVAudioPlayer unavailable: %s", exc)
            return None

        try:
            wav_bytes = files("jarvis_dictation").joinpath("assets", "sounds", filename).read_bytes()
            data = NSData.dataWithBytes_length_(wav_bytes, len(wav_bytes))
            player, error = AVAudioPlayer.alloc().initWithData_error_(data, None)
            if player is None:
                raise RuntimeError(str(error or "unknown AVAudioPlayer error"))
            player.setVolume_(volume)
            player.prepareToPlay()
            return player
        except Exception as exc:
            logging.debug("Could not load sound cue `%s`: %s", filename, exc)
            return None

    def _play(self, sound) -> None:  # noqa: ANN001
        if not self.enabled or sound is None:
            return
        try:
            sound.setCurrentTime_(0.0)
            sound.playAtTime_(sound.deviceCurrentTime() + 0.005)
        except Exception as exc:
            logging.debug("Could not play sound cue: %s", exc)


class AudioRecorder:
    def __init__(
        self,
        audio_queue: queue.Queue[np.ndarray],
        sample_rate: int = SAMPLE_RATE,
        on_level: Callable[[float], None] | None = None,
        device_name: str | None = None,
    ) -> None:
        self.audio_queue = audio_queue
        self.sample_rate = sample_rate
        self.on_level = on_level
        self.device_name = device_name
        self.stream: Optional[sd.InputStream] = None

    def start(self) -> None:
        device = resolve_input_device(self.device_name)
        if self.device_name and device is not None:
            logging.info("Using microphone: %s", self.device_name)
        elif self.device_name:
            logging.info("Using system-default microphone because the selected device is unavailable")
        self.stream = sd.InputStream(
            samplerate=self.sample_rate,
            channels=CHANNELS,
            dtype="float32",
            blocksize=int(self.sample_rate * 0.05),
            callback=self._callback,
            device=device,
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
            transcription_started_at = time.monotonic()
            final_text = self.transcriber.accept_audio(audio, final=True)
            audio_duration = audio.size / SAMPLE_RATE
            logging.info(
                "Final transcription completed in %.3fs for %.2fs of audio",
                time.monotonic() - transcription_started_at,
                audio_duration,
            )
        except Exception:
            logging.exception("Final transcription failed")
            final_text = ""
        self.on_final(final_text.strip())


class DictationController:
    def __init__(
        self,
        overlay: FloatingOverlay,
        on_state: Callable[[str], None] | None = None,
        show_overlay: bool = True,
        play_sounds: bool = True,
        preserve_clipboard: bool = True,
        input_device: str | None = None,
    ) -> None:
        self.overlay = overlay
        self.on_state = on_state
        self.show_overlay = show_overlay
        self.input_device = input_device
        self.lock = threading.Lock()
        self.model_lock = threading.Lock()
        self.loading = False
        self.recording = False
        self.finalizing = False
        self.cancel_start = False
        self.shutting_down = False
        self.transcriber = None
        self.audio_queue: Optional[queue.Queue[np.ndarray]] = None
        self.recorder: Optional[AudioRecorder] = None
        self.session: Optional[DictationSession] = None
        self.paster = ClipboardPaster(preserve_clipboard=preserve_clipboard)
        self.sounds = SoundCues()
        self.sounds.enabled = play_sounds
        self.sounds.prime()

    def apply_preferences(
        self,
        *,
        show_overlay: bool,
        play_sounds: bool,
        preserve_clipboard: bool,
        input_device: str | None,
    ) -> None:
        self.show_overlay = show_overlay
        was_enabled = self.sounds.enabled
        self.sounds.enabled = play_sounds
        if play_sounds and not was_enabled:
            self.sounds.prime()
        self.paster.preserve_clipboard = preserve_clipboard
        self.input_device = input_device

    def disconnect_transcriber(self) -> None:
        with self.model_lock:
            transcriber = self.transcriber
            self.transcriber = None
        if transcriber is not None and hasattr(transcriber, "close"):
            transcriber.close()

    def _set_state(self, state: str) -> None:
        if self.on_state is not None:
            self.on_state(state)

    def _show_overlay(self, text: str) -> None:
        if self.show_overlay:
            self.overlay.show(text)

    def _update_overlay(self, text: str) -> None:
        if self.show_overlay:
            self.overlay.update(text)

    def _hide_overlay(self) -> None:
        self.overlay.hide()

    def preload(self) -> None:
        threading.Thread(target=self._preload_model, daemon=True).start()

    def _preload_model(self) -> None:
        try:
            self._ensure_transcriber()
            logging.info("Speech transcriber is ready")
            self._set_state("ready")
        except Exception as exc:
            logging.warning("Background transcriber connection failed: %s", exc)
            self._set_state("offline")

    def _ensure_transcriber(self):
        with self.model_lock:
            if self.transcriber is None:
                from jarvis_dictation.model_server import RemoteParakeetTranscriber

                self.transcriber = RemoteParakeetTranscriber()
            return self.transcriber

    def toggle(self) -> None:
        with self.lock:
            if self.shutting_down:
                return
            if self.finalizing:
                return
            should_stop = self.recording or self.loading
        if should_stop:
            self.request_stop()
        else:
            self.request_start()

    def request_start(self) -> None:
        with self.lock:
            if self.shutting_down or self.loading or self.recording or self.finalizing:
                return
            self.loading = True
            self.cancel_start = False
        self.sounds.play_start()
        threading.Thread(target=self._start_recording, daemon=True).start()

    def request_stop(self) -> None:
        threading.Thread(target=self.stop, daemon=True).start()

    def start(self) -> None:
        self.request_start()

    def _start_recording(self) -> None:
        self._set_state("connecting")
        self._show_overlay("Connecting to local model...")
        try:
            transcriber = self._ensure_transcriber()
        except Exception:
            logging.exception("Could not connect to ASR model")
            self._show_overlay("Start the model server first.")
            self._set_state("offline")
            with self.lock:
                self.loading = False
            return

        with self.lock:
            if self.cancel_start:
                self.loading = False
                self.cancel_start = False
                self._hide_overlay()
                return

            self.audio_queue = queue.Queue()
            self.recorder = AudioRecorder(
                self.audio_queue,
                on_level=self.overlay.update_level,
                device_name=self.input_device,
            )
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
            self._show_overlay("Could not start microphone. Check permissions.")
            self._set_state("error")
            with self.lock:
                self.loading = False
            return

        with self.lock:
            self.loading = False
            self.recording = True
        self._set_state("recording")
        self._show_overlay("Listening...")

    def stop(self) -> None:
        with self.lock:
            if self.loading and not self.recording:
                self.cancel_start = True
                return
            if not self.recording:
                return
            self.recording = False
            self.finalizing = True
            recorder = self.recorder
            session = self.session

        self._set_state("finalizing")
        self._update_overlay("Finalizing...")
        self.sounds.play_stop()
        if recorder is not None:
            recorder.stop()
        if session is not None:
            session.stop()
            session.join()

    def _handle_final_text(self, text: str) -> None:
        self.overlay.update_level(0.0)
        if not text:
            logging.warning("Final transcription was empty")
            self._hide_overlay()
            with self.lock:
                self.finalizing = False
            self._set_state("ready")
            return

        logging.info("Final transcript ready (%d characters)", len(text))
        self._hide_overlay()
        if self.paster.paste_text_preserving_clipboard(text):
            with self.lock:
                self.finalizing = False
            self._set_state("ready")
            return

        with self.lock:
            self.finalizing = False
        self._show_overlay("Allow Accessibility to paste.")
        self._set_state("permission")

    def shutdown(self) -> None:
        with self.lock:
            self.shutting_down = True
            recorder = self.recorder
            session = self.session
            self.recording = False
            self.loading = False
            self.finalizing = False
        if recorder is not None:
            recorder.stop()
        if session is not None:
            session.stop()
        self.disconnect_transcriber()


class RightCommandHotkey:
    def __init__(
        self,
        on_toggle: Callable[[], None],
        on_start: Callable[[], None] | None = None,
        on_stop: Callable[[], None] | None = None,
        activation_mode: str = "toggle",
        shortcut: str = DEFAULT_SHORTCUT,
    ) -> None:
        self.on_toggle = on_toggle
        self.on_start = on_start or on_toggle
        self.on_stop = on_stop or on_toggle
        self.activation_mode = activation_mode
        self.shortcut = normalize_shortcut(shortcut)
        self.down = False
        self.capture_callback: Callable[[str | None], None] | None = None
        self.capture_lock = threading.Lock()
        self.listener: Optional[keyboard.Listener] = None

    def set_activation_mode(self, activation_mode: str) -> None:
        self.activation_mode = "hold" if activation_mode == "hold" else "toggle"

    def set_shortcut(self, shortcut: str) -> None:
        self.shortcut = normalize_shortcut(shortcut)
        self.down = False

    def begin_capture(self, callback: Callable[[str | None], None]) -> None:
        with self.capture_lock:
            self.capture_callback = callback
        self.down = False

    def cancel_capture(self) -> None:
        with self.capture_lock:
            callback = self.capture_callback
            self.capture_callback = None
        if callback is not None:
            callback(None)

    def start(self) -> None:
        self.listener = keyboard.Listener(on_press=self._on_press, on_release=self._on_release)
        self.listener.start()

    def stop(self) -> None:
        if self.listener is not None:
            self.listener.stop()

    def _on_press(self, key) -> None:  # noqa: ANN001
        with self.capture_lock:
            capture_callback = self.capture_callback
            if capture_callback is not None:
                self.capture_callback = None

        if capture_callback is not None:
            if key == keyboard.Key.esc:
                capture_callback(None)
                return
            shortcut = serialize_key(key)
            if shortcut is None:
                with self.capture_lock:
                    self.capture_callback = capture_callback
                return
            capture_callback(shortcut)
            return

        if shortcut_matches(key, self.shortcut) and not self.down:
            self.down = True
            if self.activation_mode == "hold":
                self.on_start()
            else:
                self.on_toggle()

    def _on_release(self, key) -> None:  # noqa: ANN001
        with self.capture_lock:
            if self.capture_callback is not None:
                return
        if shortcut_matches(key, self.shortcut):
            if self.down and self.activation_mode == "hold":
                self.on_stop()
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
    hotkey = RightCommandHotkey(
        controller.toggle,
        on_start=controller.request_start,
        on_stop=controller.request_stop,
    )
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

    logging.info("Jarvis dictation is running. Press %s to toggle.", shortcut_display_name(DEFAULT_SHORTCUT))
    try:
        overlay.run()
    finally:
        request_shutdown("exit", None)


if __name__ == "__main__":
    main()
