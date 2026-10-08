; Upshot's own installer messages (French). Loaded after Inno's own translation
; (packaging/installer.iss [Languages]). UTF-8 with a BOM: Inno reads a file without
; one as the ANSI code page. %1 is an argument, %n a line break.

[CustomMessages]
RamTooLow=Upshot a besoin d'un ordinateur doté d'au moins 12 Go de mémoire pour y transcrire des réunions.%n%nCet ordinateur dispose de %1 Go : Upshot ne sera donc pas installé.
PrepareTitle=Préparation d'Upshot pour la transcription
PrepareLead=Upshot transcrit sur cet ordinateur : il a donc besoin de ses modèles vocaux (environ 5,2 Go). Ils ne sont téléchargés qu'une seule fois.
PrepareStarting=Démarrage du téléchargement...
HintModel=Reconnaissance vocale pour l'hébreu (ivrit-ai) et toutes les autres langues (Whisper), depuis Hugging Face.
HintGpu=NVIDIA cuBLAS et cuDNN, pour que la transcription s'exécute sur votre carte graphique.
NoteNotStarted=Les modèles vocaux n'ont pas été téléchargés : Upshot ne peut pas encore transcrire. Relancez le programme d'installation pour terminer.
NotePaused=Le téléchargement des modèles vocaux a été suspendu : Upshot ne peut pas encore transcrire. Relancez le programme d'installation pour le reprendre.
NoteNoSpace=L'espace disque libre était insuffisant pour les modèles vocaux (environ 6,3 Go avec une marge). Libérez de l'espace, puis relancez le programme d'installation pour terminer.
NoteGpuFailed=Les modèles vocaux sont prêts. Les bibliothèques GPU n'ont pas pu être téléchargées : pour l'instant, Upshot transcrit sur le processeur.
NoteFailed=Les modèles vocaux n'ont pas pu être téléchargés : l'installation est incomplète et Upshot ne peut pas encore transcrire. Relancez le programme d'installation pour terminer ; il reprendra là où il s'est arrêté.
RemoveModels=les modèles vocaux (environ 5,2 Go)
RemoveModelsAndGpu=les modèles vocaux et les bibliothèques GPU (environ 7,5 Go)
RemoveAsk=Supprimer aussi %1 ?%n%nVos enregistrements, transcriptions et paramètres sont conservés dans tous les cas. Gardez le modèle si vous pensez réinstaller Upshot.
StatusFirstRun=Préparation du premier démarrage...
StatusPreparing=Préparation...
TermsInEnglish=Les Conditions d'utilisation sont disponibles en anglais uniquement.
