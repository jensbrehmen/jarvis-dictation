from __future__ import annotations


DEFAULT_MODEL_PRESET = "nemotron"
MODEL_PRESETS = {
    "default": "mlx-community/parakeet-tdt-0.6b-v3",
    "nemotron": "mlx-community/nemotron-3.5-asr-streaming-0.6b-8bit",
    "small-en": "mlx-community/parakeet-tdt_ctc-110m",
}
MODEL_ENGINES = {
    "default": "parakeet-mlx",
    "nemotron": "mlx-audio",
    "small-en": "parakeet-mlx",
}
MLX_MODEL_NAME = MODEL_PRESETS[DEFAULT_MODEL_PRESET]


def resolve_model_name(model_preset: str = DEFAULT_MODEL_PRESET, model_name: str | None = None) -> str:
    if model_name:
        return model_name
    try:
        return MODEL_PRESETS[model_preset]
    except KeyError as exc:
        valid = ", ".join(sorted(MODEL_PRESETS))
        raise ValueError(f"Unknown model preset `{model_preset}`. Valid presets: {valid}") from exc


def resolve_model_engine(model_preset: str = DEFAULT_MODEL_PRESET, model_name: str | None = None) -> str:
    if model_name and "nemotron" in model_name.lower():
        return "mlx-audio"
    try:
        return MODEL_ENGINES[model_preset]
    except KeyError as exc:
        valid = ", ".join(sorted(MODEL_PRESETS))
        raise ValueError(f"Unknown model preset `{model_preset}`. Valid presets: {valid}") from exc


def preset_for_model_name(model_name: str) -> str | None:
    for preset, preset_model_name in MODEL_PRESETS.items():
        if preset_model_name == model_name:
            return preset
    return None
