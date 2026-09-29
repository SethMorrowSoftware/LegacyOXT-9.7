# This file set the libraries version variables to the version defined in versions/

script_dir=$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )

function readVersion {
	local LIBNAME=$1
	local LIBNAME_LC=$(tr "[:upper:]" "[:lower:]" <<< $LIBNAME)
	
	local VERSION=$(cat "${script_dir}/../versions/${LIBNAME_LC}")
	eval ${LIBNAME}_VERSION='"${VERSION}"'
	
	if [ -f "${script_dir}/../versions/${LIBNAME_LC}_buildrevision" ] ; then
		local BUILDREVISION=$(cat "${script_dir}/../versions/${LIBNAME_LC}_buildrevision")
		eval ${LIBNAME}_BUILDREVISION='"${BUILDREVISION}"'
	fi

	# Per-platform version: versions/<lib>_<platform> (e.g. openssl_win32)
	# sets <Lib>_VERSION_<platform>, which fetch-libraries.sh uses for that
	# platform instead of the common version. It lets one platform keep using
	# published prebuilts while the others move to a newer release.
	local PLATFORM_FILE
	for PLATFORM_FILE in "${script_dir}/../versions/${LIBNAME_LC}"_* ; do
		local SUFFIX="${PLATFORM_FILE##*/${LIBNAME_LC}_}"
		case "${SUFFIX}" in
			win32|linux|mac|ios|android|emscripten)
				local PLATFORM_VERSION=$(cat "${PLATFORM_FILE}")
				eval ${LIBNAME}_VERSION_${SUFFIX}='"${PLATFORM_VERSION}"'
				;;
		esac
	done
}

readVersion OpenSSL
readVersion ICU
readVersion Curl
readVersion CEF
readVersion CEFChromium

# The Thirdparty version used to be the commit of the thirdparty git submodule.
# thirdparty/ is now vendored into this repository, so the version is pinned in
# versions/thirdparty instead: it is the livecode-thirdparty commit that the
# published Thirdparty prebuilts were built from. Change it when publishing a
# new Thirdparty prebuilt.
readVersion Thirdparty

# Libraries used on all platforms
Thirdparty_LIBS_CORE="libffi libpng libz libgif libjpeg libpcre libskia"

# Libraries used only on native (non emscripten) platforms
Thirdparty_LIBS_NATIVE="libmysql libsqlite libxml libxslt libzip"

# Libraries used only on desktop platforms
Thirdparty_LIBS_DESKTOP="libiodbc libpq libcairo"

# Libraries used by emscripten / android for fonts
Thirdparty_LIBS_FONTS="libharfbuzz libfreetype"

Thirdparty_LIBS_mac="$Thirdparty_LIBS_CORE $Thirdparty_LIBS_NATIVE $Thirdparty_LIBS_DESKTOP"
Thirdparty_LIBS_linux="$Thirdparty_LIBS_CORE $Thirdparty_LIBS_NATIVE $Thirdparty_LIBS_DESKTOP"
Thirdparty_LIBS_win32="$Thirdparty_LIBS_CORE $Thirdparty_LIBS_NATIVE $Thirdparty_LIBS_DESKTOP"
Thirdparty_LIBS_ios="$Thirdparty_LIBS_CORE $Thirdparty_LIBS_NATIVE libcairo"
Thirdparty_LIBS_emscripten="$Thirdparty_LIBS_CORE $Thirdparty_LIBS_FONTS"
Thirdparty_LIBS_android="$Thirdparty_LIBS_CORE $Thirdparty_LIBS_NATIVE $Thirdparty_LIBS_FONTS libexpat libcairo"
