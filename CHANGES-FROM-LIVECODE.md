# Changes from LiveCode's source

This repository builds **LiveCode Community 9.7.0-dp-1**, the last build
of LiveCode Community's `develop` branch (livecode/livecode `4606a10ea`,
2021-07-26), as LiveCode Ltd left it when the open source edition ended.
The tag `livecode-9.7.0-dp-1` is that commit with its two submodules
vendored at the commits it pins: `ide` (livecode-ide `ccc733a15`, tree
`8a1564b1`) and `thirdparty` (livecode-thirdparty `e5e050573`, tree
`6ea49f1c`), byte for byte as GitHub has them. Nothing in LiveCode's code is
fixed or improved here.

This file lists every difference from that tag, and why. The **Pristine
guard** workflow (`tools/ci/pristine_guard.py`) checks on every push that
the tag still has its tree (`1f8d052b`), and that every file of LiveCode's
changed or deleted after it, and every file added outside this
repository's own folders (`.github/pristine-allowlist.txt`), is named
here.

Most of the changes are build fixes that LiveCode's 2021 sources need on
today's compilers, SDKs and systems (Visual Studio 2022, current Xcode and
macOS SDKs, Apple Silicon, current Linux) and with prebuilt libraries that
LiveCode's server no longer serves. They were made for OXT-Beyond and the
OpenXTalk-Lite-1.15 repository, which build the same LiveCode develop
tree with Tom Perry's OpenXTalk work on top; here they are taken without
anything of OpenXTalk's. Each section names the commit it came from.

## Build changes

### Submodules: googletest only, at the commit upstream pinned

thirdparty/ and ide/ are vendored, so .gitmodules no longer lists them;
a recursive checkout would otherwise look for submodules that are now
ordinary folders. The googletest submodule (libcpptest/googletest,
google/googletest at dcc92d0ab6c4ce022162a23566d44f673251eee4), which
Tom Perry's tree left out, is restored at the commit LiveCode develop
4606a10ea pinned. Its default branch is now main.

Build change on top of tom-perry-1.15, as in OXT-Beyond 4c9715a778,
8ef95bac05 and 282e2462be.

Files:

- `.gitmodules`
- `libcpptest/googletest` (added)

### Fetch prebuilt libraries from the winoxt GitHub release

LiveCode's prebuilt server (downloads.livecode.com/prebuilts) now
refuses downloads, so the Windows x86_64 prebuilts are mirrored as
assets of this repository's prebuilts-v1 release.

- fetch-libraries.sh downloads from that release by default
  (PREBUILT_URL overrides it), follows redirects, retries, writes to a
  .part file first, verifies every tarball against prebuilt/SHA256SUMS,
  and extracts a Windows tarball whenever its unpacked folder is
  missing, not only right after downloading it. PREBUILT_LOCAL_DIR,
  PREBUILT_WIN32_LIBS and PREBUILT_WIN32_SUBPLATFORMS allow offline and
  Release-only fetches.
- The Thirdparty prebuilt version is pinned in versions/thirdparty.
  It used to be read from the thirdparty submodule, which no longer
  exists now that thirdparty/ is vendored.

In this tree fetch-libraries.sh also had Tom Perry's macOS change, which
made every platform but Windows copy the tarballs from the prebuilt
folder itself (LOCAL_DIR="${SCRIPT_DIR}", where his own builds of the
libraries were). That is replaced as well: PREBUILT_LOCAL_DIR names such
a folder now, and the CI builds set it to the libraries they built.

Ported from OXT-Beyond commit e0e021452ebf3c8ff83ef43b16ffdc9492845feb.

Files:

- `prebuilt/SHA256SUMS` (added)
- `prebuilt/fetch-libraries.sh`
- `prebuilt/scripts/lib_versions.bat`
- `prebuilt/scripts/lib_versions.inc`
- `prebuilt/versions/thirdparty`

### Windows build fixes: SQLite from source, Cygwin lookup, VS 2022

- Build dbsqlite and dbsqlite-server against thirdparty/libsqlite on
  Windows. They linked the libsqlite.lib from the Thirdparty prebuilt,
  which is SQLite 3.34.0, so the 3.51.1 update never reached a clean
  build.
- invoke-unix.bat prefers CYGPATH, C:\Cygwin64 and C:\Cygwin over a
  cygpath.exe that happens to be on PATH (such as Git for Windows'),
  and puts the Cygwin bin folder first on PATH with the right
  separator.
- make.cmd finds Visual Studio with vswhere (preferring an install with
  the v141 toolset), passes a Windows 10/11 SDK version to msbuild
  (WINSDK_VERSION, or the one vcvarsall picked) because gyp does not
  set one and v141 would otherwise need the 8.1 SDK, and accepts
  MSBUILD_EXTRA_ARGS.
- configure.bat prefers Python 2.7 (PYTHON or C:\Python27), stops with
  a clear error when only Python 3 is available, and skips its pauses
  when NOPAUSE or CI is set.
- util/remove_matching.py runs under Python 2 and 3.
- .gitattributes pins batch files to CRLF and YAML and SHA256SUMS to
  LF; .gitignore covers release packages and editor files; googletest
  tracks its main branch.

Ported from OXT-Beyond commit 282e2462be6a8cf31dcf7a0fd1e3170ce1f03fe9.

Files:

- `.gitattributes`
- `.gitignore`
- `configure.bat`
- `make.cmd`
- `revdb/revdb.gyp`
- `util/invoke-unix.bat`
- `util/remove_matching.py`

### Prevent Windows SDK min max macro collisions

Ported from OXT-Beyond commit 138bbb039ae7549d4da2cff101b81004ad4829ba.

(Author: Seth Morrow.)

Files:

- `config/win32.gypi`
- `engine/src/w32prefix.h`

### Harden Windows definitions; no local min macro in the ODBC driver

config/win32.gypi defines NOMINMAX, WIN32_LEAN_AND_MEAN,
_CRT_SECURE_NO_WARNINGS and _WINSOCK_DEPRECATED_NO_WARNINGS for every
Windows target, including the externals that include windows.h
directly and never see engine/src/w32prefix.h.

odbc_connection.cpp defined its own min() macro when windows.h had not;
with NOMINMAX that is always, and "#if not defined(min)" is not valid
in MSVC's preprocessor. The one use, the length of each SQLPutData
chunk, now compares the remaining length and the block size explicitly.
The loop is otherwise unchanged.

Ported from OXT-Beyond commit b5d80035ab025123092e1d451ed98102d32e2ef1,
without its change to how the loop advances (it was not needed to build).

(Author: Seth Morrow.)

Files:

- `config/win32.gypi`
- `revdb/src/odbc_connection.cpp`

### Fix Windows SDK include ordering

Ported from OXT-Beyond commit fee51ce3ed59bcaf64abd85985ec3c1e8d921bb4.

(Author: Seth Morrow.)

Files:

- `engine/src/prefix.h`
- `libcore/src/core.cpp`
- `libfoundation/src/system-commandline.cpp`
- `libfoundation/src/system-file-w32.cpp`

### Declare Windows OLE and Winsock dependencies

Ported from OXT-Beyond commit de1b1364a8cf72cbb7255da382aa206c55a83635.

(Author: Seth Morrow.)

Files:

- `libfoundation/src/foundation-string.cpp`
- `revdb/src/dbmysql.h`

### Build the engine on Linux x86_64 and arm64

The 9.7.1-OXT engine had only been built for Windows. This makes the
same sources build natively on Linux, for x86_64 and arm64.

Engine:
- Respring (Tom Perry's development-engine restart) kept its globals in
  dskw32main.cpp, so every non-Windows desktop engine failed to link.
  They now live in dskmain.cpp, which every desktop engine compiles;
  respring.cpp includes w32dc.h only on Windows, and the Linux main loop
  checks for a pending respring the same way the Windows loop does.
- Link the Linux standalone and installer engines with -no-pie:
  MCDeployToLinuxReadHeader() only accepts ET_EXEC executables, and
  current gcc builds position-independent executables by default.

arm64:
- config.py, config/arch.gypi and Makefile.common now recognise
  aarch64/arm64 hosts as linux-arm64.
- curlbuild.win.h gives aarch64 the same 64-bit long sizes as x86_64.

Prebuilt libraries (LiveCode's download server no longer serves them, so
CI builds them from the pinned upstream sources):
- platform.inc, build-icu.sh: native arm64 hosts; ICU 58's <xlocale.h>
  include, which glibc 2.26 removed, becomes <locale.h>.
- build-curl.sh: position-independent code, no optional libraries picked
  up from the build machine; download from curl.se.
- build-icu.sh: download ICU from GitHub.
- build-cef.sh: Spotify's current CEF address and its "minimal" archive
  (same Release/ and Resources/ folders, half the size); no CEF 74 exists
  for arm64, so the script skips it there.
- PREBUILT_LINUX_LIBS and PREBUILT_BUILD_LIBS choose which libraries to
  fetch and build, and PREBUILT_MAKE_JOBS runs make in parallel.
- package-libs.sh packages the shared headers and ICU data on every
  Linux architecture, so arm64 builds have their own copies.

Ported from OXT-Beyond commit becca46b174db95afd8b52dbca9dddbf147babe7.

Ported from OpenXTalk-Lite-1.15 commit 83673e5c2 without its respring parts
(engine/src/dskmain.cpp, dskw32main.cpp, dsklnxmain.cpp, respring.cpp), which
belong to Tom Perry's IDE respring feature, not to LiveCode.

Files:

- `Makefile`
- `Makefile.common`
- `config.py`
- `config/arch.gypi`
- `engine/engine.gyp`
- `prebuilt/build-libraries.sh`
- `prebuilt/fetch-libraries.sh`
- `prebuilt/package-libs.sh`
- `prebuilt/scripts/build-cef.sh`
- `prebuilt/scripts/build-curl.sh`
- `prebuilt/scripts/build-icu.sh`
- `prebuilt/scripts/platform.inc`
- `thirdparty/libcurl/include/curl/curlbuild.win.h`

### Linux CI: package Thirdparty only once built; no SIGPIPE failures

- package-libs.sh took any library folder without libz.a for a Windows
  one, so packaging OpenSSL, Curl and ICU before the Thirdparty libraries
  were built failed on the missing .lib files. Thirdparty is now packaged
  only when its .a (or Windows .lib) files are there.
- GitHub runs bash with -o pipefail, so "| head -1" and "| grep -q" can
  fail a step with SIGPIPE when they close the pipe early (exit 141 in
  the first x86_64 run). Use sed and a plain grep instead.

Ported from OXT-Beyond commit 5976cba05fe5aa8767f17739e139e6a7d2b440a8.

Files:

- `prebuilt/package-libs.sh`

### OpenSSL 1.1.1w for the libraries built from source

OpenSSL 1.1.1g's arm64 assembly refers to OPENSSL_armcap_P without
marking it hidden, so it cannot be linked into revsecurity.so on Linux
arm64 ("relocation R_AARCH64_PREL64 against symbol OPENSSL_armcap_P ...
can not be used when making a shared object"). 1.1.1w, the last 1.1.1
release, marks it hidden, has a native Apple Silicon target, and fixes
the vulnerabilities reported for 1.1.1 until its end of life.

Linux and macOS, which build OpenSSL from source, now use 1.1.1w.
Windows keeps fetching the published 1.1.1g prebuilts (prebuilts-v1)
until they are rebuilt: a per-platform version file,
prebuilt/versions/openssl_win32, overrides the common version for that
platform in fetch-libraries.sh. The OpenSSL source now comes from
GitHub, where every release is kept.

Ported from OXT-Beyond commit 26c7896fd1ecabdb644be52abab8f7704ed7b9fa.

Files:

- `prebuilt/fetch-libraries.sh`
- `prebuilt/scripts/build-openssl.sh`
- `prebuilt/scripts/lib_versions.inc`
- `prebuilt/versions/openssl`
- `prebuilt/versions/openssl_win32` (added)

### Linux arm64: use only the arm64 libffi headers

prebuilt/thirdparty.gyp gave every Linux build the Darwin x86 libffi
headers, first in the include path. They happen to suit x86_64, but on
arm64 their ffitarget.h has no ARM definitions, so libscript failed with
"FFI_DEFAULT_ABI was not declared". arm64 now gets only the
include_linux/arm64 headers that libffi itself is built with.

Ported from OXT-Beyond commit 3d609826ae7b17d7ccf4652dbda2c115bd478e10.

Files:

- `prebuilt/thirdparty.gyp`

### Linux arm64: signed char, as on every other platform

The arm64 build now compiles, but the freshly built lc-compile crashed
(SIGSEGV) the first time it ran. Plain char is unsigned on Linux arm64
but signed on x86 and on Apple arm64, which this code was written and
tested on; build Linux arm64 with -fsigned-char so the engine and tools
behave the same everywhere.

Should a tool still crash during the build, CI now reruns the LCB module
step under catchsegv to print a backtrace.

Ported from OXT-Beyond commit ca4ea8b82714340e6e8274e2d3bab902d8a896a7.

Files:

- `config/linux-settings.gypi`

### lc-compile: run on a thread with a 64 MB stack outside Windows

The freshly built lc-compile crashed (SIGSEGV) on Linux arm64 the first
time the build ran it, with or without -fsigned-char. Its parser recurses
deeply: Windows links lc-compile and gentle with a 64 MB stack
(StackReserveSize) for that reason, while Linux and macOS give the main
thread the default, usually 8 MB, and arm64 stack frames are larger.
lc-compile now runs the compiler on a thread with a 64 MB stack on every
platform other than Windows, which also covers the Extension Builder
running lc-compile on users' machines. lc-compile-lib links libpthread
on Linux (glibc before 2.34 keeps pthread_create there).

If the build still fails, CI reruns the step under libSegFault for a
backtrace, and again with a 1 GB stack limit.

Ported from OXT-Beyond commit 62f0a69918f74c54a01a100f3d94a514119d5c79.

Files:

- `toolchain/lc-compile/src/lc-compile-lib.gyp`
- `toolchain/lc-compile/src/main.c`

### libscript: allocate module definitions without type-punned references

gdb shows the Linux arm64 lc-compile crash: MCScriptEndDefinitionGroupInModule
wrote through a nil definition. script-builder.cpp allocated each
definition with MCMemoryNew((MCScriptXDefinition *&)definitions[i]) and
read the slot back as an MCScriptDefinition *. Writing through a
reference to a different pointer type is undefined behaviour under strict
aliasing, and GCC on arm64 read back the old (nil) value. All ten
allocations now go through __new_definition<T>(), which allocates into a
T * and stores it in the slot.

Linux builds also use -fno-strict-aliasing instead of -fstrict-aliasing:
this code was developed mostly with MSVC, which never optimises on
type-based aliasing, and it has other type-punned casts. That gives GCC
the memory semantics the Windows build has.

Ported from OXT-Beyond commit e8c1cda13479abe7e955df780f11493557c2aee7.

Files:

- `config/linux-settings.gypi`
- `libscript/src/script-builder.cpp`

### lc-compile: correct the comment on the 64 MB compiler thread

The Linux arm64 crash turned out to be the strict-aliasing bug in
libscript's script-builder.cpp, not the stack. The thread is kept: it
gives lc-compile the same 64 MB stack on every platform as the Windows
build has, so large modules compile everywhere.

Ported from OXT-Beyond commit ae01c6b614cbacce9c55aad5da2376fb494603a3.

Files:

- `toolchain/lc-compile/src/main.c`

### macOS arm64 and current-toolchain portability fixes

What LiveCode develop needs to compile and run on Apple Silicon and with
current Xcode, taken from Tom Perry's OpenXTalk Lite macOS source trees by
way of OXT-Beyond (its commit d98d01da0). Only the portability fixes are
taken; the behaviour changes of that commit (null-stack key presses,
recursive menubar updates, "semibold", the Android manifest) are not.

- foundation.h: recognise arm64/aarch64 (64-bit, little-endian).
- dskmac.cpp: stat64/fstat64 do not exist on arm64; the OS version comes
  from sw_vers because Gestalt is deprecated.
- deploy_macosx.cpp: Save as Standalone accepts arm64 engines (the app
  is universal).
- combiners.cpp: <cstdint> instead of local fixed-width typedefs; clang's
  enum conversion warning quietened for the dispatch templates.
- script-execute.cpp: an unused variable that is an error on arm64.
- libbrowser_osx_webview.mm: handle kJSTypeSymbol (current SDKs).
- osxbrowser.h: no HIWebView/CarbonUtils, gone from current SDKs.
- libmysql config-osx.h: 64-bit sizes for char*, long and size_t.
- gentle: ANSI prototypes instead of K&R definitions (clang 15+).

(Author: Tom Perry.)

Files:

- `engine/src/combiners.cpp`
- `engine/src/deploy_macosx.cpp`
- `engine/src/dskmac.cpp`
- `libbrowser/src/libbrowser_osx_webview.mm`
- `libfoundation/include/foundation.h`
- `libscript/src/script-execute.cpp`
- `revbrowser/src/osxbrowser.h`
- `thirdparty/libmysql/src/config-osx.h`
- `toolchain/gentle/gentle/gen.h`
- `toolchain/gentle/gentle/grts.c`
- `toolchain/gentle/gentle/main.c`
- `toolchain/gentle/gentle/output.c`

### Build the engine on macOS arm64 and x86_64

The macOS build still targeted LiveCode's 2014-era setup: x86_64 only,
the macOS 10.9 SDK and Xcode's legacy build system. This makes the same
sources build with a current Xcode, natively for Apple Silicon and Intel.

Build settings:
- config/mac.gypi: ARCHS follows target_arch. The oldest supported
  macOS is 11 on Apple Silicon and 10.13 on Intel (the oldest current
  Xcode deploys to). Binaries record SDK 11.0, so the Intel and Apple
  Silicon halves of the universal app get the same AppKit behaviour.
- config.py: the target architecture defaults to the machine's own, and
  the SDK to the one the installed Xcode provides.
- WorkspaceSettings.xcsettings: Xcode 14 removed the legacy build system.
- libffi: Apple Silicon uses the newer libffi in thirdparty/libffi/
  git_master (as iOS does), whose closures use a trampoline table; arm64
  macOS does not allow memory that is writable and executable at once.
- libcairo: no SSE2 code on Apple Silicon.

Prebuilt libraries:
- platform.inc: the selected Xcode's clang and SDK (via xcrun) instead
  of gcc and MacOSX10.9.sdk; per-architecture minimum macOS; build the
  architectures in PREBUILT_MAC_ARCHS (default: the machine's own).
- build-openssl.sh: OpenSSL 1.1.1g has no Apple Silicon target, so
  define livecode_darwin64-arm64-cc as 1.1.1i later defined it.
- build-icu.sh: ICU 58 uses the register keyword, removed in C++17; the
  <xlocale.h> change is Linux-only (macOS declares strtod_l there).
- PREBUILT_MAC_LIBS chooses the macOS libraries to fetch, and the
  shared headers and ICU data are packaged on macOS too.

Ported from OXT-Beyond commit a562559a237197946eae7b3434b1fd74b05dbfe5 without its
engine changes (desktop-dc.cpp, desktop.cpp, dskmac.cpp), which belong to Tom
Perry's dark-mode and respring work, not to LiveCode.

Files:

- `config.py`
- `config/WorkspaceSettings.xcsettings`
- `config/mac.gypi`
- `prebuilt/fetch-libraries.sh`
- `prebuilt/package-libs.sh`
- `prebuilt/scripts/build-icu.sh`
- `prebuilt/scripts/build-openssl.sh`
- `prebuilt/scripts/platform.inc`
- `thirdparty/libcairo/libcairo.gyp`
- `thirdparty/libffi/libffi.gyp`

### macOS arm64: build libffi without CFI directives; stop on Thirdparty failures

- Current clang rejects the CFI directives in libffi's sysv_arm64.S
  ("invalid CFI advance_loc expression" across its fixed-size jump
  tables). The macOS arm64 build defines FFI_NO_CFI_DIRECTIVES, which
  leaves them out (fficonfig_arm64.h); iOS is unchanged. LiveCode is
  built without C++ exceptions, so no unwinding crosses ffi_call.
- build-thirdparty.sh ignored a failed Xcode or make build, so the
  macOS CI step "succeeded" without producing the Thirdparty archive and
  the failure only showed up later as a download error. It now stops at
  the first failing command.

Files:

- `prebuilt/scripts/build-thirdparty.sh`
- `thirdparty/libffi/git_master/darwin_ios/include/fficonfig_arm64.h`
- `thirdparty/libffi/libffi.gyp`

### libpng, zlib: build with current macOS SDKs

Current macOS SDKs define TARGET_OS_MAC, which these 2013-era headers
take for classic Mac OS:
- libpng's pngpriv.h then includes <fp.h>, which the SDKs no longer
  have ("fatal error: 'fp.h' file not found" in the Intel macOS build).
- zlib's zutil.h would define fdopen() as NULL, which breaks <stdio.h>.
Both now leave Apple platforms on the normal <math.h> and fdopen().

Files:

- `thirdparty/libpng/src/pngpriv.h`
- `thirdparty/libz/src/zutil.h`

### macOS CI: report every compile error in one run

xcodebuild stops at the first failing target by default, so each CI run
showed one error. Pass -IDEBuildingContinueBuildingAfterErrors=YES
(through XCODEBUILD_FLAGS, which build-thirdparty.sh now also honours)
so a failing build lists all of them.

Files:

- `prebuilt/scripts/build-thirdparty.sh`

### macOS: build the toolchain, libbrowser and deploy code with Xcode 16

- gentle and reflex (the LCB toolchain's parser generators) are
  pre-ANSI C with implicit int and undeclared functions, which current
  clang rejects from C99 on even with warnings silenced. Compile them as
  gnu89 on macOS, where that is valid.
- libbrowser_osx_webview.mm: macOS 15's SDK adds kJSTypeBigInt, and
  -Wswitch is an error here; a default case covers new JavaScript types.
- deploy_macosx.cpp: current macOS SDKs define cpu_type_t themselves, so
  the file's own typedefs are only used off macOS (the Windows and Linux
  IDE engines also compile it to build Mac standalones). This is the
  change Tom Perry made with <mach/machine.h>, kept cross-platform.

Files:

- `engine/src/deploy_macosx.cpp`
- `libbrowser/src/libbrowser_osx_webview.mm`
- `toolchain/gentle/gentle/gentle.gyp`
- `toolchain/gentle/gentle/grts.gyp`
- `toolchain/gentle/reflex/reflex.gyp`

### macOS arm64: use the newer libffi's headers for the engine

prebuilt/thirdparty.gyp gave every macOS build the old x86-only libffi
headers. Apple Silicon links the newer libffi in git_master, so the
engine now compiles against that libffi's headers there, matching the
library.

Files:

- `prebuilt/thirdparty.gyp`

### macOS: build without strict aliasing, as on Linux and Windows

The same reasoning as the Linux change: the code has type-punned
pointer casts and was developed mostly with MSVC, which never optimises
on type-based aliasing. GCC_STRICT_ALIASING=NO gives clang the same
memory semantics on both Mac architectures.

Files:

- `config/mac.gypi`

### macOS arm64: libffi has unwind information again

On Apple Silicon, an Objective-C exception thrown in a method that
LiveCode Builder calls ended the program (SIGABRT, "uncaught exception")
instead of becoming an LCB error. libscript catches such exceptions with
@try around ffi_call. But the macOS arm64 build of libffi had no CFI
directives (FFI_NO_CFI_DIRECTIVES, 4b1d20bafb), so ffi_call_SYSV had no
unwind information and the exception could not reach the @catch. CI's
TestObjcInterop_CatchObjcException showed it.

Clang had refused the CFI with "invalid CFI advance_loc expression" at
three directives (in the build log of 2026-09-29), and the jump tables
were not the reason. In a Mach-O object, a label that the linker sees
starts a new atom, and a CFI advance cannot cross atoms:

- In ffi_call_SYSV and ffi_closure_SYSV, cfi_startproc came before the
  function's label, so the first directive after the label crossed it.
  cfi_startproc now follows the label, as in current libffi.
- The local label .Ldo_closure is not a local one on Mach-O, where
  that prefix is L. It split ffi_closure_SYSV in two, so the directive
  at 99 crossed it. The label is now Ldo_closure.

With that, libffi.gyp and fficonfig_arm64.h are as before 4b1d20bafb.
ffi_go_closure_SYSV gets the same order, though Apple builds leave Go
closures out.

Files:

- `thirdparty/libffi/git_master/darwin_ios/include/fficonfig_arm64.h`
- `thirdparty/libffi/git_master/darwin_ios/src/aarch64/sysv_arm64.S`
- `thirdparty/libffi/libffi.gyp`

### Linux x86: no CEF (its 32-bit builds ended with CEF 101), i386 archive names

The 32-bit Linux build cannot have CEF 74 from Spotify's current server,
so prebuilt/libcef.gyp, revbrowser.gyp and libbrowser.gyp take CEF on
Linux x86_64 only, and libbrowser's Linux factory list has the CEF
browser only there. (OpenXTalk Lite 1.15's 32-bit Linux runtime listed
revbrowser.so but had no libcef.so, so its browser did not work
either.) prebuilt/fetch-libraries.sh asks for the Linux x86 archives
under the name package-libs.sh gives them (i386), and platform.inc takes
either spelling.

(Author: Seth Morrow.)

Files:

- `libbrowser/libbrowser.gyp`
- `libbrowser/src/libbrowser_lnx_factories.cpp`
- `prebuilt/fetch-libraries.sh`
- `prebuilt/libcef.gyp`
- `prebuilt/scripts/platform.inc`
- `revbrowser/revbrowser.gyp`

### gitattributes, gitignore: only the build's rules

Files:

- `.gitattributes`
- `.gitignore`

## Repository files

- `README.md`: what this repository is and how it works; LiveCode's own
  README is at `git show livecode-9.7.0-dp-1:README.md`.
- `.gitattributes`: line endings that the Windows build needs (CRLF for
  `.bat` and `.cmd`, which cmd.exe misreads otherwise; LF for the
  workflows and the prebuilt libraries' checksums), binary file types of
  the IDE stored byte for byte, and the mergExt bundle of the packages
  (`Installer/legacyoxt/ext`) kept byte for byte.
- `.gitignore`: build and CI leftovers (archives, Visual Studio and
  Python caches), and an exception that keeps the mergExt bundle's
  libraries (`*.dll`, `*.so`, which LiveCode's rules ignore).

## Tests

LiveCode's engine test suites in `tests/`, as OXT-Beyond changed them to
run on CI machines and on Windows, plus OXT-Beyond's regression tests for
bugs it fixed, which fail on LiveCode's code and are recorded in the
baselines (`tools/ci/engine-tests-baseline*.txt`). By file:

- `tests/_compilertestrunner.livecodescript`
- `tests/_inputlib.livecodescript`
- `tests/_testlib.livecodescript`
- `tests/_testrunnerbehavior.livecodescript`
- `tests/lcb/stdlib/math.lcb`
- `tests/lcb/vm/foreign-invoke.lcb`
- `tests/lcb/vm/interop-objc.lcb`
- `tests/lcs/core/datetime/datetime.livecodescript`
- `tests/lcs/core/engine/engine.livecodescript`
- `tests/lcs/core/engine/widget.livecodescript`
- `tests/lcs/core/field/pageHeights.livecodescript`
- `tests/lcs/core/files/files.livecodescript`
- `tests/lcs/core/files/folders.livecodescript`
- `tests/lcs/core/interface/interface.livecodescript`
- `tests/lcs/core/interface/snapshot.livecodescript`
- `tests/lcs/core/math/errors.livecodescript`
- `tests/lcs/core/multimedia/multimedia.livecodescript`
- `tests/lcs/core/network/network.livecodescript`
- `tests/lcs/core/strings/sort.livecodescript`
- `tests/lcs/liburl/connect.livecodescript`
- `tests/lcs/liburl/forms.livecodescript`
- `tests/lcs/liburl/headers.livecodescript`
- `tests/lcs/liburl/status_codes.livecodescript`

The OXT-Beyond commits behind each are listed in OpenXTalk-Lite-1.15's
`CHANGES-FROM-TOM.md` ("Import the CI, tests and packaging tools of
OXT-Beyond"). `tests/_testlib.livecodescript` also numbers error codes by
their place in `executionerrors.h`, as the engine does, and keeps one
platform's lines of a file marked for two (no file here is).
