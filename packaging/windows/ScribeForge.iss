#ifndef MyAppVersion
#define MyAppVersion "0.1.0"
#endif

[Setup]
AppId={{7E19A857-4127-4AC4-971B-D484A412F281}
AppName=ScribeForge
AppVersion={#MyAppVersion}
AppPublisher=ScribeForge
DefaultDirName={localappdata}\Programs\ScribeForge
DefaultGroupName=ScribeForge
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\..\dist\installer
OutputBaseFilename=ScribeForge-Setup-x64
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\scribeforge_desktop.exe
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

[Files]
Source: "..\..\clients\flutter_desktop\build\windows\x64\runner\Release\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\ScribeForge"; Filename: "{app}\scribeforge_desktop.exe"
Name: "{autodesktop}\ScribeForge"; Filename: "{app}\scribeforge_desktop.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\scribeforge_desktop.exe"; Description: "Launch ScribeForge"; Flags: nowait postinstall skipifsilent
