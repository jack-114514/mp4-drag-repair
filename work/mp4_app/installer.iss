#define AppVersion "1.2.5"
#define SourceRoot AddBackslash(SourcePath)
#define AppExe "MP4一拖修复_v" + AppVersion + ".exe"
#define AppFolder "MP4一拖修复_v" + AppVersion
#ifndef AppBuildDir
  #define AppBuildDir SourceRoot + "..\..\outputs\" + AppFolder
#endif

[Setup]
AppId={{37EC29A8-BD43-4C78-A3FA-8E8A00B6A943}
AppName=MP4一拖修复
AppVersion={#AppVersion}
AppVerName=MP4一拖修复 {#AppVersion}
AppPublisher=Local Tools
AppComments=猫抓大文件转标准 MP4；自动选择 CPU/GPU；离线处理
DefaultDirName={localappdata}\Programs\MP4一拖修复
DefaultGroupName=MP4一拖修复
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64os
ArchitecturesInstallIn64BitMode=x64os
MinVersion=10.0
OutputDir={#SourceRoot}..\..\outputs
OutputBaseFilename=MP4一拖修复_安装包_v{#AppVersion}
SetupIconFile={#SourceRoot}app.ico
UninstallDisplayIcon={app}\{#AppExe}
Compression=lzma2/fast
SolidCompression=yes
WizardStyle=modern
CloseApplications=no
RestartApplications=no
SetupLogging=yes

[Languages]
Name: "chinesesimp"; MessagesFile: "{#SourceRoot}ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "快捷方式："

[Files]
Source: "{#AppBuildDir}\{#AppExe}"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#AppBuildDir}\_internal\*"; DestDir: "{app}\_internal"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#SourceRoot}使用说明.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceRoot}licenses\*"; DestDir: "{app}\开源许可证"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#SourceRoot}mp4_normalizer.py"; DestDir: "{app}\源码"; Flags: ignoreversion
Source: "{#SourceRoot}app_ui.py"; DestDir: "{app}\源码"; Flags: ignoreversion
Source: "{#SourceRoot}hardware_info.py"; DestDir: "{app}\源码"; Flags: ignoreversion

[Icons]
Name: "{group}\MP4一拖修复"; Filename: "{app}\{#AppExe}"; WorkingDir: "{app}"
Name: "{autodesktop}\MP4一拖修复"; Filename: "{app}\{#AppExe}"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "打开 MP4一拖修复"; Flags: nowait postinstall skipifsilent
