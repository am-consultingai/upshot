# Upshot

A local Windows application that records your meetings, transcribes them on your own
computer and writes a summary in the format you choose. No bot joins the call, and the
recording never leaves the machine. Hebrew and English.

## Run it

On Windows, double-click:

```
scripts\windows\run-app.cmd
```

It works out what needs downloading, asks once, then starts the app and opens it in the
browser. Everything it downloads stays in `%LOCALAPPDATA%\upshot-win`.
[`docs/windows-run.md`](docs/windows-run.md) has the details.

To try it on Linux with fake audio, transcription and summaries:

```bash
bash scripts/demo.sh
```

## Documentation

- [`docs/current-state.md`](docs/current-state.md): what works today and what is not proven yet
- [`docs/DESIGN.md`](docs/DESIGN.md): what the application is and why it is built this way
- [`docs/DECISIONS.md`](docs/DECISIONS.md): every judgment call, with its reasoning
- [`docs/known-issues.md`](docs/known-issues.md): open problems

## Acknowledgements

Upshot's Hebrew transcription is built on the speech-recognition models published by
[ivrit.ai](https://www.ivrit.ai/), a non-profit effort to make Hebrew a first-class
language for AI. Thank you to the ivrit.ai team and to everyone who contributed
recordings and transcriptions to their datasets.

How Upshot uses them:

- **Which model.** Upshot transcribes with one model,
  [`ivrit-ai/whisper-large-v3-ct2`](https://huggingface.co/ivrit-ai/whisper-large-v3-ct2),
  on an NVIDIA GPU or on the CPU. It handles Hebrew and English, mixed in one meeting
  too (docs/DECISIONS.md D60). It runs locally through
  [faster-whisper](https://github.com/SYSTRAN/faster-whisper), so no audio is sent
  anywhere to be transcribed.
- **How it gets there.** The model is not part of this repository or the application.
  The first time transcription runs, faster-whisper downloads it from Hugging Face onto
  the user's machine. A model folder already on disk (the `asr.model_path` configuration
  key) is used instead.
- **Unmodified.** Upshot loads the published weights as they are.

The models are released by ivrit.ai under the
[Apache License 2.0](https://www.apache.org/licenses/LICENSE-2.0) and are fine-tuned
from OpenAI's [Whisper](https://github.com/openai/whisper).
