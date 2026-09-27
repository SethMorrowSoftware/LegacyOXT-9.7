@echo off
REM Configures the Windows build
REM
REM Optional environment variables:
REM   PYTHON       The Python 2.7 python.exe to run config.py with. The default
REM                is C:\Python27\python.exe if it exists, else python on the PATH.
REM                If PYTHON is not Python 2, C:\Python27\python.exe is used
REM                instead when it exists; otherwise configure.bat stops
REM   TARGET_ARCH  x64 or x86_64 (the default), or x86 or i386
REM   NOPAUSE, CI  If either is set, never wait for a key press

SETLOCAL EnableExtensions

REM Set this variable if any warnings have been reported
SET warnings=0

REM Not all versions of windows the the %programfiles(x86)% variable
IF NOT DEFINED programfiles(x86) SET programfiles(x86)=%programfiles%

REM When calling configure.bat from the command line, BUILD_EDITION is not defined
IF NOT DEFINED BUILD_EDITION SET BUILD_EDITION=community

REM Target architecture currently defaults to 64-bit x86_64
IF NOT DEFINED TARGET_ARCH SET TARGET_ARCH=x64

REM Make sure TARGET_ARCH is always x86 or x86_64

IF %TARGET_ARCH%==x64 (
  SET TARGET_ARCH=x86_64
) ELSE IF %TARGET_ARCH%==i386 (
  SET TARGET_ARCH=x86
) ELSE IF %TARGET_ARCH% ==x86 (
  REM Valid
) ELSE IF %TARGET_ARCH% == x86_64 (
  REM Valid
) ELSE (
  ECHO >&2 Error: invalid target arch %TARGET_ARCH%
  EXIT /B 1
)

REM Attempt to locate a copy of Python 2.7, which config.py and gyp need: use
REM PYTHON if it is set, else C:\Python27\python.exe, else python on the PATH
IF DEFINED PYTHON SET "PYTHON=%PYTHON:"=%"
IF NOT DEFINED PYTHON IF EXIST C:\Python27\python.exe SET PYTHON=C:\Python27\python.exe
IF NOT DEFINED PYTHON (
  WHERE /Q python 1>NUL 2>NUL
  IF ERRORLEVEL 1 (
    ECHO >&2 Error: could not locate a copy of python. Install Python 2.7 in C:\Python27 or set PYTHON to its python.exe
    CALL :pause
    EXIT /B 1
  )
  SET PYTHON=python
)

REM config.py and gyp only run on Python 2. PYTHON is also used by other tools
REM (node-gyp, for example) to name a Python 3, so if it does not run Python 2,
REM fall back to C:\Python27\python.exe when that exists, and otherwise stop
"%PYTHON%" -c "import sys; sys.exit(0 if sys.version_info[0] == 2 else 1)" 1>NUL 2>NUL
IF NOT ERRORLEVEL 1 GOTO python_ok
IF /I NOT "%PYTHON%"=="C:\Python27\python.exe" IF EXIST C:\Python27\python.exe (
  ECHO Note: "%PYTHON%" is not a working Python 2, using C:\Python27\python.exe instead
  SET PYTHON=C:\Python27\python.exe
  GOTO python_ok
)
ECHO >&2 Error: config.py and gyp need Python 2.7, but "%PYTHON%" is not Python 2 or could not be run. Install Python 2.7 in C:\Python27 or set PYTHON to a Python 2.7 python.exe
CALL :pause
EXIT /B 1

:python_ok
REM Show which Python will be used
"%PYTHON%" -c "import sys, platform; print('Using Python ' + platform.python_version() + ' from ' + sys.executable)"
IF ERRORLEVEL 1 (
  ECHO >&2 Error: could not run "%PYTHON%"
  CALL :pause
  EXIT /B 1
)

REM The Microsoft Speech SDK 5.1 is not needed: revSpeech uses sapi.h from the
REM Windows SDK.

REM Pause so any warnings can be seen
IF %warnings% NEQ 0 CALL :pause

REM Run the configure step
"%PYTHON%" config.py --platform win-%TARGET_ARCH% %extra_options% %gypfile%
SET result=%ERRORLEVEL%

REM Pause so that the user gets a chance to see the output, including any error
IF %result% NEQ 0 ECHO >&2 Error: config.py failed with exit code %result%
CALL :pause
EXIT /B %result%

REM Wait for a key press, unless NOPAUSE or CI is set (unattended builds)
:pause
IF DEFINED NOPAUSE GOTO :EOF
IF DEFINED CI GOTO :EOF
PAUSE
GOTO :EOF
