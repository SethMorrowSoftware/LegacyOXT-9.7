> **In this repository.** This document comes from
> [OXT-Beyond](https://github.com/SethMorrowSoftware/OpenXTalk-Beyond), whose
> packaging tools were imported here, and describes them as they are there.
> Here they package Tom Perry's OpenXTalk Lite 1.15 as it is, so:
>
> - The product is **OpenXTalk Lite**: `package.py`'s `PRODUCT` is
>   `OpenXTalk-Lite`, so the Windows engine is `OpenXTalk-Lite.exe` (the name
>   OpenXTalk Lite 1.15 itself uses), the macOS app `OpenXTalk-Lite.app` (the
>   name in Tom Perry's macOS build scripts), the Linux engine and launcher
>   `OpenXTalk-Lite` and `openxtalk-lite`, and the stage folder and archives
>   `OpenXTalk-Lite-<ide/.version>`. The macOS bundle id is
>   `io.github.sethmorrowsoftware.openxtalk-lite`.
> - The icons are Tom Perry's: the macOS app takes
>   `Installer/openxtalk-lite/branding/OpenXTalk-Lite.icns` unchanged, the
>   Linux package the PNGs made from `ide/OpenXTalk-lite_1024.ico` (see
>   `Installer/openxtalk-lite/branding/README.md`), and the Windows
>   installer that `.ico`. OXT-Beyond's artwork, `ico.py`, the IDE stack
>   patching tools and `dark_disabled_icons.py` are not here.
> - The only licence file packaged is `LICENSE` (LiveCode Community's, with
>   LiveCode Ltd's permission for OpenSSL and ATL at its top).
> - The external asset is `oxt-runtimes-1.15`, the standalone runtimes of
>   OpenXTalk Lite 1.15 unchanged, and the xTalk Suite extensions are never
>   packaged (CI passes `--no-xtalk-extensions`; `xtalk_extensions.py` stays
>   because `package.py` imports it).
> - `Toolset/palettes/standalone settings/mac-arm-deploy.oxtstack` is an IDE
>   file, as OpenXTalk Lite 1.15 ships it; OXT-Beyond removed it and classes
>   it as junk.

# OXT layout and packaging tools

These tools need only Python 3 (standard library, 3.8 or later: the Linux
build container has 3.8) and run on Windows, Linux and macOS, except the
IDE stack tools (`ide-stack-*`), which are LiveCode scripts run by a
development engine without a user interface.

| tool | purpose |
|---|---|
| `layout.py` | maps an installed OpenXTalk Lite (or stock LiveCode 9.x) Windows program folder to this repository's layout and back: imports an OXT Lite IDE into `ide/` and `ide-support/` and checks an import |
| `package.py` | stages the installed layout of OXT-Beyond for Windows, Linux or macOS from the repository, a build and the external assets (see [Packaging](#packaging-packagepy)) |
| `package_dist.py` | writes the platform's distribution archives (portable zip, tar.xz or app zip; binaries; symbols) and `SHA256SUMS` from a staged layout (see [Distribution archives](#distribution-archives-package_distpy)) |
| `binfmt.py` | reads architectures, needed libraries, run paths and OS floors from ELF and Mach-O files without readelf or otool (used by `package.py` to check that a build is for the platform, and by `tools/ci/check_native_deps.py` and `tools/ci/run_livecode_check.py`) |
| `ico.py` | writes the Windows icon files (`ide/OXT-Beyond.ico`, `engine/rsrc/oxt-beyond.ico`) from the branding PNGs, and checks one (used by `Installer/oxt-beyond/branding/draw_branding.py`) |
| `icns.py` | writes the macOS app icon (`.icns`) from the branding PNGs without `iconutil`, and checks one (see [The macOS app's identity](#the-macos-apps-identity)) |
| `fetch_assets.py` | downloads, caches and verifies the external assets listed in `external-assets.json` (see [External assets](#external-assets)) |
| `xtalk_extensions.py` | pins, fetches and builds the xTalk Suite extensions listed in `xtalk-extensions.json` (see [xTalk Suite extensions](#xtalk-suite-extensions-xtalk_extensionspy)) |
| `make_runtimes_asset.py` | builds an `oxt-runtimes-<label>.zip` asset from this repository's CI builds (`--builds`), or from an installed OXT Lite as for 1.15 (see [The runtimes asset](#the-runtimes-asset)) |
| `ide-stack-patch.sh`, `ide-stack-patch.livecodescript` | apply the script, property and image patches in `ide-stack-patches/` to the binary IDE stacks, verified (see [Binary IDE stacks](#binary-ide-stacks)) |
| `ide-stack-dump.livecodescript` | writes every object of a stack file as text, to compare two versions of a stack |
| `dark_disabled_icons.py` | makes the dark-appearance twins (`*-disabled-dark*.png`) of the toolbar's disabled icons from the enabled icons; `--check` verifies them (run in CI by `tools/ci/check_ide_icons.py`) |

## layout.py

`layout.py` maps an installed OpenXTalk Lite (or stock LiveCode 9.x) Windows
program folder to this repository's layout and back. It is how the IDE of an
OpenXTalk Lite release is imported into `ide/` and `ide-support/`, and how an
import is checked.

```
python tools/oxt/layout.py classify <installed-root> [--out FILE] [--from-list FILE]
python tools/oxt/layout.py import   <installed-root> <repo-root> [--dry-run] [--log FILE]
python tools/oxt/layout.py assemble <repo-root> <out-dir> [--eol keep|lf|crlf] [--force]
python tools/oxt/layout.py verify   <installed-root> <repo-root> [--upstream] [--keep-temp]
```

* `classify` prints one TSV line per installed file: class, installed path,
  repository path, size and the reason (usually the `Installer/package.txt`
  line that puts the file there). `--from-list` classifies a list of paths
  instead of a folder (for example the file list of an archive). Exit status
  1 if a path matches no rule.
* `import` makes the managed repository files (see below) mirror the install:
  it copies new and changed files, deletes managed files the install does not
  have and prints added / modified / deleted / unchanged counts per area.
  `--dry-run` only reports; `--log` writes every action as TSV. It refuses to
  run while any installed path is unclassified or two mirrored copies of a
  file differ.
* `assemble` writes the class (a) part of the installed layout from the
  repository.
* `verify` assembles into a temporary folder and compares the result with the
  install's class (a) files. Exit status 1 on any difference.

Text files (extensions in `TEXT_EXTENSIONS`) are compared with line endings
normalised, because git stores them with LF and may check them out with CRLF;
all other files are compared byte for byte. `import` copies the installed
bytes unchanged and git normalises text files when they are added.

### Classes

| class | meaning | in git |
|---|---|---|
| `ide` (a) | IDE content, mapped to a repository path | yes |
| `build` (b) | produced by building or packaging this repository | no |
| `external` (c) | files for other platforms and third-party collections that this repository's Windows build does not produce | no; `package.py` adds them from the release assets in `external-assets.json`, except `Ext/` |
| `junk` (d) | not part of the product | no |
| `excluded` (e) | shipped by OXT Lite but not redistributed by this project because of its licence (`NOT_REDISTRIBUTABLE` in `layout.py`) | no; `import` removes them |
| `xtalk` (f) | the xTalk Suite extensions: `Extensions/<folder>/**` for every folder in `xtalk-extensions.json`, and `Extensions/XTALK-EXTENSIONS.txt` | no; `package.py` builds them with `xtalk_extensions.py` from their repositories at pinned commits; `import` never copies them into `ide/Extensions` |
| `unknown` | no rule matches: add a rule | - |

Excluded for licence reasons (not in the repository, its history or its
packages):

- `Documentation/linked_files/apple-human-interface-guidelines-2005.pdf`:
  Apple Inc. copyright, no licence to redistribute.
- `Documentation/linked_files/animationEngine6.zip`: a third-party library
  with no licence in the release.

### Mapping

All rules are in `RULES` in `layout.py`; the first matching rule wins. They
follow `Installer/package.txt` as run by `builder/tools_builder.livecodescript`
and `builder/package_compiler.livecodescript` (Windows: `TargetFolder`,
`SupportFolder` and `ToolsFolder` are all the install root).

| installed path | class | repository path | package.txt |
|---|---|---|---|
| `Toolset/libraries/` + the 11 ide-support scripts (revdeploylibrary{android,emscripten,ios}, revdocsparser, revhtml5urllibrary, revliburl, revsaveas{,android,emscripten,ios}standalone, revsblibrary) | ide | `ide-support/<name>` | Toolset: `stack`/`file ide-support:...` |
| `Toolset/palettes/dictionary/api.sqlite` | junk | | zero-byte file nothing refers to |
| `Toolset/**` | ide | `ide/Toolset/**` | Toolset: `rfolder ide:Toolset` |
| `Plugins/**` | ide | `ide/Plugins/**` | Plugins |
| `Resources/Mobile Examples/**` | ide | `ide/Resources/Mobile Examples/**` | Mobile.MacOSX (macOS only) |
| `Resources/Sample Icons/**` | unknown | | never installed there |
| `Resources/**` | ide | `ide/Resources/**` | Resources |
| `Documentation/**` | ide | `ide/Documentation/**` | Documentation (see below) |
| `Runtime/Windows/{x86-64,x86-32}/Support/Sample Icons/*` | ide | `ide/Resources/Sample Icons/*` | Runtime.Windows: `file ide:Resources/Sample Icons/*` |
| `.version`, `.buildnumber`, `about.dat`, `about.txt`, `License Agreement.txt`, `Open Source Licenses.txt`, `OpenXTalk-lite_1024.ico`, `OXT-Beyond.ico`, `Release Notes.pdf` | ide | `ide/<name>` | Misc (`about.txt`, licences); the rest are OpenXTalk Lite or OXT-Beyond additions |
| `Extensions/<folder>/**` for a folder in `xtalk-extensions.json`, `Extensions/XTALK-EXTENSIONS.txt` | xtalk | | not in package.txt: the bundled xTalk Suite extensions (before the next rule) |
| `Extensions/<id>/**` for an id not built here | ide | `ide/Extensions/<id>/**` | new folder, see below |
| `Extensions/<one of the 42 com.livecode.* ids>/**` | build | | Extensions: `packaged_extensions` built from `extensions/` |
| `Extensions/com.livecode.library.timezone/code/x86_64-win32/**` | build | | TimeZone (win-x86_64) |
| `Extensions/com.livecode.library.timezone/code/**` (other platforms) | external | | TimeZone (other platform builds) |
| `Extensions/com.livecode.library.timezone/resources/**` | external | | TimeZone: the zoneinfo data, compiled by `zic` only in macOS and Linux builds (`tz.gyp` target `tzdata`); upstream takes the whole extension from the macOS build |
| `*.exe`, `revpdfprinter.dll`, `revsecurity.dll` (root) | build | | Engine.Windows; `.setup.exe` is the Uninstaller |
| `edition.txt` | build | | Toolset: `emit variable TargetEdition` |
| `Externals/**`, `Toolchain/**` | build | | Externals, Databases, Externals.CEF.Windows, Mobile.Windows, Toolchain.Windows |
| `Runtime/Windows/x86-64/**` | build | | Runtime.Windows x86-64 |
| `Runtime/**` (Windows x86-32, Linux, Android, Emscripten, macOS, iOS) | external | | Runtime.* for other platforms |
| `Ext/**` | external | | Ext: mergExt collection downloaded by the builder |
| `*.lnk`, `test.db`, `Toolset/palettes/standalone settings/mac-arm-deploy.oxtstack`, names starting with `.` below the root | junk | | |

Notes on the choices:

* **Toolset.** The packager generates nothing under `Toolset/`: in the stock
  9.6.3 package every one of the 988 Toolset files comes from `ide/Toolset`
  (977) or `ide-support` (11). Files that exist in OpenXTalk Lite's Toolset but
  not in the stock `ide/Toolset` are OpenXTalk Lite additions.
* **Documentation.** Upstream installs a selection of `ide/Documentation` plus
  `repo:docs/guides`, docs builder output (`html_viewer/resources/data/**`,
  `guides/Release Notes.md`) and a generated PDF. OpenXTalk Lite replaced this
  with its own tree (dictionary stack `oxt_dictionary.oxtstack`, its own
  `api.sqlite`, plain-text exports, PDFs, debranded guides, `linked_files`), so
  the whole installed `Documentation/` maps to `ide/Documentation/` and is
  tracked. Several guides are debranded copies of files under `docs/`
  (`docs/development`, `docs/specs`, `docs/guides`); they are kept as shipped.
* **ide/Extensions/** holds extensions the IDE ships but this repository does
  not build (their `.lcb` sources are included; the compiled `module.2.lcm`
  and `.lci` files are shipped as they are). In the repository layout the IDE
  loads extensions only from `<build>/packaged_extensions`
  (`revEnvironmentExtensionsPaths` in `Toolset/home.livecodescript`), so
  packaging has to place these in `Extensions/`.
* **Root files.** The IDE reads `.version`, `.buildnumber` and `about.dat` from
  the folder above `Toolset/` (`revmenubar.livecodescript`), which is `ide/`
  in the repository layout. `edition.txt` is written by the packager and stays
  ignored. OpenXTalk Lite 1.07 and earlier ship their own `Release Notes.pdf`
  (upstream generates one).
* **Sample Icons** are installed twice (x86-64 and x86-32 runtime folders);
  both copies must be identical, and `assemble` writes both.
* **Junk.** `OpenXTalk Lite.lnk` is a shortcut left by the install script; in
  1.15 it points at the 1.07 file name `OpenXTalk Lite.exe` (the 1.15 engine is
  `OpenXTalk-Lite.exe`) and contains a machine name and user SID. `test.db` and
  `Toolset/palettes/dictionary/api.sqlite` are zero-byte SQLite files; no
  script refers to the latter (the IDE and the Quick Dictionary plugin use
  `Documentation/html_viewer/resources/data/api/api.sqlite`).
  `mac-arm-deploy.oxtstack`, a macOS ARM standalone builder, is opened by
  nothing, and its button calls `_internal build MacARM`, which this engine
  does not have; it was removed from `ide/` in 0.2.1, and is junk so that
  an import does not bring it back.

### Managed files

`import` and `assemble` work on the managed set only: the files under
`ide/Toolset`, `ide/Plugins`, `ide/Resources`, `ide/Documentation`,
`ide/Extensions`, the root files listed above and the 11 `ide-support`
scripts, except

* names starting with `.` inside those folders (upstream packaging skips
  them), and
* `ide/Resources/Mobile Examples` (installed on macOS only; a Windows install
  says nothing about them).

Everything else (`ide/.gitignore`, `ide/.gitattributes`, `ide/tests`,
`ide/notes`, `ide/examples`, the other `ide-support` files, `docs/`,
engine sources) is never written or deleted. Ignored files that happen to be in a managed folder (for
example docs builder output in `ide/Documentation/html_viewer/resources/data`)
are treated like any other file there.

### Stock LiveCode packages (`verify --upstream`)

A stock LiveCode install differs from `ide/` in ways that are expected:
`Release Notes.pdf`, `Documentation/guides/**` from `repo:docs/guides` and the
docs builder, `Documentation/html_viewer/resources/data/**` (docs builder
output) and `Documentation/pdf/**` exist in the install only, and
`Plugins/livecodeTestInterface.livecode`, `Documentation/Docs Helper.livecode`,
`Documentation/dictionary/**` and `Documentation/specs/**` are in `ide/` but not
packaged. `--upstream` accepts exactly these (`UPSTREAM_DIFFERENCES`) as
long as they are missing on one side; any content difference still fails.

### Git

* Case-only renames: `import` reports paths whose letter case changes. With
  `core.ignorecase` (the Windows default) git does not see such a rename by
  itself; record it with `git rm --cached -q -- <old>` and `git add -- <new>`.
* Empty folders cannot be stored in git. `verify` lists empty folders of the
  install (1.15 has two, under
  `Documentation/html_viewer/resources/data/api/exports/{builder,datagrid}/plugins`).
* `ide/.gitignore` un-ignores the shipped files that its general rules (and
  `*.xz` in the top-level `.gitignore`) would hide: the dictionary database and
  data scripts, `Documentation/linked_files/*.zip`, `Resources/MacOs/*.zip` and
  the Linux theme archive.
* The longest managed path is 142 characters; a checkout in a deep
  folder on Windows may need `core.longpaths`.

### OpenXTalk Lite 1.15 import

Source: `openxtalk-lite-1.15-win-noinstaller.7z` (`.version` 1.15,
`.buildnumber` 202605052228), 7,211 files. `classify` at the time of the
import: 5,898 ide, 966 build (406.3 MB), 342 external (452.6 MB), 3 junk and
2 excluded (8.1 MB, see above). Since the timezone zoneinfo data (474 files)
became class external, the counts are 5,898 ide, 492 build (405.8 MB), 816
external (453.1 MB), 3 junk and 2 excluded. The 5,898 ide files are 5,896
repository files (the two Sample Icons are installed twice). The counts in
the table below were taken before the two excluded files were removed from
the import.

| area | added | modified | deleted | unchanged |
|---|---:|---:|---:|---:|
| ide (root files) | 4 | 1 | 1 | 1 |
| ide-support | 0 | 5 | 0 | 6 |
| ide/Documentation | 3,789 | 50 | 8 | 405 |
| ide/Extensions | 24 | 0 | 0 | 0 |
| ide/Plugins | 3 | 0 | 9 | 0 |
| ide/Resources | 16 | 0 | 14 | 155 |
| ide/Toolset | 553 | 337 | 91 | 549 |
| total | 4,389 | 393 | 123 | 1,116 |

Four of the Documentation deletions and two of its modifications were ignored,
locally generated docs builder files, not tracked files. `verify` of 1.15
against the repository passes (5,900 identical; 5,898 with the rules as they
are now, which leave out the two excluded files); so does `verify` of a copy
staged into a scratch git index and checked out again with `core.autocrlf`
true and false. `verify --upstream` of the stock LiveCode Community 9.6.3
Windows package (rebuilt from the payload in its `.setup.exe`) against the
original `ide/` and `ide-support/` passes: 1,624 identical, 35 expected
differences.

## Packaging (`package.py`)

```
python tools/oxt/package.py [--platform P] --repo <repo> --bin <build output> --out <stage-parent>
    [--build-number N] [--assets-cache DIR] [--no-external-assets] [--offline]
    [--no-xtalk-extensions] [--xtalk-compiler-bin DIR] [--vc-redist DIR]
    [--xtalk-cache DIR] [--xtalk-manifest FILE] [--eol lf|crlf|keep]
    [--allow-single-arch] [--summary-json FILE]
    [--compare <installed folder or classify TSV> [--report FILE]]
```

writes the installed program folder to `<stage-parent>/OXT-Beyond-<version>/`
(`<version>` is `ide/.version`; an existing folder of that name is replaced).
`--platform` chooses the layout; the default, `win-x86_64`, is the Windows
layout described first below, and the others are described in
[Linux and macOS layouts](#linux-and-macos-layouts):

| `--platform` | build output (`--bin` default) | layout |
|---|---|---|
| `win-x86_64` | `win-x86_64-bin` | `OXT-Beyond.exe` and everything else in one folder |
| `linux-x86_64` | `linux-x86_64-bin` | the engine as `OXT-Beyond` and everything else in one folder |
| `linux-arm64` | `linux-arm64-bin` | the same without CEF and without a runtime (staging only) |
| `mac-universal` | `_build/mac/Release`, lipo-merged | `OXT-Beyond.app`, the IDE in `Contents/Tools` |
| `mac-arm64`, `mac-x86_64` | `_build/mac/Release` of one architecture | the same from one architecture (for checking the layout) |

`tools/ci/package-windows.ps1` runs it into `dist/stage` and zips the result
as the portable package; the installer is built from the same folder. Nothing
is written when the plan has a problem (a missing build output, two sources
for one path, an asset that fails its checksum). Exit status: 0 success, 1
unexplained differences from `--compare`, 2 errors.

The folder is put together from:

* **IDE**: `layout.py` assemble (all class `ide` paths, including the Sample
  Icons in both `Runtime/Windows/<arch>/Support`). Text files are written with
  LF line endings (`--eol lf`, the default), as git stores them and as OXT Lite
  1.15 shipped nearly all of them, so the result does not depend on
  `core.autocrlf`. `.buildnumber` is replaced by the build number:
  `--build-number`, else `OXT_BUILD_NUMBER`, else the UTC time as
  `YYYYMMDDHHMM`; `ide/.buildnumber` is a placeholder.
* **Build outputs**, placed as `Installer/package.txt` places them for Windows
  x86-64 Community with `TargetFolder`, `SupportFolder` and `ToolsFolder` all
  the install root:

  | build output (`win-x86_64-bin/`) | installed path | package.txt |
  |---|---|---|
  | `LiveCode-Community.exe` | `OXT-Beyond.exe` | Engine.Windows (`as [[ProductName]].exe`) |
  | `revpdfprinter.dll`, `revsecurity.dll` | root | Engine.Windows |
  | `revspeech.dll`, `revxml.dll`, `revbrowser.dll`, `revzip.dll`, `revdb.dll` | `Externals/` | Externals.Windows, Databases.Windows |
  | `dbmysql.dll`, `dbodbc.dll`, `dbpostgresql.dll`, `dbsqlite.dll` | `Externals/Database Drivers/` | Databases.Windows |
  | `libbrowser-cefprocess.exe`, `revbrowser-cefprocess.exe` (build root) | `Externals/CEF/` | Externals.CEF.Windows |
  | `Externals/CEF/`: `libcef.dll`, `d3dcompiler_47.dll`, `libEGL.dll`, `libGLESv2.dll`, `chrome_elf.dll`, the `.pak`, `.dat` and `.bin` files, `swiftshader/*`, `locales/**` | `Externals/CEF/` | Externals.CEF.Windows |
  | `revandroid.dll` | `Externals/` | Mobile.Windows |
  | `lc-compile.exe`, `lc-run.exe`, `lc-compile-ffi-java.exe`, `modules/**` | `Toolchain/`, `Toolchain/modules/` | Toolchain.Windows |
  | `standalone-community.exe` | `Runtime/Windows/x86-64/Standalone` (no extension) | Runtime.Windows |
  | `w32-manifest-template*.xml` (3) | `Runtime/Windows/x86-64/` | Runtime.Windows |
  | `revpdfprinter.dll`, `revsecurity.dll` | `Runtime/Windows/x86-64/Support/` | Runtime.Windows |
  | the Externals rows above (without `revandroid.dll`) | `Runtime/Windows/x86-64/Externals/...` | Runtime x86-64: `include Externals` |
  | `packaged_extensions/<id>/**` for the 42 ids in `REPO_BUILT_EXTENSIONS` | `Extensions/<id>/**` | Extensions, TimeZone |

* **Generated files**: `edition.txt` (`community`, no line break);
  `Externals.txt` (`Speech,revspeech.dll`, `XML,revxml.dll`,
  `Browser,revbrowser.dll`, `Revolution Zip,revzip.dll`, `Database,revdb.dll`)
  and `Database Drivers/Database Drivers.txt` (`MySQL,dbmysql.dll`,
  `ODBC,dbodbc.dll`, `PostgreSQL,dbpostgresql.dll`, `SqLite,dbsqlite.dll`),
  CRLF line endings, in both `Externals/` and
  `Runtime/Windows/x86-64/Externals/`; all five generated build files are
  byte-identical to OXT Lite 1.15's. The empty folders
  `Documentation/html_viewer/resources/data/api/exports/{builder,datagrid}/plugins`.
* **Licence files**: `LICENSE`, `LICENSE-EXCEPTION.md` and
  `THIRD-PARTY-NOTICES.md` from the repository root, with CRLF line endings.
* **External assets** from `external-assets.json` (`--no-external-assets`
  leaves them out).
* **xTalk Suite extensions** from `xtalk-extensions.json`: `package.py`
  runs `xtalk_extensions.build` with the `--bin` build's `lc-compile` and
  `modules/lci` into a temporary folder and stages every file under
  `Extensions/` byte for byte (origin `xtalk`), plus
  `Extensions/XTALK-EXTENSIONS.txt` (with the platform's line endings).
  `--vc-redist` is passed through (the
  Visual C++ runtime DLLs for enetxt and Box2Dxt); without it packaging
  warns. The cache is `--xtalk-cache`, else `OXT_XTALK_CACHE`, else the
  `xtalk` folder of the asset cache; `--offline` applies to it too.
  `--no-xtalk-extensions` leaves them out. The summary (and
  `--summary-json`: `xtalk_extensions`, `vc_redist_version`,
  `vc_runtime_files`, `xtalk_missing_runtime`) lists them.

Build outputs that are not installed (every run lists them): `*.pdb` (they go
into the symbols zip), `installer.exe` (OXT-Beyond uses Inno Setup),
`server-*` (package.txt installs no server engine),
`Externals/CEF/devtools_resources.pak` (not in package.txt), the second copies
of the two CEF helper executables in `Externals/CEF/` (package.txt takes the
identical copies at the build root), and `packaged_extensions/` for
`com.livecode.library.canvas` and `com.livecode.library.ini` (not in
package.txt; neither LiveCode 9.6.3 nor OXT Lite 1.15 ships them). Build
outputs that no rule covers are listed with a warning.

### Linux and macOS layouts

The Linux and macOS tables (`Platform` in `package.py`) transcribe the same
`Installer/package.txt` components for `TargetPlatform Linux` and `MacOSX`.
The IDE part, the generated files, the licence files, the external assets
and the xTalk Suite extensions are the same as on Windows (every platform
gets the xTalk libraries of all five platform ids, so that it can build
standalones for the others). Where `package.txt` and the IDE or the engine
disagree, the layout follows the IDE:

* **macOS: everything under `OXT-Beyond.app/Contents/Tools`.** The engine
  (`engine/src/environment/stackbehavior.livecodescript`) takes
  `X.app/Contents/Tools` as the tools folder of `X.app/Contents/MacOS/<exe>`,
  and the IDE (`revEnvironmentToolsPath`, the folder above `Toolset`) looks
  there for `Resources`, `Externals`, `Extensions`, `Runtime` and the root
  files `.version`, `about.dat` and `.buildnumber`; `package.txt` puts some
  of them into `Contents/Support`. Only the engine stays outside: the build's
  `LiveCode-Community.app` becomes `OXT-Beyond.app` with
  `Contents/MacOS/OXT-Beyond`, OXT-Beyond's own `Info.plist` and icon
  (see [The macOS app's identity](#the-macos-apps-identity)).
  `revsecurity.dylib` and
  `revpdfprinter.bundle` go into `Contents/MacOS` from the build root
  (package.txt Engine.MacOSX: the stripped copies; the build's copies
  inside the app keep their symbols).
  The app's `_CodeSignature` is left out, because it seals the old
  executable name and `Info.plist`. Also, every Mach-O the build strips
  (`tools/extract-debug-symbols.sh` runs after Xcode's ad-hoc signing)
  keeps a stale signature. So after assembly the app is signed ad hoc
  from the inside out and verified with `codesign --verify --deep
  --strict` ([`tools/ci/sign_mac_app.py`](../ci/sign_mac_app.py), see
  [Universal build, signing and disk image](#universal-build-signing-and-disk-image-macos)).
  `Resources/Mobile Examples` (package.txt Mobile.MacOSX) is staged only
  here.
* **macOS runtimes: the folder names the standalone builder uses**
  (revsblibrary `revEngineCheck`/`revSBEnginePath`, revsaveasstandalone):
  `Runtime/Mac OS X/x86-64/Standalone.app` and
  `x64-ARM64/Standalone-blank.app` (the engines deployed for the Intel and
  the Apple Silicon target, each with `Support/` and `Externals/`; the
  Apple Silicon target reads its externals from `arm64/`, below), and
  `x86-32/Standalone.app`, which is only looked at but must exist: it
  enables the Intel target and every Mac standalone takes its icons from
  it. Each is the build's `Standalone-Community.app` (the executable keeps
  its name, which revSBEnginePath expects). `Runtime/Mac OS X/arm64` holds
  only `Externals/` (with `Externals.txt` and `Database Drivers/`): for the
  Apple Silicon target `revStandalonePlatformDetails` takes `Support` from
  `x64-ARM64` but passes the architecture `arm64`, and `revExternalPath`
  and `revDBDriverPath` then read `Mac OS X/arm64/Externals`. Without it
  the standalone gets no revXML, revZip, revDB, revBrowser, revSpeech or
  database driver, and the builder only warns. `x64-ARM64/Externals`
  stays, because `revStandaloneDatabaseDriversPath` warns when the target
  folder's `Externals` is missing. `x86-32` has no `Externals`: its target
  cannot build (the engine has no i386 slice, and the IDE disables it on
  macOS 12 and later). `mac-universal` has all four; `mac-arm64` has
  `x64-ARM64`, `arm64` and `x86-32`; `mac-x86_64` has `x86-64` and
  `x86-32`. The runtime's `Info.plist` is staged as built (the builder
  edits it line by line), so it must already declare the lowest minimum
  macOS of its engine; `package.py` checks that it does (after the lipo
  merge: the x86_64 build's 10.13).
  [`tools/ci/standalone_check.py`](../ci/standalone_check.py) checks
  these folders in an installed app (engines, architectures, minimum
  macOS, icons, `Support`, `Externals` lists, signatures) and builds and
  runs a standalone from `x64-ARM64/Standalone-blank.app` with the
  engine's deploy command (what the builder's
  `revStandaloneDeployWithParams` runs). It does the same for the Windows
  and Linux runtimes (`--targets all`: every runtime of a layout, each
  standalone built with the layout's engine and run where the machine can
  run it), and CI runs it on every package and on the 32-bit builds; see
  [BUILDING.md](../../BUILDING.md#standalone-check). **Still to do:** a
  test that runs the IDE's builder itself for the `MacOSX x64-ARM64`
  target (what the standalone settings' `MacOS-IntelArmUniversal` button
  selects) with revXML and the SQLite driver, checks that
  `Contents/MacOS/Externals/revxml.bundle` and
  `Contents/MacOS/Externals/database_drivers/dbsqlite.bundle` are in it,
  and fails on any builder warning (`revStandaloneGetWarnings`): an app
  without externals builds whether or not `arm64/Externals` exists. The
  builder (`revSaveAsStandalone`, `revDoSaveAsStandalone`) needs the IDE's
  home stack and libraries loaded in the headless engine, as upstream's
  `tests/_standalonetestrunner/StandaloneTestRunnerBuilder.livecodescript`
  does for a source checkout; doing that with an installed IDE is not
  done yet.
* **Linux CEF helpers where the code starts them:** `revbrowser-cefprocess`
  in `Externals/CEF` (cefbrowser.cpp starts it from the libcef folder) and
  `libbrowser-cefprocess` next to the engine (libbrowser_cef.cpp, Linux:
  the executable's folder); `package.txt`'s "horrible workaround" puts both
  at the root. `Runtime/Linux/x86-64` also gets both at its root, where
  `revCopyCEFResources` copies them from for a standalone. `chrome-sandbox`
  is left out: CEF runs with `no_sandbox`, and the helper would only work
  owned by root with mode 4755.
* **Linux engine name:** `OXT-Beyond`, not `package.txt`'s
  `OXT-Beyond.x86_64` (one architecture per package, started through a
  launcher script).
* **Linux launcher, install scripts and desktop files** (origin
  `desktop`, `LINUX_DESKTOP` in `package.py`; package.txt's
  `Installer/application.desktop` is LiveCode's and names an icon the
  package does not have): from `Installer/linux/`, with LF line endings,
  `oxt-beyond` (the launcher), `install.sh` and `uninstall.sh` at the
  root with mode 0755, and `linux/` with `libraries.txt` (the system
  libraries the launcher checks and the CI installs),
  `oxt-beyond.desktop` (its `StartupWMClass` filled in with the window
  class the engine gives the IDE's windows, `livecodecommunity_` and
  the engine version with `.` and `-` as `_`), `oxt-beyond.xml` (the
  MIME types) and `icons/oxt-beyond-<n>.png`, the branding PNGs in the
  sizes install.sh installs (16 to 512). The launcher checks the
  libraries, sets `LIVECODE_USE_CEF=0` where the browser cannot load,
  and execs the engine; `install.sh` installs the folder for one user
  under `${XDG_DATA_HOME:-~/.local/share}/oxt-beyond` with a desktop
  entry, icons, MIME types and `~/.local/bin/oxt-beyond`, and records
  what it made for `uninstall.sh`. The scripts' own comments describe
  them.

| build output | installed path (Linux) | installed path (macOS, below `Contents/Tools` unless noted) |
|---|---|---|
| development engine | `OXT-Beyond` | `OXT-Beyond.app` (not below `Tools`) |
| `revpdfprinter`, `revsecurity` | root (`.so`) | `OXT-Beyond.app/Contents/MacOS/` (`.bundle`, `.dylib`) |
| externals | `Externals/revxml.so`, `revzip.so`, `revbrowser.so`, `revdb.so` (no revspeech on Linux) | `Externals/revspeech.bundle`, `revxml.bundle`, `revbrowser.bundle`, `revzip.bundle`, `revdb.bundle` |
| database drivers | `Externals/Database Drivers/db*.so` | `Externals/Database Drivers/db*.bundle` |
| CEF (x86_64 only) | `Externals/CEF/` (libcef.so, libEGL.so, libGLESv2.so, the `.pak`, `.dat` and `.bin` files, `swiftshader/`, `locales/`, `revbrowser-cefprocess`), `libbrowser-cefprocess` at the root | none (revbrowser uses WebKit) |
| mobile | `Externals/revandroid.so` | `Externals/reviphone.bundle`, `revandroid.bundle` |
| toolchain | `Toolchain/lc-compile`, `lc-run`, `lc-compile-ffi-java`, `modules/` | the same |
| standalone engine | `Runtime/Linux/x86-64/Standalone` with `Support/` and `Externals/` (including CEF and both helpers at its root) | `Runtime/Mac OS X/x86-64/Standalone.app`, `x64-ARM64/Standalone-blank.app` (each with `Support/` and `Externals/`) and `x86-32/Standalone.app`; the Apple Silicon target's externals in `Runtime/Mac OS X/arm64/Externals/` |
| `packaged_extensions/<id>` | `Extensions/<id>` (the 42 ids; the timezone library brings its own zoneinfo and native code) | the same |
| (from the repository) `Installer/linux/*`, branding PNGs | `oxt-beyond`, `install.sh`, `uninstall.sh`, `linux/` | none |

Not installed on Linux: `*.dbg` (the symbols archive), `installer`,
`server-*`, `Externals/CEF/chrome-sandbox` and `devtools_resources.pak`,
canvas and ini, and the build's own tools if present (`gentle-target`,
`reflex-target`, `perfect-target`, `lc-bootstrap-compile*`,
`lc-compile-stage*`, `zic`, `lcidlc`, `*.a`). On macOS: `*.dSYM`,
`Installer.app`, `installer-stub`, `server-*`, the root copies of
`reviphoneproxy`, `tz.dylib` and `inih.dylib`, the app's own `Info.plist`,
`_CodeSignature` and unstripped support files, canvas and ini, and the same
build tools.

`package.py` checks that the build is for the platform: the Linux engines
must be ELF files of the platform's architecture, and on macOS the engine,
the standalone engine and `revsecurity.dylib` must hold every architecture
of the layout (arm64 and x86_64 for `mac-universal`, which therefore needs
the lipo merge of the two CI builds, `tools/ci/merge_universal.py`;
`--allow-single-arch` stages a
single-architecture build as `mac-universal` anyway, with a warning).
`tools/oxt/binfmt.py` reads the headers, so this works on any host.

xTalk Suite extensions are compiled with the `lc-compile` and
`modules/lci` of `--xtalk-compiler-bin` (default: `--bin`). The compiled
modules do not depend on the platform, so a macOS layout can be staged on
Linux with a Linux build's compiler.

The Linux and macOS layouts are Unix trees, staged on Linux or macOS (or
WSL, under a Linux path such as `/tmp`: a Windows drive such as `/mnt/c`
keeps neither modes nor letter case, and both `package.py` and
`package_dist.py` refuse it, as a probe file in `--out` and next to the
stage shows; a `--bin` folder there is refused too, since every build
output would read as executable). `package.py` refuses to stage them on
Windows, which keeps neither modes nor symbolic links:

* `--bin-tar` takes the build output as the tarball a CI build uploads
  (`OXT-Beyond-linux-<arch>-bin.tar.xz`, `OXT-Beyond-mac-<arch>-bin.tar.xz`)
  instead of `--bin`. Its one top-level folder is extracted to a temporary
  folder, keeping modes, symbolic and hard links, and without the debug
  symbols (`*.dbg`, `*.dSYM`, `*.pdb`) and the `._*` AppleDouble files
  that macOS tar adds. Member names and links are checked, as Python
  3.8's tarfile has no extraction filter: each member's folder is resolved
  on disk, since a chain of links whose text looks harmless (`sub -> ..`,
  `l -> sub/..`) could otherwise carry a later member out of the folder.
* **Modes**: executable build outputs (any `x` bit), the xTalk native
  libraries and asset members stored with an `x` bit get 0755; every
  other file 0644 and every folder 0755, whatever the umask or the
  checkout (git marks some IDE images executable).
* **Symbolic links** inside a build output folder (a macOS framework's
  `Versions/Current`) are staged as the same relative links; one that is
  absolute or leads out of its folder is an error. A build output named in
  a table is copied through a link.
* **Names**: paths that differ only in letter case conflict on Windows and
  macOS (volumes are usually case-insensitive), not on Linux.
* **Text**: `Externals.txt`, `Database Drivers.txt`,
  `Extensions/XTALK-EXTENSIONS.txt` and the licence files get LF line
  endings (the IDE and the standalone builder read the lists line by line,
  so a CR would end up in every file name, and `cut` or `awk` would end
  every field of the tab-separated xTalk list with one); CRLF on Windows.

Every platform warns when the stage path has a folder name that switches
the engine into repository mode (`_build`, `<platform>-<processor>-bin`,
`<platform>-bin`, `build-<platform>-<processor>`, as
`stackbehavior.livecodescript` and the home stack's
`revEnvironmentGuessRepositoryPath` test them): an engine started there
runs the IDE of the checkout above it, not the staged one.

### Checking a package against OpenXTalk Lite 1.15

`--compare` (`win-x86_64` only: the reference is a Windows install) checks the staged folder against a reference install or a
`layout.py classify` TSV (also a part of one, such as its `build` rows).
Every `ide`, `build` and `external` path of the reference must be staged,
with the engine under its new name, unless `INTENDED_MISSING` gives a reason;
every staged path of the classes the reference lists must be in it unless it
is an intended addition. With a folder, external asset files must be
byte-identical to the reference and empty folders must match. IDE changes
since the reference are listed but are not errors. `--report` writes the
status of every path as TSV.

Intended differences from OXT Lite 1.15:

| path | why |
|---|---|
| `OpenXTalk-Lite.exe` | staged as `OXT-Beyond.exe` |
| `Ext/**` (46 files) | the mergExt collection is not redistributed (licence unclear) |
| `Toolchain/modules/lci/` `com.livecode.library.native.android.barcode`, `...barcodesupport`, `com.livecode.library.native.speech`, `com.livecode.library.securekey`, `com.livecode.widget.native.android.barcodescanner`, `com.livecode.widget.native.map`, `com.livecode.widget.pdf`, `com.livecode.widget.pdf.pdfium`, `com.livecode.widget.signature` (`.lci`) | interfaces of LiveCode commercial-edition modules. OXT Lite 1.15's `Toolchain/` is the stock LiveCode 9.6.3 one (all 78 files identical), which has them; the modules are not in this repository and the IDE does not use them |
| `Toolchain/modules/lci/com.livecode.commercial.license.lci` | also from stock 9.6.3; this repository compiles `engine/src/license.lcb` into lc-compile (`engine_syntax_only_lcb_files`) and writes no `.lci` for it |
| the 3 junk and 2 excluded files | see [Classes](#classes) |
| `LICENSE`, `LICENSE-EXCEPTION.md`, `THIRD-PARTY-NOTICES.md` | added: OXT-Beyond's licence files |
| `PROVENANCE-oxt-runtimes-*.md` | added: provenance of the runtimes asset (`PROVENANCE-oxt-runtimes-0.2.1-rc.6.md`, and `PROVENANCE-oxt-runtimes-1.15.md` for its Android files) |
| `Runtime/Windows/x86-32/**`, `Runtime/Linux/**`, timezone `code/**` and `resources/**` | with a runtimes asset made from this repository's builds (any but `oxt-runtimes-1.15`), these may differ from 1.15's files, lack what 1.15's Linux runtimes bundled in `lib/` (shared libraries of other projects) and its iOS and macOS 10.9 timezone code, or have new files (the externals lists of the 64-bit Linux runtime, for example); `Runtime/Android/**`, carried over unchanged, is compared byte for byte |
| `Extensions/<xTalk folders>/**`, `Extensions/XTALK-EXTENSIONS.txt` | added: the xTalk Suite extensions (class `xtalk`); against a reference that has them they are compared like build outputs, and `--no-xtalk-extensions` makes them intended differences |

Result with the CI build of this repository
(`OpenXTalkLite-9.7.1-OXT-win-x86_64-binaries.zip`, as the binaries zip
was named then), the runtimes asset and
the IDE as it was when this was written, compared with the 1.15 install:
`COMPARE PASSED`. All 5,898 `ide` paths are staged (7 of them already changed
and 2 files added by the OXT-Beyond branding and updater work); of the 492
`build` paths, 482 are staged (320 byte-identical to 1.15, 157 rebuilt, 5
generated and identical) and 10 are intended differences; all 770
redistributed `external` paths are staged and byte-identical; the 7 empty
folders match. Against the 966 class `build` rows of the 1.15 import (taken
before the zoneinfo data became external): 956 staged, 10 intended
differences.

### The macOS app's identity

`package.py` (`mac_info_plist`) writes the app's `Info.plist` from the
build's (`engine/rsrc/LiveCode-Info.plist` as Xcode wrote it; its `DT*`
keys stay):

| key | value | why |
|---|---|---|
| `CFBundleIdentifier` | `io.github.sethmorrowsoftware.oxt-beyond` | OXT-Beyond's own, so that macOS keeps its preferences, document bindings and permissions apart from LiveCode's (`com.runrev.livecode`) |
| `CFBundleName`, `CFBundleDisplayName` | `OXT-Beyond` | |
| `CFBundleShortVersionString` | `ide/.version` | the version users see |
| `CFBundleVersion` | the build number, the UTC build time `202609291200` as `2026.929.1200` | at most three period-separated integers (Apple's rule); higher for every build, so LaunchServices prefers the newer of two copies |
| `CFBundleGetInfoString`, `CFBundleLongVersionString` | OXT-Beyond's version, build number and the engine's version | |
| `NSHumanReadableCopyright` | the OXT-Beyond contributors; OpenXTalk Lite by Terry Little, Tom Perry and the OpenXTalk contributors; LiveCode Community, © 2000-2020 LiveCode Ltd.; GPLv3 | as `about.dat` and the installer credit them |
| `CFBundleIconFile` | `OXT-Beyond.icns` | made from `Installer/oxt-beyond/branding/png/` by `icns.py` |
| `CFBundleExecutable` | `OXT-Beyond` | the renamed executable |
| `LSArchitecturePriority` | the layout's architectures, arm64 first | LiveCode's says `x86_64, i386`: LaunchServices starts the first listed architecture the binary has, so a universal app would run under Rosetta on Apple Silicon |
| `LSMinimumSystemVersion` | the lowest `minos` of the engine's slices: 10.13 for `mac-universal` | Xcode writes the deployment target of the one build the tree came from (11.0 for arm64); a universal app must declare its lowest slice's, or Intel Macs below 11 refuse it |
| `CFBundleDocumentTypes` | `.oxtstack` and `.oxtscript` with rank Owner; LiveCode's `.rev`, `.livecode`, `.livecodescript` entry with rank Alternate | the app opens LiveCode's files but does not take them over from an installed LiveCode, as on Windows, where the installer associates only the OXT types |
| `UTExportedTypeDeclarations` | `io.github.sethmorrowsoftware.oxt-beyond.stack` (`.oxtstack`, public.data) and `.script` (`.oxtscript`, public.script: plain text, so Quick Look shows it) | OXT-Beyond's types; no document icon is named, so macOS 11 and later draw one from the app icon |
| `UTImportedTypeDeclarations` | LiveCode's `com.runrev.*` types, which the engine's plist exports | LiveCode owns them; its two declarations of `com.runrev.livecode.stack` become one |

`icns.py` writes the `.icns` from the PNG files without `iconutil`: the
entries `ic11`, `ic12`, `ic07`, `ic13`, `ic08`, `ic14`, `ic09` and `ic10`
(32 to 1024 pixels, at 1x and 2x) hold the PNG files unchanged, after a
table of contents, as `iconutil` and Pillow write them; the 16 and 32
pixel 1x types of `iconutil` (`ic04`, `ic05`) hold ARGB data, so they are
left out and macOS scales the 2x images down. `icns.py check FILE`
reads one back (every PNG's chunks, CRCs, size and image data).

### Universal build, signing and disk image (macOS)

The macOS workflow (`.github/workflows/build-macos.yml`, job "Package
mac-universal") builds the release app from the two CI builds:

1. **Merge**: [`tools/ci/merge_universal.py`](../ci/merge_universal.py)
   `--arm64 A/Release --x86_64 X/Release --out U/Release` (each build's
   bin and symbols tarballs extracted into a folder of its own) joins
   every Mach-O file at the same path with `lipo -create` (the DWARF
   files of the `.dSYM` bundles too), compares every other file byte for
   byte, keeps links, modes, dates and empty folders, and then requires
   every Mach-O file of macOS code in the result to hold arm64 and
   x86_64. Differences are errors unless a rule in the script explains
   them. On the arm64 and x86_64 CI builds of this repository: 41 Mach-O
   files joined, 871 files identical, no file in one build only, and
   these explained:

   | rule | files | why |
   |---|---:|---|
   | `DIFFER` plist: `**/Contents/Info.plist` | 16 (every bundle's) | they differ only in `LSMinimumSystemVersion` (11.0 / 10.13; Xcode writes each build's deployment target); the x86_64 build's copy is taken byte for byte (the standalone builder edits the runtime's line by line), and it must match the lowest `minos` of the bundle's executable. Any other key that differs fails, except the build machine's (`DT*`, `BuildMachineOSBuild`), which are reported. After writing, one array is edited: `LiveCode-Community.app`'s `LSArchitecturePriority` says `x86_64, i386` (LiveCode's Intel days), and LaunchServices starts the first listed architecture the executable holds, so the universal development engine would run under Rosetta on Apple Silicon; it becomes `arm64, x86_64` (only that array, as text; a plist without the key, such as the runtime's, is not touched) |
   | `DIFFER` seal: `**/Contents/_CodeSignature/CodeResources` | 2 (`LiveCode-Community.app`, `reviphone.bundle`) | a bundle's resource seal records the code-directory hash of its nested code, which differs per architecture; it is stale after the merge either way and written again when the tree is signed |
   | `SINGLE_ARCH`: `**/reviphoneproxy`, `**/reviphoneproxy.dSYM/**` | 2 (`reviphoneproxy`, `reviphone.bundle/Contents/MacOS/reviphoneproxy`) | the iOS simulator helper is built for x86_64 only on both runners (revmobile.gyp); the x86_64 build's copy (10.13) is taken, and nothing of the arm64 build's copy (also x86_64 only, 11.0) is lost |
   | `ONE_SIDED`: `**/*.dSYM/Contents/Resources/Relocations/<arch>/**` | none in these builds' binaries | dsymutil writes each architecture's relocations into a folder of its own, when it writes them |

   `--dry-run` only reports; `--no-lipo` joins the slices with the
   script's own fat-file writer, so the merge and what follows can be
   tried on Linux or WSL. `--check DIR` checks a tree or the app only
   (also that no bundle's `Info.plist` puts x86_64 before arm64 in
   `LSArchitecturePriority` when its executable holds both).
2. **Sign the merged tree** ad hoc (`sign_mac_app.py U/Release`), so that
   the binaries archive holds code that runs on Apple Silicon.
3. **Stage** `package.py --platform mac-universal --bin U/Release`.
4. **Sign the app**: [`tools/ci/sign_mac_app.py`](../ci/sign_mac_app.py)
   `OXT-Beyond.app` runs `codesign --force --sign - --timestamp=none` on
   every bundle whose executable is macOS code (as a bundle) and every
   other signable Mach-O file of macOS code, deepest first, then on the
   app, and verifies each with `codesign --verify --strict` and the app
   with `--deep` as well. No `--deep` for signing (it would reach only the
   nested code locations, not `Contents/Tools`) and no
   `--preserve-metadata` (the build's signatures hold only made-up
   identifiers and Xcode's `get-task-allow` debugging entitlement).
   Extended attributes are removed first. The time zone library's iOS
   files, object files and `.dSYM` bundles are left alone.
5. **Check** that the app is universal (`merge_universal.py --check`,
   with `lipo -archs` of every file agreeing).
6. **Archives**: `package_dist.py --dmg` (see [Distribution archives](#distribution-archives-package_distpy)).

## Distribution archives (`package_dist.py`)

```
python tools/oxt/package_dist.py (--summary <package.py --summary-json> | --platform P --stage DIR)
    (--bin DIR | --bin-tar FILE | --no-binaries) [--symbols-tar FILE] --out DIR
    [--xtalk-sources [--xtalk-cache DIR] [--assets-cache DIR]]
    [--dmg] [--zip-level N] [--xz-preset N] [--no-hardlinks] [--summary-json FILE]
```

writes the archives of a staged layout and of its build output, and
`SHA256SUMS` (`<sha256>  <file>`, LF: binaries, disk image, package,
symbols, sources). `<root>` is `OXT-Beyond-<version>`:

| platform | package | binaries | symbols |
|---|---|---|---|
| `win-x86_64` | `<root>-win-x86_64-portable.zip`: every staged file under `<root>/`, then the empty folders | `<root>-win-x86_64-binaries.zip`: `win-x86_64-bin/` without `*.pdb`, licence files at the top | `<root>-win-x86_64-symbols.zip`: the `*.pdb` under `win-x86_64-bin/` |
| `linux-<arch>` | `<root>-linux-<arch>.tar.xz`: the staged folder as `<root>/` | `<root>-linux-<arch>-binaries.tar.xz`: `linux-<arch>-bin/` without `*.dbg` and without the build's own tools (below), licence files | `<root>-linux-<arch>-symbols.tar.xz`: the `*.dbg` (not the build tools') |
| `mac-<arch>` | `<root>-mac-<arch>.zip`: `OXT-Beyond.app` (`ditto -c -k --sequesterRsrc --keepParent` on macOS); with `--dmg`, also `<root>-mac-<arch>.dmg` | `<root>-mac-<arch>-binaries.tar.xz`: `Release/` (for `mac-universal` the merged tree) without `*.dSYM` and without the build's own tools (below), licence files | `<root>-mac-<arch>-symbols.zip`: the `*.dSYM` bundles (not the build tools') |

The Linux and macOS build outputs hold the tools the build makes to build
itself (`package.BUILD_PROGRAMS`: `gentle-target`, `reflex-target`,
`perfect-target`, the lc-compile bootstrap stages `lc-bootstrap-compile*`
and `lc-compile-stage*`, `zic`, `lcidlc`). `package.py` never installs
them, and the binaries and symbols archives leave them out too, with
their `.dSYM` bundles and `.dbg` files (the log counts them):
`gentle-target` and `reflex-target` are GENTLE 97, which may not be
redistributed (see `THIRD-PARTY-NOTICES.md`, "GENTLE"), and the Windows
binaries zip holds no build tools either. An entry named `gentle-*` or
`reflex-*` that still reaches an archive is an error, before any archive
is written.

`--xtalk-sources` adds `<root>-xtalk-sources.zip` (`xtalk_extensions.py
export`, from the cache only).

`--dmg` (a macOS layout, on macOS) writes the disk image: `ditto` copies
the (signed) app into a temporary folder next to a link to
`/Applications`, and `hdiutil create -format UDZO -fs HFS+ -volname
"OXT-Beyond <version>"` makes the image (HFS+: every macOS the app runs
on reads it, and it is case-insensitive like the names `package.py`
checks). A failure is retried up to four times (hdiutil sometimes fails
with "Resource busy" on CI runners), and `hdiutil verify` checks the
result. Sign the app before this step: the image and the zip hold it as
it is.

* **Windows**: the same names and entries (names, bytes, file dates, folder
  entries) as `tools/ci/package-windows.ps1`, which the Windows CI keeps
  using for now. Only the entry order (PowerShell sorts by culture rules),
  the compressed bytes (.NET's Optimal is Windows' own zlib build; zlib
  level 9, the default `--zip-level`, comes closest) and the date of an
  empty folder's entry (the folder's own date; .NET stamps the time of
  writing) differ. Checked with the 0.0.1 build output: all three zips
  have the same 417, 7,274 and 0 entries with the same CRCs, sizes and
  file dates, the sources zip is byte-identical, and `SHA256SUMS` lists
  the same files in the same order.
* **Unix archives** keep what `package.py` staged: modes, symbolic links
  and empty folders; owners 0 without names; dates in whole seconds
  (clamped to `SOURCE_DATE_EPOCH` when set); entries sorted, folders
  first. In a Linux package, staged files of 64 KiB or more with the same
  content and mode are stored once as hard links (`--no-hardlinks` stores
  each): the runtime's CEF, externals and helpers are the IDE's files. A
  hard link extracts with its target's mode, hence the mode in the key.
  The tarball therefore holds hard links and must be extracted onto a file
  system that supports them (not FAT, which cannot hold the package's
  modes either). Tarballs
  are compressed by `xz -T0` when the `xz` program exists (Python's lzma
  uses one core; `OXT_PYTHON_XZ=1` forces it), preset 6 (`--xz-preset`).
* **The macOS zip** is `ditto`'s own on macOS. Elsewhere (or with
  `OXT_PYTHON_ZIP=1`) it is written here, with folder entries, each
  file's Unix mode and symbolic links stored as links, as `ditto` writes
  them, so that `ditto -x -k`, Archive Utility or `unzip` restore the app
  with its executables. A signed app needs nothing else: signatures live
  inside the files (a `__MACOSX/` side file, which `--sequesterRsrc`
  writes for an extended attribute, is reported; `sign_mac_app.py`
  removes them first).
* `--bin-tar` reads a CI tarball once, as a stream, and sends each member
  to the binaries or the symbols archive under the platform's folder name
  (`linux-<arch>-bin`, `Release`), without `._` AppleDouble files and with
  modes, symbolic and hard links. The Linux and macOS archives are written
  on Linux or macOS only (in WSL, from a stage under a Linux path: a probe
  next to the stage refuses a Windows drive, whose files all read 0777).
* `--symbols-tar` takes the CI's symbols artifact
  (`OXT-Beyond-linux-<arch>-symbols.tar.xz`: the build output folder with
  only its `*.dbg` files; the macOS one holds the `*.dSYM` bundles), since
  the bin tarball no longer has them, and adds its files to the symbols
  archive. A member that is not a debug symbol file, a hard link, or a
  file the build output has too is an error.
* A path of the Linux package with a folder named like a build output
  (`package.repository_mode_trap`: `_build`, `linux-bin`,
  `linux-<arch>-bin`, `build-linux-<arch>`) is an error: an engine on
  such a path runs the IDE of a source checkout. There is none today.

Under GitHub Actions it writes the step outputs `version`, `package-root`,
`platform`, `dist-dir`, `package`, `dmg` (with `--dmg`) and `sha256sums`
and a table of the archives to the job summary.

## External assets

`external-assets.json` lists archives that packaging adds to the installed
layout: files this repository does not build, kept out of git and published
as GitHub Release assets. Now this is the runtimes asset: the other
platforms' standalone runtimes, built from this repository, with OXT Lite
1.15's Android runtime (see [The runtimes asset](#the-runtimes-asset)). (The
xTalk Suite extensions are not assets: see
[below](#xtalk-suite-extensions-xtalk_extensionspy).)

```json
{
  "assets": [
    {
      "id": "oxt-runtimes-0.2.1-rc.6",
      "url": "https://github.com/SethMorrowSoftware/OpenXTalk-Beyond/releases/download/runtimes-0.2.1-rc.6/oxt-runtimes-0.2.1-rc.6.zip",
      "sha256": "<64 lowercase hex digits>",
      "size": 123456789,
      "kind": "zip",
      "strip": 1,
      "dest": "",
      "rename": { "PROVENANCE.md": "PROVENANCE-oxt-runtimes-0.2.1-rc.6.md" },
      "exclude": { "win-x86_64": ["Runtime/Windows/x86-64/**", "..."], "...": ["..."] },
      "description": "...", "licence": "...", "source": "..."
    }
  ]
}
```

* `url` must be HTTPS; `size` and `sha256` pin the archive.
* `kind` is `zip` (the only kind so far). `strip` removes that many leading
  folders from every member; the rest is placed under `dest` (relative to the
  installed root; `""` is the root). `rename` (optional) maps a member path,
  after `strip`, to another path under `dest`. Directory entries become
  folders, so empty folders are kept. Absolute paths, `..`, drive letters and
  names Windows cannot store are rejected; two members may not map to the
  same path, and a path claimed both by an asset and by the IDE or the build
  is an error.
* `platforms` (optional) lists the `package.py` platforms the asset is for
  (names or fnmatch patterns such as `mac-*`); without it every platform
  gets it.
* `exclude` (optional) maps platform patterns to globs of installed paths
  (after `strip`, `rename` and `dest`; `*` within one path component, `**`
  anything, as in `layout.py`): on a matching platform those members, and
  folder entries inside such a tree, are left out. This is how an asset
  stops supplying files that the platform's own build now produces,
  instead of `package.py`'s "comes from both" error; `exclude_note` says
  why. A key that matches no platform is an error, and a glob that matches
  nothing on the platform is reported (a stale glob excludes nothing).
* `description`, `licence` and `source` are for people.

The runtimes asset excludes, per platform, what that platform's own build
makes (`make_runtimes_asset.py` writes these; `OWN_PARTS` there):

| platform | left out of `oxt-runtimes-0.2.1-rc.6` | why |
|---|---|---|
| `win-x86_64` | `Runtime/Windows/x86-64/**`, timezone `code/x86_64-win32/**` | the build's own runtime and timezone code |
| `linux-x86_64` | `Runtime/Linux/x86-64/**`, timezone `code/x86_64-linux/**`, timezone `resources/**` | the build's own runtime and timezone code, and the zoneinfo data (tz.gyp `tzdata`, which the Windows builds do not make) |
| `linux-arm64` | timezone `resources/**` | zoneinfo from the build (the asset has no arm64 runtime) |
| `mac-*` | timezone `resources/**` | zoneinfo from the build; the asset has no macOS or iOS code, so 1.15's `code/universal-mac-macosx10.9` no longer comes along |

Until 0.2.1-rc.3 the packages took `oxt-runtimes-1.15` (OpenXTalk Lite
1.15's runtimes, unchanged), whose `exclude` left out only what the Linux and
macOS builds made: the Windows package took all of it.

The cache folder is `--assets-cache`, else `OXT_ASSETS_CACHE`, else
`prebuilt/fetched-assets` (ignored by git). An archive whose file name (the
last part of the URL) is in the cache with the right size and SHA-256 is used
as it is, so an asset can be tested before it is published by pointing
`--assets-cache` at the folder that holds it. Otherwise it is downloaded over
HTTPS (redirects are followed, only to HTTPS URLs) to `<name>.part`, with up
to 4 attempts for network errors, HTTP 408/425/429/5xx and cut-off
transfers, and moved into place once size and SHA-256 match. A complete
download that does not match, and any other HTTP error, is fatal. `--offline`
never downloads.

```
python tools/oxt/fetch_assets.py [--manifest FILE] [--assets-cache DIR] [--id ID] [--offline] [--list]
```

fetches and verifies the assets without packaging (for example to fill a CI
cache).

## xTalk Suite extensions (`xtalk_extensions.py`)

```
python tools/oxt/xtalk_extensions.py [--manifest FILE] pin   [--member NAME] [--ref REF [--allow-off-branch]]
                                     [--cache DIR | --no-cache]
python tools/oxt/xtalk_extensions.py [--manifest FILE] fetch [--cache DIR] [--offline] [--platforms LIST] [--member NAME]
python tools/oxt/xtalk_extensions.py [--manifest FILE] build --bin DIR --out DIR [--vc-redist DIR]
                                     [--allow-unpinned-vc-runtime] [--cache DIR] [--offline]
                                     [--platforms LIST] [--summary-json FILE]
python tools/oxt/xtalk_extensions.py [--manifest FILE] pin-vc-runtime --vc-redist DIR
python tools/oxt/xtalk_extensions.py [--manifest FILE] export --out FILE.zip [--cache DIR] [--offline]
python tools/oxt/xtalk_extensions.py [--manifest FILE] list
```

`xtalk-extensions.json` pins the eight xTalk Suite member repositories
(`SethMorrowSoftware/<repository>`) at commits and lists, per member, the
files taken (with the SHA-256 and size of each Git blob, and the DLLs each
Windows library imports) and the extensions made from them (kind `lcb` or
`lcs`, id, folder, main file, the smoke test's probe and expected value,
and for script libraries the stack name, title, author and `requires`).
BUILDING.md describes the fields
([xTalk Suite extensions](../../BUILDING.md#xtalk-suite-extensions)).
Nothing of the members is kept in this repository.

* `pin` resolves the commit of each member (or `--member`; `--ref` a
  branch, tag or commit, default the head of the default branch) with
  `git ls-remote` or the GitHub API. A `--ref` commit must be on the
  member's default branch (one call of the GitHub compare API,
  `compare/<commit>...HEAD`, status `ahead` or `identical`): a SHA-1
  always, because `raw.githubusercontent.com` serves every commit of the
  repository's fork network, including forks and unmerged pull requests;
  a branch or tag named with `--ref` unless `--allow-off-branch`, because
  its commit disappears when the branch is deleted. It downloads every
  listed file from
  `https://raw.githubusercontent.com/<repository>/<commit>/<path>` and
  rewrites the manifest (commit, version from `version_from`, `sha256`,
  `size`, `imports`) without changing its order or layout. It checks the
  libraries against the member's `src/code/MANIFEST.sha256` (and that
  file lists no library the manifest does not take), that each LCB source
  still declares the extension's module id and that each script library's
  `script` line, if any, names its stack. It also stores the downloads in
  the cache. Files with a `repository` and `commit` of their own
  (OpenSSL's licence text) keep their commit.
* `fetch` puts every pinned file into the cache
  (`<cache>/<repository>/<commit>/<path>`; `--cache`, else
  `OXT_XTALK_CACHE`, else the `xtalk` folder of the asset cache
  (`OXT_ASSETS_CACHE`, default `prebuilt/fetched-assets/xtalk`), the
  same folder `package.py` uses) and verifies
  size and SHA-256; a file that does not match is deleted. Downloads go
  through `fetch_assets.fetch` (HTTPS only, retries with backoff).
  `--platforms` limits the native libraries to some platform ids.
* `build` fetches, then writes one folder per extension into `--out` and
  `XTALK-EXTENSIONS.txt` (the extensions with their commits, and the
  Visual C++ runtime DLLs bundled or missing). An extension that gets
  runtime DLLs also gets `licenses/Microsoft-Visual-C++-Runtime.txt`
  (CRLF): the DLLs with their file versions, Microsoft's copyright, the
  licence they are under (the Distributable Code terms of Visual Studio
  2022 for a 14.3x/14.4x runtime, `VC_RUNTIME_LICENCES`) with links, a
  summary of what those terms ask of anyone who redistributes the DLLs,
  and the alternative of requiring the Visual C++ Redistributable:

  | kind | folder | contents |
  |---|---|---|
  | `lcb` | the module id | `<name>.lcb`, `module.lcm` and `manifest.xml` from `lc-compile --modulepath <bin>/modules/lci --interface <temp>/<id>.lci --manifest manifest.xml --output module.lcm <name>.lcb` (run in the folder, no `-Werror`), `code/<platform-id>/<library>` for every platform id, `licenses/` |
  | `lcs` | the stack name | `<stack>.livecodescript` (the pinned file with `script "<stack>"` added as line 1 if it has no such line, and an `extensionInitialize` / `extensionFinalize` pair appended), `manifest.xml` written from the JSON, `licenses/` |

  With `--vc-redist` (Visual Studio's `VC\Redist\MSVC\<version>`), every
  DLL a library in `code/x86_64-win32` or `code/x86-win32` imports that
  is neither a Windows system DLL nor in the folder is copied from the
  redistributable into the folder. Each copy must match the SHA-256 and
  size that the manifest's `vc_runtime` pins for its platform (otherwise
  the build stops, naming the file, where it came from, both hashes and
  the pinned file version; `--allow-unpinned-vc-runtime` only warns, and
  the stamp and the summary's `vc_runtime_pinned` say so), and the copies
  must export what the library imports from them (which catches a
  redistributable that lacks a function, not every older one). The build warns when a copy's file
  version (from its `VS_VERSION_INFO`) is older than the MSVC linker
  version of the library that imports it, and `XTALK-EXTENSIONS.txt`
  records each copy's SHA-256 and file version. Without `--vc-redist`
  the build warns and lists the libraries concerned. The build fails if `modules/lci` changed while it
  ran, if a library's imports differ from the manifest's, or if
  `lc-compile` fails. Folders listed in an earlier
  `XTALK-EXTENSIONS.txt` of `--out` and the folders of the current
  manifest are replaced; nothing else in `--out` is touched. The output is
  the same for the same pins (including `vc_runtime`) and `lc-compile`.
* `pin-vc-runtime --vc-redist DIR` rewrites `vc_runtime` from a
  redistributable folder: for every Windows platform id of the manifest,
  the SHA-256 and size of each DLL that the libraries' recorded `imports`
  need from it (and that those DLLs import in turn), the folder's name
  (`redist`) and the DLLs' file version (`file_version`; the DLLs must
  agree on it). The block's `comment` is kept. `package-windows.ps1`
  prefers, among `VCToolsRedistDir` and the Visual Studio installs that
  `vswhere` finds, the folder that has the pinned DLLs.
* `export` fetches, then writes every pinned file (all members and
  platforms) into a zip in the cache layout `<repository>/<commit>/<path>`,
  with a copy of the manifest (LF line endings) and a `README.txt`.
  Entries are sorted and dated 1980-01-01, so the same pins give the same
  zip. Releases publish it (`OXT-Beyond-<ver>-xtalk-sources.zip`, written
  once, by the Windows job's `package-windows.ps1 -XtalkSourcesZip`), so that a release can be rebuilt with the extracted
  folder as the cache (`--cache DIR --offline`) if a member repository
  loses a pinned commit.
* `list` prints the members, their extensions and probes, and the
  libraries that need DLLs other than Windows system DLLs.

Exit status 0 on success, 1 on any error.

## The runtimes asset

The runtimes asset holds the standalone runtimes that each package
installs for the platforms other than its own. `package.py` adds its
files at their installed paths, without the parts that the package's own
build makes (the asset's `exclude` in `external-assets.json`).

### From this repository's builds (`--builds`)

```
python tools/oxt/make_runtimes_asset.py --builds --label LABEL --out DIR
    --bin win-x86=PATH --bin win-x86_64=PATH --bin linux-x86=PATH --bin linux-x86_64=PATH
    [--carry oxt-runtimes-1.15.zip] [--assets-cache DIR] [--commit SHA]
    [--run BUILDS=URL]... [--update-manifest [FILE]]
```

Each `PATH` is a build output folder or the CI archive that holds it
(`OXT-Beyond-win-x86-bin.zip`, the binaries zip of the `OXT-Beyond-win-x86_64`
artifact, `OXT-Beyond-linux-<arch>-bin.tar.xz`). From each build the tool
takes what `package.py` installs as that platform's own runtime, with
`package.py`'s tables (`plan_runtimes`): `Runtime/Windows/x86-32`,
`Runtime/Windows/x86-64`, `Runtime/Linux/x86-32` and `Runtime/Linux/x86-64`
(engine, `Support`, externals, database drivers and their lists, and on
Windows and Linux x86-64 the CEF files), and the timezone library's code
for that platform; the zoneinfo data comes from the Linux x86-64 build
(the Windows builds do not make it). `Runtime/Android` and the timezone
library's Android code are not built here: they are carried over unchanged
from `oxt-runtimes-1.15` (downloaded into the assets cache and checked
against its SHA-256, which the tool records), and that asset's
`PROVENANCE.md` goes into the new archive as
`PROVENANCE-oxt-runtimes-1.15.md`. Every `.exe`, `.dll`, `.so` and engine
must be a PE or ELF file of its build's architecture, and every
`Standalone` must carry this source tree's engine version, or no archive
is written. The generated `PROVENANCE.md` names the commit and the CI
runs (`--commit`, `--run`), the archives the builds came from with their
SHA-256, each engine's version strings, the licences, and every file with
its size, SHA-256, date and build. Besides the zip, the `--out` folder gets
`PROVENANCE.md` and `oxt-runtimes-<label>.manifest.json`, the manifest
entry: its `exclude` leaves out of each package what its build makes
(`win-x86_64`: its runtime and timezone code; `linux-x86_64`: the same
and the zoneinfo data; `linux-arm64` and `mac-*`: the zoneinfo data).
`--update-manifest` puts that entry in place of the `oxt-runtimes-*`
entries of `external-assets.json`.

[`.github/workflows/runtimes.yml`](../../.github/workflows/runtimes.yml)
("Runtimes asset", started by hand) does all of this from the artifacts
of a "Build (Windows)" and a "Build (Linux)" run of the same commit,
whose 32-bit and 64-bit builds must have passed; it uploads the result
as the artifact `runtimes-asset` and, with `publish`, creates the
pre-release `runtimes-<label>` at that commit (which must be on `main`)
with the zip and its `PROVENANCE.md`. A published asset is never
replaced: a new one gets a new label. The packages take it once a pull
request pins its entry in `external-assets.json`.

### From an installed OpenXTalk Lite (`oxt-runtimes-1.15`)

```
python tools/oxt/make_runtimes_asset.py "<OXT Lite 1.15 install>" --out DIR
    [--stock-setup "<LiveCode Community 9.6.3 install>\.setup.exe"]
    [--source-archive openxtalk-lite-1.15-win-noinstaller.7z] [--update-manifest]
```

builds `oxt-runtimes-1.15.zip` from the 1.15 install: every class `external`
file under `Runtime/Windows/x86-32/`, `Runtime/Linux/`, `Runtime/Android/`
and `Extensions/com.livecode.library.timezone/{code,resources}/` (not `Ext/`),
unchanged, under one top folder `oxt-runtimes-1.15/`, with the empty folders
of those trees and a generated `PROVENANCE.md`. PROVENANCE.md lists every
file with its size, SHA-256 and date and, with `--stock-setup`, whether it is
byte-identical to the file that the stock LiveCode Community 9.6.3 Windows
installer installs at the same path (read from the package payload of an
installed copy's `.setup.exe`, which is only read). It also gives the engine
version strings found in each `Standalone` and where the corresponding source
is. The zip is reproducible (sorted entries; the files' dates, in UTC).
`--update-manifest` writes its size and SHA-256 into `external-assets.json`.

| folder | files | same as stock 9.6.3 | notes |
|---|---:|---:|---|
| `Runtime/Windows/x86-32` | 86 | 86 | engine `9.6.3` |
| `Runtime/Linux/x86-32` | 104 | 10 | `Standalone` (`9.6.3-rc-3`, dated 2026-05-31) and the two `.txt` lists differ; `lib/` has 91 shared library files of other projects |
| `Runtime/Linux/x86-64` | 45 | 0 | `Standalone` reports `9.7.1-OXT` (dated 2026-05-31); both `Support` libraries differ; `lib/` has 42 shared library files of other projects; no externals |
| `Runtime/Android` | 38 | 31 | the four `Standalone` engines (`9.6.3-rc-3`), `Classes`, `Manifest.xml` and arm64 `DbMysql` differ |
| timezone `code/` (all but x86_64-win32) | 23 | 17 | the macOS and iOS simulator `tz.dylib` differ |
| timezone `resources/zoneinfo` | 474 | 474 | |

770 files, 449,955,398 bytes; the zip is 199,237,317 bytes. In the packaged
program its provenance file is `PROVENANCE-oxt-runtimes-1.15.md`.

Publishing (maintainer, after review): create the release `runtimes-1.15`
as a pre-release and upload `oxt-runtimes-1.15.zip` and its
`PROVENANCE.md` to it, with the same command as in
[BUILDING.md](../../BUILDING.md#external-assets):

```
gh release create runtimes-1.15 oxt-runtimes-1.15.zip PROVENANCE.md --prerelease \
    --title "Standalone runtimes from OpenXTalk Lite 1.15" \
    --notes "Prebuilt files used by tools/oxt/package.py. See PROVENANCE.md (also inside the zip)."
```

A pre-release never becomes the repository's latest release: the IDE's
update check reads `releases/latest`, which has to stay an OXT-Beyond
release. The `runtimes-1.15` release was published on 29 September 2026.
A new asset has to be published the same way before packaging can
download it; until then, packaging fails at the download (HTTP 404)
unless the assets are left out (`--no-external-assets`,
`package-windows.ps1 -NoExternalAssets`, or in CI the workflow input
`no_external_assets` or the repository variable `OXT_NO_EXTERNAL_ASSETS=1`)
or a cache that holds the zip is given.

## Binary IDE stacks

Most of the IDE is script-only stacks (`*.livecodescript`), which are
edited as text. Some code lives in binary stacks (`*.livecode`, `*.rev`),
for example the dataView behaviour in `ide/Toolset/palettes/revCore.8.livecode`.
Those are changed only through the script, property and image patches in
`ide-stack-patches/`, applied by a headless engine, so that every change is
reviewable text and the result is verified:

```
tools/oxt/ide-stack-patch.sh <development engine> [--check]
```

`<development engine>` is `LiveCode-Community` of a Linux or macOS build
(for example from the `linux-x86_64-bin` artifact of a CI run). Do not use a
Windows engine on a PC where OXT-Beyond or LiveCode is running: a second
Windows engine hands its command line to the running one instead of running
it. `--check` only reports.

A script patch file names a stack file (relative to `ide/`), an object of
it and the exact script text to replace and its replacement, each line
between bars (`|...|`) so that blanks survive editors. The text must occur
exactly once; a patch whose replacement is already there is skipped, so the
tool can run again at any time.

A property patch file names a stack file and, for one or more objects, a
property with the value the file has and the new one, for example a colour
saved into a dialog that the dark appearance cannot change:

```
file: Toolset/palettes/revsearch.rev
object: button "Search In" of card 1
property: foregroundColor
from: black
to:
```

Values are written as the dump writes them and compared exactly; a property
that already has the new value is skipped.

An image patch file names a stack file and, for one or more images, a PNG
file of the repository (relative to its root) and the SHA-1 of the image's
data in the stack file, as the dump shows it; the PNG must have the image's
size. The 0.2.1 icon in the About window and the dialogs is one:

```
file: Toolset/palettes/revgeneralicons.rev
object: image id 210112 of card "theAlertIcons"
image: Installer/oxt-beyond/branding/png/oxt-beyond-64.png
from-sha1: 0f68ea29bec282ccb97aa734914244242d1561c6
```

For each stack file the tool dumps every object
(`ide-stack-dump.livecodescript`), applies the patches, checks that only the
patched scripts and properties differ, saves the stack, loads the saved file
again and checks that it dumps exactly like the patched stack and keeps its
file format (for example `REVO7000`); otherwise it writes the original back.

The bytes of a stack saved by the engine differ from the original also where
no content is: the character-set byte (the platform that saved it), the
font-table names of inherited fonts, and the order of custom properties. To
compare two versions of a stack by content, dump both and diff the dumps:

```
OXT_DUMP_FILE=<stack file> OXT_DUMP_OUT=<text file> \
    <development engine> -ui tools/oxt/ide-stack-dump.livecodescript
```

`tools/ci/check_ide_stacks.py` (run in CI before the build) checks that
every script and image patch is applied, from the stack files' bytes,
without an engine. Property patches cannot be seen in the bytes that way, so
`tools/ci/ide-contrast-check.ps1` checks every patch with the engine of the
build (`OXT_PATCH_CHECK=1`, on the staged installed layout).
