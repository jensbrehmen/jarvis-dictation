from __future__ import annotations

from dataclasses import dataclass


RECOMMENDED_MODEL_PRESET = "nemotron"
DEFAULT_MODEL_PRESET = RECOMMENDED_MODEL_PRESET
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
MODEL_ENGINE_LABELS = {
    "mlx-audio": "MLX Audio (Nemotron ASR)",
    "parakeet-mlx": "Parakeet MLX",
}


@dataclass(frozen=True)
class ModelSpec:
    key: str
    title: str
    model_name: str
    engine: str
    description: str
    detail: str
    built_in: bool = False


BUILTIN_MODEL_SPECS = {
    "nemotron": ModelSpec(
        key="nemotron",
        title="Nemotron 3.5",
        model_name=MODEL_PRESETS["nemotron"],
        engine=MODEL_ENGINES["nemotron"],
        description="Fast multilingual 8-bit model",
        detail="~1.1 GB loaded",
        built_in=True,
    ),
    "default": ModelSpec(
        key="default",
        title="Parakeet 0.6B",
        model_name=MODEL_PRESETS["default"],
        engine=MODEL_ENGINES["default"],
        description="High-quality multilingual model",
        detail="~2.5 GB weights",
        built_in=True,
    ),
    "small-en": ModelSpec(
        key="small-en",
        title="Parakeet 110M",
        model_name=MODEL_PRESETS["small-en"],
        engine=MODEL_ENGINES["small-en"],
        description="Compact English-only model",
        detail="~459 MB weights",
        built_in=True,
    ),
}


def resolve_model_name(model_preset: str = DEFAULT_MODEL_PRESET, model_name: str | None = None) -> str:
    if model_name:
        return model_name
    try:
        return MODEL_PRESETS[model_preset]
    except KeyError as exc:
        valid = ", ".join(sorted(MODEL_PRESETS))
        raise ValueError(f"Unknown model preset `{model_preset}`. Valid presets: {valid}") from exc


def resolve_model_engine(
    model_preset: str = DEFAULT_MODEL_PRESET,
    model_name: str | None = None,
    model_engine: str | None = None,
) -> str:
    if model_engine:
        if model_engine not in MODEL_ENGINE_LABELS:
            valid = ", ".join(sorted(MODEL_ENGINE_LABELS))
            raise ValueError(f"Unknown model engine `{model_engine}`. Valid engines: {valid}")
        return model_engine
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


def builtin_model_spec(model_preset: str) -> ModelSpec:
    try:
        return BUILTIN_MODEL_SPECS[model_preset]
    except KeyError as exc:
        valid = ", ".join(sorted(BUILTIN_MODEL_SPECS))
        raise ValueError(f"Unknown model preset `{model_preset}`. Valid presets: {valid}") from exc


def custom_model_spec(payload: dict) -> ModelSpec | None:
    try:
        key = str(payload["key"]).strip()
        title = str(payload["title"]).strip()
        model_name = str(payload["model_name"]).strip()
        engine = str(payload["engine"]).strip()
    except (KeyError, TypeError):
        return None

    if not key.startswith("custom:") or not title or not model_name or engine not in MODEL_ENGINE_LABELS:
        return None

    return ModelSpec(
        key=key,
        title=title,
        model_name=model_name,
        engine=engine,
        description=f"Custom Hugging Face model via {MODEL_ENGINE_LABELS[engine]}",
        detail="Downloaded on first use",
    )
