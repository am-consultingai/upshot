; Per-user install, no admin prompt (SECURITY-AND-AUTH.md §10 step 1).
#define AppName "Upshot"
#define AppVersion "1.0.0"
#define AppExe "upshot.exe"

[Setup]
AppName={#AppName}
AppVersion={#AppVersion}
DefaultDirName={localappdata}\Programs\upshot
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=..\dist
OutputBaseFilename=Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
; Setup.exe's own icon. The installed app gets the same mark from the .exe, which
; PyInstaller stamps with this file (see packaging/upshot.spec); both are generated
; by scripts/make_icons.py.
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\{#AppExe}

[Files]
Source: "..\dist\upshot\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{userprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{userstartup}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: startup

[Tasks]
Name: "startup"; Description: "Start {#AppName} when I sign in"; GroupDescription: "Autostart:"

[Run]
Filename: "{app}\{#AppExe}"; Parameters: "--bootstrap"; StatusMsg: "Preparing first run..."; Flags: runhidden waituntilterminated
Filename: "{app}\{#AppExe}"; Description: "Launch {#AppName}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
; The logon task is ours; remove it with the app.
Filename: "schtasks"; Parameters: "/Delete /F /TN Upshot"; Flags: runhidden; RunOnceId: "RemoveLogonTask"
