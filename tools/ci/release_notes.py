#!/usr/bin/env python3
# Copyright (C) 2026 OXT-Beyond contributors.
#
# This file is part of OXT-Beyond.
#
# OXT-Beyond is free software; you can redistribute it and/or modify it under
# the terms of the GNU General Public License v3 as published by the Free
# Software Foundation.
#
# OXT-Beyond is distributed in the hope that it will be useful, but WITHOUT ANY
# WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE.  See the GNU General Public License for more
# details.
#
# You should have received a copy of the GNU General Public License
# along with OXT-Beyond.  If not see <http://www.gnu.org/licenses/>.

"""Write the release notes of a LegacyOXT release (LiveCode Community built
from LiveCode's source in this repository), for Windows, macOS and Linux
(Markdown, for gh release create --notes-file).

  python3 tools/ci/release_notes.py --version V [--tag TAG] [--commit SHA]
      (--dir DIR | --files NAME... | --files-from FILE)
      [--out FILE] [--summary]

The files are the release's (tools/ci/release_assets.py: --dir is the
folder that its "assemble" wrote; --files and --files-from, one name per
line, give the names alone, to try the notes without the files). They
must be exactly the files release_assets.py expects for --version, so the
notes never name a file that the release lacks, nor leave one out.

The notes say what the release is (LiveCode Community's source, built and
tested here), which file to download for each platform and what it needs,
how to check a download, and where the parts come from. GitHub's
generated list of changes since the previous release follows when
release.yml creates the release. --summary appends the notes to the
GitHub Actions job summary. Adapted from OpenXTalk-Lite-1.15's.

Only the Python 3 standard library is used.
"""

import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(HERE)), 'tools', 'oxt'))
import release_assets  # noqa: E402
import package  # noqa: E402

# The minimum systems the builds check (build-linux.yml: glibc 2.31 by
# check_elf_floor.py; build-macos.yml: 10.13 and 11.0 by
# check_macho_floor.py)
WINDOWS_MIN = 'Windows 10 or later (x64)'
MAC_MIN = 'macOS 11 or later, or 10.13 or later on Intel'
LINUX_MIN = 'Linux x86-64 (glibc 2.31 or later)'

INTRO = '''\
LiveCode Community {version} (LiveCode's last development build, "{title}") for Windows, macOS and Linux, built from LiveCode's source by LegacyOXT{tag_text}.

This is LiveCode Community as LiveCode Ltd left it when its open source edition ended: the engine and IDE of livecode/livecode `develop` (commit `4606a10ea`, 2021-07-26), compiled, packaged and tested by this repository's CI. Nothing in it was fixed or improved: it differs from LiveCode's source only where today's compilers and systems need it to build; see "About this release" below. LegacyOXT is not affiliated with or endorsed by LiveCode Ltd.

Download from the release page, under Assets:
- {windows_min}: {root}-win-x86_64-setup.exe
- {mac_min}: {root}-mac-universal.dmg
- {linux_min}: {root}-linux-x86_64.tar.xz
'''

BODY = '''\
## Windows (x64)

Needs 64-bit Windows 10 or later.

- `{root}-win-x86_64-setup.exe`: the installer. It installs for all users or only for you, as LiveCode's installer did: the folder "{title}" (under `LegacyOXT`), the program `{exe}`, a Start menu shortcut and a desktop shortcut, and no file types.
- `{root}-win-x86_64-portable.zip`: the same program folder without an installer. Extract it and run `{exe}`.
- `{root}-win-x86_64-binaries.zip`: the engine, externals and tools as built (`win-x86_64-bin`), without debug symbols, plus the licence.
- `{root}-win-x86_64-symbols.zip`: debug symbols (`*.pdb`) for the binaries.

The executables are not code-signed, so Windows SmartScreen may warn when they are run for the first time.

## macOS (Apple Silicon and Intel)

One universal app, `{app}`, LiveCode's app as built (its name, bundle identifier, icons and Info.plist), for Apple Silicon and Intel (LiveCode built it for Intel only). It needs macOS 11 Big Sur or later on Apple Silicon, or macOS 10.13 High Sierra or later on an Intel Mac.

- `{root}-mac-universal.dmg`: the disk image. Open it and drag the app onto the Applications folder next to it.
- `{root}-mac-universal.zip`: the same app, for scripted installs (`ditto -x -k {root}-mac-universal.zip /Applications`).
- `{root}-mac-universal-binaries.tar.xz`: the build output (`Release/`, the Apple Silicon and Intel builds joined with lipo), signed ad hoc, without debug symbols and the build's own tools, plus the licence.
- `{root}-mac-universal-symbols.zip`: debug symbols (`.dSYM` bundles) for the binaries.

**Opening it for the first time.** The app is signed ad hoc: it is not signed with an Apple Developer ID and not notarized by Apple, so macOS does not open a downloaded copy until you allow it. You do this once. On macOS 15 Sequoia and later:

1. Double-click the app. macOS says that it was not opened; click **Done** (not *Move to Trash*).
2. Open **System Settings > Privacy & Security** and scroll down to *Security*. Next to the message that the app was blocked to protect your Mac, click **Open Anyway**.
3. Confirm with **Open Anyway** and your password (or Touch ID).

On macOS 13 and 14, Control-click the app in Finder, choose *Open* and then *Open* again; on macOS 12 and earlier, the button is in *System Preferences > Security & Privacy > General*. Or, in Terminal, remove the quarantine flag that the browser set on the download:

```sh
xattr -dr com.apple.quarantine "/Applications/{app}"
```

The app keeps LiveCode's bundle identifier (`com.runrev.livecode`), so it shares its preferences with any LiveCode installed on the same Mac.

## Linux (x86-64)

Needs 64-bit x86 Linux with glibc 2.31 or later (Ubuntu 20.04, Debian 11, Fedora 32 or later) and an X11 desktop (on Wayland it runs through XWayland), with GTK 2: on Debian and Ubuntu the package `libgtk2.0-0` (`libgtk2.0-0t64` on Ubuntu 24.04 and Debian 13), on Fedora `gtk2`. The browser widget and revBrowser also need NSS, ALSA and a few more X11 libraries.

- `{root}-linux-x86_64.tar.xz`: the program folder `{root}`. Extract it onto a Linux file system (not FAT, exFAT or a Windows drive) and run `./{linux_engine}` in it (in quotes: the name has a space), or run `./install.sh` to install it for yourself where LiveCode's installer did (`~/.runrev/components/{linux_folder}`), with LiveCode's menu entry and icon, without administrator rights; `uninstall.sh` in the installed folder removes it.
- `{root}-linux-x86_64-binaries.tar.xz`: the engine, externals and tools as built (`linux-x86_64-bin`), without debug symbols and the build's own tools, plus the licence.
- `{root}-linux-x86_64-symbols.tar.xz`: debug symbols (`.dbg` files) for the binaries.

## All platforms

- `SHA256SUMS`: SHA-256 checksums of all the files above.

**Checking a download.** Compare its SHA-256 with the line for it in `SHA256SUMS`:

- Windows, in Command Prompt: `certutil -hashfile {root}-win-x86_64-setup.exe SHA256`
- macOS, in Terminal: `shasum -a 256 {root}-mac-universal.dmg`
- Linux, in the folder with the download and `SHA256SUMS`: `sha256sum -c SHA256SUMS --ignore-missing`

## About this release

Made by the "Release" workflow (`.github/workflows/release.yml`){commit}, which builds and tests all three platforms and publishes the release only when every package has passed.

- **The code** is LiveCode Community 9.7.0-dp-1: livecode/livecode `develop` at `4606a10ea` with its `ide` and `thirdparty` submodules (the tag `livecode-9.7.0-dp-1`). The commits after it change only what is needed to build it on current compilers and systems (Visual Studio 2022, current Xcode, Apple Silicon, current Linux) and add the CI, tests and packaging. `CHANGES-FROM-LIVECODE.md` lists every difference from LiveCode's source and why, and the "Pristine guard" workflow checks that the list is complete.
- **The packages** follow LiveCode's installers (`Installer/package.txt`): the same layout and program names, LiveCode's mergExt `Ext` folder (byte for byte as LiveCode 9.6.3's installer has it), the repository's guides, and the Dictionary data written by LiveCode's own docs builder. Every package has the standalone runtimes for Windows x86-64, Linux x86 and x86-64 and macOS; LiveCode's 32-bit Windows and Android runtimes are not built. LiveCode's release notes and user guide PDFs are not made.
- **The tests** are LiveCode's engine test suites; they fail in places, and `tools/ci/*-baseline*.txt` records each failure of LiveCode's code, which is kept as it is.
- **The prebuilt libraries** are LiveCode's own on Windows (OpenSSL 1.1.1g, curl 7.51.0, ICU 58.2, CEF 74), and built from source with the same versions on macOS and Linux (OpenSSL 1.1.1w there: 1.1.1g cannot be linked on arm64).

LiveCode Community is by LiveCode Ltd and its contributors, under the GNU GPL version 3 (see `License Agreement.txt` in each package). "LiveCode" is a trademark of LiveCode Ltd, used here only to say what the software is.
'''


class NotesError(Exception):
    pass


def fill(template, version, commit, tag):
    return template.format(
        version=version,
        root=release_assets.package_root(version),
        title=package.LIVECODE_NAME + ' ' + package.readable_version(version),
        app=package.LIVECODE_NAME + ' ' + package.readable_version(version) + '.app',
        exe=package.LIVECODE_NAME + '.exe',
        linux_engine=package.LIVECODE_NAME + '.x86_64',
        linux_folder='livecodecommunity-%s.x86_64' % version,
        tag_text=' (release %s)' % tag if tag else '',
        windows_min=WINDOWS_MIN, mac_min=MAC_MIN, linux_min=LINUX_MIN,
        commit=' from commit %s' % commit if commit else '')


def check_files(version, names):
    want = release_assets.release_files(version)
    got = [n for n in names if n]
    problems = []
    for name in want:
        if name not in got:
            problems.append('%s is not among the files' % name)
    for name in got:
        if name not in want:
            problems.append('%s is not a file of the release' % name)
    if len(set(got)) != len(got):
        problems.append('a file name is given twice')
    if problems:
        raise NotesError('the files are not the release\'s (tools/ci/release_assets.py): ' + '; '.join(problems))


def notes(version, names, commit=None, tag=None):
    if not release_assets.VERSION_RE.match(version):
        raise NotesError('%r is not a version such as 9.7.0-dp-1' % version)
    check_files(version, names)
    body = fill(BODY, version, commit, tag)
    for name in release_assets.release_files(version):
        # Every file is described (the table and the text must agree)
        if '`%s`' % name not in body:
            raise NotesError('the notes do not describe %s' % name)
    return fill(INTRO, version, commit, tag) + '\n' + body


def read_names(args):
    if args.dir:
        return sorted(e for e in os.listdir(args.dir) if os.path.isfile(os.path.join(args.dir, e)))
    if args.files_from:
        with open(args.files_from, encoding='utf-8') as f:
            return [line.strip() for line in f if line.strip()]
    return list(args.files)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--version', required=True, help='product version (BUILD_SHORT_VERSION)')
    ap.add_argument('--tag', help='the release tag (v<version>-r<n>)')
    ap.add_argument('--commit', help='the commit the release is made from')
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument('--dir', help='the release folder (release_assets.py assemble)')
    src.add_argument('--files', nargs='+', metavar='NAME', help='the release file names')
    src.add_argument('--files-from', metavar='FILE', help='a file with one release file name per line')
    ap.add_argument('--out', help='write the notes here (default: standard output)')
    ap.add_argument('--summary', action='store_true', help='also write to the GitHub Actions job summary')
    args = ap.parse_args(argv)

    try:
        text = notes(args.version, read_names(args), args.commit, args.tag)
    except (NotesError, OSError) as e:
        if os.environ.get('GITHUB_ACTIONS') == 'true':
            print('::error title=Release notes::%s' % e)
        else:
            sys.stderr.write('error: %s\n' % e)
        return 1

    if args.out:
        with open(args.out, 'w', encoding='utf-8', newline='\n') as f:
            f.write(text)
    else:
        sys.stdout.write(text)

    if args.summary and os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a', encoding='utf-8', newline='\n') as f:
            f.write('### Release notes\n\nGitHub\'s generated list of changes follows them in the release.\n\n'
                    '<details><summary>Show</summary>\n\n%s\n</details>\n\n' % text)
    return 0


if __name__ == '__main__':
    sys.exit(main())
