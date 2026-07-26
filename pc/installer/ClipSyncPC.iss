#define MyAppName "ClipSync PC"
#define MyAppVersion "0.9.18"
#define MyAppPublisher "Florentino356"
#define MyAppExeName "ClipSyncPC.exe"

[Setup]
AppId={{D1E8D5FE-6A1C-4B8C-9D7F-A2E7A92BCA41}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={userappdata}\Programs\ClipSync PC
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=..\dist
OutputBaseFilename=ClipSyncPC_Setup
SetupIconFile=..\assets\clipsync.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional icons:"; Flags: unchecked

[Files]
Source: "..\dist\ClipSyncPC\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; Stable Load unpacked path — overwrite on every Setup so Chrome Reload picks up updates.
Source: "..\dist\ClipSyncPC\chrome-extension\*"; DestDir: "{userappdata}\ClipSync\chrome-extension"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
Type: filesandordirs; Name: "{app}\_internal"
; Clean orphan files under the stable extension folder before re-copy (do not touch other ClipSync AppData).
Type: filesandordirs; Name: "{userappdata}\ClipSync\chrome-extension"

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{userdesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent
