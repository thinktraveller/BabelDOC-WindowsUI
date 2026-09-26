; BabelDOC 本地翻译工作台 —— Inno Setup 安装脚本（用户级安装，不含敏感注册表项）
;
; 构建前先执行：
;   .\.venv\Scripts\python.exe desktop\packaging\build_portable.py
; 然后用 Inno Setup 6 编译本文件：
;   iscc desktop\packaging\installer.iss

#define MyAppName "BabelDOC 工作台"
#define MyAppVersion "0.1.0"
#define MyAppPublisher "BabelDOC Workbench"
#define MyAppExeName "BabelDOC.exe"
#define SourceDir "..\..\release\BabelDOC-portable"

[Setup]
AppId={{8E3C1C4E-3D2A-4F0F-9C1E-BABELDOCWORKBENCH}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
; 安装到用户目录，不需要管理员权限
DefaultDirName={localappdata}\Programs\BabelDOC
PrivilegesRequired=lowest
DisableProgramGroupPage=yes
OutputDir=..\..\release
OutputBaseFilename=BabelDOC-Setup-{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
SetupIconFile=assets\babeldoc.ico
UninstallDisplayName={#MyAppName}
UninstallDisplayIcon={app}\{#MyAppExeName}

[Languages]
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务："; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "启动 {#MyAppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; 只删除安装目录，不触碰用户数据
Type: filesandordirs; Name: "{app}"

[Code]
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then
  begin
    MsgBox('已卸载 BabelDOC 工作台。' + #13#10 +
           '你的任务、术语表与日志仍保留在：' + #13#10 +
           ExpandConstant('{localappdata}\BabelDOC Workbench') + #13#10 +
           '如需彻底清理，请手动删除该目录。', mbInformation, MB_OK);
  end;
end;
