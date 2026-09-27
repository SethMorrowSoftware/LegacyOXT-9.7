REM # This file sets the libraries versions defined in versions/

FOR %%L in (Thirdparty OpenSSL ICU CEF Curl CEFChromium) DO (
	SET PREBUILT_LIB=%%L
	SET /P !PREBUILT_LIB!_VERSION=<versions\!PREBUILT_LIB!
	IF EXIST "versions\!PREBUILT_LIB!_buildrevision" (
		SET /P !PREBUILT_LIB!_BUILDREVISION=<versions\!PREBUILT_LIB!_buildrevision
	)
)

REM # Thirdparty_VERSION comes from versions\thirdparty like the others. It used
REM # to be the commit of the thirdparty git submodule; thirdparty\ is now vendored
REM # into this repository, so the version is pinned to the livecode-thirdparty
REM # commit that the published Thirdparty prebuilts were built from.
