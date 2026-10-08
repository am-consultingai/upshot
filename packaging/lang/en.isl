; Upshot's own installer messages (English). Loaded after Inno's own translation
; (packaging/installer.iss [Languages]). UTF-8 with a BOM: Inno reads a file without
; one as the ANSI code page. %1 is an argument, %n a line break.

[CustomMessages]
RamTooLow=Upshot needs a computer with at least 12 GB of memory to transcribe meetings on it.%n%nThis computer has %1 GB, so Upshot will not be installed.
PrepareTitle=Getting Upshot ready to transcribe
PrepareLead=Upshot transcribes on this computer, so it needs its speech models (about 5.2 GB). This is a one-time download.
PrepareStarting=Starting the download...
HintModel=Speech recognition for Hebrew (ivrit-ai) and every other language (Whisper), from Hugging Face.
HintGpu=NVIDIA cuBLAS and cuDNN, so transcription runs on your graphics card.
NoteNotStarted=The speech models were not downloaded, so Upshot cannot transcribe yet. Run the installer again to finish.
NotePaused=The speech model download was paused, so Upshot cannot transcribe yet. Run the installer again to continue it.
NoteNoSpace=There was not enough free disk space for the speech models (about 6.3 GB with room to spare). Free some space, then run the installer again to finish.
NoteGpuFailed=The speech models are ready. The GPU libraries could not be downloaded, so Upshot transcribes on the processor for now.
NoteFailed=The speech models could not be downloaded, so the installation is incomplete and Upshot cannot transcribe yet. Run the installer again to finish; it continues where it stopped.
RemoveModels=the speech models (about 5.2 GB)
RemoveModelsAndGpu=the speech models and the GPU libraries (about 7.5 GB)
RemoveAsk=Also remove %1?%n%nYour recordings, transcripts and settings are kept either way. Keep the model if you may reinstall Upshot.
StatusFirstRun=Preparing first run...
StatusPreparing=Preparing...
TermsInEnglish=
