@echo off

REM This batch file forwards commands to a Unix-like shell

REM We use the delayed-expansion feature of the command processor
SETLOCAL EnableDelayedExpansion
SETLOCAL EnableExtensions

SET commands=%*

REM Does this look like a wine environment?
REM We guess this by looking for a common-ish Wine env var
IF DEFINED WINEDEBUG (
  @echo Invoking Unix command '%commands%' via Wine
  %~dp0\invoke-unix-wine.exe %commands%
  EXIT %ERRORLEVEL%
)

REM Locate Cygwin. Prefer an explicit installation: CYGPATH if it is set,
REM then C:\Cygwin64, then C:\Cygwin. Only when none of those exist, fall back
REM to a cygpath.exe on the PATH. That can be Git for Windows' usr\bin, whose
REM bash has no flex or bison, so it is the last resort; its folder is then put
REM first on the PATH so that the bash.exe next to it is used, not for example
REM the WSL launcher in System32. The Cygwin path must not contain spaces.
SET cygwin_path=
SET cygwin_path_dir=
IF DEFINED CYGPATH (
  IF NOT EXIST "!CYGPATH!\bin\bash.exe" (
    @ECHO >&2 CYGPATH is set to "!CYGPATH!" but "!CYGPATH!\bin\bash.exe" does not exist
    EXIT 1
  )
  SET "cygwin_path=!CYGPATH!\bin\"
) ELSE IF EXIST C:\Cygwin64\bin\bash.exe (
  SET cygwin_path=C:\Cygwin64\bin\
) ELSE IF EXIST C:\Cygwin\bin\bash.exe (
  SET cygwin_path=C:\Cygwin\bin\
) ELSE (
  FOR /F "delims=" %%G IN ('WHERE cygpath.exe 2^>NUL') DO (
    IF NOT DEFINED cygwin_path_dir SET "cygwin_path_dir=%%~dpG"
  )
  IF NOT DEFINED cygwin_path_dir (
    @ECHO >&2 Cannot locate a Cygwin installation: install Cygwin in C:\Cygwin64 or set CYGPATH to its root folder
    EXIT 1
  )
  SET "PATH=!cygwin_path_dir!;!PATH!"
)

REM We need to work around what looks to be a MCVS bug: the final parameter is
REM missing the terminating '"' in come circumstances
SET commands=%commands%

@echo Invoking Unix command '!commands!' via Cygwin

REM Obscure way to get the cmd.exe equivalent to `...` substitution
FOR /F "usebackq tokens=*" %%x IN (`%cygwin_path%cygpath.exe %CD%`) DO SET cygwin_cd=%%x
FOR %%x IN (!commands!) DO (
  FOR /F "usebackq tokens=*" %%y IN (`%cygwin_path%bash.exe -c 'if [[ \'%%x\' ^=^= -* ]] ^; then echo \'%%x\' ^; else /bin/cygpath \'%%x\' ^; fi'`) DO SET cygwin_cmd=!cygwin_cmd! %%y
)

REM All the parameters have been Unix-ified; run the command. Put the Cygwin
REM bin folder first on the PATH (Windows separates entries with ';') so that
REM bash finds Cygwin's tools before same-named Windows programs such as the
REM tar.exe and curl.exe in System32.
IF DEFINED cygwin_path SET PATH=%cygwin_path%;%PATH%
%cygwin_path%bash.exe -c 'cd !cygwin_cd! ^&^& !cygwin_cmd!'
EXIT %ERRORLEVEL%

