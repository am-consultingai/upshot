; Upshot's own installer messages (Spanish). Loaded after Inno's own translation
; (packaging/installer.iss [Languages]). UTF-8 with a BOM: Inno reads a file without
; one as the ANSI code page. %1 is an argument, %n a line break.

[CustomMessages]
RamTooLow=Upshot necesita un equipo con al menos 12 GB de memoria para transcribir reuniones.%n%nEste equipo tiene %1 GB, por lo que Upshot no se instalará.
PrepareTitle=Preparando Upshot para transcribir
PrepareLead=Upshot transcribe en este equipo, por lo que necesita sus modelos de voz (unos 6,7 GB). Se descargan una sola vez.
PrepareStarting=Iniciando la descarga...
HintModel=Reconocimiento de voz para hebreo (ivrit-ai) y todos los demás idiomas (Whisper), desde Hugging Face.
HintGpu=NVIDIA cuBLAS y cuDNN, para que la transcripción use la tarjeta gráfica.
NoteNotStarted=Los modelos de voz no se descargaron, por lo que Upshot aún no puede transcribir. Vuelva a ejecutar el instalador para terminar.
NotePaused=La descarga de los modelos de voz se pausó, por lo que Upshot aún no puede transcribir. Vuelva a ejecutar el instalador para continuarla.
NoteNoSpace=No había suficiente espacio libre en disco para los modelos de voz (unos 7,7 GB con margen). Libere espacio y vuelva a ejecutar el instalador para terminar.
NoteGpuFailed=Los modelos de voz están listos. No se pudieron descargar las bibliotecas de GPU, por lo que por ahora Upshot transcribe con el procesador.
NoteFailed=No se pudieron descargar los modelos de voz, por lo que la instalación está incompleta y Upshot aún no puede transcribir. Vuelva a ejecutar el instalador para terminar; continuará donde se detuvo.
RemoveModels=los modelos de voz (unos 6,7 GB)
RemoveModelsAndGpu=los modelos de voz y las bibliotecas de GPU (unos 9 GB)
RemoveAsk=¿Quitar también %1?%n%nSus grabaciones, transcripciones y configuración se conservan en cualquier caso. Conserve el modelo si es posible que vuelva a instalar Upshot.
StatusFirstRun=Preparando el primer inicio...
StatusPreparing=Preparando...
TermsInEnglish=Los Términos del servicio solo están disponibles en inglés.
