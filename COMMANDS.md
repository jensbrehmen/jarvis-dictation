# Jarvis Dictation — Command Reference

## Activate the environment

```bash
source /Users/jensbrehmen/Documents/Jarvis/.venv/bin/activate
```

---

## One-command startup (recommended)

Starts the model server, waits for it to be ready, then launches the hotkey/overlay app.
When the app exits, the server is stopped automatically.

```bash
jarvis-dictation-run
```

Keep the server running after the app exits:

```bash
jarvis-dictation-run --keep-server
```

---

## Native menu-bar application

Run the native AppKit application from the environment:

```bash
jarvis-dictation-mac
```

Build and open the local alias-mode application bundle:

```bash
python setup_app.py py2app -A
open "dist/Jarvis Dictation.app"
```

The menu-bar app starts the selected model, owns the hotkey and overlay, and provides a native settings window.

---

## Model presets

Three built-in presets:

| Preset | Model | Size | Notes |
|---|---|---|---|
| `default` | `mlx-community/parakeet-tdt-0.6b-v3` | ~2.5 GB weights | Strong multilingual quality |
| `small-en` | `mlx-community/parakeet-tdt_ctc-110m` | ~459 MB weights | Faster, lower memory, English only |
| `nemotron` | `mlx-community/nemotron-3.5-asr-streaming-0.6b-8bit` | ~756 MB weights | Fast 8-bit multilingual model |

Run with a specific preset:

```bash
jarvis-dictation-run --model-preset small-en
jarvis-dictation-run --model-preset default
jarvis-dictation-run --model-preset nemotron
```

Use a custom Hugging Face model or local path:

```bash
jarvis-dictation-run --model-name mlx-community/my-custom-model
```

---

## Prepare a model (first-time / after switching presets)

Downloads the model, runs a smoke test, and writes a preparation marker.

```bash
jarvis-dictation-prepare
jarvis-dictation-prepare --model-preset small-en
jarvis-dictation-prepare --model-preset nemotron
```

---

## Run server and client separately

**Terminal 1 — start the model server:**

```bash
jarvis-dictation-server
# Wait for: "Model server ready"
```

```bash
jarvis-dictation-server --model-preset small-en
jarvis-dictation-server --model-preset nemotron
```

**Terminal 2 — start the overlay app:**

```bash
jarvis-dictation
```

**Server control commands:**

```bash
jarvis-dictation-server --status
jarvis-dictation-server --stop
```

---

## Install as a login service (fastest startup)

Registers the model server as a macOS LaunchAgent so it starts automatically at login and stays warm in the background.

```bash
jarvis-dictation-service install
jarvis-dictation-service install --model-preset small-en
jarvis-dictation-service install --model-preset nemotron
```

After installing, just run the overlay app directly — no need to start the server manually:

```bash
jarvis-dictation
```

Other service commands:

```bash
jarvis-dictation-service status
jarvis-dictation-service start
jarvis-dictation-service stop
jarvis-dictation-service uninstall
```

---

## Permissions (run once)

```bash
jarvis-dictation-permissions
```

If microphone permission was previously denied and macOS won't ask again:

```bash
tccutil reset Microphone
# Then quit and reopen your terminal, then run jarvis-dictation-permissions again
```

---

## Debug mode

```bash
jarvis-dictation-run --debug
jarvis-dictation-server --debug
jarvis-dictation --debug
jarvis-dictation-mac --debug
```

---

## Performance / memory tradeoffs

| Goal | Command |
|---|---|
| Reduce memory usage | `--model-preset small-en` |
| Fast multilingual MLX model | `--model-preset nemotron` |
| Fastest startup after reboot | `jarvis-dictation-service install` |
| Keep server warm between sessions | `jarvis-dictation-run --keep-server` |
| See what model the server is using | `jarvis-dictation-server --status` |
