; Baja Studio installer (Inno Setup 6).
;
; Installs dist\app (made by build.ps1) to %ProgramFiles%\BajaStudio, keeps the
; data in %ProgramData%\BajaStudio (or the folder chosen on first install), and
; registers two Windows services with NSSM:
;   BajaStudio         the studio service (python -m baja_studio, port 8888)
;   BajaStudioGateway  Caddy on ports 80/443, started after BajaStudio
;
; Build:   iscc /DAppVersion=1.2.3 installer\BajaStudio.iss
; Silent:  BajaStudio-Setup-1.2.3.exe /VERYSILENT /SUPPRESSMSGBOXES [/DATADIR=D:\Studio]
; Uninstall silently, deleting the data too:  unins000.exe /VERYSILENT /PURGEDATA=1

#ifndef AppVersion
  #error Give the version: iscc /DAppVersion=1.2.3 BajaStudio.iss
#endif
#ifndef DistDirectory
  #define DistDirectory "..\dist\app"
#endif
#ifndef OutputDirectory
  #define OutputDirectory "..\dist"
#endif

[Setup]
AppId={{6B0E2C9A-3F1D-4D8E-9A57-2C4B1E7F0A13}
AppName=Baja Studio
AppVersion={#AppVersion}
AppPublisher=Baja SAE Brasil
DefaultDirName={autopf}\BajaStudio
DefaultGroupName=Baja Studio
DisableDirPage=yes
DisableProgramGroupPage=yes
DisableWelcomePage=no
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir={#OutputDirectory}
OutputBaseFilename=BajaStudio-Setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayName=Baja Studio
; The services are stopped by PrepareToInstall; nothing else holds our files.
CloseApplications=no
SetupLogging=yes

[Tasks]
Name: firewall; Description: "Open the firewall for LAN access (TCP 80, 443 and 8888 on private and domain networks)"

[InstallDelete]
; An upgrade replaces the program folder: nothing of the old version stays behind.
Type: filesandordirs; Name: "{app}\python"
Type: filesandordirs; Name: "{app}\frontend"
Type: filesandordirs; Name: "{app}\caddy"
Type: filesandordirs; Name: "{app}\nssm"

[Files]
Source: "{#DistDirectory}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[INI]
Filename: "{group}\Baja Studio.url"; Section: "InternetShortcut"; Key: "URL"; String: "http://localhost:8888/"

[Icons]
Name: "{group}\Baja Studio logs"; Filename: "{code:DataDirectory}\logs"
Name: "{group}\Uninstall Baja Studio"; Filename: "{uninstallexe}"

[UninstallDelete]
Type: files; Name: "{group}\Baja Studio.url"

[Registry]
; Where the data is, for upgrades (same folder again) and the uninstaller.
Root: HKLM; Subkey: "Software\BajaStudio"; ValueType: string; ValueName: "DataDirectory"; ValueData: "{code:DataDirectory}"; Flags: uninsdeletekey

[Run]
Filename: "http://localhost:8888/"; Description: "Open Baja Studio in the browser"; Flags: postinstall shellexec nowait skipifsilent

[Code]
const
  StudioService = 'BajaStudio';
  GatewayService = 'BajaStudioGateway';
  FirewallRule = 'Baja Studio (LAN)';
  RegistryKey = 'Software\BajaStudio';

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

{ --- Wizard: welcome -> data folder -> tasks (firewall) -> install ------------------ }

procedure InitializeWizard();
var
  DefaultDirectory: String;
begin
  DataDirectoryPage := CreateInputDirPage(wpWelcome, 'Data folder',
    'Where should Baja Studio keep its data?',
    'Settings, the playout queue, logs and certificates are kept here. ' +
    'They stay when Baja Studio is upgraded, and by default when it is uninstalled.',
    False, '');
  DataDirectoryPage.Add('');
  DefaultDirectory := ExpandConstant('{param:DATADIR|{commonappdata}\BajaStudio}');
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
  { Upgrade: stop both services (Caddy first, it depends on the studio) so the
    program folder can be replaced. NSSM is still the old version's here. }
  if FileExists(ExpandConstant('{app}\nssm\nssm.exe')) then
  begin
    Nssm('stop ' + GatewayService);
    Nssm('stop ' + StudioService);
  end;
  Result := '';
end;

{ Settings NSSM keeps for both services; `set` replaces, so this is idempotent. }
procedure ConfigureCommon(const Service, FileName, Directory, DisplayName, Description, LogName: String);
var
  Logs: String;
begin
  Logs := DataDirectory('') + '\logs\';
  { `install` fails when the service exists (an upgrade); every `set` below applies anyway. }
  Nssm('install ' + Service + ' ' + AddQuotes(FileName));
  NssmStep('set ' + Service + ' Application ' + AddQuotes(FileName));
  NssmStep('set ' + Service + ' AppDirectory ' + AddQuotes(Directory));
  NssmStep('set ' + Service + ' DisplayName ' + AddQuotes(DisplayName));
  NssmStep('set ' + Service + ' Description ' + AddQuotes(Description));
  NssmStep('set ' + Service + ' Start SERVICE_AUTO_START');
  NssmStep('set ' + Service + ' AppStdout ' + AddQuotes(Logs + LogName + '-stdout.log'));
  NssmStep('set ' + Service + ' AppStderr ' + AddQuotes(Logs + LogName + '-stderr.log'));
  NssmStep('set ' + Service + ' AppRotateFiles 1');
  NssmStep('set ' + Service + ' AppRotateOnline 1');
  NssmStep('set ' + Service + ' AppRotateBytes 5242880');
  NssmStep('set ' + Service + ' AppExit Default Restart');
  NssmStep('set ' + Service + ' AppRestartDelay 2000');
  { Ctrl+C first, with time for a clean shutdown (workers, child processes, SQLite). }
  NssmStep('set ' + Service + ' AppStopMethodConsole 15000');
end;

procedure ConfigureServices();
var
  Data: String;
begin
  Data := DataDirectory('');
  ConfigureCommon(StudioService, ExpandConstant('{app}\python\python.exe'), ExpandConstant('{app}'),
    'Baja Studio', 'Camera control, playout and audio for the Baja SAE broadcast.', 'service');
  NssmStep('set ' + StudioService + ' AppParameters "-m baja_studio --host 0.0.0.0 --port 8888"');
  { Paths go in the environment, not on the command line: no quoting to get wrong. }
  NssmStep('set ' + StudioService + ' AppEnvironmentExtra' +
    ' ' + AddQuotes('BAJA_STUDIO_DATA=' + Data) +
    ' ' + AddQuotes('BAJA_STUDIO_INSTALL_DIR=' + ExpandConstant('{app}')) +
    ' ' + AddQuotes('BAJA_STUDIO_FRONTEND=' + ExpandConstant('{app}\frontend')) +
    ' PYTHONUNBUFFERED=1 PYTHONUTF8=1');

  { Caddy runs in <data>\caddy and reads the Caddyfile the studio writes there.
    Its AppEnvironmentExtra (the Cloudflare token) belongs to the gateway module:
    the installer never sets it, so upgrades keep the token. }
  ConfigureCommon(GatewayService, ExpandConstant('{app}\caddy\caddy.exe'), Data + '\caddy',
    'Baja Studio gateway', 'Caddy: HTTPS for the studio and the apps behind it.', 'caddy');
  NssmStep('set ' + GatewayService + ' AppParameters "run --config Caddyfile --adapter caddyfile"');
  NssmStep('set ' + GatewayService + ' DependOnService ' + StudioService);
end;

procedure ConfigureFirewall();
begin
  { Delete first, then add: running the installer again never doubles the rule. }
  NetSh('delete rule name="' + FirewallRule + '"');
  if WizardIsTaskSelected('firewall') then
    if NetSh('add rule name="' + FirewallRule + '" dir=in action=allow protocol=TCP' +
      ' localport=80,443,8888 profile=private,domain') <> 0 then
      FailedSteps := FailedSteps + #13#10 + 'firewall rule';
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Data: String;
begin
  if CurStep <> ssPostInstall then
    exit;
  Data := DataDirectory('');
  ForceDirectories(Data + '\logs');
  ForceDirectories(Data + '\caddy');
  { Caddy may start before the studio has written its Caddyfile: give it the
    default one now. An upgrade keeps the one the studio wrote. }
  if RunHidden(ExpandConstant('{app}\python\python.exe'),
      '-m baja_studio.modules.gateway.caddyfile --if-missing --data-dir ' + AddQuotes(Data)) <> 0 then
    FailedSteps := FailedSteps + #13#10 + 'writing the default Caddyfile';
  ConfigureServices();
  ConfigureFirewall();
  NssmStep('start ' + StudioService);
  NssmStep('start ' + GatewayService);
  if FailedSteps <> '' then
    SuppressibleMsgBox('Baja Studio is installed, but these steps failed:' + FailedSteps + #13#10#13#10 +
      'The setup log (in %TEMP%) has the details.', mbError, MB_OK, IDOK);
end;

{ --- Uninstall -------------------------------------------------------------------- }

function InitializeUninstall(): Boolean;
begin
  UninstallDataDirectory := PreviousDataDirectory();
  KeepDataOnUninstall := ExpandConstant('{param:PURGEDATA|0}') <> '1';
  if KeepDataOnUninstall and (UninstallDataDirectory <> '') then
    KeepDataOnUninstall := SuppressibleMsgBox(
      'Keep the settings, playout queue, logs and certificates in ' + UninstallDataDirectory +
      '?' + #13#10#13#10 + 'Yes keeps them for a later install. No deletes them.',
      mbConfirmation, MB_YESNO, IDYES) = IDYES;
  Result := True;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then
  begin
    Nssm('stop ' + GatewayService);
    Nssm('stop ' + StudioService);
    Nssm('remove ' + GatewayService + ' confirm');
    Nssm('remove ' + StudioService + ' confirm');
    NetSh('delete rule name="' + FirewallRule + '"');
  end;
  if (CurUninstallStep = usPostUninstall) and not KeepDataOnUninstall and
     (UninstallDataDirectory <> '') then
    DelTree(UninstallDataDirectory, True, True, True);
end;
