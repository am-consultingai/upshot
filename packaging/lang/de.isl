; Upshot's own installer messages (German). Loaded after Inno's own translation
; (packaging/installer.iss [Languages]). UTF-8 with a BOM: Inno reads a file without
; one as the ANSI code page. %1 is an argument, %n a line break.

[CustomMessages]
RamTooLow=Upshot benötigt einen Computer mit mindestens 12 GB Arbeitsspeicher, um Besprechungen darauf zu transkribieren.%n%nDieser Computer hat %1 GB, daher wird Upshot nicht installiert.
PrepareTitle=Upshot wird für die Transkription vorbereitet
PrepareLead=Upshot transkribiert auf diesem Computer und benötigt dafür seine Sprachmodelle (etwa 6,7 GB). Sie werden nur einmal heruntergeladen.
PrepareStarting=Download wird gestartet...
HintModel=Spracherkennung für Hebräisch (ivrit-ai) und alle anderen Sprachen (Whisper), von Hugging Face.
HintGpu=NVIDIA cuBLAS und cuDNN, damit die Transkription auf Ihrer Grafikkarte läuft.
NoteNotStarted=Die Sprachmodelle wurden nicht heruntergeladen, daher kann Upshot noch nicht transkribieren. Führen Sie das Installationsprogramm erneut aus, um die Installation abzuschließen.
NotePaused=Der Download der Sprachmodelle wurde angehalten, daher kann Upshot noch nicht transkribieren. Führen Sie das Installationsprogramm erneut aus, um ihn fortzusetzen.
NoteNoSpace=Für die Sprachmodelle war nicht genug freier Speicherplatz vorhanden (etwa 7,7 GB mit Reserve). Geben Sie Speicherplatz frei und führen Sie das Installationsprogramm erneut aus.
NoteGpuFailed=Die Sprachmodelle sind bereit. Die GPU-Bibliotheken konnten nicht heruntergeladen werden, daher transkribiert Upshot vorerst auf dem Prozessor.
NoteFailed=Die Sprachmodelle konnten nicht heruntergeladen werden. Die Installation ist unvollständig, und Upshot kann noch nicht transkribieren. Führen Sie das Installationsprogramm erneut aus; es setzt dort fort, wo es aufgehört hat.
RemoveModels=die Sprachmodelle (etwa 6,7 GB)
RemoveModelsAndGpu=die Sprachmodelle und die GPU-Bibliotheken (etwa 9 GB)
RemoveAsk=Auch %1 entfernen?%n%nIhre Aufnahmen, Transkripte und Einstellungen bleiben in jedem Fall erhalten. Behalten Sie das Modell, wenn Sie Upshot vielleicht erneut installieren.
StatusFirstRun=Erster Start wird vorbereitet...
StatusPreparing=Wird vorbereitet...
TermsInEnglish=Die Nutzungsbedingungen liegen nur auf Englisch vor.
