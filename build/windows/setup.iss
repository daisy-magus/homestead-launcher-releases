; Homestead Launcher — Inno Setup script
; Installs per-user (no UAC) into %LOCALAPPDATA%\HomesteadLauncher

#define MyAppName "Homestead Launcher"
#define MyAppVersion GetEnv("LAUNCHER_VERSION")
#define MyAppPublisher "Homestead"
#define MyAppExeName "Homestead.exe"

[Setup]
AppId={{B4F7E2A1-C3D8-4B2E-9F1A-7E3C5D6B8A2F}}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\HomesteadLauncher
DefaultGroupName={#MyAppName}
; Per-user install — no UAC, no admin required
PrivilegesRequired=lowest
OutputBaseFilename=HomesteadSetup-{#MyAppVersion}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
DisableDirPage=yes
DisableProgramGroupPage=yes
UninstallDisplayIcon={app}\{#MyAppExeName}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
; Python embeddable runtime
Source: "python-embed\*"; DestDir: "{app}\python"; Flags: ignoreversion recursesubdirs

; Launcher source
Source: "..\..\launcher\*"; DestDir: "{app}\launcher"; Flags: ignoreversion recursesubdirs
Source: "..\..\main.py"; DestDir: "{app}"; Flags: ignoreversion

; Launcher stub exe (built by PyInstaller in a separate step — just a thin wrapper)
Source: "dist\Homestead.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\Homestead"; Filename: "{app}\{#MyAppExeName}"
Name: "{commondesktop}\Homestead"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional icons:"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch Homestead"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}"
