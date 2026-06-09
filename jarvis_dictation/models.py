from __future__ import annotations


DEFAULT_MODEL_PRESET = "default"
MODEL_PRESETS = {
    "default": "mlx-community/parakeet-tdt-0.6b-v3",
    "small-en": "mlx-community/parakeet-tdt_ctc-110m",
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
