#!/bin/bash
# Runs the Linux x86 (32-bit) build of .github/workflows/build-linux.yml in a
# Debian 11 (bullseye) i386 container that the job starts itself.
#
#   tools/ci/i386-container.sh start          start the container and install
#                                             the build tools (as the x86_64
#                                             and arm64 legs do in their
#                                             Ubuntu 20.04 container)
#   tools/ci/i386-container.sh run COMMAND    run COMMAND with bash -eo
#                                             pipefail in the container, in
#                                             the current folder
#   tools/ci/i386-container.sh stop           remove the container
#
# Why a container of its own: Ubuntu has no i386 images after 18.04, and
# GitHub's JavaScript actions (checkout, cache, upload) cannot run inside a
# 32-bit job container. So the job runs on the x86_64 runner, the actions
# run there, and this script runs the build commands in the container.
# Debian 11 has the same glibc (2.31) and the libstdc++ of GCC 10 that the
# other Linux legs' Ubuntu 20.04 has, so the 32-bit files need no newer
# system than the 64-bit ones. Every command runs under linux32, so that
# uname -m says i686: the build scripts (prebuilt/scripts/platform.inc) and
# gyp (config/arch.gypi) take it for the machine's own architecture, and
# nothing is cross-compiled.
#
# The workspace and RUNNER_TEMP are mounted at the same paths as on the
# runner, so paths in variables mean the same inside and outside. The
# variables passed into the container are the build's (ARCH, MODE,
# BUILDTYPE, OXT_*, PREBUILT_*, CI and GitHub's step files).

set -eo pipefail

NAME=oxt-i386
IMAGE=debian:bullseye

# The x86_64 and arm64 legs' package list (build-linux.yml, "Install build
# tools and headers"), as Debian 11 names it
PACKAGES="build-essential gcc g++ make perl bison flex gawk pkg-config
	python2 python-is-python2 python3 openjdk-11-jdk-headless
	git curl ca-certificates bzip2 xz-utils zip unzip file binutils
	libx11-dev libxext-dev libxrender-dev libxft-dev libxinerama-dev
	libxv-dev libxcursor-dev libfreetype6-dev libfontconfig1-dev
	libexpat1-dev libgtk2.0-dev libpopt-dev liblcms2-dev gdb"

run_in() {
	local args=()
	local var
	for var in ARCH MODE BUILDTYPE OXT_BUILD_NUMBER CI GITHUB_ACTIONS GITHUB_WORKSPACE \
	           GITHUB_STEP_SUMMARY GITHUB_OUTPUT GITHUB_ENV RUNNER_TEMP DEBIAN_FRONTEND \
	           $(compgen -v | grep -E '^(PREBUILT|OXT)_' || true) ; do
		if [ -n "${!var+x}" ] ; then
			args+=(-e "${var}")
		fi
	done
	docker exec "${args[@]}" -w "$PWD" "$NAME" linux32 bash -eo pipefail -c "$1"
}

case "$1" in
	start)
		docker rm -f "$NAME" > /dev/null 2>&1 || true
		docker run -d --name "$NAME" --platform linux/386 \
			-v "${GITHUB_WORKSPACE:-$PWD}:${GITHUB_WORKSPACE:-$PWD}" \
			-v "${RUNNER_TEMP:-/tmp}:${RUNNER_TEMP:-/tmp}" \
			"$IMAGE" sleep infinity > /dev/null
		export DEBIAN_FRONTEND=noninteractive
		# Debian 11's LTS ended in August 2026: its security archive still
		# lists the bullseye-security packages but no longer has their
		# files, and the release itself moves to archive.debian.org in
		# time. A build container needs the toolchain, not security
		# updates, so it takes the release and bullseye-updates only, from
		# archive.debian.org when deb.debian.org no longer has them. The
		# image comes with some security versions installed (libc6,
		# perl-base, ...), which the release's -dev packages do not
		# match: pinning the two suites above priority 1000 lets apt move
		# those back to the release's versions.
		run_in "sed -i -e '/bullseye-security/d' /etc/apt/sources.list"
		# A mirror that drops a connection (as deb.debian.org now and
		# then does) fails the whole install without retries
		run_in "echo 'Acquire::Retries \"5\";' > /etc/apt/apt.conf.d/80-oxt-retries"
		run_in "printf 'Package: *\nPin: release n=bullseye\nPin-Priority: 1001\n\nPackage: *\nPin: release n=bullseye-updates\nPin-Priority: 1001\n' > /etc/apt/preferences.d/oxt-bullseye-release"
		if ! run_in 'apt-get update' ; then
			run_in "sed -i -e 's|deb.debian.org|archive.debian.org|' /etc/apt/sources.list && apt-get update"
		fi
		run_in 'apt-get -y --allow-downgrades dist-upgrade'
		run_in "apt-get install -y --no-install-recommends $(echo $PACKAGES)"
		# The checkout belongs to the runner's user; git in the container
		# runs as root (config.py runs git rev-parse)
		run_in "git config --global --add safe.directory '${GITHUB_WORKSPACE:-$PWD}'"
		run_in 'uname -m; gcc --version | sed -n 1p; python --version; ldd --version | sed -n 1p; echo "Processors: $(nproc)"'
		;;
	run)
		shift
		run_in "$*"
		;;
	stop)
		docker rm -f "$NAME" > /dev/null 2>&1 || true
		;;
	*)
		echo "usage: $0 start | run COMMAND | stop" >&2
		exit 2
		;;
esac
