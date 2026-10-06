; LegacyOXT installer for Windows x86-64 (Inno Setup 6.3 or later): LiveCode
; Community, built from LiveCode's source in this repository, installed as
; LiveCode's own installer laid it out.
;
; Packs the staged installed layout written by tools/oxt/package.py
; (dist/stage/LegacyOXT-<version>/) into LegacyOXT-<version>-win-x86_64-setup.exe.
; tools/ci/build-installer.ps1 runs the compiler with these defines:
;
;   /DAppVersion=<version>      LiveCode's BUILD_SHORT_VERSION, such as 9.7.0-dp-1
;   /DProductTitle=<title>      LiveCode's ProductTitle, such as
;                               "LiveCode Community 9.7 (dp 1)"
;   /DBuildNumber=<number>      the CI build number
;   /DStageDir=<folder>         the staged installed layout
;   /DOutputDir=<folder>        where the setup program is written (dist)
;   /DRepoRoot=<folder>         repository root (for engine/rsrc/installer.ico)
;
; What it installs follows LiveCode's installer (Installer/package.txt and
; builder/installer): the program folder named ProductTitle, the engine
; "LiveCode Community.exe", shortcuts named ProductTitle in the Start menu
; and on the desktop, an entry in Settings > Apps, and no file types
; (LiveCode's installer registered none). Its parent folder is LegacyOXT
; instead of LiveCode's RunRev, so that it never replaces an installed
; LiveCode.
;
; Setup first asks whether to install for all users (Program Files, needs
; administrator rights; the default) or for the current user only
; (%LOCALAPPDATA%\Programs); /ALLUSERS or /CURRENTUSER on the command line
; choose without asking. The uninstaller removes the program files and
; shortcuts, but not the IDE's preferences, which it keeps in the user's
; own folders.

#if VER < EncodeVer(6, 3, 0, 0)
  #error Inno Setup 6.3 or later is required to compile this script.
#endif

#define AppFileName "LegacyOXT"
#define AppExeName "LiveCode Community.exe"
#define RepoUrl "https://github.com/SethMorrowSoftware/LegacyOXT-9.7"

#ifndef RepoRoot
  #define RepoRoot AddBackslash(SourcePath) + "..\.."
#endif

#ifndef AppVersion
  #error Define AppVersion (tools/ci/build-installer.ps1 does).
#endif
#ifndef ProductTitle
  #error Define ProductTitle (tools/ci/build-installer.ps1 does).
#endif

#ifndef StageDir
  #define StageDir RepoRoot + "\dist\stage\" + AppFileName + "-" + AppVersion
#endif

#ifndef OutputDir
  #define OutputDir RepoRoot + "\dist"
#endif

#ifndef BuildNumber
  #define BuildNumber "0"
#endif

#if !DirExists(StageDir)
  #pragma message "Staged layout not found: " + StageDir
  #error The staged installed layout does not exist. Run tools/oxt/package.py first (see tools/ci/build-installer.ps1).
#endif
#if !FileExists(StageDir + "\" + AppExeName)
  #pragma message "Missing: " + StageDir + "\" + AppExeName
  #error The staged layout has no LiveCode Community.exe.
#endif
#if !FileExists(StageDir + "\License Agreement.txt")
  #error The staged layout has no License Agreement.txt.
#endif

; Windows version resources take numbers only: drop a pre-release suffix
; (9.7.0-dp-1 -> 9.7.0).
#define NumericVersion AppVersion
#if Pos("-", NumericVersion) > 0
  #define NumericVersion Copy(NumericVersion, 1, Pos("-", NumericVersion) - 1)
#endif
#if Pos("+", NumericVersion) > 0
  #define NumericVersion Copy(NumericVersion, 1, Pos("+", NumericVersion) - 1)
#endif

#define SetupIcon RepoRoot + "\engine\rsrc\installer.ico"
#if !FileExists(SetupIcon)
  #pragma message "Missing: " + SetupIcon
  #error engine/rsrc/installer.ico was not found.
#endif

[Setup]
; Fixed for every build of this version line: it names the uninstall
; registry key ({4F0E2A71-9C3B-4D8E-A5B6-7C1D2E3F4A97}_is1) and lets a new
; build replace an installed one. Never change it.
AppId={{4F0E2A71-9C3B-4D8E-A5B6-7C1D2E3F4A97}
AppName={#ProductTitle}
AppVersion={#AppVersion}
; Settings > Apps shows LiveCode's ProductTitle, as LiveCode's installer
; registered it (DisplayName), with the package's name after it
AppVerName={#ProductTitle} (LegacyOXT)
UninstallDisplayName={#ProductTitle} (LegacyOXT)
AppPublisher=LegacyOXT (an unofficial build, not by LiveCode Ltd)
AppPublisherURL={#RepoUrl}
AppSupportURL={#RepoUrl}/issues
AppUpdatesURL={#RepoUrl}/releases
AppComments=LiveCode Community {#AppVersion}, built from LiveCode Ltd's GPL source by the LegacyOXT project. Not affiliated with or endorsed by LiveCode Ltd.
DefaultDirName={autopf}\LegacyOXT\{#ProductTitle}
DefaultGroupName=LegacyOXT
DisableProgramGroupPage=yes
UninstallDisplayIcon={app}\{#AppExeName}
; The engine and all externals are 64-bit (x64). x64compatible also admits
; Windows 11 on Arm64, which runs x64 programs.
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=admin
PrivilegesRequiredOverridesAllowed=dialog commandline
LicenseFile={#StageDir}\License Agreement.txt
OutputDir={#OutputDir}
OutputBaseFilename={#AppFileName}-{#AppVersion}-win-x86_64-setup
SetupIconFile={#SetupIcon}
WizardStyle=modern
Compression=lzma2/max
SolidCompression=yes
; Compress two blocks of the solid stream in parallel (each block also uses
; two match-finder threads); the compressor runs as a separate 64-bit process.
LZMANumBlockThreads=2
LZMAUseSeparateProcess=yes
; Always write a Setup log to %TEMP%, for bug reports.
SetupLogging=yes
VersionInfoVersion={#NumericVersion}
VersionInfoProductVersion={#NumericVersion}
VersionInfoTextVersion={#AppVersion}
VersionInfoProductTextVersion={#AppVersion} (build {#BuildNumber})
VersionInfoProductName=LegacyOXT: {#ProductTitle}
VersionInfoDescription=LegacyOXT Setup: {#ProductTitle}
VersionInfoCompany=LegacyOXT

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
; LiveCode's installer made a desktop shortcut unless it was unticked
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[InstallDelete]
; When a new build is installed over an older one, remove the program
; folders that the IDE and engine load by listing their contents (every
; palette and library under Toolset, every extension under Extensions,
; every external under Externals and Ext), so files that a newer build no
; longer ships are not picked up. These folders hold program files only:
; the IDE keeps user extensions, plugins and preferences in the user's own
; folders.
Type: filesandordirs; Name: "{app}\Toolset"
Type: filesandordirs; Name: "{app}\Extensions"
Type: filesandordirs; Name: "{app}\Externals"
Type: filesandordirs; Name: "{app}\Ext"
Type: filesandordirs; Name: "{app}\Toolchain"
Type: filesandordirs; Name: "{app}\Runtime"

[Files]
Source: "{#StageDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
; package.txt: shortcut "Programs/LiveCode/[[ProductTitle]]" and
; "Desktop/[[ProductTitle]]" (in a LegacyOXT folder of the Start menu)
Name: "{group}\{#ProductTitle}"; Filename: "{app}\{#AppExeName}"; WorkingDir: "{app}"
Name: "{group}\{cm:UninstallProgram,{#ProductTitle}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#ProductTitle}"; Filename: "{app}\{#AppExeName}"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "{cm:LaunchProgram,{#ProductTitle}}"; WorkingDir: "{app}"; Flags: nowait postinstall skipifsilent
