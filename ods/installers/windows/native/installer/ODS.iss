; ============================================================================
; ODS Windows-native app -- per-user Inno Setup 6 installer
; ============================================================================
; Part of: installers/windows/native/installer/
; Purpose: Install the bundle staged by build-bundle.ps1 into
;          %LOCALAPPDATA%\Programs\ODS for the current user only (no admin
;          rights), add the Start menu entry "ODS", set ODS up and start it,
;          and remove everything again on uninstall.
;
; Expects: ISCC.exe /DBundleDir=<bundle folder> [/DAppVersion=<x.y.z>]
;          [/O<output folder>] ODS.iss
; Provides: ODS-Setup-<version>.exe. Silent install:
;             ODS-Setup-<version>.exe /VERYSILENT /SUPPRESSMSGBOXES /CURRENTUSER /NORESTART /LOG=<file>
;           Silent uninstall (add /REMOVEDATA to delete models and data too):
;             "%LOCALAPPDATA%\Programs\ODS\unins000.exe" /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /REMOVEDATA
;           Setup exits with code 20 when the files installed but
;           "ods-native.ps1 install-state" or "start" failed.
;
; Modder notes:
;   install-state and start run from [Code] (not [Run]) so their exit codes
;   are checked: [Run] ignores them. The state lives in
;   %LOCALAPPDATA%\ODS\Native (see ods-native.ps1). Uninstall keeps the
;   user's models and data unless the user says yes to the question or
;   passes /REMOVEDATA. {app} is fixed (no directory page), so deleting it
;   whole on uninstall (Python's __pycache__ folders) is safe.
; ============================================================================

#ifndef AppVersion
  #define AppVersion "0.0.0-dev"
#endif
#ifndef BundleDir
  #error Pass /DBundleDir=<folder written by build-bundle.ps1>
#endif

#define PowerShellExe "{sys}\WindowsPowerShell\v1.0\powershell.exe"

[Setup]
AppId={{5E3CDDB9-03AB-428D-85D6-F520BFB0D03A}
AppName=ODS
AppVersion={#AppVersion}
AppVerName=ODS {#AppVersion}
AppPublisher=ODS
PrivilegesRequired=lowest
DefaultDirName={localappdata}\Programs\ODS
DisableDirPage=yes
DisableProgramGroupPage=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
CloseApplications=no
OutputBaseFilename=ODS-Setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
SetupLogging=yes
UninstallDisplayName=ODS

[Files]
Source: "{#BundleDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{userprograms}\ODS"; Filename: "{sys}\wscript.exe"; Parameters: """{app}\launch-ods.vbs"""; WorkingDir: "{app}"; Comment: "Open ODS, your local AI"

[Run]
Filename: "{sys}\wscript.exe"; Parameters: """{app}\launch-ods.vbs"""; Description: "Open ODS"; Flags: postinstall nowait skipifsilent

[UninstallRun]
Filename: "{#PowerShellExe}"; Parameters: "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File ""{app}\ods-native.ps1"" uninstall-state"; Flags: runhidden waituntilterminated; RunOnceId: "ODSUninstallState"

[UninstallDelete]
Type: filesandordirs; Name: "{app}"

[Code]
var
  SetupStateFailed: Boolean;

function RunControl(Command: String): Integer;
var
  ResultCode: Integer;
begin
  Log('ods-native.ps1 ' + Command);
  { Same command line as [UninstallRun]; quotes are single here, not doubled. }
  if not Exec(ExpandConstant('{#PowerShellExe}'),
              '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' + ExpandConstant('{app}') + '\ods-native.ps1" ' + Command, '',
              SW_HIDE, ewWaitUntilTerminated, ResultCode) then
    Result := -1
  else
    Result := ResultCode;
  Log('ods-native.ps1 ' + Command + ' exited with ' + IntToStr(Result));
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := '';
  { An upgrade replaces files the running supervisor holds open. }
  if FileExists(ExpandConstant('{app}\ods-native.ps1')) then
    if RunControl('stop') <> 0 then
      Result := 'ODS is running and could not be stopped. Restart Windows and run Setup again.';
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep <> ssPostInstall then
    Exit;
  if (RunControl('install-state') <> 0) or (RunControl('start') <> 0) then
  begin
    SetupStateFailed := True;
    SuppressibleMsgBox('ODS was installed but could not be set up for this Windows user.' + #13#10#13#10 +
      'Details: ' + ExpandConstant('{localappdata}') + '\ODS\Native\logs\ods-native.log', mbError, MB_OK, IDOK);
  end;
end;

function GetCustomSetupExitCode: Integer;
begin
  if SetupStateFailed then
    Result := 20
  else
    Result := 0;
end;

function RemoveDataRequested: Boolean;
var
  I: Integer;
begin
  Result := False;
  for I := 1 to ParamCount do
    if CompareText(ParamStr(I), '/REMOVEDATA') = 0 then
      Result := True;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  RemoveData: Boolean;
begin
  if CurUninstallStep <> usUninstall then
    Exit;
  RemoveData := RemoveDataRequested;
  if not RemoveData and not UninstallSilent then
    RemoveData := MsgBox('Also delete your ODS models, chats and settings?' + #13#10#13#10 +
      'They are in ' + ExpandConstant('{localappdata}') + '\ODS\Native. Choose No to keep them for a later reinstall.',
      mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES;
  { Runs before [UninstallRun]; its plain uninstall-state then finds nothing left to do. }
  if RemoveData and (RunControl('uninstall-state -RemoveData') <> 0) then
    SuppressibleMsgBox('ODS could not delete all of its data in ' + ExpandConstant('{localappdata}') + '\ODS\Native.',
      mbError, MB_OK, IDOK);
end;
