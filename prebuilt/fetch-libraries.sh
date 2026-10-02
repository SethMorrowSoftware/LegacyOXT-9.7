#!/bin/bash

# Downloads the prebuilt third-party libraries into prebuilt/fetched and
# unpacks them (into prebuilt/unpacked/<Lib>/ for Windows).
#
# Environment variables (all optional):
#   PREBUILT_URL           Where to download the tarballs from (default below).
#   PREBUILT_LOCAL_DIR     A folder holding the .tar.bz2 files; they are copied
#                          from there instead of being downloaded.
#   PREBUILT_CACHE_DIR     Folder to download into instead of prebuilt/fetched.
#   PREBUILT_WIN32_LIBS    Windows libraries to fetch, e.g. "OpenSSL Curl".
#   PREBUILT_LINUX_LIBS, PREBUILT_MAC_LIBS
#                          The same for Linux and macOS.
#   PREBUILT_WIN32_SUBPLATFORMS
#                          Windows subplatforms to fetch, e.g.
#                          "v141_static_release" for a Release-only build.
#   PREBUILT_SKIP_VERIFY=1 Do not check tarballs against prebuilt/SHA256SUMS.
#   PREBUILT_STRICT=1      Fail when a tarball has no entry in SHA256SUMS.
# The folder variables also accept Windows paths (C:\...).

# Libraries to fetch
PLATFORMS=( mac linux win32 android ios emscripten )
ARCHS_android=( armv7 arm64 x86 x86_64 )
ARCHS_mac=( Universal )
ARCHS_ios=( Universal )
ARCHS_win32=( x86 x86_64 )
ARCHS_linux=( i386 x86_64 arm64 )
ARCHS_emscripten=( js )
LIBS_android=( Thirdparty OpenSSL ICU )
LIBS_mac=( Thirdparty OpenSSL ICU )
LIBS_ios=( Thirdparty OpenSSL ICU )
LIBS_win32=( Thirdparty OpenSSL Curl ICU CEF )
LIBS_linux=( Thirdparty OpenSSL Curl ICU CEF )
LIBS_emscripten=( Thirdparty ICU )

SUBPLATFORMS_ios=(iPhoneSimulator11.2 iPhoneSimulator12.1 iPhoneSimulator13.2 iPhoneSimulator14.4 iPhoneSimulator14.5 iPhoneOS11.2 iPhoneOS12.1 iPhoneOS13.2 iPhoneOS14.4 iPhoneOS14.5)
SUBPLATFORMS_win32=(v141_static_debug v141_static_release)
SUBPLATFORMS_android=(ndk16r15)

# Override the Windows library and subplatform lists (space or comma separated)
if [ -n "${PREBUILT_WIN32_LIBS}" ] ; then
	read -r -a LIBS_win32 <<< "${PREBUILT_WIN32_LIBS//,/ }"
fi
if [ -n "${PREBUILT_LINUX_LIBS}" ] ; then
	read -r -a LIBS_linux <<< "${PREBUILT_LINUX_LIBS//,/ }"
fi
if [ -n "${PREBUILT_MAC_LIBS}" ] ; then
	read -r -a LIBS_mac <<< "${PREBUILT_MAC_LIBS//,/ }"
fi
if [ -n "${PREBUILT_WIN32_SUBPLATFORMS}" ] ; then
	read -r -a SUBPLATFORMS_win32 <<< "${PREBUILT_WIN32_SUBPLATFORMS//,/ }"
fi

# util/invoke-unix.bat runs this script in a non-login Cygwin shell, where the
# Windows PATH can put native programs (System32's curl.exe and tar.exe) ahead
# of the Cygwin ones. Those do not understand /cygdrive paths.
case "$(uname -s)" in
	CYGWIN*)
		PATH="/usr/bin:${PATH}"
		;;
esac

# Convert a Windows path given in the environment (C:\foo) to the form the
# shell uses (/cygdrive/c/foo under Cygwin, /c/foo under Git Bash)
function toUnixPath {
	case "$1" in
		[A-Za-z]:[\\/]*)
			if command -v cygpath >/dev/null 2>&1 ; then
				cygpath -u "$1"
				return
			fi
			;;
	esac
	echo "$1"
}

# Fetch settings
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
FETCH_DIR="${SCRIPT_DIR}/fetched"
EXTRACT_DIR="${SCRIPT_DIR}"
WIN32_EXTRACT_DIR="${SCRIPT_DIR}/unpacked"
SUMS_FILE="${SCRIPT_DIR}/SHA256SUMS"

# LiveCode's own server (downloads.livecode.com/prebuilts) no longer serves the
# prebuilts. The default is a GitHub Release of this repository, which only has
# the x86_64 Windows (MSVC v141) tarballs.
URL="${PREBUILT_URL:-https://github.com/SethMorrowSoftware/winoxt/releases/download/prebuilts-v1}"
URL="${URL%/}"

# Optional local folder to copy tarballs from: PREBUILT_LOCAL_DIR, otherwise
# the folder that LiveCode's Windows build machines used, if it exists
LOCAL_DIR=
if [ -n "${PREBUILT_LOCAL_DIR}" ] ; then
	LOCAL_DIR=$(toUnixPath "${PREBUILT_LOCAL_DIR}")
	if [ ! -d "${LOCAL_DIR}" ] ; then
		echo "warning: PREBUILT_LOCAL_DIR '${PREBUILT_LOCAL_DIR}' is not a folder; ignoring it" >&2
		LOCAL_DIR=
	fi
elif [ "${OS}" = "Windows_NT" ] ; then
	if [ -d /cygdrive/c/LiveCode/Prebuilt/libraries ] ; then
		LOCAL_DIR=/cygdrive/c/LiveCode/Prebuilt/libraries
	fi
fi

# Versions
source "${SCRIPT_DIR}/scripts/lib_versions.inc"

# Override the fetch location via an environment variable
if [ ! -z "${PREBUILT_CACHE_DIR}" ] ; then
	FETCH_DIR=$(toUnixPath "${PREBUILT_CACHE_DIR}")
fi

mkdir -p "${FETCH_DIR}" || exit 1
mkdir -p "${EXTRACT_DIR}" || exit 1

# A partly written file or folder, removed if the script stops early
PARTIAL_PATH=
trap 'if [ -n "${PARTIAL_PATH}" ] ; then rm -rf "${PARTIAL_PATH}" ; fi' EXIT

# Only show download progress when someone is watching
if [ -t 2 ] ; then
	CURL_QUIET=
	WGET_QUIET=
else
	CURL_QUIET="-sS"
	WGET_QUIET="-nv"
fi

# Download a URL to a file, via "<file>.part" so that an interrupted download
# never looks like a complete one
function downloadFile {
	local SRC=$1
	local DEST=$2
	local PART="${DEST}.part"
	local RESULT=1

	rm -f "${PART}"
	PARTIAL_PATH="${PART}"

	# Download using an HTTP client of some variety
	if command -v curl >/dev/null 2>&1 ; then
		# -L is needed for GitHub release assets, which redirect
		curl -fL ${CURL_QUIET} --retry 3 --retry-delay 5 --connect-timeout 30 -o "${PART}" "${SRC}"
		RESULT=$?
	elif command -v wget >/dev/null 2>&1 ; then
		wget ${WGET_QUIET} --tries=3 --waitretry=5 --timeout=30 -O "${PART}" "${SRC}"
		RESULT=$?
	elif command -v perl >/dev/null 2>&1 ; then
		# Perl as a last resort (useful for Cygwin)
		perl -MLWP::Simple -e '$s = getstore($ARGV[0], $ARGV[1]); is_success($s) or die "perl LWP: status $s\n"' "${SRC}" "${PART}"
		RESULT=$?
	else
		echo "error: no download tool found (install curl)" >&2
	fi

	if [ "${RESULT}" -ne 0 ] || [ ! -s "${PART}" ] ; then
		rm -f "${PART}"
		PARTIAL_PATH=
		echo "error: failed to download ${SRC}" >&2
		return 1
	fi

	mv -f "${PART}" "${DEST}"
	RESULT=$?
	rm -f "${PART}"
	PARTIAL_PATH=
	return ${RESULT}
}

# Print the lower-case SHA-256 of a file
function sha256Of {
	local SUM
	if command -v sha256sum >/dev/null 2>&1 ; then
		SUM=$(sha256sum -b "$1") || return 1
	elif command -v shasum >/dev/null 2>&1 ; then
		SUM=$(shasum -a 256 -b "$1") || return 1
	else
		return 1
	fi
	SUM="${SUM%% *}"
	# sha256sum prefixes the line with '\' when the file name needs escaping
	SUM="${SUM#\\}"
	echo "${SUM}" | tr 'A-F' 'a-f'
}

# Check a tarball against SHA256SUMS; a bad tarball is deleted
function verifyTarball {
	local FILE=$1
	local FILE_NAME=$(basename "${FILE}")

	if [ "${PREBUILT_SKIP_VERIFY}" = "1" ] ; then
		echo "warning: not verifying ${FILE_NAME} (PREBUILT_SKIP_VERIFY=1)" >&2
		return 0
	fi

	# "<sha256>  <file>" lines; tolerate CRLF line endings and the '*' that
	# marks binary mode
	local EXPECTED=
	if [ -f "${SUMS_FILE}" ] ; then
		EXPECTED=$(awk -v name="${FILE_NAME}" '
			{ sub(/\r$/, "") }
			/^[ \t]*#/ { next }
			{
				file = $2
				sub(/^\*/, "", file)
				if (file == name) { print tolower($1); exit }
			}' "${SUMS_FILE}")
	fi

	if [ -z "${EXPECTED}" ] ; then
		local REASON="no entry in ${SUMS_FILE}"
		if [ ! -f "${SUMS_FILE}" ] ; then
			REASON="${SUMS_FILE} not found"
		fi
		if [ "${PREBUILT_STRICT}" = "1" ] ; then
			echo "error: cannot verify ${FILE_NAME}: ${REASON} (PREBUILT_STRICT=1)" >&2
			exit 1
		fi
		echo "warning: ${FILE_NAME} not verified: ${REASON}" >&2
		return 0
	fi

	local ACTUAL
	ACTUAL=$(sha256Of "${FILE}")
	if [ $? -ne 0 ] || [ -z "${ACTUAL}" ] ; then
		echo "error: cannot compute the SHA-256 of ${FILE}: install sha256sum or shasum, or set PREBUILT_SKIP_VERIFY=1" >&2
		exit 1
	fi

	if [ "${ACTUAL}" != "${EXPECTED}" ] ; then
		rm -f "${FILE}"
		echo "error: SHA-256 mismatch for ${FILE_NAME}" >&2
		echo "    expected: ${EXPECTED}" >&2
		echo "    actual:   ${ACTUAL}" >&2
		echo "    The file has been deleted; run again to fetch it again." >&2
		exit 1
	fi

	echo "Checksum OK: ${FILE_NAME}"
}

function extractLibrary {
	local TARBALL=$1
	local NAME=$2
	local PLATFORM=$3
	local UNPACKED_DIR=$4

	echo "Extracting library: ${NAME}"

	if [ "${PLATFORM}" != "win32" ] ; then
		( cd "${EXTRACT_DIR}" && tar -jxf "${TARBALL}" )
		if [ $? -ne 0 ] ; then
			echo "error: failed to extract ${TARBALL}" >&2
			exit 1
		fi
		return 0
	fi

	# Windows tarballs hold a single top-level folder named after the triple.
	# Unpack into a temporary folder and then move it into place, so that an
	# interrupted extraction never leaves a partial folder behind that a later
	# run would take as complete.
	local LIB_DIR=$(dirname "${UNPACKED_DIR}")
	local TRIPLE=$(basename "${UNPACKED_DIR}")
	local TEMP_DIR="${LIB_DIR}/.${TRIPLE}.tmp"

	rm -rf "${TEMP_DIR}"
	mkdir -p "${TEMP_DIR}" || exit 1
	PARTIAL_PATH="${TEMP_DIR}"

	( cd "${TEMP_DIR}" && tar -jxf "${TARBALL}" )
	if [ $? -ne 0 ] ; then
		echo "error: failed to extract ${TARBALL}" >&2
		exit 1
	fi
	if [ ! -d "${TEMP_DIR}/${TRIPLE}" ] ; then
		echo "error: ${TARBALL} does not contain the expected folder ${TRIPLE}" >&2
		exit 1
	fi

	# Replace an older copy, and the 'Fast' copy made from it (see below)
	rm -rf "${UNPACKED_DIR}"
	if [[ ${TRIPLE} =~ _release$ ]] ; then
		rm -rf "${LIB_DIR}/${TRIPLE/release/fast}"
	fi
	mv "${TEMP_DIR}/${TRIPLE}" "${UNPACKED_DIR}"
	if [ $? -ne 0 ] ; then
		echo "error: failed to move ${TRIPLE} into ${LIB_DIR}" >&2
		exit 1
	fi
	rm -rf "${TEMP_DIR}"
	PARTIAL_PATH=
}

function fetchLibrary {
	local LIB=$1
	local PLATFORM=$2
	local ARCH=$3
	local SUBPLATFORM=$4

	# A per-platform version (versions/<lib>_<platform>) wins over the common one
	eval "local VERSION=\${${LIB}_VERSION_${PLATFORM}:-\${${LIB}_VERSION}}"
	eval "local BUILDREVISION=\${${LIB}_BUILDREVISION}"

	if [ -z "${VERSION}" ] ; then
		echo "error: unknown prebuilt library '${LIB}' (no version in prebuilt/versions)" >&2
		exit 1
	fi

	# We now use standard GNU triple ordering for the naming of windows prebuilts
	local NAME=""
	if [ "${PLATFORM}" = "win32" ]; then
		NAME="${LIB}-${VERSION}-${ARCH}-${PLATFORM}-${SUBPLATFORM}"
	else
		NAME="${LIB}-${VERSION}-${PLATFORM}-${ARCH}"
		if [ ! -z "${SUBPLATFORM}" ] ; then
			NAME+="-${SUBPLATFORM}"
		fi
	fi
	
	if [ ! -z "${BUILDREVISION}" ] ; then
		NAME+="-${BUILDREVISION}"
	fi

	local TARBALL="${FETCH_DIR}/${NAME}.tar.bz2"

	# Windows prebuilts are unpacked into their own folder, so they can be
	# (re-)extracted from an existing tarball whenever that folder is missing.
	# Other platforms share one folder and are only extracted after fetching.
	local UNPACKED_DIR=""
	if [ "${PLATFORM}" = "win32" ]; then
		UNPACKED_DIR="${WIN32_EXTRACT_DIR}/${LIB}/${ARCH}-${PLATFORM}-${SUBPLATFORM}"
	fi

	if [ -f "${TARBALL}" ]; then
		if [ -n "${UNPACKED_DIR}" ] && [ ! -d "${UNPACKED_DIR}" ] ; then
			echo "Already fetched, not yet extracted: ${NAME}"
			verifyTarball "${TARBALL}"
			extractLibrary "${TARBALL}" "${NAME}" "${PLATFORM}" "${UNPACKED_DIR}"
		else
			echo "Already fetched: ${NAME}"
		fi
		return 0
	fi

	if [ -n "${LOCAL_DIR}" ] && [ -f "${LOCAL_DIR}/${NAME}.tar.bz2" ]; then
		echo "Fetching local library: ${NAME}"
		PARTIAL_PATH="${TARBALL}.part"
		if ! cp "${LOCAL_DIR}/${NAME}.tar.bz2" "${TARBALL}.part" || ! mv -f "${TARBALL}.part" "${TARBALL}" ; then
			echo "error: failed to copy ${LOCAL_DIR}/${NAME}.tar.bz2" >&2
			exit 1
		fi
		PARTIAL_PATH=
	else
		echo "Fetching remote library: ${NAME}"
		if ! downloadFile "${URL}/${NAME}.tar.bz2" "${TARBALL}" ; then
			echo "error: failed to find library ${NAME} either remotely or locally." >&2
			echo "    Set PREBUILT_URL to a location that has ${NAME}.tar.bz2," >&2
			echo "    or PREBUILT_LOCAL_DIR to a folder that contains it." >&2
			exit 1
		fi
	fi

	verifyTarball "${TARBALL}"
	extractLibrary "${TARBALL}" "${NAME}" "${PLATFORM}" "${UNPACKED_DIR}"
}

if [ 0 -eq "$#" ]; then
    SELECTED_PLATFORMS="${PLATFORMS[@]}"
else
    ARGS_ARE_PLATFORMS=1
    for SELECTED_PLATFORM in "$@" ; do
        ARG_IS_PLATFORM=0
        for AVAILABLE_PLATFORM in "${PLATFORMS[@]}"; do
            if [ "$AVAILABLE_PLATFORM" == "$SELECTED_PLATFORM" ]; then
                ARG_IS_PLATFORM=1
                break
            fi
        done
        if [ "$ARG_IS_PLATFORM" != "1" ]; then
            ARGS_ARE_PLATFORMS=0
            break
        fi
    done

    if [ "$ARGS_ARE_PLATFORMS" == "1" ]; then
        SELECTED_PLATFORMS="$@"
    else
        # If not all args are platforms, assume first arg is platform and the
        # rest are architectures
        SELECTED_PLATFORMS="$1"
        shift 1
    fi
fi

FETCH_HEADERS=0

for PLATFORM in ${SELECTED_PLATFORMS} ; do
	# Work around an issue where Gyp is too enthusiastic in path-ifying arguments
	PLATFORM=$(basename "$PLATFORM")

	# Windows prebuilts now include their headers
	if [ "$PLATFORM" = "win32" ]; then
		FETCH_HEADERS=1
	fi

    if [ "$ARGS_ARE_PLATFORMS" == "1" ]; then
	    eval "ARCHS=( \${ARCHS_${PLATFORM}[@]} )"
        SELECTED_ARCHS="${ARCHS[@]}"
    else
        SELECTED_ARCHS="$@"
    fi

	eval "LIBS=( \${LIBS_${PLATFORM}[@]} )"
	eval "SUBPLATFORMS=( \${SUBPLATFORMS_${PLATFORM}[@]} )"

	for ARCH in ${SELECTED_ARCHS} ; do
		# The archives of Linux x86 are named i386 (package-libs.sh), while
		# gyp asks for x86; their library folder is lib/linux/x86 either way
		if [ "${PLATFORM}" = "linux" ] && [ "${ARCH}" = "x86" ] ; then
			ARCH=i386
		fi
		for LIB in "${LIBS[@]}" ; do
			# CEF only exists for x86_64 Linux (see prebuilt/libcef.gyp)
			if [ "${PLATFORM}" = "linux" ] && [ "${LIB}" = "CEF" ] && [ "${ARCH}" != "x86_64" ] ; then
				continue
			fi
			if [ ! -z "${SUBPLATFORMS}" ] ; then
				for SUBPLATFORM in "${SUBPLATFORMS[@]}" ; do
					fetchLibrary "${LIB}" "${PLATFORM}" "${ARCH}" "${SUBPLATFORM}"
				done
			else
				fetchLibrary "${LIB}" "${PLATFORM}" "${ARCH}"
			fi
		done
	done

	# TODO[2017-03-03] Our official architecture name (as used in
	# the gyp "toolset_os" variable) is "x86" rather than "i386",
	# but the prebuilt naming uniformly uses "i386".  Workaround
	# this by renaming the "i386" output folder to "x86"
	if [ -d "${EXTRACT_DIR}/lib/${PLATFORM}/i386" ] ; then
		if [ ! -d "${EXTRACT_DIR}/lib/${PLATFORM}/x86" ] ; then
			mkdir "${EXTRACT_DIR}/lib/${PLATFORM}/x86"
		fi
		cp -R "${EXTRACT_DIR}/lib/${PLATFORM}/i386/"* "${EXTRACT_DIR}/lib/${PLATFORM}/x86/"
		rm -r "${EXTRACT_DIR}/lib/${PLATFORM}/i386"
	fi

        # Windows-only hacks
	if [ "$PLATFORM" = "win32" ]; then

		# TODO[2017-02-17] Monkey-patch in a "Fast" prebuilt
		# The "Fast" configuration used by CI builds is identical to
		# the "Release" configuration, in terms of linkability.  So if
		# there's a "Release" build of any given library, duplicate
		# it for "Fast"
        for ARCH in ${SELECTED_ARCHS} ; do
                for LIB in "${LIBS[@]}" ; do
                        echo "Providing 'Fast' configuration for ${LIB} library"
                        for SUBPLATFORM in "${SUBPLATFORMS[@]}"; do
                                if [[ ! ${SUBPLATFORM} =~ _release$ ]]; then
                                        continue
                                fi
                                SRC_TRIPLE="${ARCH}-${PLATFORM}-${SUBPLATFORM}"
                                DST_TRIPLE=$(echo "${SRC_TRIPLE}" | sed 's/release/fast/')
                                SRC_DIR="${WIN32_EXTRACT_DIR}/${LIB}/${SRC_TRIPLE}"
                                DST_DIR="${WIN32_EXTRACT_DIR}/${LIB}/${DST_TRIPLE}"
                                if [[ -d "${SRC_DIR}" && ! -d "${DST_DIR}" ]]; then
                                        cp -a "${SRC_DIR}" "${DST_DIR}"
                                fi
                        done
                done
        done
                        
        for ARCH in ${SELECTED_ARCHS} ; do
                for LIB in "${LIBS[@]}" ; do
                        echo "Monkey patching toolset/arch for ${LIB} library"
                        for SUBPLATFORM in "v140_static_debug" "v140_static_release" "v140_static_fast"; do
                                SRC_TRIPLE="${ARCH}-${PLATFORM}-${SUBPLATFORM}"
                                DST_TRIPLE=$(echo "${SRC_TRIPLE}" | sed 's/v140/v141/')
                                SRC_DIR="${WIN32_EXTRACT_DIR}/${LIB}/${SRC_TRIPLE}"
                                DST_DIR="${WIN32_EXTRACT_DIR}/${LIB}/${DST_TRIPLE}"
                                if [[ -d "${SRC_DIR}" && ! -d "${DST_DIR}" ]]; then
                                        cp -a "${SRC_DIR}" "${DST_DIR}"
                                fi
                        done
                done
        done
	fi
done

# Don't forget the headers & data on non-Windows platforms
if [ 0 -eq "$FETCH_HEADERS" ]; then
	fetchLibrary OpenSSL All Universal Headers
	fetchLibrary ICU All Universal Headers
	fetchLibrary ICU All Universal Data
fi
