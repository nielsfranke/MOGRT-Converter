; Inno Setup script for MOGRT Converter (called by packaging/build.py)
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{6B1C8E2A-4F3D-4C1B-9E7A-2D5F8A9C1E30}
AppName=MOGRT Converter
AppVersion={#AppVersion}
AppPublisher=MOGRT Converter
DefaultDirName={autopf}\MOGRT Converter
DefaultGroupName=MOGRT Converter
OutputDir={#OutDir}
OutputBaseFilename=MOGRT-Converter-{#AppVersion}-Setup
SetupIconFile=..\icons\icon.ico
LicenseFile=..\..\LICENSE
Compression=lzma2
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64compatible
ChangesAssociations=yes
PrivilegesRequiredOverridesAllowed=dialog

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{group}\MOGRT Converter"; Filename: "{app}\MOGRT Converter.exe"
Name: "{autodesktop}\MOGRT Converter"; Filename: "{app}\MOGRT Converter.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Desktop-Verknüpfung anlegen"; Flags: unchecked
Name: "assoc"; Description: ".mogrt-Dateien mit MOGRT Converter öffnen"; Flags: unchecked

[Registry]
Root: HKA; Subkey: "Software\Classes\.mogrt\OpenWithProgids"; ValueType: string; ValueName: "MOGRTConverter.mogrt"; ValueData: ""; Flags: uninsdeletevalue; Tasks: assoc
Root: HKA; Subkey: "Software\Classes\MOGRTConverter.mogrt"; ValueType: string; ValueName: ""; ValueData: "Motion Graphics Template"; Flags: uninsdeletekey; Tasks: assoc
Root: HKA; Subkey: "Software\Classes\MOGRTConverter.mogrt\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\MOGRT Converter.exe,0"; Tasks: assoc
Root: HKA; Subkey: "Software\Classes\MOGRTConverter.mogrt\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\MOGRT Converter.exe"" ""%1"""; Tasks: assoc

[Run]
Filename: "{app}\MOGRT Converter.exe"; Description: "MOGRT Converter starten"; Flags: nowait postinstall skipifsilent
