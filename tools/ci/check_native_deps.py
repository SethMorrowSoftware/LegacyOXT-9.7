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

"""Check that the native libraries of the extensions in an installed
OpenXTalk-Lite layout find the libraries they need: the Linux (ELF) and macOS
(Mach-O) counterpart of check-extension-imports.ps1, which it also covers
for Windows (PE), so one tool checks every platform's code folders.

  python tools/ci/check_native_deps.py --root <tools folder>
      [--platform windows|linux|mac|all] [--max-glibc X.Y] [--max-macos X.Y]
      [--json FILE] [--quiet]

<tools folder> is the folder with Extensions (the layout root; on macOS
OpenXTalk-Lite.app/Contents/Tools). Every library in
Extensions/<extension>/code/<platform id>/ of the chosen platforms (default:
this machine's) is read, and each library it needs must be

  Windows  a Windows system DLL (tools/oxt/xtalk_extensions.py SYSTEM_DLLS
           and api-ms-win-crt-*), or a file in the same folder (the engine
           loads extension libraries with LOAD_WITH_ALTERED_SEARCH_PATH);
  Linux    a library of the base system (LINUX_SYSTEM: glibc, libstdc++,
           libgcc_s, zlib, OpenSSL), or a file in the same folder that the
           loader will look for there: the library must have $ORIGIN
           itself (not $ORIGIN/lib) in its DT_RUNPATH or DT_RPATH, since
           dlopen does not search the folder of the library that needs
           another;
  macOS    in /usr/lib or /System/Library, or @loader_path/<file> (or
           @rpath/<file> with an LC_RPATH that is @loader_path) with the
           file in the same folder. @executable_path is the engine's
           folder, not the extension's, and anything else (/usr/local,
           /opt/homebrew) is not on a user's Mac. A .bundle or .framework
           folder (the IDE maps folders too) is checked through its
           Mach-O, Contents/MacOS/<name> or <name>, and fails without one.

It also checks that each library is built for its folder: code/x86_64-*
must hold x86_64 code, x86-* x86 (i386), arm64-* arm64, and universal-*
(macOS) both arm64 and x86_64. The OS floor each library sets is reported
(the newest GLIBC_, GLIBCXX_ and OPENSSL_ symbol versions it needs; the
minimum macOS it was built for); --max-glibc and --max-macos make a library
that needs a newer one a failure.

Build machines and CI runners have many libraries a user's machine may
lack, so loading a library there proves little; this reads the files
instead (tools/oxt/binfmt.py and xtalk_extensions.pe_info, no readelf,
otool or dumpbin), and runs on any host.

Exit status 0 when every library passes, 1 otherwise, 2 when the layout
cannot be read. With --json the results are written as a list of objects
(path, extension, folder, format, archs, needs, missing, problems,
floors); tools/ci/run_livecode_check.py uses them for the smoke test.

Only the Python 3 standard library is used.
"""

import argparse
import json
import os
import posixpath
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
OXT = os.path.join(os.path.dirname(HERE), 'oxt')
sys.path.insert(0, OXT)
import binfmt  # noqa: E402
import xtalk_extensions  # noqa: E402

# Libraries every desktop Linux the Linux package supports has: glibc's
# own libraries and loader, the GCC runtime that C++ code needs, zlib and
# OpenSSL (whose version still sets a floor, which is reported).
LINUX_SYSTEM = (
    r'libc\.so\.6', r'libm\.so\.6', r'libdl\.so\.2', r'libpthread\.so\.0', r'librt\.so\.1',
    r'libresolv\.so\.2', r'libutil\.so\.1', r'libanl\.so\.1', r'libnsl\.so\.[12]',
    r'libcrypt\.so\.[12]', r'ld-linux[-a-z0-9_.]*\.so\.[12]', r'libstdc\+\+\.so\.6',
    r'libgcc_s\.so\.1', r'libatomic\.so\.1', r'libz\.so\.1', r'libssl\.so\.(3|1\.1)',
    r'libcrypto\.so\.(3|1\.1)',
)
LINUX_SYSTEM_RX = re.compile(r'^(%s)$' % '|'.join(LINUX_SYSTEM))
MAC_SYSTEM_PREFIXES = ('/usr/lib/', '/System/Library/')

# code folder name -> the architectures its libraries must hold
FOLDER_ARCHS = (
    (r'^x86_64-', ('x86_64',)),
    (r'^x86-', ('x86', 'i386')),
    (r'^(arm64|aarch64)-', ('arm64',)),
    (r'^universal-', ('arm64', 'x86_64')),
)
PE_MACHINES = {'x86_64': 0x8664, 'x86': 0x14c, 'arm64': 0xaa64}
FAMILIES = ('windows', 'linux', 'mac')


def family_of_folder(name):
    """windows, linux, mac or None for a code folder name (the IDE's
    filters: *-win32*, *-linux*, *-mac*)."""
    if '-win32' in name:
        return 'windows'
    if '-linux' in name:
        return 'linux'
    if '-mac' in name:
        return 'mac'
    return None


def host_family():
    if sys.platform.startswith('win'):
        return 'windows'
    if sys.platform == 'darwin':
        return 'mac'
    return 'linux'


def _folder_archs(folder):
    """The architectures a code folder's libraries must hold (x86 has two
    spellings: binfmt says x86 for ELF, i386 for Mach-O), or None."""
    for rx, archs in FOLDER_ARCHS:
        if re.match(rx, folder):
            return archs
    return None


def check_pe(path, folder, present):
    with open(path, 'rb') as f:
        info = xtalk_extensions.pe_info(f.read(), path)
    needs = list(info.imports)
    missing = [d for d in needs if not xtalk_extensions.is_system_dll(d) and d.lower() not in present]
    problems = []
    arch = {v: k for k, v in PE_MACHINES.items()}.get(info.machine, 'machine-0x%x' % info.machine)
    expected = _folder_archs(folder)
    if expected and arch not in expected:
        problems.append('PE machine %s, expected %s for %s' % (arch, ' or '.join(expected), folder))
    return [arch], needs, missing, problems, {}


def _is_origin(rpath):
    """True for a run path that is the library's own folder: $ORIGIN or
    ${ORIGIN}, alone or with / or /. after it. $ORIGIN/lib is a subfolder
    that the loader searches instead, so it does not find a file next to
    the library (the Mach-O check likewise takes only @loader_path)."""
    for token in ('$ORIGIN', '${ORIGIN}'):
        if rpath.startswith(token):
            rest = rpath[len(token):]
            return rest == '' or (rest.startswith('/') and posixpath.normpath('.' + rest) == '.')
    return False


def check_elf(path, folder, present):
    b = binfmt.parse(path)
    problems, missing = [], []
    origin = any(_is_origin(r) for r in b.rpaths)
    for lib in b.needed:
        if LINUX_SYSTEM_RX.match(lib):
            continue
        if lib in present:
            if not origin:
                missing.append(lib)
                problems.append('%s is next to it, but no run path is $ORIGIN itself, so the loader does not '
                                'look there' % lib)
            continue
        missing.append(lib)
    expected = _folder_archs(folder)
    if expected and not set(b.archs) & set(expected):
        problems.append('ELF %s, expected %s for %s' % (' '.join(b.archs), ' or '.join(expected), folder))
    return b.archs, b.needed, missing, problems, b.floors


def _check_macho_slice(sl, present, missing, problems, tag):
    """Check one slice's libraries against that slice's own LC_RPATHs
    (dyld never uses another slice's), adding to missing and problems;
    tag names the slice in messages of a universal file."""
    def miss(lib, why=None):
        if lib not in missing:
            missing.append(lib)
        if why and tag + why not in problems:
            problems.append(tag + why)

    for lib in sl.needed:
        if lib.startswith(MAC_SYSTEM_PREFIXES):
            continue
        name = lib.rsplit('/', 1)[-1]
        if lib.startswith('@loader_path/'):
            rel = posixpath.normpath(lib[len('@loader_path/'):])
            if '/' not in rel and rel in present:
                continue
            miss(lib)
        elif lib.startswith('@rpath/'):
            rel = lib[len('@rpath/'):]
            ok = any(r.rstrip('/') in ('@loader_path', '@loader_path/.') and '/' not in rel and rel in present
                     for r in sl.rpaths)
            if not ok:
                miss(lib, '%s: no LC_RPATH of @loader_path finds it next to the library' % lib)
        elif lib.startswith('@executable_path/'):
            miss(lib, '%s is relative to the engine, not to the extension' % lib)
        else:
            miss(lib, ('%s: the copy next to it is not used (install name %s; use @loader_path/%s)'
                       % (name, lib, name)) if name in present else None)


def check_macho(path, folder, present):
    b = binfmt.parse(path)
    problems, missing = [], []
    for sl in b.slices:
        tag = '[%s] ' % ' '.join(sl.archs) if len(b.slices) > 1 else ''
        _check_macho_slice(sl, present, missing, problems, tag)
    expected = _folder_archs(folder)
    if expected:
        lacking = [a for a in expected if a not in b.archs]
        if folder.startswith('universal-') and lacking:
            problems.append('%s holds %s, not arm64 and x86_64' % (folder, ' '.join(b.archs)))
        elif not folder.startswith('universal-') and not set(b.archs) & set(expected):
            problems.append('Mach-O %s, expected %s for %s' % (' '.join(b.archs), ' or '.join(expected), folder))
    return b.archs, b.needed, missing, problems, b.floors


CHECKERS = {'windows': check_pe, 'linux': check_elf, 'mac': check_macho}
SUFFIXES = {'windows': ('.dll',), 'linux': ('.so',), 'mac': ('.dylib', '.so', '.bundle')}


def _is_library(family, path, name):
    if family == 'linux':
        return name.endswith('.so') or '.so.' in name
    if family == 'mac' and not name.endswith(SUFFIXES['mac']):
        # a Mach-O file without a suffix (LiveCode maps any file name)
        return binfmt.macho_archs(path) is not None
    return name.lower().endswith(SUFFIXES[family])


def _bundle_binary(path):
    """The Mach-O of a .bundle or .framework folder, named like the folder
    (as Xcode names it): Contents/MacOS/<name>, or <name> (a framework's
    link into Versions/Current); None when there is none."""
    base = os.path.splitext(os.path.basename(path))[0]
    for parts in (('Contents', 'MacOS', base), (base,), ('Versions', 'Current', base)):
        p = os.path.join(path, *parts)
        if os.path.isfile(p):
            return p
    return None


def check_layout(root, families, max_glibc=None, max_macos=None):
    """[result dict] for every library of the families in root/Extensions."""
    results = []
    ext_dir = os.path.join(root, 'Extensions')
    if not os.path.isdir(ext_dir):
        return results
    for ext in sorted(os.listdir(ext_dir)):
        code = os.path.join(ext_dir, ext, 'code')
        if not os.path.isdir(code):
            continue
        for folder in sorted(os.listdir(code)):
            fam = family_of_folder(folder)
            fdir = os.path.join(code, folder)
            if fam not in families or not os.path.isdir(fdir):
                continue
            names = sorted(n for n in os.listdir(fdir) if not n.startswith('.'))
            present = {n.lower() for n in names} if fam == 'windows' else set(names)
            for name in names:
                path = os.path.join(fdir, name)
                here = present
                if os.path.isdir(path):
                    # The IDE maps folders as well as files, and the Mac
                    # engine loads a .bundle or .framework folder: check the
                    # Mach-O in it, whose @loader_path is its own folder, and
                    # fail one without it rather than pass it unread
                    if fam != 'mac' or not name.endswith(('.bundle', '.framework')):
                        continue
                    inner = _bundle_binary(path)
                    if inner:
                        path = inner
                        here = set(n for n in os.listdir(os.path.dirname(inner)) if not n.startswith('.'))
                elif not _is_library(fam, path, name):
                    continue
                rel = os.path.relpath(path, root).replace(os.sep, '/')
                r = dict(path=rel, extension=ext, folder=folder, format={'windows': 'pe', 'linux': 'elf',
                                                                         'mac': 'macho'}[fam],
                         archs=[], needs=[], missing=[], problems=[], floors={})
                if os.path.isdir(path):
                    r['problems'].append('no Mach-O at Contents/MacOS/<name> or <name>, so it is not checked')
                    results.append(r)
                    continue
                try:
                    archs, needs, missing, problems, floors = CHECKERS[fam](path, folder, here)
                except (binfmt.FormatError, xtalk_extensions.XtalkError, OSError) as e:
                    r['problems'].append('cannot read it: %s' % e)
                    results.append(r)
                    continue
                r.update(archs=archs, needs=needs, missing=missing, problems=problems,
                         floors={k: binfmt.version_text(v) for k, v in floors.items()})
                if max_glibc and 'GLIBC' in floors and floors['GLIBC'] > max_glibc:
                    r['problems'].append('needs glibc %s, newer than %s' % (binfmt.version_text(floors['GLIBC']),
                                                                              binfmt.version_text(max_glibc)))
                if max_macos and 'macOS' in floors and floors['macOS'] > max_macos:
                    r['problems'].append('needs macOS %s, newer than %s' % (binfmt.version_text(floors['macOS']),
                                                                              binfmt.version_text(max_macos)))
                results.append(r)
    return results


def failed(r):
    return bool(r['missing'] or r['problems'])


def describe(r):
    """One line for a failed library: its problems, then the missing
    libraries that no problem already names."""
    plain = [m for m in r['missing'] if not any(m in p for p in r['problems'])]
    return '; '.join(r['problems'] + (['needs %s, which is neither a system library nor next to it'
                                       % ', '.join(plain)] if plain else []))


def _version(text):
    try:
        return tuple(int(x) for x in text.split('.'))
    except ValueError:
        raise argparse.ArgumentTypeError('%r is not a version such as 2.31' % text)


def main(argv=None):
    ap = argparse.ArgumentParser(description='Check the native dependencies of the extension libraries '
                                             'in an installed OpenXTalk-Lite layout.')
    ap.add_argument('--root', required=True, help='the tools folder (with Extensions)')
    ap.add_argument('--platform', choices=FAMILIES + ('all',),
                    help='code folders to check (default: this machine\'s platform)')
    ap.add_argument('--max-glibc', type=_version, metavar='X.Y', help='fail libraries that need a newer glibc')
    ap.add_argument('--max-macos', type=_version, metavar='X.Y', help='fail libraries built for a newer macOS')
    ap.add_argument('--json', metavar='FILE', help='write the results as JSON')
    ap.add_argument('--quiet', action='store_true', help='print only failures and the result')
    args = ap.parse_args(argv)
    if not os.path.isdir(args.root):
        sys.stderr.write('error: not a folder: %s\n' % args.root)
        return 2
    families = FAMILIES if args.platform == 'all' else (args.platform or host_family(),)
    results = check_layout(args.root, families, args.max_glibc, args.max_macos)
    if args.json:
        with open(args.json, 'w', encoding='utf-8', newline='\n') as f:
            json.dump(results, f, indent=1)
            f.write('\n')
    bad = [r for r in results if failed(r)]
    print('Layout: %s' % args.root)
    print('Libraries in Extensions/*/code/* (%s): %d' % (', '.join(families), len(results)))
    for r in results:
        floors = ', '.join('%s %s' % kv for kv in sorted(r['floors'].items()) if kv[0] in ('GLIBC', 'GLIBCXX',
                                                                                          'OPENSSL', 'macOS'))
        if failed(r):
            print('%s %s: %s' % ('::error::' if os.environ.get('GITHUB_ACTIONS') == 'true' else 'FAIL',
                                 r['path'], describe(r)))
        elif not args.quiet:
            print('OK   %s (%s)%s' % (r['path'], ' '.join(r['archs']), ('  [%s]' % floors) if floors else ''))
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a', encoding='utf-8', newline='\n') as f:
            f.write('### Extension library dependencies\n\n%s (`tools/ci/check_native_deps.py`, %s).\n\n'
                    % ('all %d libraries find what they need' % len(results) if not bad else
                       '%d of %d libraries miss something' % (len(bad), len(results)), ', '.join(families)))
    print('Native dependency check %s: %d of %d libraries fail'
          % ('FAILED' if bad else 'passed', len(bad), len(results)))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
