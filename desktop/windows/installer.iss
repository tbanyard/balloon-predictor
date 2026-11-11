#define AppName "BalloonPredictor"
#define RepoUrl "https://github.com/tbanyard/balloon-predictor"

[Setup]
AppId={{B315DEEA-B38F-4D79-94F8-80E5E4646205}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=BalloonPredictor
AppPublisherURL={#RepoUrl}
AppSupportURL={#RepoUrl}/issues
DefaultDirName={localappdata}\Programs\BalloonPredictor
DefaultGroupName=BalloonPredictor
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64os
ArchitecturesInstallIn64BitMode=x64os
MinVersion=10.0.19045
AppMutex=Local\BalloonPredictor.Desktop
WizardStyle=modern
Compression=lzma2/max
SolidCompression=yes
SetupIconFile={#Payload}\app\desktop\windows\BalloonPredictor.ico
UninstallDisplayIcon={app}\app\desktop\windows\BalloonPredictor.ico
OutputBaseFilename=BalloonPredictor-{#AppVersion}-Windows-x64-Setup
VersionInfoVersion={#AppVersion}
VersionInfoProductName={#AppName}
VersionInfoDescription=BalloonPredictor Setup

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked

[Files]
Source: "{#Bootstrapper}"; Flags: dontcopy
Source: "{#Payload}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\BalloonPredictor"; Filename: "{app}\runtime\pythonw.exe"; Parameters: """{app}\app\desktop\launcher.py"""; IconFilename: "{app}\app\desktop\windows\BalloonPredictor.ico"; WorkingDir: "{app}"
Name: "{autodesktop}\BalloonPredictor"; Filename: "{app}\runtime\pythonw.exe"; Parameters: """{app}\app\desktop\launcher.py"""; IconFilename: "{app}\app\desktop\windows\BalloonPredictor.ico"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\runtime\pythonw.exe"; Parameters: """{app}\app\desktop\launcher.py"""; Description: "Start BalloonPredictor"; WorkingDir: "{app}"; Flags: nowait postinstall skipifsilent

[Code]
const
  WebViewKey = 'Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}';
  WebViewSetup = 'MicrosoftEdgeWebview2Setup.exe';

function HasWebView2(): Boolean;
var
  Version: String;
begin
  Result :=
    (RegQueryStringValue(HKLM32, WebViewKey, 'pv', Version) and
      (Version <> '') and (Version <> '0.0.0.0')) or
    (RegQueryStringValue(HKCU, WebViewKey, 'pv', Version) and
      (Version <> '') and (Version <> '0.0.0.0'));
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ExitCode: Integer;
begin
  Result := '';
  if HasWebView2() then
    Exit;

  ExtractTemporaryFile(WebViewSetup);
  WizardForm.StatusLabel.Caption := 'Installing Microsoft Edge WebView2 Runtime...';
  if Exec(ExpandConstant('{tmp}\' + WebViewSetup), '/silent /install', '',
    SW_HIDE, ewWaitUntilTerminated, ExitCode) then
    Log(Format('WebView2 bootstrapper exit code: %d', [ExitCode]));

  if not HasWebView2() then
    Result := 'BalloonPredictor requires Microsoft Edge WebView2 Runtime. ' +
      'Connect to the internet and run Setup again, or install WebView2 from Microsoft.';
end;
