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

To try it on Linux with fake audio, transcription and summaries:

```bash
bash scripts/demo.sh
```

## Acknowledgements

Upshot's Hebrew transcription is built on the speech-recognition models published by
[ivrit.ai](https://www.ivrit.ai/), a non-profit effort to make Hebrew a first-class
language for AI. Thank you to the ivrit.ai team and to everyone who contributed
recordings and transcriptions to their datasets.

How Upshot uses them:

- **Which model.** Upshot transcribes Hebrew meetings with
  [`ivrit-ai/whisper-large-v3-ct2`](https://huggingface.co/ivrit-ai/whisper-large-v3-ct2),
  on an NVIDIA GPU or on the CPU. Meetings in any other language go to OpenAI's Whisper
  large-v3 ([`Systran/faster-whisper-large-v3`](https://huggingface.co/Systran/faster-whisper-large-v3)),
  and Whisper small ([`Systran/faster-whisper-small`](https://huggingface.co/Systran/faster-whisper-small))
  first tells which language a meeting was in. All of it runs
  locally through [faster-whisper](https://github.com/SYSTRAN/faster-whisper), so no
  audio is sent anywhere to be transcribed.
- **How it gets there.** The models are not part of this repository or the application.
  The installer downloads all three from Hugging Face onto the user's machine, about
  6.7 GB, each pinned to a revision and checked file by file. Nothing is downloaded while
  transcribing.
- **Unmodified.** Upshot loads the published weights as they are.

The ivrit.ai models are released under the
[Apache License 2.0](https://www.apache.org/licenses/LICENSE-2.0) and are fine-tuned
from OpenAI's [Whisper](https://github.com/openai/whisper). The two stock Whisper models
are OpenAI's weights converted for faster-whisper by SYSTRAN, released under the MIT
licence.
