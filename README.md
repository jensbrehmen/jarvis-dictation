# Jarvis Dictation MVP

Fully local macOS background dictation from the terminal.

## Behavior

- Press Right Command once to start dictation.
- A polished bottom-center overlay appears.
- While recording, animated input wave bars update in the overlay.
- Press Right Command again to stop.
- The final transcript is pasted into the currently focused input field.
- The previous clipboard contents are restored after paste.

## Model

This uses MLX locally with Parakeet models from Hugging Face. No cloud APIs are used.

The app records locally and transcribes the full utterance once when you stop. The model server stays warm in the background so this final pass starts quickly.

## Setup

```bash
cd /Users/jensbrehmen/Documents/Jarvis
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
```

Use Python 3.10, 3.11, or 3.12. MLX/parakeet-mlx are not a good bet on Python 3.13 yet.

If you used an earlier version of this MVP with other ASR dependencies, the cleanest path is to recreate `.venv` and reinstall with the commands above. Also reinstall the LaunchAgent so it points at the current MLX-only server command:

```bash
jarvis-dictation-service uninstall
jarvis-dictation-service install
```

If `sounddevice` cannot find PortAudio:

```bash
brew install portaudio
python -m pip install --force-reinstall sounddevice
```

## Prepare Once

First ask macOS for the runtime permissions:

```bash
source .venv/bin/activate
jarvis-dictation-permissions
```

If macOS already has a denied microphone decision saved, it may refuse to ask again. Reset that saved decision, quit and reopen your terminal app, then run the permissions command again:

```bash
tccutil reset Microphone
```

Run this before regular use:

```bash
source .venv/bin/activate
jarvis-dictation-prepare
```

This separate preparation step:

- downloads/caches the selected MLX Parakeet model
- initializes MLX once
- checks the default microphone device
- does a short silent transcription smoke test
- writes a local preparation marker to `~/Library/Application Support/JarvisDictation/prepared.json`

The first preparation run can be slow because it downloads the model. Later runs should be much faster.

## Run Smoothly

Start everything with one command:

```bash
source .venv/bin/activate
jarvis-dictation-run
```

This starts the persistent model server, waits until it is reachable, then starts the hotkey/overlay app. When the app exits, the server started by this command is stopped too.

To leave the server running after the app exits:

```bash
jarvis-dictation-run --keep-server
```

For the fastest startup, install the model server as a macOS user LaunchAgent:

```bash
jarvis-dictation-service install
```

This starts the model server at login, keeping Parakeet warm in the background. After that, starting the overlay is quick:

```bash
jarvis-dictation
```

LaunchAgent helpers:

```bash
jarvis-dictation-service status
jarvis-dictation-service stop
jarvis-dictation-service start
jarvis-dictation-service uninstall
```

You can still run the two pieces manually if you want to inspect logs separately. Start the server first:

```bash
jarvis-dictation-server
```

Wait until it logs:

```text
Model server ready
```

Then start the hotkey/overlay app in another terminal:

```bash
cd /Users/jensbrehmen/Documents/Jarvis
source .venv/bin/activate
jarvis-dictation
```

Now the app connects to the already-loaded local model instead of loading Parakeet itself.

Default dictation behavior is tuned for reliability:

- microphone blocks: 50 ms
- no live transcript decode while recording
- the full recorded utterance is transcribed once when you press Right Command again
- the overlay still shows live microphone wave bars

## MLX Model

The app supports three MLX model presets:

- `default`: `mlx-community/parakeet-tdt-0.6b-v3`, larger multilingual model, best current default quality.
- `small-en`: `mlx-community/parakeet-tdt_ctc-110m`, smaller English model, lower RAM target, likely lower quality.
- `nemotron`: `mlx-community/nemotron-3.5-asr-streaming-0.6b-8bit`, quantized multilingual Nemotron 3.5 ASR through MLX Audio.

Prepare and run the small English model:

```bash
jarvis-dictation-prepare --model-preset small-en
jarvis-dictation-run --model-preset small-en
```

Start only the small English server:

```bash
jarvis-dictation-server --model-preset small-en
```

Install the small English model as the login server:

```bash
jarvis-dictation-service uninstall
jarvis-dictation-service install --model-preset small-en
```

All presets transcribe the final recorded utterance only, matching the app's normal dictation flow.

### Nemotron 3.5

Nemotron requires MLX Audio's current GitHub version because its ASR support has not shipped in the latest PyPI release yet:

```bash
python -m pip install -e '.[nemotron]'
```

Then prepare and run it:

```bash
jarvis-dictation-prepare --model-preset nemotron
jarvis-dictation-run --model-preset nemotron
```

Or install it as the persistent login server:

```bash
jarvis-dictation-service uninstall
jarvis-dictation-service install --model-preset nemotron
```

Nemotron is architected for streaming, but this preset intentionally keeps the app's existing reliable behavior: record the utterance, then run one final transcription pass after Right Command is pressed again.

#### Nemotron Specs and Performance

| Specification | Value |
| --- | --- |
| Parameters | 600M |
| Architecture | Cache-aware FastConformer encoder with RNN-T decoder |
| MLX checkpoint | 8-bit weights, group size 64 |
| Model download | Approximately 756 MB |
| Language coverage | 40 language-locales |
| Streaming chunks | 80, 160, 320, 560, or 1,120 ms |
| Output formatting | Punctuation and capitalization |

Measured on the development MacBook with the persistent Nemotron server loaded:

| Measurement | Result |
| --- | --- |
| Idle physical footprint | Approximately 1.1 GB |
| Observed peak physical footprint | Approximately 1.9 GB |
| MLX/Metal allocation | Approximately 765 MB |
| Idle CPU | Approximately 0% |

These figures were measured with macOS `footprint` and `vmmap`. Normal `ps` RSS can substantially under-report MLX memory because model weights and inference buffers are allocated through Metal and Apple unified memory.

NVIDIA reports up to 240 simultaneous streams at the 80 ms setting on one H100, compared with 14 streams for Parakeet RNNT 1.1B. This is a server-throughput comparison, not a claim that a single transcription is 17 times faster on a MacBook. Published English FLEURS word error rates range from 9.43% at 80 ms chunks to 7.91% at 1,120 ms chunks when the language is supplied.

References:

- [MLX 8-bit model card](https://huggingface.co/mlx-community/nemotron-3.5-asr-streaming-0.6b-8bit)
- [Official NVIDIA model and benchmarks](https://huggingface.co/nvidia/nemotron-3.5-asr-streaming-0.6b)
- [MLX Audio Nemotron implementation](https://github.com/Blaizzy/mlx-audio/blob/main/mlx_audio/stt/models/nemotron_asr/README.md)

The `sonic-speech` INT8/INT4 checkpoints are interesting, but the current `parakeet-mlx` loader rejects their quantization tensors. Use them only with a loader version that explicitly supports those checkpoints:

```bash
jarvis-dictation-server --model-name <compatible-model-id-or-local-path>
```

Useful server commands:

```bash
jarvis-dictation-server --status
jarvis-dictation-server --stop
```

`Ctrl-C` should also stop the server terminal. If it is in the middle of heavy model initialization, the stop command from another terminal is usually more reliable.

## macOS Permissions

Run `jarvis-dictation-permissions` to let macOS ask for permissions where it can.

- Privacy & Security -> Microphone: required for `sounddevice` microphone input.
- Privacy & Security -> Accessibility: required for global hotkey listening and synthetic Cmd+V paste.
- Privacy & Security -> Input Monitoring: required for reliable global Right Command detection.

macOS does not always show an Input Monitoring prompt for terminal Python processes, so add your terminal app manually there if Right Command is not detected.

## Notes

- Audio is captured as 16 kHz mono float32.
- Right Command is handled with `pynput`.
- The floating overlay uses PyObjC/AppKit.
- Text insertion uses `pyperclip` and synthetic Cmd+V, then restores the clipboard.
- Packaging is intentionally skipped until the terminal MVP is known-good on the target Mac.
