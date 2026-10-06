# tools/oxt: packaging

The tools that turn a build of LiveCode Community into LegacyOXT's packages,
adapted from OpenXTalk-Lite-1.15's (and OXT-Beyond's) for LiveCode's own
layout and names.

| tool | what it does |
|---|---|
| `package.py` | stages the installed layout of one platform from a build output, as `Installer/package.txt` installs it: the IDE (`layout.py`), the build's engines, externals, toolchain, runtimes and extensions, LiveCode's mergExt `Ext` bundle (`Installer/legacyoxt/ext`), and on Linux the install scripts with LiveCode's desktop entry |
| `layout.py` | which file of `ide/` and `ide-support/` goes where in the installed layout, and what LiveCode's packages leave out (`MANAGED_EXCLUDE`) |
| `package_dist.py` | writes the archives from a staged layout: the Linux tar.xz, the macOS zip and disk image, and the binaries and symbols archives |
| `fetch_assets.py`, `external-assets.json` | external archives every package installs (none yet) |
| `make_runtimes_asset.py` | makes a runtimes asset from builds of this repository, for the standalone runtimes of the other platforms |
| `binfmt.py`, `icns.py` | read PE, ELF and Mach-O files, and .icns icons |

The program keeps LiveCode's names: `LiveCode Community.exe` on Windows,
`LiveCode Community.<arch>` on Linux, `LiveCode Community <version>.app`
on macOS (with `<version>` as LiveCode's installer wrote it, such as
`9.7 (dp 1)`). The package's own folder and files are `LegacyOXT-<version>`,
where `<version>` is `BUILD_SHORT_VERSION` of the file `version`
(`9.7.0-dp-1`).

The Windows installer is `Installer/legacyoxt/legacyoxt.iss` (Inno Setup),
built and tested by `tools/ci/build-installer.ps1` and
`tools/ci/test-installer.ps1`.
