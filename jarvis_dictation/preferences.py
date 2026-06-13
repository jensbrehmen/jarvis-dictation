from __future__ import annotations

import uuid
from dataclasses import dataclass

from jarvis_dictation.models import (
    BUILTIN_MODEL_SPECS,
    DEFAULT_MODEL_PRESET,
    ModelSpec,
    builtin_model_spec,
    custom_model_spec,
)
from jarvis_dictation.shortcuts import DEFAULT_SHORTCUT, normalize_shortcut


PREFERENCES_DOMAIN = "com.jensbrehmen.jarvisdictation"


@dataclass(frozen=True)
class PreferenceDefaults:
    model_preset: str = DEFAULT_MODEL_PRESET
    input_device: str = ""
    shortcut: str = DEFAULT_SHORTCUT
    activation_mode: str = "toggle"
    play_sounds: bool = True
    show_overlay: bool = True
    preserve_clipboard: bool = True
    start_model_on_launch: bool = True


class Preferences:
    PREFERENCES_VERSION = 3
    PREFERENCES_VERSION_KEY = "preferencesVersion"
    MODEL_PRESET_KEY = "modelPreset"
    CUSTOM_MODELS_KEY = "customModels"
    INPUT_DEVICE_KEY = "inputDevice"
    SHORTCUT_KEY = "shortcut"
    ACTIVATION_MODE_KEY = "activationMode"
    PLAY_SOUNDS_KEY = "playSounds"
    SHOW_OVERLAY_KEY = "showOverlay"
    PRESERVE_CLIPBOARD_KEY = "preserveClipboard"
    START_MODEL_ON_LAUNCH_KEY = "startModelOnLaunch"

    def __init__(self, defaults_store=None) -> None:
        from Foundation import NSUserDefaults

        defaults = PreferenceDefaults()
        self.defaults = defaults_store or NSUserDefaults.standardUserDefaults()
        saved_model_preset = self.defaults.objectForKey_(self.MODEL_PRESET_KEY)
        saved_shortcut = self.defaults.objectForKey_(self.SHORTCUT_KEY)
        saved_preferences_version = self.defaults.integerForKey_(self.PREFERENCES_VERSION_KEY)
        self.defaults.registerDefaults_(
            {
                self.MODEL_PRESET_KEY: defaults.model_preset,
                self.CUSTOM_MODELS_KEY: [],
                self.INPUT_DEVICE_KEY: defaults.input_device,
                self.SHORTCUT_KEY: defaults.shortcut,
                self.ACTIVATION_MODE_KEY: defaults.activation_mode,
                self.PLAY_SOUNDS_KEY: defaults.play_sounds,
                self.SHOW_OVERLAY_KEY: defaults.show_overlay,
                self.PRESERVE_CLIPBOARD_KEY: defaults.preserve_clipboard,
                self.START_MODEL_ON_LAUNCH_KEY: defaults.start_model_on_launch,
            }
        )
        if saved_preferences_version < self.PREFERENCES_VERSION:
            if saved_model_preset is None or str(saved_model_preset) == "default":
                self.defaults.setObject_forKey_(DEFAULT_MODEL_PRESET, self.MODEL_PRESET_KEY)
            self.defaults.setObject_forKey_(normalize_shortcut(saved_shortcut), self.SHORTCUT_KEY)
            self.defaults.setInteger_forKey_(self.PREFERENCES_VERSION, self.PREFERENCES_VERSION_KEY)
            self.defaults.synchronize()
            self.has_saved_model_preset = True
        else:
            self.has_saved_model_preset = saved_model_preset is not None

    @property
    def model_selection(self) -> str:
        value = str(self.defaults.stringForKey_(self.MODEL_PRESET_KEY) or DEFAULT_MODEL_PRESET)
        valid_keys = set(BUILTIN_MODEL_SPECS) | {model.key for model in self.custom_models}
        return value if value in valid_keys else DEFAULT_MODEL_PRESET

    @model_selection.setter
    def model_selection(self, value: str) -> None:
        valid_keys = set(BUILTIN_MODEL_SPECS) | {model.key for model in self.custom_models}
        if value not in valid_keys:
            raise ValueError(f"Unknown model selection: {value}")
        self.defaults.setObject_forKey_(value, self.MODEL_PRESET_KEY)

    @property
    def model_preset(self) -> str:
        return self.model_selection

    @model_preset.setter
    def model_preset(self, value: str) -> None:
        self.model_selection = value

    @property
    def custom_models(self) -> list[ModelSpec]:
        payloads = self.defaults.arrayForKey_(self.CUSTOM_MODELS_KEY) or []
        models = []
        for payload in payloads:
            model = custom_model_spec(payload)
            if model is not None:
                models.append(model)
        return models

    @property
    def selected_model(self) -> ModelSpec:
        selection = self.model_selection
        if selection in BUILTIN_MODEL_SPECS:
            return builtin_model_spec(selection)
        for model in self.custom_models:
            if model.key == selection:
                return model
        return builtin_model_spec(DEFAULT_MODEL_PRESET)

    def add_custom_model(self, title: str, model_name: str, engine: str) -> ModelSpec:
        title = title.strip()
        model_name = model_name.strip()
        if not title or not model_name:
            raise ValueError("Custom models require a display name and Hugging Face model ID")

        payloads = [self._model_payload(model) for model in self.custom_models]
        for payload in payloads:
            if payload["model_name"] == model_name and payload["engine"] == engine:
                payload["title"] = title
                self.defaults.setObject_forKey_(payloads, self.CUSTOM_MODELS_KEY)
                model = custom_model_spec(payload)
                if model is None:
                    raise ValueError("Invalid custom model")
                return model

        payload = {
            "key": f"custom:{uuid.uuid4().hex[:12]}",
            "title": title,
            "model_name": model_name,
            "engine": engine,
        }
        model = custom_model_spec(payload)
        if model is None:
            raise ValueError("Invalid custom model or unsupported engine")
        payloads.append(payload)
        self.defaults.setObject_forKey_(payloads, self.CUSTOM_MODELS_KEY)
        return model

    def remove_custom_model(self, key: str) -> bool:
        current = self.custom_models
        remaining = [model for model in current if model.key != key]
        if len(remaining) == len(current):
            return False
        self.defaults.setObject_forKey_([self._model_payload(model) for model in remaining], self.CUSTOM_MODELS_KEY)
        if self.model_selection == key:
            self.model_selection = DEFAULT_MODEL_PRESET
        return True

    @staticmethod
    def _model_payload(model: ModelSpec) -> dict:
        return {
            "key": model.key,
            "title": model.title,
            "model_name": model.model_name,
            "engine": model.engine,
        }

    @property
    def input_device(self) -> str:
        return str(self.defaults.stringForKey_(self.INPUT_DEVICE_KEY) or "")

    @input_device.setter
    def input_device(self, value: str) -> None:
        self.defaults.setObject_forKey_(str(value), self.INPUT_DEVICE_KEY)

    @property
    def shortcut(self) -> str:
        return normalize_shortcut(self.defaults.stringForKey_(self.SHORTCUT_KEY))

    @shortcut.setter
    def shortcut(self, value: str) -> None:
        normalized = normalize_shortcut(value)
        if normalized != value:
            raise ValueError(f"Unknown shortcut: {value}")
        self.defaults.setObject_forKey_(normalized, self.SHORTCUT_KEY)

    @property
    def activation_mode(self) -> str:
        value = str(self.defaults.stringForKey_(self.ACTIVATION_MODE_KEY) or "toggle")
        return value if value in {"toggle", "hold"} else "toggle"

    @activation_mode.setter
    def activation_mode(self, value: str) -> None:
        if value not in {"toggle", "hold"}:
            raise ValueError(f"Unknown activation mode: {value}")
        self.defaults.setObject_forKey_(value, self.ACTIVATION_MODE_KEY)

    @property
    def play_sounds(self) -> bool:
        return bool(self.defaults.boolForKey_(self.PLAY_SOUNDS_KEY))

    @play_sounds.setter
    def play_sounds(self, value: bool) -> None:
        self.defaults.setBool_forKey_(bool(value), self.PLAY_SOUNDS_KEY)

    @property
    def show_overlay(self) -> bool:
        return bool(self.defaults.boolForKey_(self.SHOW_OVERLAY_KEY))

    @show_overlay.setter
    def show_overlay(self, value: bool) -> None:
        self.defaults.setBool_forKey_(bool(value), self.SHOW_OVERLAY_KEY)

    @property
    def preserve_clipboard(self) -> bool:
        return bool(self.defaults.boolForKey_(self.PRESERVE_CLIPBOARD_KEY))

    @preserve_clipboard.setter
    def preserve_clipboard(self, value: bool) -> None:
        self.defaults.setBool_forKey_(bool(value), self.PRESERVE_CLIPBOARD_KEY)

    @property
    def start_model_on_launch(self) -> bool:
        return bool(self.defaults.boolForKey_(self.START_MODEL_ON_LAUNCH_KEY))

    @start_model_on_launch.setter
    def start_model_on_launch(self, value: bool) -> None:
        self.defaults.setBool_forKey_(bool(value), self.START_MODEL_ON_LAUNCH_KEY)

    def synchronize(self) -> None:
        self.defaults.synchronize()
