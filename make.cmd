@rem Try to detect if running on 64-bit or 32-bit windows
@rem Choose some sensible defaults
set ProgramFilesBase=%ProgramFiles(x86)%
if defined ProgramFiles(x86) (
    if not defined BUILD_PLATFORM set BUILD_PLATFORM=win-x86_64
) else (
    set ProgramFilesBase=%ProgramFiles%
    if not defined BUILD_PLATFORM set BUILD_PLATFORM=win-x86
)

@rem Guess build mode
if not defined BUILDTYPE set BUILDTYPE=Debug

@rem Guess build project
if not defined BUILD_EDITION set BUILD_EDITION=community
if /I "%BUILD_EDITION%"=="commercial" (
    set BUILD_PROJECT=livecode-commercial.sln
    set DEFAULT_TARGET=____\default
) else (
    set BUILD_PROJECT=livecode\livecode.sln
    set DEFAULT_TARGET=default
)

@rem Guess target architecture based on build platform
if /I "%BUILD_PLATFORM%"=="win-x86_64" (
    @set VSCMD_ARG_TGT_ARCH=x64
    @set MSBUILD_PLATFORM=x64
)
if /I "%BUILD_PLATFORM%"=="win-x86" (
    @set VSCMD_ARG_TGT_ARCH=x86
    @set MSBUILD_PLATFORM=Win32
)

@rem Unless VSINSTALLDIR is set, use vswhere to find Visual Studio. Prefer an
@rem installation that has the VS 2017 (v141) C++ toolset used by the generated
@rem projects, then VS 2017 itself (its v141 compiler is the VC.Tools component,
@rem as VS 2017 has no VC.v141 component), then any installation with the x86/x64
@rem C++ build tools, and finally fall back to the default VS 2017 Build Tools
@rem location.
set "VSWHERE_EXE=%ProgramFilesBase%\Microsoft Visual Studio\Installer\vswhere.exe"
if not defined VSINSTALLDIR if exist "%VSWHERE_EXE%" for /f "usebackq delims=" %%i in (`"%VSWHERE_EXE%" -nologo -latest -products * -requires Microsoft.VisualStudio.Component.VC.v141.x86.x64 -property installationPath`) do set "VSINSTALLDIR=%%i"
if not defined VSINSTALLDIR if exist "%VSWHERE_EXE%" for /f "usebackq delims=" %%i in (`"%VSWHERE_EXE%" -nologo -latest -products * -version [15.0^,16.0^) -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath`) do set "VSINSTALLDIR=%%i"
if not defined VSINSTALLDIR if exist "%VSWHERE_EXE%" for /f "usebackq delims=" %%i in (`"%VSWHERE_EXE%" -nologo -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath`) do set "VSINSTALLDIR=%%i"
if not defined VSINSTALLDIR set "VSINSTALLDIR=%ProgramFilesBase%\Microsoft Visual Studio\2017\BuildTools\"

@rem VSINSTALLDIR must not be quoted and must end with a backslash
set "VSINSTALLDIR=%VSINSTALLDIR:"=%"
if not "%VSINSTALLDIR:~-1%"=="\" set "VSINSTALLDIR=%VSINSTALLDIR%\"
if not exist "%VSINSTALLDIR%VC\Auxiliary\Build\vcvarsall.bat" goto no_vcvarsall
call "%VSINSTALLDIR%VC\Auxiliary\Build\vcvarsall.bat" %VSCMD_ARG_TGT_ARCH%
"%SystemRoot%\System32\where.exe" /q msbuild.exe
if errorlevel 1 goto no_msbuild

@rem gyp does not write WindowsTargetPlatformVersion into the projects, so the
@rem v141 toolset would otherwise look for the Windows 8.1 SDK. Use
@rem WINSDK_VERSION if it is set, otherwise the Windows SDK that vcvarsall
@rem selected (WindowsSDKVersion, which ends with a backslash).
set MSBUILD_WINSDK_VERSION=%WINSDK_VERSION%
if not defined MSBUILD_WINSDK_VERSION set MSBUILD_WINSDK_VERSION=%WindowsSDKVersion%
if defined MSBUILD_WINSDK_VERSION if "%MSBUILD_WINSDK_VERSION:~-1%"=="\" set MSBUILD_WINSDK_VERSION=%MSBUILD_WINSDK_VERSION:~0,-1%
set MSBUILD_WINSDK_ARG=
if defined MSBUILD_WINSDK_VERSION set MSBUILD_WINSDK_ARG=/p:WindowsTargetPlatformVersion=%MSBUILD_WINSDK_VERSION%

@if "%1" NEQ "" (
	set MSBUILD_TARGET_ARG=/t:%1
) else (
	set MSBUILD_TARGET_ARG=/t:%DEFAULT_TARGET%
)

@rem MSBUILD_EXTRA_ARGS, if set, is passed on to msbuild unchanged
msbuild %BUILD_PROJECT% /fl /flp:Verbosity=normal /nologo /m:1 %MSBUILD_TARGET_ARG% /p:Configuration=%BUILDTYPE% /p:Platform=%MSBUILD_PLATFORM% %MSBUILD_WINSDK_ARG% %MSBUILD_EXTRA_ARGS%

@exit %ERRORLEVEL%

:no_vcvarsall
@echo ERROR: Cannot find "%VSINSTALLDIR%VC\Auxiliary\Build\vcvarsall.bat". 1>&2
@echo Install Visual Studio 2022 or the VS 2022 Build Tools with the C++ build tools and the v141 toolset, or set VSINSTALLDIR to the Visual Studio installation folder. 1>&2
@exit 1

:no_msbuild
@echo ERROR: msbuild.exe is not on the PATH after running "%VSINSTALLDIR%VC\Auxiliary\Build\vcvarsall.bat". 1>&2
@exit 1
