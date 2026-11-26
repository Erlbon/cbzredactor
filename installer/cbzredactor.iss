#if 0
; The Windows installer of The ƆBZ Redactor (Inno Setup 6): the one-folder build (dist\cbzredactor\, built with
; CBZREDACTOR_BUILD=folder; it starts faster than the one-file exe, which unpacks itself at every start) into the
; user's own programs folder, with a Start menu entry. The one-file dist\cbzredactor.exe stays the portable download.
;
; Build:   build_exe.bat (it builds both programs and passes /DUpdateContract), or by hand:
;          iscc /DAppBuild=2026-11-26#02 /DUpdateContract=<python -m redactor_common.release contract-path> installer\cbzredactor.iss
; Output:  dist\cbzredactor-setup.exe
;
; Per user, no administrator rights: %LOCALAPPDATA%\Programs\The CBZ Redactor. Updating goes through the family's
; update framework (redactor_common docs/update-framework.md): its installer contract, included at the END of this
; file, writes {app}\update-channel = windows-installer, so Help > Check for Updates runs the new
; cbzredactor-setup.exe the release's update-manifest.json names, silently with /relaunch=yes, and the contract
; starts the program again. The settings, crash log and logs sit beside the exe (the Redactor apps' convention):
; an update leaves them, and so does the uninstall.
;
; These notes sit in "#if 0", as in the shared contract.
#endif

#ifndef AppBuild
  #define AppBuild "0000-00-00#00"
#endif
#ifndef UpdateContract
  #error Pass /DUpdateContract (build_exe.bat does: python -m redactor_common.release contract-path)
#endif

; For the installer contract included at the end. The program has no --quit: Windows' close request ends it.
#define UpdateExe "cbzredactor.exe"

[Setup]
AppId={{0CE15C55-4C12-42E8-A9E8-656019110013}
AppName=The ƆBZ Redactor
AppVersion={#AppBuild}
AppVerName=The ƆBZ Redactor {#AppBuild}
AppPublisher=Erlbon
AppPublisherURL=https://github.com/Erlbon/cbzredactor
AppSupportURL=https://github.com/Erlbon/cbzredactor/issues
AppUpdatesURL=https://github.com/Erlbon/cbzredactor/releases
VersionInfoVersion=0.0.0.0
VersionInfoProductTextVersion={#AppBuild}
PrivilegesRequired=lowest
DefaultDirName={autopf}\The CBZ Redactor
DefaultGroupName=The ƆBZ Redactor
DisableProgramGroupPage=yes
UninstallDisplayIcon={app}\cbzredactor.exe
UninstallDisplayName=The ƆBZ Redactor
SetupIconFile=..\assets\icon.ico
LicenseFile=..\LICENSE
OutputDir=..\dist
OutputBaseFilename=cbzredactor-setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes
RestartApplications=no

[Tasks]
Name: "desktopicon"; Description: "A shortcut on the desktop"; GroupDescription: "Shortcuts:"; Flags: unchecked

[Files]
Source: "..\dist\cbzredactor\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\README.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\LICENSE"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\The ƆBZ Redactor"; Filename: "{app}\cbzredactor.exe"
Name: "{autodesktop}\The ƆBZ Redactor"; Filename: "{app}\cbzredactor.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\cbzredactor.exe"; Description: "Start The ƆBZ Redactor"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; what Help > Check for Updates may leave beside the exe: the old exe after a swap, and a download that did not finish
Type: files; Name: "{app}\cbzredactor.exe.old"
Type: files; Name: "{app}\cbzredactor.exe.new"
Type: files; Name: "{app}\cbzredactor.exe.new.part"

#include UpdateContract
