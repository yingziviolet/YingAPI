; Inno Setup 脚本:把 PyInstaller 产物打成 Windows 安装包
; 构建顺序:
;   1) cd console && npm run build
;   2) pyinstaller packaging/gateway.spec --noconfirm
;   3) iscc packaging/installer.iss
; 产物:packaging/output/LLMGateway-Setup-<版本>.exe

#define AppName "LLM Gateway"
; CI 里用 iscc /DAppVersion=x.y.z 按 git tag 覆盖;本地构建用默认值
#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif
#define AppPublisher "yingziviolet"
#define AppExeName "LLMGateway.exe"

[Setup]
AppId={{8F3C1A62-9E4D-4B7A-9C21-5D0E7A1B2C33}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\LLMGateway
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
; 装到 Program Files 需要管理员;数据写在 %LOCALAPPDATA%,卸载不动用户数据
PrivilegesRequired=admin
OutputDir=output
OutputBaseFilename=LLMGateway-Setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "chinesesimplified"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务:"
Name: "startupicon"; Description: "开机自动启动网关"; GroupDescription: "附加任务:"; Flags: unchecked

[Files]
Source: "..\dist\LLMGateway\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{group}\卸载 {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon
Name: "{userstartup}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: startupicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "立即启动网关"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; 只删程序目录残留;用户数据(%LOCALAPPDATA%\LLMGateway)保留
Type: filesandordirs; Name: "{app}\_internal"
