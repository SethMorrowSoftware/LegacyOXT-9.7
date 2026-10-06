# LegacyOXT 9.7: LiveCode Community 9.7.0-dp-1, built from source

This repository builds **LiveCode Community 9.7.0-dp-1**, the last build of
LiveCode Community's `develop` branch (livecode/livecode `4606a10ea`,
2021-07-26), on today's systems, with continuous integration around it:
builds for Windows, macOS and Linux and LiveCode's own test suites.
LiveCode's code is kept as LiveCode Ltd left it: nothing is fixed or
improved. Every difference from its source is listed, with its reason, in
**[CHANGES-FROM-LIVECODE.md](CHANGES-FROM-LIVECODE.md)**, and CI checks
that the list is complete.

LegacyOXT is not affiliated with or endorsed by LiveCode Ltd. "LiveCode" is
a trademark of LiveCode Ltd; it is used here only to say what the source
is. LiveCode Community is free software under the GNU General Public
License version 3 (see [`LICENSE`](LICENSE)). LiveCode's own README is at
`git show livecode-9.7.0-dp-1:README.md`.

It is the vanilla base that OpenXTalk Lite (Tom Perry's
[OpenXTalk-Lite-1.15](https://github.com/SethMorrowSoftware/OpenXTalk-Lite-1.15))
and [OXT-Beyond](https://github.com/SethMorrowSoftware/OpenXTalk-Beyond)
were built on, and so the reference they can be compared against.

## What is in it

```
LiveCode Community history, up to develop 4606a10ea (2021-07-26, 9.7.0-dp-1)
  Vendor thirdparty submodule into the repository
  Vendor the ide submodule into the repository        <- tag livecode-9.7.0-dp-1
  build fixes (CHANGES-FROM-LIVECODE.md)
  CI and tests
```

The tag `livecode-9.7.0-dp-1` is LiveCode's commit with its `ide` and
`thirdparty` submodules vendored at the commits it pins (livecode-ide
`ccc733a15`, livecode-thirdparty `e5e050573`), whose trees are byte for
byte those of LiveCode's archived repositories on GitHub. The program keeps
LiveCode's own names (`LiveCode-Community.exe`, `LiveCode-Community.app`).

The build fixes come from OXT-Beyond and OpenXTalk-Lite-1.15, without any
of OpenXTalk's own changes: Visual Studio 2022, current Xcode and macOS
SDKs, Apple Silicon (LiveCode built macOS for Intel only), current Linux,
and LiveCode's prebuilt libraries, which its server no longer serves.

## CI

| workflow | what it does |
|---|---|
| [Build (Windows)](.github/workflows/build-windows.yml) | x86-64 build with LiveCode's own prebuilt libraries; smoke test, IDE compile check and engine tests on the build |
| [Build (macOS)](.github/workflows/build-macos.yml) | arm64 and x86_64 builds; smoke test, IDE compile check and engine tests on each |
| [Build (Linux)](.github/workflows/build-linux.yml) | x86_64, arm64 and x86 builds; smoke test, IDE compile check and engine tests on each |
| [Pristine guard](.github/workflows/pristine-guard.yml) | every difference from LiveCode's source is in CHANGES-FROM-LIVECODE.md |

Every check compares its failures with a baseline
(`tools/ci/engine-tests-baseline*.txt`, `tools/ci/ide-compile-baseline*.txt`).
A failure not in the baseline fails the job. A baseline line records how
LiveCode's code behaves, often caught by one of OXT-Beyond's regression
tests for a bug it fixed; each has a comment saying which.

Packages (installers, disk images, archives) and releases are next.

## Libraries

- **Windows**: LiveCode's own x86-64 (MSVC v141) prebuilt libraries,
  OpenSSL 1.1.1g, curl 7.51.0, ICU 58.2, CEF 74.1.19, as `prebuilt/versions`
  pins them, from the mirror
  [`prebuilts-v1`](https://github.com/SethMorrowSoftware/OpenXTalk-Beyond/releases/tag/prebuilts-v1),
  each checked against `prebuilt/SHA256SUMS`.
- **Linux and macOS**: the same versions built from source in CI (OpenSSL
  1.1.1w instead of 1.1.1g, which cannot be linked on arm64), cached.

## Credits

- **LiveCode Ltd** and the LiveCode Community contributors wrote LiveCode
  Community.
- The build fixes and CI come from **OXT-Beyond** and its contributors,
  and some of the macOS portability fixes from **Tom Perry**'s OpenXTalk
  Lite macOS work.
