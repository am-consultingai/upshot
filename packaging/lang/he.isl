; Upshot's own installer messages (Hebrew). Loaded after Inno's own translation
; (packaging/installer.iss [Languages]). UTF-8 with a BOM: Inno reads a file without
; one as the ANSI code page. %1 is an argument, %n a line break.

[CustomMessages]
RamTooLow=כדי לתמלל פגישות, Upshot צריך מחשב עם 12 GB זיכרון לפחות.%n%nבמחשב הזה יש %1 GB, ולכן Upshot לא יותקן.
PrepareTitle=מכינים את Upshot לתמלול
PrepareLead=Upshot מתמלל במחשב הזה, ולכן הוא צריך את מודלי הדיבור שלו (כ-5.2 GB). ההורדה נעשית פעם אחת בלבד.
PrepareStarting=ההורדה מתחילה...
HintModel=זיהוי דיבור לעברית (ivrit-ai) ולכל שפה אחרת (Whisper), מ-Hugging Face.
HintGpu=NVIDIA cuBLAS ו-cuDNN, כדי שהתמלול ירוץ על כרטיס המסך.
NoteNotStarted=מודלי הדיבור לא הורדו, ולכן Upshot עדיין לא יכול לתמלל. הריצו שוב את תוכנית ההתקנה כדי לסיים.
NotePaused=הורדת מודל הדיבור הושהתה, ולכן Upshot עדיין לא יכול לתמלל. הריצו שוב את תוכנית ההתקנה כדי להמשיך.
NoteNoSpace=לא היה מספיק מקום פנוי בדיסק למודלי הדיבור (כ-6.3 GB, עם מרווח). פנו מקום והריצו שוב את תוכנית ההתקנה כדי לסיים.
NoteGpuFailed=מודלי הדיבור מוכנים. לא ניתן היה להוריד את ספריות ה-GPU, ולכן בינתיים Upshot מתמלל על המעבד.
NoteFailed=לא ניתן היה להוריד את מודלי הדיבור, ולכן ההתקנה לא הושלמה ו-Upshot עדיין לא יכול לתמלל. הריצו שוב את תוכנית ההתקנה כדי לסיים; היא תמשיך מהמקום שבו עצרה.
RemoveModels=את מודלי הדיבור (כ-5.2 GB)
RemoveModelsAndGpu=את מודלי הדיבור ואת ספריות ה-GPU (כ-7.5 GB)
RemoveAsk=להסיר גם %1?%n%nההקלטות, התמלילים וההגדרות נשמרים בכל מקרה. כדאי להשאיר את המודל אם ייתכן שתתקינו את Upshot שוב.
StatusFirstRun=מכינים את ההפעלה הראשונה...
StatusPreparing=מכינים...
TermsInEnglish=תנאי השימוש מוצגים באנגלית.
