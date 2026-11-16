; Baja camera node installer (Inno Setup 6), WP15, ADR 0023.
;
; The slim second distributable: installs dist\node\app (made by
; `build.ps1 -Node`) to %ProgramFiles%\BajaCameraNode on a trackside laptop,
; keeps the data in %ProgramData%\BajaCameraNode (or the folder chosen on
; first install), and registers one Windows service with NSSM:
;   BajaCameraNode   the camera node (python -m baja_studio.node), port 8891 by
;                    default (its settings page can change it; the firewall
;                    rule covers 8891 only), started at boot with nobody logged in
; No Caddy, MediaMTX, playout or tray: the node's own page (http://<laptop>:8891/)
; shows its status. Its ffmpeg is in {app}\ffmpeg.
;
; Build:   iscc /DAppVersion=1.2.3 installer\BajaCameraNode.iss
; Silent:  BajaCameraNode-1.2.3.exe /VERYSILENT /SUPPRESSMSGBOXES [/DATADIR=D:\Node]
; Uninstall silently, deleting the data too:  unins000.exe /VERYSILENT /PURGEDATA=1

#ifndef AppVersion
  #error Give the version: iscc /DAppVersion=1.2.3 BajaCameraNode.iss
#endif
#ifndef DistDirectory
  #define DistDirectory "..\dist\node\app"
#endif
#ifndef OutputDirectory
  #define OutputDirectory "..\dist"
#endif

[Setup]
; Its own AppId: the node and the studio are separate programs (both may be on one PC).
AppId={{3C9A7E41-58B2-4F0D-9E6A-71D2B4C8F5A0}
AppName=Baja camera node
AppVersion={#AppVersion}
AppPublisher=Baja SAE Brasil
DefaultDirName={autopf}\BajaCameraNode
DefaultGroupName=Baja camera node
DisableDirPage=yes
DisableProgramGroupPage=yes
DisableWelcomePage=no
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir={#OutputDirectory}
OutputBaseFilename=BajaCameraNode-{#AppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
SetupIconFile=..\tray\baja_studio_tray\assets\baja-studio.ico
UninstallDisplayName=Baja camera node
UninstallDisplayIcon={app}\baja-studio.ico
; The service is stopped by PrepareToInstall; nothing else holds our files.
CloseApplications=no
SetupLogging=yes

[Tasks]
Name: firewall; Description: "Open the firewall for the studio (TCP 8891, on private and domain networks)"

[InstallDelete]
; An upgrade replaces the program folder: nothing of the old version stays behind.
Type: filesandordirs; Name: "{app}\python"
Type: filesandordirs; Name: "{app}\frontend"
Type: filesandordirs; Name: "{app}\nssm"
Type: filesandordirs; Name: "{app}\ffmpeg"

[Files]
Source: "{#DistDirectory}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[INI]
Filename: "{group}\Baja camera node.url"; Section: "InternetShortcut"; Key: "URL"; String: "http://localhost:8891/"
Filename: "{group}\Baja camera node.url"; Section: "InternetShortcut"; Key: "IconFile"; String: "{app}\baja-studio.ico"
Filename: "{group}\Baja camera node.url"; Section: "InternetShortcut"; Key: "IconIndex"; String: "0"

[Icons]
Name: "{group}\Baja camera node logs"; Filename: "{code:DataDirectory}\logs"
Name: "{group}\Uninstall Baja camera node"; Filename: "{uninstallexe}"

[UninstallDelete]
Type: files; Name: "{group}\Baja camera node.url"

[Registry]
; Where the program and the data are, for upgrades (same folder again) and the uninstaller.
Root: HKLM; Subkey: "Software\BajaCameraNode"; ValueType: string; ValueName: "DataDirectory"; ValueData: "{code:DataDirectory}"; Flags: uninsdeletekey
Root: HKLM; Subkey: "Software\BajaCameraNode"; ValueType: string; ValueName: "InstallDirectory"; ValueData: "{app}"; Flags: uninsdeletekey

[Run]
Filename: "http://localhost:8891/"; Description: "Open the camera node's page in the browser"; Flags: postinstall shellexec nowait skipifsilent

[Code]
const
  NodeService = 'BajaCameraNode';
  FirewallRule = 'Baja camera node (LAN)';
  NodePort = '8891';
  RegistryKey = 'Software\BajaCameraNode';
  NodeInfoUrl = 'http://127.0.0.1:8891/agent/info';
  { The node answers within a few seconds; the install finishes anyway after this. }
  NodeWaitSeconds = 60;

var
  DataDirectoryPage: TInputDirWizardPage;
  KeepDataOnUninstall: Boolean;
  UninstallDataDirectory: String;
  FailedSteps: String;

{ --- Helpers --------------------------------------------------------------------- }

function PreviousDataDirectory(): String;
begin
  Result := '';
  RegQueryStringValue(HKLM, RegistryKey, 'DataDirectory', Result);
end;

function IsUpgrade(): Boolean;
begin
  Result := PreviousDataDirectory() <> '';
end;

{ The data folder chosen on the wizard page (used by the sections above too). }
function DataDirectory(Param: String): String;
begin
  Result := RemoveBackslashUnlessRoot(DataDirectoryPage.Values[0]);
end;

function RunHidden(const FileName, Parameters: String): Integer;
var
  ResultCode: Integer;
begin
  if not Exec(FileName, Parameters, '', SW_HIDE, ewWaitUntilTerminated, ResultCode) then
    ResultCode := -1;
  Log(Format('%s %s -> %d', [FileName, Parameters, ResultCode]));
  Result := ResultCode;
end;

function Nssm(const Parameters: String): Integer;
begin
  Result := RunHidden(ExpandConstant('{app}\nssm\nssm.exe'), Parameters);
end;

{ Run an NSSM command that must work; a failure is reported at the end. }
procedure NssmStep(const Parameters: String);
begin
  if Nssm(Parameters) <> 0 then
    FailedSteps := FailedSteps + #13#10 + 'nssm ' + Parameters;
end;

function NetSh(const Parameters: String): Integer;
begin
  Result := RunHidden(ExpandConstant('{sys}\netsh.exe'), 'advfirewall firewall ' + Parameters);
end;

{ True when the node answers /agent/info (open without a token, as for a subnet scan). }
function NodeAnswers(): Boolean;
var
  Request: Variant;
begin
  Result := False;
  try
    Request := CreateOleObject('WinHttp.WinHttpRequest.5.1');
    Request.SetTimeouts(1000, 1000, 1000, 1000);
    Request.Open('GET', NodeInfoUrl, False);
    Request.Send('');
    Result := Request.Status = 200;
  except
    Result := False;
  end;
end;

function WaitForNode(): Boolean;
var
  Second: Integer;
begin
  WizardForm.StatusLabel.Caption := 'Waiting for the camera node to start...';
  for Second := 1 to NodeWaitSeconds do
  begin
    if NodeAnswers() then
    begin
      Log(Format('the node answered after about %d s', [Second]));
      Result := True;
      exit;
    end;
    Sleep(1000);
  end;
  Result := False;
end;

{ Stop any ffmpeg left running from this program folder (a node that crashed can
  leave its children behind): they hold ffmpeg.exe, which an upgrade replaces and
  uninstall deletes, and they hold the cameras. }
procedure StopNodeFfmpeg();
begin
  RunHidden(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'),
    '-NoProfile -NonInteractive -Command "Get-Process ffmpeg -ErrorAction SilentlyContinue | ' +
    'Where-Object { $_.Path -eq ''' + ExpandConstant('{app}\ffmpeg\ffmpeg.exe') + ''' } | ' +
    'Stop-Process -Force"');
end;

{ --- Wizard: welcome -> data folder -> tasks (firewall) -> install ------------------ }

procedure InitializeWizard();
var
  DefaultDirectory: String;
begin
  DataDirectoryPage := CreateInputDirPage(wpWelcome, 'Data folder',
    'Where should the camera node keep its data?',
    'Its settings and logs are kept here. They stay when the node is upgraded, ' +
    'and by default when it is uninstalled.',
    False, '');
  DataDirectoryPage.Add('');
  DefaultDirectory := ExpandConstant('{param:DATADIR|{commonappdata}\BajaCameraNode}');
  if IsUpgrade() then
    DefaultDirectory := PreviousDataDirectory();
  DataDirectoryPage.Values[0] := DefaultDirectory;
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  { An upgrade keeps its data folder: moving it would leave the data behind. }
  Result := (PageID = DataDirectoryPage.ID) and IsUpgrade();
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if (CurPageID = DataDirectoryPage.ID) and
     (Length(DataDirectory('')) <= 3) then
  begin
    MsgBox('Choose a folder, not a whole drive.', mbError, MB_OK);
    Result := False;
  end;
end;

{ --- Install ------------------------------------------------------------------------ }

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  { Upgrade: stop the service (and with it the node's ffmpeg children) so the
    program folder can be replaced. }
  if FileExists(ExpandConstant('{app}\nssm\nssm.exe')) then
    Nssm('stop ' + NodeService);
  StopNodeFfmpeg();
  Result := '';
end;

procedure ConfigureService();
var
  Data, Logs, Python: String;
begin
  Data := DataDirectory('');
  Logs := Data + '\logs\';
  Python := ExpandConstant('{app}\python\python.exe');
  { `install` fails when the service exists (an upgrade); every `set` below applies anyway. }
  Nssm('install ' + NodeService + ' ' + AddQuotes(Python));
  NssmStep('set ' + NodeService + ' Application ' + AddQuotes(Python));
  NssmStep('set ' + NodeService + ' AppDirectory ' + AddQuotes(ExpandConstant('{app}')));
  NssmStep('set ' + NodeService + ' DisplayName "Baja camera node"');
  NssmStep('set ' + NodeService + ' Description "The laptop''s USB cameras as crop cameras for Baja Studio."');
  NssmStep('set ' + NodeService + ' Start SERVICE_AUTO_START');
  { No --port: the node reads it from its settings (8891 unless changed on its page). }
  NssmStep('set ' + NodeService + ' AppParameters "-m baja_studio.node --host 0.0.0.0"');
  { Paths go in the environment, not on the command line: no quoting to get wrong. }
  NssmStep('set ' + NodeService + ' AppEnvironmentExtra' +
    ' ' + AddQuotes('BAJA_NODE_DATA=' + Data) +
    ' ' + AddQuotes('BAJA_STUDIO_INSTALL_DIR=' + ExpandConstant('{app}')) +
    ' ' + AddQuotes('BAJA_STUDIO_FRONTEND=' + ExpandConstant('{app}\frontend')) +
    ' PYTHONUNBUFFERED=1 PYTHONUTF8=1');
  NssmStep('set ' + NodeService + ' AppStdout ' + AddQuotes(Logs + 'node-stdout.log'));
  NssmStep('set ' + NodeService + ' AppStderr ' + AddQuotes(Logs + 'node-stderr.log'));
  NssmStep('set ' + NodeService + ' AppRotateFiles 1');
  NssmStep('set ' + NodeService + ' AppRotateOnline 1');
  NssmStep('set ' + NodeService + ' AppRotateBytes 5242880');
  NssmStep('set ' + NodeService + ' AppExit Default Restart');
  NssmStep('set ' + NodeService + ' AppRestartDelay 2000');
  { Ctrl+C first, with time to stop each camera's ffmpeg and close SQLite. }
  NssmStep('set ' + NodeService + ' AppStopMethodConsole 15000');
end;

procedure ConfigureFirewall();
begin
  { Delete first, then add: running the installer again never doubles a rule. }
  NetSh('delete rule name="' + FirewallRule + '"');
  if WizardIsTaskSelected('firewall') then
    if NetSh('add rule name="' + FirewallRule + '" dir=in action=allow protocol=TCP' +
      ' localport=' + NodePort + ' profile=private,domain') <> 0 then
      FailedSteps := FailedSteps + #13#10 + 'firewall rule';
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep <> ssPostInstall then
    exit;
  ForceDirectories(DataDirectory('') + '\logs');
  ConfigureService();
  ConfigureFirewall();
  NssmStep('start ' + NodeService);
  if not WaitForNode() then
    FailedSteps := FailedSteps + #13#10 +
      Format('the camera node did not answer within %d s (see logs\node-stderr.log)', [NodeWaitSeconds]);
  if FailedSteps <> '' then
  begin
    { The smoke test looks for this line in the setup log. }
    Log('Failed steps:' + FailedSteps);
    SuppressibleMsgBox('The camera node is installed, but these steps failed:' + FailedSteps + #13#10#13#10 +
      'The setup log (in %TEMP%) has the details.', mbError, MB_OK, IDOK);
  end;
end;

{ --- Uninstall -------------------------------------------------------------------- }

function InitializeUninstall(): Boolean;
begin
  UninstallDataDirectory := PreviousDataDirectory();
  KeepDataOnUninstall := ExpandConstant('{param:PURGEDATA|0}') <> '1';
  if KeepDataOnUninstall and (UninstallDataDirectory <> '') then
    KeepDataOnUninstall := SuppressibleMsgBox(
      'Keep the camera node''s settings and logs in ' + UninstallDataDirectory +
      '?' + #13#10#13#10 + 'Yes keeps them for a later install. No deletes them.',
      mbConfirmation, MB_YESNO, IDYES) = IDYES;
  Result := True;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then
  begin
    Nssm('stop ' + NodeService);
    StopNodeFfmpeg();
    Nssm('remove ' + NodeService + ' confirm');
    NetSh('delete rule name="' + FirewallRule + '"');
  end;
  if (CurUninstallStep = usPostUninstall) and not KeepDataOnUninstall and
     (UninstallDataDirectory <> '') then
    DelTree(UninstallDataDirectory, True, True, True);
end;
