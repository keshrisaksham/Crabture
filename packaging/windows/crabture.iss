; Inno Setup script for Crabture.  Build with:  scripts\build_windows.ps1 -Installer
; (which passes /DAppVersion=... and /DRootDir=... and has already produced dist\Crabture\)

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef RootDir
  #define RootDir ".."
#endif

#define AppName "Crabture"
#define AppExe "Crabture.exe"

[Setup]
AppId={{6F0E5B7E-5C0A-4C51-9E0B-C8A7B1F3A2D4}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=YawnByte
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
OutputDir={#RootDir}\dist
OutputBaseFilename=Crabture-{#AppVersion}-Setup
SetupIconFile={#RootDir}\assets\logo.ico
UninstallDisplayIcon={app}\{#AppExe}
LicenseFile={#RootDir}\LICENSE
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; Per-user by default (no admin prompt); the user can still choose "for all users".
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Shortcuts:"; Flags: unchecked
Name: "contextmenu"; Description: "Add ""Edit with Crabture"" to the right-click menu of images and ""Open folder with Crabture"" to folders"; GroupDescription: "Explorer integration:"
Name: "autostart"; Description: "Start Crabture in the notification area at sign-in (capture shortcuts work everywhere)"; GroupDescription: "Screen capture:"; Flags: unchecked

[Files]
Source: "{#RootDir}\dist\Crabture\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\Crabture - capture region"; Filename: "{app}\{#AppExe}"; Parameters: "--capture region"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Registry]
; "Edit with Crabture" for every image type (HKA = HKCU for a per-user install, HKLM for all users).
Root: HKA; Subkey: "Software\Classes\SystemFileAssociations\image\shell\CrabtureEdit"; ValueType: string; ValueName: ""; ValueData: "Edit with Crabture"; Flags: uninsdeletekey; Tasks: contextmenu
Root: HKA; Subkey: "Software\Classes\SystemFileAssociations\image\shell\CrabtureEdit"; ValueType: string; ValueName: "Icon"; ValueData: """{app}\{#AppExe}"""; Tasks: contextmenu
Root: HKA; Subkey: "Software\Classes\SystemFileAssociations\image\shell\CrabtureEdit\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#AppExe}"" ""%1"""; Tasks: contextmenu
; Folders (right-click a folder, or the empty space inside one).
Root: HKA; Subkey: "Software\Classes\Directory\shell\CrabtureOpen"; ValueType: string; ValueName: ""; ValueData: "Open folder with Crabture"; Flags: uninsdeletekey; Tasks: contextmenu
Root: HKA; Subkey: "Software\Classes\Directory\shell\CrabtureOpen"; ValueType: string; ValueName: "Icon"; ValueData: """{app}\{#AppExe}"""; Tasks: contextmenu
Root: HKA; Subkey: "Software\Classes\Directory\shell\CrabtureOpen\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#AppExe}"" --directory ""%1"""; Tasks: contextmenu
Root: HKA; Subkey: "Software\Classes\Directory\Background\shell\CrabtureOpen"; ValueType: string; ValueName: ""; ValueData: "Open folder with Crabture"; Flags: uninsdeletekey; Tasks: contextmenu
Root: HKA; Subkey: "Software\Classes\Directory\Background\shell\CrabtureOpen"; ValueType: string; ValueName: "Icon"; ValueData: """{app}\{#AppExe}"""; Tasks: contextmenu
Root: HKA; Subkey: "Software\Classes\Directory\Background\shell\CrabtureOpen\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#AppExe}"" --directory ""%V"""; Tasks: contextmenu
; Start at sign-in.  Crabture's own Configure > Capture > "Apply to desktop" writes/removes the same value.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "Crabture"; ValueData: """{app}\{#AppExe}"" --background"; Flags: uninsdeletevalue; Tasks: autostart

[Run]
Filename: "{app}\{#AppExe}"; Description: "Launch {#AppName}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
; Make sure a running background instance does not keep files locked.
Filename: "{sys}\taskkill.exe"; Parameters: "/F /IM {#AppExe}"; Flags: runhidden; RunOnceId: "StopCrabture"
