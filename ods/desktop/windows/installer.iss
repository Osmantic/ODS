; SPDX-License-Identifier: Apache-2.0
; Build-time overrides let tests use an isolated application ID and shortcuts.
#ifndef AppGuid
  #define AppGuid "A1A2BD01-A38F-4790-94D7-75ED422D9FC1"
#endif
#ifndef AppTitle
  #define AppTitle "ODS Windows Preview"
#endif
#ifndef PreviewVersion
  #define PreviewVersion "0.1.0.0"
#endif

[Setup]
AppId={#AppGuid}
AppName={#AppTitle}
AppVersion={#PreviewVersion}
AppVerName={#AppTitle} {#PreviewVersion}
AppPublisher=ODS contributors
AppPublisherURL=https://github.com/Osmantic/ODS
AppSupportURL=https://github.com/Osmantic/ODS/issues
DefaultDirName={localappdata}\Programs\{#AppTitle}
DefaultGroupName={#AppTitle}
DisableDirPage=auto
DisableProgramGroupPage=yes
UsePreviousAppDir=yes
PrivilegesRequired=lowest
SetupArchitecture=x64
ArchitecturesAllowed=x64os
MinVersion=10.0.19041
WizardStyle=modern dynamic
DisableWelcomePage=no
SetupIconFile={#PackageDir}\portal\osmantic-os.ico
UninstallDisplayIcon={app}\ODS Windows Preview.exe
LicenseFile={#PackageDir}\LICENSE-ODS.txt
OutputDir={#InstallerOutput}
OutputBaseFilename=ODS-Windows-Preview-Setup
Compression=lzma2
SolidCompression=yes
CloseApplications=no
RestartApplications=no
RestartIfNeededByRun=no
SetupLogging=yes
UninstallLogMode=append

[Files]
Source: "{#PackageDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#PackageDir}\ODS Windows Preview.exe"; Flags: dontcopy
Source: "{#WebViewBootstrapper}"; Flags: dontcopy
Source: "{#MarkerFile}"; DestDir: "{app}"; DestName: ".ods-preview-install"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\{#AppTitle}"; Filename: "{app}\ODS Windows Preview.exe"; WorkingDir: "{app}"

[Run]
Filename: "{app}\ODS Windows Preview.exe"; Description: "Open {#AppTitle}"; Flags: nowait postinstall skipifsilent

[Code]
const WebViewKey = 'Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}';

type TNativeFindData = record
  Attributes: LongWord;
  CreationTime, LastAccessTime, LastWriteTime: TFileTime;
  SizeHigh, SizeLow, Reserved0, Reserved1: LongWord;
  FileName: array[0..259] of Word;
  AlternateName: array[0..13] of Word;
end;

function GetFileAttributes(Path: String): LongWord;
external 'GetFileAttributesW@kernel32.dll stdcall';
function NativeFindFirst(Path: String; var Row: TNativeFindData): THandle;
external 'FindFirstFileW@kernel32.dll stdcall';
function NativeFindNext(Handle: THandle; var Row: TNativeFindData): Boolean;
external 'FindNextFileW@kernel32.dll stdcall';
function NativeFindClose(Handle: THandle): Boolean;
external 'FindClose@kernel32.dll stdcall';

function NormalPath(Path: String): Boolean;
var Attr, Error: LongWord; Parent: String;
begin
  Result := True;
  while Length(Path) > 3 do begin
    Attr := GetFileAttributes(Path);
    Error := DLLGetLastError;
    if (Attr = $FFFFFFFF) and (Error <> 2) and (Error <> 3) then begin Result := False; exit; end;
    if (Attr <> $FFFFFFFF) and ((Attr and $400) <> 0) then begin Result := False; exit; end;
    Parent := ExtractFileDir(Path);
    if Parent = Path then exit;
    Path := Parent;
  end;
end;

function NoLinksInTree(Path: String; Depth: Integer; var Visited: Integer): Boolean;
var Row: TNativeFindData; Handle: THandle; Name: String; Index: Integer;
begin
  Result := False;
  if Depth > 64 then exit;
  { Direct Win32 calls preserve their last-error codes. Failed inspection is
    never evidence of an empty directory or a complete, link-free scan. }
  Handle := NativeFindFirst(AddBackslash(Path) + '*', Row);
  if Handle = THandle(-1) then begin Result := DLLGetLastError = 2; exit; end;
  try
    while True do begin
      Name := '';
      for Index := 0 to 259 do begin
        if Row.FileName[Index] = 0 then break;
        Name := Name + Chr(Row.FileName[Index]);
      end;
      if (Name <> '.') and (Name <> '..') then begin
        Visited := Visited + 1;
        if (Visited > 5000) or ((Row.Attributes and $400) <> 0) then exit;
        if (Row.Attributes and FILE_ATTRIBUTE_DIRECTORY) <> 0 then
          if not NoLinksInTree(AddBackslash(Path) + Name, Depth + 1, Visited) then exit;
      end;
      if not NativeFindNext(Handle, Row) then begin Result := DLLGetLastError = 18; exit; end;
    end;
  finally NativeFindClose(Handle); end;
end;

function NextVersionPart(var Value: String): Integer;
var Index: Integer; Part: String;
begin
  Index := Pos('.', Value);
  if Index = 0 then begin Part := Value; Value := ''; end
  else begin Part := Copy(Value, 1, Index - 1); Delete(Value, 1, Index); end;
  Result := StrToIntDef(Part, -1);
  if Result > 65535 then Result := -1;
end;

function Downgrade(Previous, Candidate: String): Boolean;
var Index, OldPart, NewPart, Comparison: Integer;
begin
  Result := True; Comparison := 0;
  for Index := 1 to 4 do begin
    OldPart := NextVersionPart(Previous); NewPart := NextVersionPart(Candidate);
    if (OldPart < 0) or (NewPart < 0) then exit;
    if Comparison = 0 then begin
      if OldPart > NewPart then Comparison := 1;
      if OldPart < NewPart then Comparison := -1;
    end;
  end;
  Result := (Previous <> '') or (Candidate <> '') or (Comparison > 0);
end;

function WebViewInstalled: Boolean;
var MachineVersion, UserVersion: String;
begin
  Result := (RegQueryStringValue(HKLM32, WebViewKey, 'pv', MachineVersion) and (NextVersionPart(MachineVersion) >= 155)) or
            (RegQueryStringValue(HKCU, WebViewKey, 'pv', UserVersion) and (NextVersionPart(UserVersion) >= 155));
end;

function AppIdle(Executable: String): Boolean;
var Code: Integer;
begin
  Result := Exec(Executable, '--installation-check', '', SW_HIDE, ewWaitUntilTerminated, Code) and (Code = 0);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var Target, Marker, Previous: String; Code, Visited: Integer;
begin
  Result := ''; Target := ExpandConstant('{app}'); Marker := AddBackslash(Target) + '.ods-preview-install';
  if not NormalPath(Target) or not NormalPath(Marker) then begin Result := 'The installation folder must be a normal local folder, without links. Your existing files were kept.'; exit; end;
  Visited := 0;
  if DirExists(Target) and not NoLinksInTree(Target, 0, Visited) then begin Result := 'The installation folder contains links or too many unexpected files. They were kept. Inspect the folder before retrying.'; exit; end;
  if Visited > 0 then begin
    if GetIniString('Install', 'AppId', '', Marker) <> '{#AppGuid}' then begin Result := 'This folder contains files that do not belong to this installer. They were kept. Choose a different folder.'; exit; end;
    Previous := GetIniString('Install', 'Version', '', Marker);
    if Downgrade(Previous, '{#PreviewVersion}') then begin Result := 'A newer or unrecognized installation is already present. Use its installer to repair it. Saved conversations were kept.'; exit; end;
  end;
  ExtractTemporaryFile('ODS Windows Preview.exe');
  if not AppIdle(ExpandConstant('{tmp}\ODS Windows Preview.exe')) then begin Result := 'Close ODS Windows Preview, then retry. Setup will not stop your running AI or discard its conversation.'; exit; end;
  if not WebViewInstalled then begin
    if WizardSilent then begin Result := 'Microsoft WebView2 Runtime 155 or newer is required. Run this installer interactively to install it.'; exit; end;
    if MsgBox('ODS needs Microsoft WebView2 to display Portal. Install this Microsoft component now?', mbConfirmation, MB_YESNO) <> IDYES then begin Result := 'WebView2 installation was cancelled. ODS has not been installed. Run Setup again when ready.'; exit; end;
    ExtractTemporaryFile('MicrosoftEdgeWebview2Setup.exe');
    WizardForm.StatusLabel.Caption := 'Installing Microsoft WebView2. This can take a few minutes...';
    if not Exec(ExpandConstant('{tmp}\MicrosoftEdgeWebview2Setup.exe'), '/install', '', SW_SHOW, ewWaitUntilTerminated, Code) or
       not WebViewInstalled then begin Result := 'Microsoft WebView2 did not finish installing. Check your connection and retry Setup. Existing ODS data was kept.'; exit; end;
  end;
end;

function InitializeUninstall: Boolean;
var Executable, Target, Marker: String; Visited: Integer;
begin
  Target := ExpandConstant('{app}'); Marker := AddBackslash(Target) + '.ods-preview-install'; Visited := 0;
  Result := NormalPath(Target) and NormalPath(Marker) and (GetIniString('Install', 'AppId', '', Marker) = '{#AppGuid}') and NoLinksInTree(Target, 0, Visited);
  if not Result then begin SuppressibleMsgBox('The installation ownership or folder paths could not be confirmed. Existing files were kept. Restore the original folder or use the installer to repair it.', mbError, MB_OK, IDOK); exit; end;
  Executable := AddBackslash(Target) + 'ODS Windows Preview.exe';
  Result := not FileExists(Executable) or AppIdle(Executable);
  if not Result then SuppressibleMsgBox('Close ODS Windows Preview before uninstalling. Your AI and saved conversations were kept.', mbError, MB_OK, IDOK);
end;

procedure InitializeWizard;
begin
  WizardForm.WelcomeLabel2.Caption := 'Install ODS Portal and local CPU AI for this Windows account.' + #13#10 + #13#10 +
    'No Docker, WSL, Git or terminal is needed. The first launch downloads the checked AI model (1.28 GB).' + #13#10 + #13#10 +
    'This is an unsigned development preview. Saved conversations and model files are kept when you repair or uninstall the application.';
end;
