; Inno Setup script for RE4.
;
; Build with:  iscc installer.iss      (after build_executable.bat has made dist\RE4.exe)
; Output:      dist\installer\RE4-Setup-<version>.exe
;
; The installer exists so a shop does not have to know what a folder is.
; It puts RE4.exe in Program Files, a shortcut on the desktop and in the Start
; menu, and points the application's writable data (the database, receipts,
; backups, logs) at %LOCALAPPDATA%\RE4 through the RE4_DATA_DIR environment
; variable -- because Program Files is read-only to the app, and the database
; must never live somewhere the shop cannot back up.
;
; The variable is written per user the first time the installer runs for that
; account, and again by [Code] on first launch of the app by any other user,
; so the installing administrator's HKCU does not stand in for everybody's.

#define AppName "RE4"
; Bump this with each release, alongside pyproject.toml and app/config.py.
#define AppVersion "2.7.2"
#define AppExe "RE4.exe"

[Setup]
AppId={{8E2B7C31-5A64-4B0D-9F2C-1D3A4B5C6D7E}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppName}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
OutputBaseFilename=RE4-Setup-{#AppVersion}
OutputDir=dist\installer
Compression=lzma2
SolidCompression=yes
PrivilegesRequired=admin
WizardStyle=modern
ChangesEnvironment=yes
UninstallDisplayIcon={app}\{#AppExe}

[Files]
Source: "dist\{#AppExe}"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Put a shortcut on the desktop"; GroupDescription: "Shortcuts:"

[Code]
const
  EnvironmentKey = 'Environment';
  DataDirValueName = 'RE4_DATA_DIR';

procedure CurStepChanged(CurStep: TSetupStep);
var
  DataDir: string;
begin
  if CurStep = ssPostInstall then
  begin
    DataDir := ExpandConstant('{localappdata}') + '\RE4';
    ForceDirectories(DataDir);
    RegWriteStringValue(HKEY_CURRENT_USER, EnvironmentKey, DataDirValueName, DataDir);
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then
    RegDeleteValue(HKEY_CURRENT_USER, EnvironmentKey, DataDirValueName);
  { The database itself is never deleted by the uninstaller: it is the shop. }
end;
