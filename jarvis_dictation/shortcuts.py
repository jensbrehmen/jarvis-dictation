from __future__ import annotations

from pynput import keyboard


DEFAULT_SHORTCUT = "key:cmd_r"
LEGACY_SHORTCUTS = {
    "right_command": "key:cmd_r",
    "right_option": "key:alt_r",
    "right_control": "key:ctrl_r",
    "f8": "key:f8",
    "f9": "key:f9",
}
SPECIAL_KEY_LABELS = {
    "alt": "Option",
    "alt_l": "Left Option",
    "alt_r": "Right Option",
    "backspace": "Delete",
    "cmd": "Command",
    "cmd_l": "Left Command",
    "cmd_r": "Right Command",
    "ctrl": "Control",
    "ctrl_l": "Left Control",
    "ctrl_r": "Right Control",
    "esc": "Escape",
    "page_down": "Page Down",
    "page_up": "Page Up",
    "shift": "Shift",
    "shift_l": "Left Shift",
    "shift_r": "Right Shift",
    "space": "Space",
}


def serialize_key(key) -> str | None:  # noqa: ANN001
    if isinstance(key, keyboard.Key):
        return f"key:{key.name}"

    char = getattr(key, "char", None)
    if char:
        return f"char:{char.lower()}"

    vk = getattr(key, "vk", None)
    if vk is not None:
        return f"vk:{int(vk)}"
    return None


def deserialize_shortcut(shortcut: str):
    shortcut = normalize_shortcut(shortcut)
    kind, value = shortcut.split(":", 1)
    if kind == "key":
        return getattr(keyboard.Key, value)
    if kind == "char":
        return keyboard.KeyCode.from_char(value)
    return keyboard.KeyCode.from_vk(int(value))


def normalize_shortcut(shortcut: str | None) -> str:
    value = LEGACY_SHORTCUTS.get(str(shortcut or ""), str(shortcut or ""))
    try:
        kind, payload = value.split(":", 1)
        if kind == "key" and isinstance(getattr(keyboard.Key, payload), keyboard.Key):
            return value
        if kind == "char" and payload:
            return f"char:{payload.lower()}"
        if kind == "vk":
            int(payload)
            return value
    except (AttributeError, TypeError, ValueError):
        pass
    return DEFAULT_SHORTCUT


def shortcut_display_name(shortcut: str) -> str:
    kind, value = normalize_shortcut(shortcut).split(":", 1)
    if kind == "char":
        return value.upper()
    if kind == "vk":
        return f"Key {value}"
    if value in SPECIAL_KEY_LABELS:
        return SPECIAL_KEY_LABELS[value]
    if value.startswith("f") and value[1:].isdigit():
        return value.upper()
    return value.replace("_", " ").title()


def shortcut_matches(key, shortcut: str) -> bool:  # noqa: ANN001
    captured = serialize_key(key)
    return captured is not None and normalize_shortcut(captured) == normalize_shortcut(shortcut)
