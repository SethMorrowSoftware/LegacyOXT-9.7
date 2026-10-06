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

"""Check the system libraries of an extracted or staged Linux package of
OpenXTalk-Lite against the list its launcher checks, and against this machine.

  python tools/ci/check_linux_libraries.py --root <OpenXTalk-Lite-<version> folder>
      [--libraries FILE] [--repo DIR] [--no-ldd] [--json FILE]

--libraries is the list the launcher reads (default
<root>/linux/libraries.txt, from Installer/linux/libraries.txt). Every ELF
file of the package built for this machine's architecture (the engine, the
standalone engine, the externals and database drivers, CEF and its helpers,
the toolchain and the extensions' libraries; files for other architectures,
such as the 1.15 asset's x86-32 runtime, and for Android are counted and
skipped) is read
with tools/oxt/binfmt.py, and three things are checked:

  list      every library it needs (DT_NEEDED) is a base-system library
            (tools/ci/check_native_deps.py LINUX_SYSTEM: glibc, the GCC
            runtime, zlib, OpenSSL), a file of the package, or in the list:
            otherwise a user who lacks it gets no word from the launcher.
            With --repo, the same for the libraries the engine loads with
            dlopen: every module of engine/src/linux.stubs that the engine
            requires (initialise_required_weak_link_<module>(), which ends
            the engine when it fails; "core" when dsklnxmain.cpp requires
            it at start-up, else "gui" or "core"), and the Pango modules
            without which MCNewFontlist draws no text (lnxflst.cpp).
  ldd       "ldd <file>" finds every library ("not found" is a failure),
            except KNOWN_UNRESOLVED below. Run it where the list's Debian
            packages are installed; a library that is missing from this
            machine and in the list is reported with its package.
            --no-ldd skips this part.
  format    the list itself: four columns, a known use, no soname twice.

Exit status 0 when all pass, 1 on any problem, 2 when the package cannot
be read. Under GitHub Actions each problem is also an error annotation, and
a table goes to the job summary. Only the Python 3 standard library is used
(3.8 or later).
"""

import argparse
import collections
import fnmatch
import json
import os
import platform as host
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), 'oxt'))
sys.path.insert(0, HERE)
import binfmt  # noqa: E402
import check_native_deps  # noqa: E402

USES = ('core', 'gui', 'browser')

# (installed path glob, library, why) that ldd may report as not found
KNOWN_UNRESOLVED = (
    ('Runtime/Linux/*/revbrowser-cefprocess', 'libcef.so',
     'the copy at the runtime\'s root, which revCopyCEFResources (revsaveasstandalone) copies next to a '
     'standalone engine; revBrowser starts the one in Externals/CEF, next to libcef.so (its run path is '
     '$ORIGIN), and never this one'),
)

# ELF files that are not for Linux although they may be for this machine's
# architecture: the Android runtimes (x86_64 among them) and the Android
# code folders of LCB extensions (fnmatch: "*" also matches "/")
NOT_LINUX = ('Runtime/Android/*', 'Extensions/*/code/*-android*/*')

# The Pango modules MCNewFontlist::create loads (lnxflst.cpp); without them
# it falls back to MCDummyFontlist, which draws no text
FONT_MODULES = ('pango', 'pangocairo', 'pangoft2')


def read_list(path):
    """{soname: (use, deb, rpm)} from the launcher's library list."""
    out = collections.OrderedDict()
    problems = []
    with open(path, encoding='utf-8') as f:
        for n, line in enumerate(f, 1):
            text = line.strip()
            if not text or text.startswith('#'):
                continue
            cols = text.split()
            if len(cols) != 4:
                problems.append('%s line %d: expected <soname> <use> <deb> <rpm>, got %r' % (path, n, text))
                continue
            soname, use, deb, rpm = cols
            if use not in USES:
                problems.append('%s line %d: use %r is not one of %s' % (path, n, use, ', '.join(USES)))
            if soname in out:
                problems.append('%s line %d: %s is listed twice' % (path, n, soname))
            out[soname] = (use, deb, rpm)
    return out, problems


def host_arch():
    m = host.machine().lower()
    return {'amd64': 'x86_64', 'aarch64': 'arm64'}.get(m, m)


def elf_files(root):
    """[(relative path, architecture)] of the regular ELF files below root
    (symbolic links are not followed: their targets are listed anyway)."""
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for name in sorted(filenames):
            path = os.path.join(dirpath, name)
            if os.path.islink(path):
                continue
            try:
                arch = binfmt.elf_arch(path)
            except OSError:
                continue
            if arch:
                out.append((os.path.relpath(path, root).replace(os.sep, '/'), arch))
    return out


def stub_modules(repo):
    """{module: [sonames]} from engine/src/linux.stubs (a module line is
    "<module> <soname>[,<soname>...]", the symbols follow indented)."""
    modules = {}
    with open(os.path.join(repo, 'engine', 'src', 'linux.stubs'), encoding='utf-8', errors='replace') as f:
        for line in f:
            m = re.match(r'^(\w+)\s+(lib\S+)', line)
            if m:
                modules[m.group(1)] = [s for s in m.group(2).split(',') if s]
    return modules


def required_modules(repo):
    """{module: set of source files} of the initialise_required_weak_link_
    calls in engine/src (declarations, which say extern, are not calls)."""
    calls = collections.defaultdict(set)
    src = os.path.join(repo, 'engine', 'src')
    for name in sorted(os.listdir(src)):
        if not name.endswith(('.cpp', '.mm', '.c')):
            continue
        with open(os.path.join(src, name), encoding='utf-8', errors='replace') as f:
            for line in f:
                if 'extern' in line:
                    continue
                for m in re.finditer(r'\binitialise_required_weak_link_(\w+)\s*\(\s*\)', line):
                    calls[m.group(1)].add(name)
    return calls


def check_dlopen(repo, libs):
    """Problems for dlopen'ed engine libraries missing from the list."""
    problems = []
    modules = stub_modules(repo)
    calls = required_modules(repo)
    if not calls:
        return ['no initialise_required_weak_link_* call found in %s/engine/src: the check is out of date' % repo]
    with open(os.path.join(repo, 'engine', 'src', 'lnxflst.cpp'), encoding='utf-8', errors='replace') as f:
        fontlist = f.read()
    wanted = []
    for module, files in sorted(calls.items()):
        need = ('core',) if 'dsklnxmain.cpp' in files else ('core', 'gui')
        wanted.append((module, need, 'initialise_required_weak_link_%s() in %s' % (module, ', '.join(sorted(files)))))
    for module in FONT_MODULES:
        if 'initialise_weak_link_%s()' % module not in fontlist:
            problems.append('lnxflst.cpp no longer loads the module %s: update FONT_MODULES' % module)
        wanted.append((module, ('core', 'gui'), 'MCNewFontlist::create (lnxflst.cpp) draws no text without it'))
    for module, need, why in wanted:
        sonames = modules.get(module)
        if not sonames:
            problems.append('engine/src/linux.stubs has no module %s (%s)' % (module, why))
            continue
        # a module line may name alternatives; the launcher needs one of them
        listed = [s for s in sonames if s in libs]
        if not listed:
            problems.append('the engine loads %s (module %s: %s), which the list does not name'
                            % (' or '.join(sonames), module, why))
        elif libs[listed[0]][0] not in need:
            problems.append('%s is listed as "%s", but the engine needs it as %s (%s)'
                            % (listed[0], libs[listed[0]][0], ' or '.join('"%s"' % n for n in need), why))
    return problems


def run_ldd(path):
    """(not found sonames, None) or (None, the error)."""
    env = dict(os.environ, LC_ALL='C')
    try:
        proc = subprocess.run(['ldd', path], stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
                              timeout=60)
    except (OSError, subprocess.TimeoutExpired) as e:
        return None, str(e)
    out = proc.stdout.decode('utf-8', 'replace')
    err = proc.stderr.decode('utf-8', 'replace').strip()
    if proc.returncode != 0:
        if 'not a dynamic executable' in out + err or 'statically linked' in out + err:
            return [], None
        return None, err or out.strip() or 'ldd exited with status %d' % proc.returncode
    missing = []
    for line in out.splitlines():
        m = re.match(r'^\s*(\S+)\s+=>\s+not found', line)
        if m:
            missing.append(m.group(1))
    return missing, None


def main(argv=None):
    ap = argparse.ArgumentParser(description='Check the system libraries of a Linux OpenXTalk-Lite package.')
    ap.add_argument('--root', required=True, help='the package folder (OpenXTalk-Lite-<version>)')
    ap.add_argument('--libraries', help='the launcher\'s list (default: <root>/linux/libraries.txt)')
    ap.add_argument('--repo', help='repository root: also check the libraries the engine loads with dlopen')
    ap.add_argument('--no-ldd', action='store_true', help='do not run ldd')
    ap.add_argument('--json', metavar='FILE', help='write the results as JSON')
    args = ap.parse_args(argv)
    gha = os.environ.get('GITHUB_ACTIONS') == 'true'
    root = os.path.abspath(args.root)
    list_path = args.libraries or os.path.join(root, 'linux', 'libraries.txt')
    if not os.path.isdir(root) or not os.path.isfile(list_path):
        sys.stderr.write('error: %s is not a Linux package folder (no %s)\n' % (root, list_path))
        return 2
    libs, problems = read_list(list_path)
    arch = host_arch()
    files = elf_files(root)
    android = [rel for rel, _ in files if any(fnmatch.fnmatchcase(rel, pat) for pat in NOT_LINUX)]
    files = [(rel, a) for rel, a in files if rel not in android]
    mine = [(rel, a) for rel, a in files if a == arch]
    others = collections.Counter(a for _, a in files if a != arch)
    if android:
        others['Android'] = len(android)
    # The names a library of the package can be found by: this
    # architecture's ELF files, and links to them (a library may be a link
    # named after its soname). Not the 1.15 asset's x86-32 runtime, whose
    # lib/ has a libgtk-x11-2.0.so.0 that an x86_64 engine cannot load.
    real = {os.path.realpath(os.path.join(root, *rel.split('/'))) for rel, _ in mine}
    names = {os.path.basename(rel) for rel, _ in mine}
    for dirpath, _, filenames in os.walk(root):
        for name in filenames:
            path = os.path.join(dirpath, name)
            if os.path.islink(path) and os.path.realpath(path) in real:
                names.add(name)

    print('Package   : %s' % root)
    print('List      : %s (%d libraries: %s)' % (list_path, len(libs), ', '.join(
        '%d %s' % (sum(1 for u, _, _ in libs.values() if u == use), use) for use in USES)))
    print('ELF files : %d for %s%s' % (len(mine), arch, ''.join(
        '; %d for %s (skipped)' % (n, a) for a, n in sorted(others.items()))))

    # 1. Every DT_NEEDED is base system, bundled or listed
    unlisted = collections.OrderedDict()
    for rel, _ in mine:
        try:
            b = binfmt.parse(os.path.join(root, *rel.split('/')))
        except (OSError, binfmt.FormatError) as e:
            problems.append('%s: cannot be read (%s)' % (rel, e))
            continue
        for lib in b.needed:
            if check_native_deps.LINUX_SYSTEM_RX.match(lib) or lib in libs or lib in names:
                continue
            unlisted.setdefault(lib, []).append(rel)
    for lib, users in unlisted.items():
        problems.append('%s needs %s, which is neither in the list (Installer/linux/libraries.txt), a base-system '
                        'library nor a file of the package' % (', '.join(users[:4]) + (' ...' if len(users) > 4 else ''),
                                                              lib))

    # 2. The libraries the engine loads with dlopen
    if args.repo:
        problems.extend(check_dlopen(os.path.abspath(args.repo), libs))

    # 3. ldd on this machine
    ldd_rows = []
    if not args.no_ldd:
        for rel, _ in mine:
            missing, error = run_ldd(os.path.join(root, *rel.split('/')))
            if error:
                problems.append('ldd %s failed: %s' % (rel, error))
                continue
            for lib in missing:
                why = next((w for pat, l, w in KNOWN_UNRESOLVED if l == lib and fnmatch.fnmatchcase(rel, pat)), None)
                if why:
                    ldd_rows.append((rel, lib, 'expected: ' + why))
                    continue
                hint = ' (on this machine; the list names it: Debian package %s)' % libs[lib][1] if lib in libs else ''
                problems.append('ldd %s: %s not found%s' % (rel, lib, hint))
                ldd_rows.append((rel, lib, 'NOT FOUND'))
        print('ldd       : %d files, %d unresolved library references (%d expected)'
              % (len(mine), len(ldd_rows), sum(1 for r in ldd_rows if r[2].startswith('expected'))))
        for rel, lib, what in ldd_rows:
            print('  %s: %s: %s' % (rel, lib, what))

    print('')
    if problems:
        print('Problems (%d):' % len(problems))
        for i, p in enumerate(problems):
            print('  ' + p)
            if gha and i < 30:
                print('::error title=Linux libraries::%s' % p)
    else:
        print('Every library the package needs is a base-system library, in the package, or in the list%s.'
              % ('' if args.no_ldd else ', and ldd finds all of them here'))
    if args.json:
        with open(args.json, 'w', encoding='utf-8', newline='\n') as f:
            json.dump(collections.OrderedDict([('root', root), ('arch', arch), ('elf_files', [r for r, _ in mine]),
                                               ('ldd_unresolved', ldd_rows), ('problems', problems)]), f, indent=2)
            f.write('\n')
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a', encoding='utf-8', newline='\n') as f:
            f.write('### Linux libraries\n\n%d ELF files for %s checked against `linux/libraries.txt`%s: %s.\n\n'
                    % (len(mine), arch, '' if args.no_ldd else ' and with ldd',
                       'passed' if not problems else '**%d problem(s)**' % len(problems)))
            for p in problems[:30]:
                f.write('- %s\n' % p)
            f.write('\n')
    return 1 if problems else 0


if __name__ == '__main__':
    sys.exit(main())
