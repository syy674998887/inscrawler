[Setup]
AppId=inscrawler
AppName=inscrawler
AppVersion={#AppVersion}
AppPublisher={#Publisher}
DefaultDirName={localappdata}\Programs\inscrawler
DefaultGroupName=inscrawler
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
MinVersion=10.0.17763
SetupArchitecture=x64
SetupIconFile=..\src\inscrawler\assets\logo.ico
UninstallDisplayIcon={app}\inscrawler.exe
VersionInfoVersion={#FileVersion}
VersionInfoProductTextVersion={#AppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern

[Files]
Source: "{#Payload}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\inscrawler"; Filename: "{app}\inscrawler.exe"; WorkingDir: "{app}"
