#define AppName "OculiDoC"
#ifndef AppVersion
  #error AppVersion must be supplied by the release script.
#endif
#ifndef SourceDir
  #error SourceDir must be supplied by the release script.
#endif
#ifndef OutputDir
  #error OutputDir must be supplied by the release script.
#endif

[Setup]
AppId={{0D948729-9AE7-43F4-99E7-4C2A156C970A}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=Etymodes
AppPublisherURL=https://github.com/Etymodes/OculiDoC
AppSupportURL=https://github.com/Etymodes/OculiDoC/issues
AppUpdatesURL=https://github.com/Etymodes/OculiDoC/releases
DefaultDirName={code:DefaultInstallDir}
UsePreviousAppDir=yes
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#OutputDir}
OutputBaseFilename=OculiDoC-Setup
SetupIconFile={#SourceDir}\_internal\oculidoc\assets\app_icon.ico
UninstallDisplayIcon={app}\OculiDoC.exe
LicenseFile={#SourceDir}\LICENSE-v0.1.1.txt
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no
VersionInfoVersion={#AppVersion}
AppComments=仅限非临床工程评估；不是医疗器械或医院官方发行。

[Languages]
Name: "chinesesimp"; MessagesFile: "languages\ChineseSimplified.isl"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
Type: filesandordirs; Name: "{app}\_internal"

[Icons]
Name: "{autodesktop}\OculiDoC"; Filename: "{app}\OculiDoC.exe"; WorkingDir: "{app}"
Name: "{autoprograms}\OculiDoC"; Filename: "{app}\OculiDoC.exe"; WorkingDir: "{app}"
Name: "{autoprograms}\卸载 OculiDoC"; Filename: "{uninstallexe}"

[Run]
Filename: "{app}\OculiDoC.exe"; Description: "启动 OculiDoC"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}\_internal"
Type: files; Name: "{app}\OculiDoC.exe"

[Code]
const
  LatestSetupUrl =
    'https://github.com/Etymodes/OculiDoC/releases/latest/download/OculiDoC-Setup.exe';
  LatestHashUrl =
    'https://github.com/Etymodes/OculiDoC/releases/latest/download/OculiDoC-Setup.exe.sha256';

var
  InstallModePage: TInputOptionWizardPage;
  DownloadPage: TDownloadWizardPage;
  OnlineHandoff: Boolean;

function HasCommandLineParam(Value: String): Boolean;
var
  Index: Integer;
begin
  Result := False;
  for Index := 1 to ParamCount do
    if CompareText(ParamStr(Index), Value) = 0 then
    begin
      Result := True;
      Exit;
    end;
end;

function DefaultInstallDir(Param: String): String;
var
  PortableDir: String;
  LegacyV010Dir: String;
begin
  PortableDir := ExpandConstant('{localappdata}\Programs\OculiDoC');
  LegacyV010Dir :=
    ExpandConstant('{localappdata}\Programs\OculiDoC-v0.1.0\OculiDoC');
  if DirExists(PortableDir) then
    Result := PortableDir
  else if FileExists(LegacyV010Dir + '\OculiDoC.exe') then
    Result := LegacyV010Dir
  else
    Result := ExpandConstant('{localappdata}\Programs\OculiDoC');
end;

procedure InitializeWizard;
begin
  OnlineHandoff := False;
  DownloadPage := CreateDownloadPage(
    '正在获取最新版本',
    '正在下载并校验 GitHub 上的 OculiDoC 正式安装包……',
    nil
  );
  if not HasCommandLineParam('/OFFLINE') then
  begin
    InstallModePage := CreateInputOptionPage(
      wpWelcome,
      '选择安装方式',
      '安装 OculiDoC {#AppVersion}',
      '在线模式会获取 GitHub 上的最新正式版；离线模式安装本安装包内置版本。',
      True,
      False
    );
    InstallModePage.Add('在线安装最新版本（需要联网）');
    InstallModePage.Add('离线安装当前版本 {#AppVersion}');
    InstallModePage.SelectedValueIndex := 0;
  end;
end;

function ReadExpectedHash(HashPath: String): String;
var
  HashText: AnsiString;
begin
  if not LoadStringFromFile(HashPath, HashText) then
    RaiseException('无法读取最新安装包的 SHA-256 文件。');
  Result := Lowercase(Trim(Copy(String(HashText), 1, 64)));
  if Length(Result) <> 64 then
    RaiseException('最新安装包的 SHA-256 文件格式无效。');
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var
  SetupPath: String;
  HashPath: String;
  ExpectedHash: String;
  ActualHash: String;
  ResultCode: Integer;
begin
  Result := True;
  if (CurPageID <> wpReady) or
     (InstallModePage = nil) or
     (InstallModePage.SelectedValueIndex <> 0) then
    Exit;

  SetupPath := ExpandConstant('{tmp}\OculiDoC-Setup-latest.exe');
  HashPath := SetupPath + '.sha256';
  try
    DownloadTemporaryFile(LatestHashUrl, HashPath, '', nil);
    ExpectedHash := ReadExpectedHash(HashPath);
    DownloadPage.Clear;
    DownloadPage.Add(LatestSetupUrl, ExtractFileName(SetupPath), ExpectedHash);
    DownloadPage.Show;
    try
      DownloadPage.Download;
    finally
      DownloadPage.Hide;
    end;
    ActualHash := Lowercase(GetSHA256OfFile(SetupPath));
    if ActualHash <> ExpectedHash then
      RaiseException('最新安装包二次 SHA-256 校验失败，已停止安装。');
    if not Exec(
      SetupPath,
      '/OFFLINE',
      '',
      SW_SHOWNORMAL,
      ewNoWait,
      ResultCode
    ) then
      RaiseException('无法启动最新安装包。');
    OnlineHandoff := True;
    WizardForm.Close;
    Result := False;
  except
    MsgBox(
      GetExceptionMessage +
      Chr(13) + Chr(10) + Chr(13) + Chr(10) +
      '请返回并选择“离线安装当前版本”。',
      mbError,
      MB_OK
    );
    Result := False;
  end;
end;

procedure CancelButtonClick(CurPageID: Integer; var Cancel, Confirm: Boolean);
begin
  if OnlineHandoff then
    Confirm := False;
end;
