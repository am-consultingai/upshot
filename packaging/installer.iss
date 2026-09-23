; Per-user install, no admin prompt (SECURITY-AND-AUTH.md section 10 step 1).
;
; packaging\build.ps1 passes the version and commit: ISCC /DAppVersion=... /DAppCommit=...
; Keep this file ASCII: Inno reads a script without a BOM as the ANSI code page.
#define AppName "Upshot"
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef AppCommit
  #define AppCommit "unknown"
#endif
#define AppExe "upshot.exe"

[Setup]
; Never change the AppId: it is how an upgrade finds the installed copy.
AppId={{6F1B7C2E-4A8D-4E57-9C1D-3B2A9E0F5D11}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=AM Consulting
VersionInfoVersion={#AppVersion}
VersionInfoDescription={#AppName} installer ({#AppCommit})
DefaultDirName={localappdata}\Programs\Upshot
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist
OutputBaseFilename=Upshot-{#AppVersion}-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
; Setup.exe's own icon. The installed app gets the same mark from the .exe, which
; PyInstaller stamps with this file (see packaging/upshot.spec); both are generated
; by scripts/make_icons.py.
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\{#AppExe}
#ifdef Sign
; build.ps1 -Sign defines the "upshot" sign tool (packaging\sign.ps1): Setup and the
; uninstaller Inno generates are both signed.
SignTool=upshot
SignedUninstaller=yes
#endif
; A running copy holds its files open; ask it to close rather than failing half-way.
CloseApplications=yes

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

; User data (meetings, recordings, settings, the speech model) lives in
; %LOCALAPPDATA%\upshot, outside {app}, so the uninstaller leaves it alone.
