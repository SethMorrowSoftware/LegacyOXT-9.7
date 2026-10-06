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

"""Join the arm64 and the x86_64 macOS build outputs into one universal
build output, and check that a tree holds universal code.

  python tools/ci/merge_universal.py --arm64 DIR --x86_64 DIR --out DIR
      [--dry-run | --no-lipo] [--report FILE]

  python tools/ci/merge_universal.py --check DIR [--no-lipo]

The macOS workflow builds each architecture natively on its own runner,
and both CI tarballs unpack as Release/ (_build/mac/Release). Extract each
into a folder of its own (and its symbols tarball over it, which puts
every .dSYM bundle next to its binary), then pass the two Release folders.
Every path of the two trees is looked at once:

  Mach-O files     at the same path in both: joined with "lipo -create";
                   the arm64 build's copy must hold arm64 only and the
                   x86_64 build's x86_64 only. The DWARF files inside the
                   .dSYM bundles are Mach-O files too, so the debug
                   symbols become universal the same way.
  other files      compared byte for byte and copied once. A file that
                   differs is an error unless DIFFER explains it (see
                   there: the bundles' Info.plist and resource seals).
                   After writing, an Info.plist whose LSArchitecturePriority
                   puts x86_64 before arm64 while its bundle's executable
                   is universal now gets arm64, x86_64 there, and nothing
                   else changes (fix_arch_priority).
  one build only   an error unless ONE_SIDED explains it.
  SINGLE_ARCH      Mach-O files that hold one architecture by design
                   (reviphoneproxy, the iOS simulator helper, which both
                   builds make for x86_64 only): the named build's copy is
                   taken, whatever the other build has at that path.
  symbolic links   must be the same in both, and stay links.
  modes, dates     kept (a file whose mode differs between the builds is
                   an error); folders, empty ones too, are the union.

Then every Mach-O file of the result must hold exactly arm64 and x86_64,
except SINGLE_ARCH (the check below). Nothing is written when the plan has
a problem. Exit status 0 success, 1 problems (listed), 2 usage errors.

--dry-run   only plans and reports; writes nothing (lipo is not needed).
--no-lipo   writes the tree, joining the slices with this script's own
            fat-file writer instead of lipo (write_fat): for trying the
            merge and what follows it on a host without lipo, such as
            Linux or WSL. The CI job runs lipo.
--report    writes every path with what was done to it, as TSV.

--check DIR (the merged tree or the assembled OpenXTalk-Lite.app): every
Mach-O file of macOS code must hold exactly arm64 and x86_64, except
SINGLE_ARCH and code in an architecture's own folder of an extension
(code/arm64-mac*, code/x86_64-mac*, which the IDE maps by the processor).
Code for other platforms (the time zone library's iOS and iOS simulator
files, from the runtimes asset) is listed but not checked: its slices are
for iOS, which the macOS app only copies into iOS standalones. When lipo
is on PATH (and without --no-lipo), "lipo -archs" of every file must also
agree with what tools/oxt/binfmt.py reads. A bundle's Contents/Info.plist
must not put x86_64 before arm64 in LSArchitecturePriority when its
executable holds both (LaunchServices would start it under Rosetta on
Apple Silicon).

Only the Python 3 standard library is used (3.8 or later); lipo only
without --dry-run and --no-lipo.
"""

import argparse
import collections
import os
import plistlib
import posixpath
import re
import shutil
import stat
import struct
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), 'oxt'))
import binfmt  # noqa: E402

ARCHS = ('arm64', 'x86_64')          # the builds, in the order of the options
UNIVERSAL = ['arm64', 'x86_64']      # sorted, as the check compares them

# Mach-O files that hold one architecture by design: (pattern, the build
# whose copy is taken, why). Patterns: "*" within one path component, "**"
# any number of components ("**/" also none), relative to the tree.
SINGLE_ARCH = (
    ('**/reviphoneproxy', 'x86_64',
     'the iOS simulator helper: revmobile.gyp builds it for x86_64 only, on both runners (it runs '
     'under Rosetta on Apple Silicon). The arm64 runner\'s copy records that build\'s deployment '
     'target (11.0); the x86_64 build\'s records 10.13 like the rest of the universal app'),
    ('**/reviphoneproxy.dSYM/**', 'x86_64',
     'the debug symbols of reviphoneproxy (x86_64 only, see there), taken from the same build'),
)

# Files that differ between the two builds for a known reason: (pattern,
# kind, why). The kind says how the copy is chosen (see plan_differ).
DIFFER = (
    ('**/Contents/Info.plist', 'plist',
     'Xcode writes each build\'s deployment target into its bundles as LSMinimumSystemVersion '
     '(11.0 on arm64, 10.13 on x86_64). The copy with the lower one is taken, so that a universal '
     'bundle declares the macOS its x86_64 slice runs on, byte for byte (the standalone builder '
     'edits the runtime\'s Info.plist line by line). Any other key that differs is an error, '
     'except the build machine\'s (BUILD_MACHINE_KEYS), which are reported. One edit follows the '
     'write (fix_arch_priority): LiveCode-Community.app\'s says LSArchitecturePriority x86_64, i386 '
     '(from LiveCode\'s Intel days), which LaunchServices would follow into Rosetta once the '
     'executable is universal, so that array becomes arm64, x86_64'),
    ('**/Contents/_CodeSignature/CodeResources', 'seal',
     'the bundle\'s resource seal, which records the code directory hash of its nested code '
     '(per architecture) and is bound to its Info.plist. Neither copy matches the merged bundle, '
     'whose Mach-O slices keep their own stale signatures too: tools/ci/sign_mac_app.py signs the '
     'merged tree and the app again, which writes new seals. The x86_64 build\'s is taken, from '
     'the same build as the Info.plist'),
)
# The build machine's keys in an Info.plist (Xcode's DT* keys and
# BuildMachineOSBuild): the two runners may differ in them without the
# bundles differing; the plist rule reports them instead of failing.
BUILD_MACHINE_KEYS = frozenset(('BuildMachineOSBuild', 'DTCompiler', 'DTPlatformBuild', 'DTPlatformName',
                                'DTPlatformVersion', 'DTSDKBuild', 'DTSDKName', 'DTXcode', 'DTXcodeBuild'))

# LaunchServices starts the first architecture listed in a bundle's
# LSArchitecturePriority that its executable holds. LiveCode's plist (so
# LiveCode-Community.app's) still lists x86_64, i386, which did no harm in
# the arm64 build, whose executable had no x86_64 slice; in the merged,
# universal one it would run the engine under Rosetta on Apple Silicon
# (or have macOS offer to install Rosetta). fix_arch_priority rewrites the
# array to ARCH_PRIORITY after writing, as package.py's mac_info_plist
# writes it for OpenXTalk-Lite.app, and the check refuses a plist that still
# puts x86_64 first.
PLIST = '**/Contents/Info.plist'
ARCH_PRIORITY_KEY = 'LSArchitecturePriority'
ARCH_PRIORITY = ['arm64', 'x86_64']
# Only the array that follows the key, as text: plistlib.dump would sort
# the keys (sort_keys=False keeps them, but not Xcode's layout), and the
# rest of the file stays byte for byte, as the plist rule promises
_PRIORITY_ARRAY = re.compile(br'<key>LSArchitecturePriority</key>\s*<array>(.*?)</array>', re.S)

# Files that only one build has: (pattern, build, why)
ONE_SIDED = (
    ('**/*.dSYM/Contents/Resources/Relocations/aarch64/**', 'arm64',
     'dsymutil writes the relocations of each architecture into a folder of its own'),
    ('**/*.dSYM/Contents/Resources/Relocations/arm64/**', 'arm64',
     'dsymutil writes the relocations of each architecture into a folder of its own'),
    ('**/*.dSYM/Contents/Resources/Relocations/x86_64/**', 'x86_64',
     'dsymutil writes the relocations of each architecture into a folder of its own'),
)

# The fat header lipo writes: x86_64 first, then arm64 ("lipo -archs"
# prints "x86_64 arm64"), each slice aligned to its page size (2^12 on
# x86_64, 2^14 on arm64, as lipo aligns them)
FAT_ORDER = {'x86_64': 0, 'arm64': 1}
FAT_ALIGN = {'x86_64': 12, 'arm64': 14}

# code folders of an extension that hold one architecture, which the IDE
# maps by "the processor" (revideextensionlibrary __MapCodeLibraryForIDE)
ARCH_CODE_FOLDER = re.compile(r'^(arm64|x86_64)-mac')


class MergeError(Exception):
    pass


def log(msg=''):
    print(msg)
    sys.stdout.flush()


def gha():
    return os.environ.get('GITHUB_ACTIONS') == 'true'


def _pattern(pattern):
    rx, i = [], 0
    while i < len(pattern):
        if pattern.startswith('**/', i):
            rx.append('(?:.*/)?')
            i += 3
        elif pattern.startswith('**', i):
            rx.append('.*')
            i += 2
        elif pattern[i] == '*':
            rx.append('[^/]*')
            i += 1
        else:
            rx.append(re.escape(pattern[i]))
            i += 1
    return re.compile(''.join(rx) + r'\Z')


_COMPILED = {}


def match(pattern, path):
    rx = _COMPILED.get(pattern)
    if rx is None:
        rx = _COMPILED[pattern] = _pattern(pattern)
    return rx.match(path) is not None


def rule_for(rules, path):
    return next((r for r in rules if match(r[0], path)), None)


# ---------------------------------------------------------------------------
# Trees

Entry = collections.namedtuple('Entry', 'kind mode mtime link')   # kind: file, link


def scan(root):
    """({relative path: Entry}, [folders]) of a build output. The "._"
    AppleDouble files that a non-macOS tar makes of extended attributes,
    and .DS_Store, are skipped; nothing else is."""
    entries, folders = {}, []
    for dirpath, dirnames, filenames in os.walk(root):
        rel = os.path.relpath(dirpath, root)
        rel = '' if rel == '.' else rel.replace(os.sep, '/') + '/'
        links = [d for d in dirnames if os.path.islink(os.path.join(dirpath, d))]
        dirnames[:] = sorted(d for d in dirnames if d not in links and not d.startswith('._'))
        folders.extend(rel + d for d in dirnames)
        for name in filenames + links:
            if name.startswith('._') or name == '.DS_Store':
                continue
            path = os.path.join(dirpath, name)
            st = os.lstat(path)
            if stat.S_ISLNK(st.st_mode):
                entries[rel + name] = Entry('link', 0o777, st.st_mtime, os.readlink(path))
            elif stat.S_ISREG(st.st_mode):
                entries[rel + name] = Entry('file', stat.S_IMODE(st.st_mode), st.st_mtime, None)
            else:
                raise MergeError('%s is a device, FIFO or other special file' % path)
    return entries, folders


def native(root, rel):
    return os.path.join(root, *rel.split('/'))


def macho_info(path):
    """(archs, macOS or not) of a Mach-O file, or None when it is not one.
    A file is macOS code when a slice records a minimum macOS
    (LC_BUILD_VERSION platform macOS, LC_VERSION_MIN_MACOSX) or records no
    platform at all; iOS and simulator code records its own platform."""
    archs = binfmt.macho_archs(path)
    if archs is None:
        return None
    try:
        data = binfmt.read_file(path)
        macos = True
        for _, off, size in binfmt.macho_slices(data):
            b = binfmt.parse_macho(data[off:off + size])
            if not b.floors.get('macOS') and _records_platform(data[off:off + size]):
                macos = False
    except (struct.error, IndexError, binfmt.FormatError) as e:
        raise MergeError('%s: cannot be read as Mach-O: %s' % (path, e))
    return archs, macos


def _records_platform(s):
    """True when a thin Mach-O image has a build version or minimum-OS
    load command (for any platform)."""
    order = '<' if struct.unpack_from('<I', s, 0)[0] in (binfmt.MH_MAGIC, binfmt.MH_MAGIC_64) else '>'
    magic, _, _, _, ncmds, _ = struct.unpack_from(order + 'IiiIII', s, 0)
    o = 32 if magic == binfmt.MH_MAGIC_64 else 28
    for _ in range(ncmds):
        cmd, size = struct.unpack_from(order + 'II', s, o)
        # LC_VERSION_MIN_MACOSX, _IPHONEOS, _TVOS, _WATCHOS, LC_BUILD_VERSION
        if cmd in (0x24, 0x25, 0x2f, 0x30, 0x32):
            return True
        if size < 8:
            break
        o += size
    return False


def same_bytes(a, b):
    if os.path.getsize(a) != os.path.getsize(b):
        return False
    with open(a, 'rb') as fa, open(b, 'rb') as fb:
        while True:
            x, y = fa.read(1 << 20), fb.read(1 << 20)
            if x != y:
                return False
            if not x:
                return True


def version_tuple(text):
    try:
        return tuple(int(x) for x in str(text).split('.'))
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Plan

Action = collections.namedtuple('Action', 'path what source detail')
# what: copy (source: the build whose file is copied), lipo, link, take
# (a rule chose one build's copy); detail: for the report


class Planner(object):
    def __init__(self, roots):
        self.roots = roots            # {'arm64': dir, 'x86_64': dir}
        self.actions = []
        self.problems = []
        self.notes = []               # allowed differences, for the summary
        self.counts = collections.Counter()

    def problem(self, path, msg):
        self.problems.append('%s: %s' % (path, msg))

    def add(self, path, what, source, detail=''):
        self.actions.append(Action(path, what, source, detail))
        self.counts[what] += 1


def plan(roots):
    """The actions that make the universal tree, and the folders."""
    pl = Planner(roots)
    trees, folders = {}, set()
    for arch in ARCHS:
        if not os.path.isdir(roots[arch]):
            raise MergeError('--%s %s is not a folder' % (arch, roots[arch]))
        trees[arch], dirs = scan(roots[arch])
        folders.update(dirs)
    a, x = trees['arm64'], trees['x86_64']
    for path in sorted(set(a) | set(x)):
        ea, ex = a.get(path), x.get(path)
        single = rule_for(SINGLE_ARCH, path)
        if ea is None or ex is None:
            plan_one_sided(pl, path, 'arm64' if ex is None else 'x86_64', single)
        elif ea.kind == 'link' or ex.kind == 'link':
            if ea.kind != ex.kind or ea.link != ex.link:
                pl.problem(path, 'a symbolic link in one build (%s) but not the same link in the other (%s)'
                           % (_describe(ea), _describe(ex)))
            elif ea.link.startswith('/') or \
                    posixpath.normpath(posixpath.join(posixpath.dirname(path), ea.link)).split('/')[0] == '..':
                pl.problem(path, 'symbolic link -> %s leads out of the build output' % ea.link)
            else:
                pl.add(path, 'link', 'x86_64', ea.link)
        else:
            plan_pair(pl, path, ea, ex, single)
    return pl, sorted(folders)


def _describe(e):
    return 'link -> %s' % e.link if e.kind == 'link' else 'file'


def plan_one_sided(pl, path, arch, single):
    other = [b for b in ARCHS if b != arch][0]
    rule = rule_for(ONE_SIDED, path)
    if single and single[1] == arch:
        pl.add(path, 'take', arch, 'only in the %s build; %s' % (arch, single[2]))
    elif rule and rule[1] == arch:
        pl.add(path, 'take', arch, 'only in the %s build: %s' % (arch, rule[2]))
    else:
        pl.problem(path, 'exists in the %s build only, not in the %s build (a universal tree would lack it '
                         'on one architecture; if that is intended, add a rule to ONE_SIDED or SINGLE_ARCH in '
                         'tools/ci/merge_universal.py)' % (arch, other))


def plan_pair(pl, path, ea, ex, single):
    pa, px = native(pl.roots['arm64'], path), native(pl.roots['x86_64'], path)
    ia, ix = macho_info(pa), macho_info(px)
    if single:
        keep = single[1]
        other = 'arm64' if keep == 'x86_64' else 'x86_64'
        kept, dropped = (ix, ia) if keep == 'x86_64' else (ia, ix)
        # nothing may be lost: the other build's copy must not hold an
        # architecture that the kept copy lacks
        if kept and dropped and [z for z in dropped[0] if z not in kept[0]]:
            pl.problem(path, 'SINGLE_ARCH takes the %s build\'s copy (%s), but the %s build\'s holds %s, which '
                             'would be lost; remove the rule' % (keep, ' '.join(kept[0]), other,
                                                                  ' '.join(dropped[0])))
        else:
            pl.add(path, 'take', keep, single[2])
        return
    if ea.mode != ex.mode:
        pl.problem(path, 'mode %o in the arm64 build, %o in the x86_64 build' % (ea.mode, ex.mode))
        return
    if ia or ix:
        if not (ia and ix):
            pl.problem(path, 'a Mach-O file in the %s build only (the other build has another kind of file '
                             'there)' % ('arm64' if ia else 'x86_64'))
        elif same_bytes(pa, px):
            # the same (prebuilt) file in both builds: the check below
            # still requires it to be universal
            pl.add(path, 'copy', 'x86_64', 'the same Mach-O file (%s) in both builds' % ' '.join(ix[0]))
        elif ia[0] == ['arm64'] and ix[0] == ['x86_64']:
            pl.add(path, 'lipo', None, 'arm64 + x86_64')
        else:
            pl.problem(path, 'holds %s in the arm64 build and %s in the x86_64 build; expected arm64 and '
                             'x86_64 (each build makes its own architecture only)'
                       % (' '.join(ia[0]), ' '.join(ix[0])))
        return
    if same_bytes(pa, px):
        pl.add(path, 'copy', 'x86_64')
        return
    rule = rule_for(DIFFER, path)
    if rule is None:
        pl.problem(path, 'differs between the builds (%d and %d bytes); if that is intended, add a rule to '
                         'DIFFER in tools/ci/merge_universal.py' % (os.path.getsize(pa), os.path.getsize(px)))
    elif rule[1] == 'plist':
        plan_plist(pl, path, pa, px)
    else:
        pl.add(path, 'take', 'x86_64', rule[2])
        pl.notes.append(('resource seal (x86_64 build\'s copy; written again when signed)', path))


def plan_plist(pl, path, pa, px):
    """An Info.plist that differs: only in LSMinimumSystemVersion (and the
    build machine's keys) is fine; the copy with the lower minimum is taken
    as it is, byte for byte. That minimum must be the lowest minimum macOS
    that the bundle's executable records in either build."""
    try:
        with open(pa, 'rb') as f:
            da = plistlib.load(f)
        with open(px, 'rb') as f:
            dx = plistlib.load(f)
    except (OSError, ValueError, plistlib.InvalidFileException) as e:
        pl.problem(path, 'cannot be read as a property list: %s' % e)
        return
    keys = sorted(k for k in set(da) | set(dx) if da.get(k) != dx.get(k))
    other = [k for k in keys if k != 'LSMinimumSystemVersion' and k not in BUILD_MACHINE_KEYS]
    if other:
        pl.problem(path, 'the builds\' copies differ in %s, not only in LSMinimumSystemVersion'
                   % ', '.join('%s (%r / %r)' % (k, da.get(k), dx.get(k)) for k in other))
        return
    ma, mx = da.get('LSMinimumSystemVersion'), dx.get('LSMinimumSystemVersion')
    va, vx = version_tuple(ma), version_tuple(mx)
    if 'LSMinimumSystemVersion' in keys and (va is None or vx is None):
        pl.problem(path, 'LSMinimumSystemVersion %r / %r is not a version' % (ma, mx))
        return
    take = 'arm64' if (va is not None and vx is not None and va < vx) else 'x86_64'
    chosen = ma if take == 'arm64' else mx
    detail = 'LSMinimumSystemVersion %s (arm64 build) and %s (x86_64 build): the %s build\'s copy' % (
        ma, mx, take)
    machine = [k for k in keys if k in BUILD_MACHINE_KEYS]
    if machine:
        detail += '; the build machines also differ in %s' % ', '.join(
            '%s (%s / %s)' % (k, da.get(k), dx.get(k)) for k in machine)
    # the executable's own floor, over both builds, must agree
    exe = dx.get('CFBundleExecutable') or da.get('CFBundleExecutable')
    bundle = path[:-len('Contents/Info.plist')]
    if chosen is not None and exe:
        floors = []
        for arch in ARCHS:
            p = native(pl.roots[arch], bundle + 'Contents/MacOS/' + exe)
            if os.path.isfile(p) and binfmt.macho_archs(p):
                data = binfmt.read_file(p)
                floors += [binfmt.parse_macho(data[o:o + n]).floors.get('macOS')
                           for _, o, n in binfmt.macho_slices(data)]
        floors = [f for f in floors if f]
        if floors and version_tuple(binfmt.version_text(min(floors))) != version_tuple(chosen):
            pl.problem(path, 'LSMinimumSystemVersion would be %s, but %sContents/MacOS/%s runs from macOS %s'
                       % (chosen, bundle, exe, binfmt.version_text(min(floors))))
            return
    pl.add(path, 'take', take, detail)
    pl.notes.append(('Info.plist: ' + detail.split(':')[0], path))


# ---------------------------------------------------------------------------
# Write

def write_fat(out, inputs):
    """Join thin Mach-O files into one fat file as "lipo -create" does
    (for --no-lipo): the fat header (big-endian), then each slice at an
    offset aligned to its page size, x86_64 first."""
    slices = []
    for path in inputs:
        data = binfmt.read_file(path)
        if binfmt.macho_slices(data)[0][0] is not None:
            raise MergeError('%s is already a fat file' % path)
        order = '<' if struct.unpack_from('<I', data, 0)[0] in (binfmt.MH_MAGIC, binfmt.MH_MAGIC_64) else '>'
        cputype, cpusubtype = struct.unpack_from(order + 'ii', data, 4)
        arch = binfmt.MACHO_CPUS.get(cputype & 0xffffffff)
        if arch not in FAT_ALIGN:
            raise MergeError('%s: architecture %s' % (path, arch))
        slices.append((FAT_ORDER[arch], arch, cputype, cpusubtype, data))
    slices.sort()
    header = struct.pack('>II', binfmt.FAT_MAGIC, len(slices))
    offset = len(header) + 20 * len(slices)
    table, body = [], []
    for _, arch, cputype, cpusubtype, data in slices:
        align = FAT_ALIGN[arch]
        start = (offset + (1 << align) - 1) & ~((1 << align) - 1)
        body.append(b'\0' * (start - offset))
        body.append(data)
        table.append(struct.pack('>iiIII', cputype, cpusubtype, start, len(data), align))
        offset = start + len(data)
    with open(out, 'wb') as f:
        f.write(header)
        f.write(b''.join(table))
        for b in body:
            f.write(b)


def lipo_archs(path):
    out = subprocess.run(['lipo', '-archs', path], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if out.returncode:
        raise MergeError('lipo -archs %s failed: %s' % (path, out.stderr.decode('utf-8', 'replace').strip()))
    return sorted(out.stdout.decode('ascii', 'replace').split())


def write(pl, folders, out, use_lipo):
    roots = pl.roots
    os.makedirs(out, exist_ok=True)
    for d in folders:
        os.makedirs(native(out, d), exist_ok=True)
    for act in pl.actions:
        dst = native(out, act.path)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        if act.what == 'link':
            os.symlink(act.detail, dst)
        elif act.what in ('copy', 'take'):
            shutil.copy2(native(roots[act.source], act.path), dst)
        elif act.what == 'lipo':
            srcs = [native(roots[a], act.path) for a in ARCHS]
            if use_lipo:
                r = subprocess.run(['lipo', '-create'] + srcs + ['-output', dst],
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
                if r.returncode:
                    raise MergeError('lipo -create %s failed: %s'
                                     % (act.path, r.stdout.decode('utf-8', 'replace').strip()))
            else:
                write_fat(dst, srcs)
            st = [os.stat(s) for s in srcs]
            os.chmod(dst, stat.S_IMODE(st[1].st_mode))
            mtime = max(s.st_mtime for s in st)
            os.utime(dst, (mtime, mtime))
    # folders keep the x86_64 build's modes (os.makedirs used the umask)
    for d in folders:
        src = native(roots['x86_64'], d)
        if not os.path.isdir(src):
            src = native(roots['arm64'], d)
        os.chmod(native(out, d), stat.S_IMODE(os.stat(src).st_mode))


def first_held(priority, archs):
    """The architecture that LaunchServices starts: the first one of an
    LSArchitecturePriority list that the executable holds (None: none,
    and LaunchServices picks the Mac's own)."""
    return next((a for a in priority if a in archs), None) if isinstance(priority, list) else None


def read_plist_priority(root, path):
    """(the plist's bytes, its dict, the sorted archs of its bundle's
    executable) of a Contents/Info.plist that has LSArchitecturePriority,
    or None when it has no such key. The key is looked for in the bytes
    first (a binary plist stores key names as text too), so that no other
    plist is parsed."""
    full = native(root, path)
    with open(full, 'rb') as f:
        data = f.read()
    if ARCH_PRIORITY_KEY.encode('ascii') not in data:
        return None
    try:
        plist = plistlib.loads(data)
    except (ValueError, plistlib.InvalidFileException) as e:
        raise MergeError('%s: cannot be read as a property list: %s' % (full, e))
    if not isinstance(plist, dict) or ARCH_PRIORITY_KEY not in plist:
        return None
    archs = None
    exe = plist.get('CFBundleExecutable')
    if exe:
        exe_path = native(root, path[:-len('Info.plist')] + 'MacOS/' + exe)
        if os.path.isfile(exe_path):
            archs = binfmt.macho_archs(exe_path)
    return data, plist, sorted(archs) if archs else None


def fix_arch_priority(pl, out):
    """After write: every Contents/Info.plist of the plan whose bundle's
    executable is universal now (arm64 and x86_64), and whose
    LSArchitecturePriority would have LaunchServices start the x86_64
    slice (LiveCode-Community.app's x86_64, i386), gets arm64, x86_64 in
    that array; only the array is rewritten, the rest of the file stays
    byte for byte, and so do mode and date (a plist without the key, such
    as the runtime's, is never touched). Must run before the tree is
    signed: sign_mac_app.py seals the edited plist. Returns [(path, what
    was changed)]."""
    edited = []
    for act in pl.actions:
        if act.what == 'link' or not match(PLIST, act.path):
            continue
        found = read_plist_priority(out, act.path)
        if found is None:
            continue
        data, plist, archs = found
        old = plist[ARCH_PRIORITY_KEY]
        if archs != UNIVERSAL or first_held(old, archs) != 'x86_64':
            continue
        full = native(out, act.path)
        want = dict(plist)
        want[ARCH_PRIORITY_KEY] = list(ARCH_PRIORITY)
        if data.startswith(b'bplist'):
            # no text to keep; Xcode writes XML, so this is only a fallback
            new = plistlib.dumps(want, fmt=plistlib.FMT_BINARY, sort_keys=False)
        else:
            m = _PRIORITY_ARRAY.search(data)
            if m is None:
                raise MergeError('%s: LSArchitecturePriority %s is not written as <key>...</key> <array> '
                                 '<string>... (the text rewrite expects an XML plist as Xcode writes it)'
                                 % (full, ', '.join(map(str, old))))
            inner = m.group(1)
            ws = re.match(br'\s*', inner).group(0)      # each <string>'s indent, as Xcode wrote it
            new = data[:m.start(1)] + b''.join(ws + b'<string>' + a.encode('ascii') + b'</string>'
                                               for a in ARCH_PRIORITY) \
                + inner[len(inner.rstrip()):] + data[m.end(1):]
        # the edit must change that key and nothing else
        try:
            ok = plistlib.loads(new) == want
        except (ValueError, plistlib.InvalidFileException):
            ok = False
        if not ok:
            raise MergeError('%s: rewriting LSArchitecturePriority %s as %s would change more than that key; '
                             'the plist is left as it is' % (full, ', '.join(map(str, old)),
                                                             ', '.join(ARCH_PRIORITY)))
        st = os.stat(full)
        tmp = full + '.oxt-tmp'
        with open(tmp, 'wb') as f:
            f.write(new)
        os.chmod(tmp, stat.S_IMODE(st.st_mode))
        os.utime(tmp, (st.st_atime, st.st_mtime))
        os.replace(tmp, full)
        detail = 'LSArchitecturePriority %s -> %s' % (', '.join(map(str, old)), ', '.join(ARCH_PRIORITY))
        pl.notes.append(('Info.plist: ' + detail, act.path))
        edited.append((act.path, detail))
    return edited


# ---------------------------------------------------------------------------
# Check

def check_tree(root, use_lipo):
    """(checked, not macOS, problems) for every Mach-O file below root,
    and every bundle's Contents/Info.plist that would start a universal
    executable's x86_64 slice on Apple Silicon (see fix_arch_priority)."""
    checked, other, problems = [], [], []
    entries, _ = scan(root)
    for path in sorted(entries):
        if entries[path].kind != 'file':
            continue
        full = native(root, path)
        if match(PLIST, path):
            try:
                found = read_plist_priority(root, path)
            except (MergeError, OSError) as e:
                problems.append(str(e))
                continue
            if found and found[2] == UNIVERSAL and first_held(found[1][ARCH_PRIORITY_KEY], found[2]) == 'x86_64':
                problems.append('%s: LSArchitecturePriority %s puts x86_64 before arm64, but its executable holds '
                                'both: LaunchServices would run it under Rosetta on Apple Silicon (expected %s)'
                                % (path, ', '.join(map(str, found[1][ARCH_PRIORITY_KEY])),
                                   ', '.join(ARCH_PRIORITY)))
            continue
        try:
            info = macho_info(full)
        except MergeError as e:
            problems.append(str(e))
            continue
        if info is None:
            continue
        archs, macos = info
        if not macos:
            other.append((path, archs))
            continue
        single = rule_for(SINGLE_ARCH, path)
        parts = path.split('/')
        folder = next((m.group(1) for m in (ARCH_CODE_FOLDER.match(parts[i + 1])
                                            for i in range(len(parts) - 2) if parts[i] == 'code') if m), None)
        if single:
            expected = [single[1]]
        elif folder:
            expected = [folder]
        else:
            expected = UNIVERSAL
        found = sorted(archs)
        if found != sorted(expected):
            problems.append('%s holds %s, not %s' % (path, ' '.join(archs) or 'nothing',
                                                     ' and '.join(expected)))
        elif use_lipo:
            try:
                by_lipo = lipo_archs(full)
            except MergeError as e:
                problems.append(str(e))
                continue
            if by_lipo != found:
                problems.append('%s: lipo -archs says %s, binfmt.py read %s' % (path, ' '.join(by_lipo),
                                                                                  ' '.join(found)))
        checked.append((path, archs, expected is not UNIVERSAL))
    return checked, other, problems


def report_check(root, checked, other, problems):
    single = [c for c in checked if c[2]]
    log('Mach-O files in %s: %d of macOS code checked (%d universal, %d single-architecture by design), '
        '%d of other platforms not checked' % (root, len(checked), len(checked) - len(single), len(single),
                                              len(other)))
    for path, archs, _ in single:
        log('  single architecture by design: %s (%s)' % (path, ' '.join(archs)))
    if other:
        log('  code for other platforms (iOS, simulators), not checked:')
        for path, archs in other[:20]:
            log('    %s (%s)' % (path, ' '.join(archs)))
        if len(other) > 20:
            log('    ... and %d more' % (len(other) - 20))
    for p in problems:
        log('::error title=Universal check::%s' % p if gha() else 'error: %s' % p)
    if not checked and not problems:
        problems.append('no Mach-O file of macOS code in %s: the wrong folder?' % root)
        log('error: %s' % problems[-1])
    log('Universal check %s.' % ('FAILED (%d problems)' % len(problems) if problems else 'passed'))
    return 1 if problems else 0


# ---------------------------------------------------------------------------
# Command line

def main(argv=None):
    ap = argparse.ArgumentParser(description='Join the arm64 and x86_64 macOS build outputs into one universal '
                                             'build output, or check that a tree is universal.')
    ap.add_argument('--arm64', metavar='DIR', help='the arm64 build\'s Release folder')
    ap.add_argument('--x86_64', metavar='DIR', help='the x86_64 build\'s Release folder')
    ap.add_argument('--out', metavar='DIR', help='the universal Release folder to write (must not exist or be empty)')
    ap.add_argument('--dry-run', action='store_true', help='plan and report only; write nothing')
    ap.add_argument('--no-lipo', action='store_true',
                    help='join the slices with this script\'s own fat-file writer (hosts without lipo; for tests)')
    ap.add_argument('--report', metavar='FILE', help='write every path and what was done to it as TSV')
    ap.add_argument('--check', metavar='DIR', help='only check that every Mach-O file of macOS code under DIR is '
                                                   'universal')
    args = ap.parse_args(argv)
    use_lipo = not args.no_lipo and not args.dry_run

    if args.check:
        if args.arm64 or args.x86_64 or args.out:
            ap.error('--check takes no --arm64, --x86_64 or --out')
        if not os.path.isdir(args.check):
            ap.error('--check %s is not a folder' % args.check)
        lipo = use_lipo and shutil.which('lipo') is not None
        if use_lipo and not lipo:
            log('lipo is not on PATH: the architectures are read by tools/oxt/binfmt.py only')
        try:
            checked, other, problems = check_tree(os.path.abspath(args.check), lipo)
        except MergeError as e:
            log('error: %s' % e)
            return 1
        return report_check(args.check, checked, other, problems)

    if not (args.arm64 and args.x86_64) or not (args.out or args.dry_run):
        ap.error('pass --arm64 and --x86_64, and --out (or --dry-run)')
    if use_lipo and shutil.which('lipo') is None:
        ap.error('lipo is not on PATH (it comes with the Xcode command line tools); on another host use '
                 '--no-lipo or --dry-run')
    roots = {'arm64': os.path.abspath(args.arm64), 'x86_64': os.path.abspath(args.x86_64)}
    out = os.path.abspath(args.out) if args.out else None
    try:
        if out and os.path.lexists(out) and (not os.path.isdir(out) or os.listdir(out)):
            raise MergeError('--out %s exists and is not an empty folder' % out)
        for arch, root in roots.items():
            if out and (out == root or out.startswith(root + os.sep) or root.startswith(out + os.sep)):
                raise MergeError('--out %s overlaps the %s build %s' % (out, arch, root))
        log('arm64 build : %s' % roots['arm64'])
        log('x86_64 build: %s' % roots['x86_64'])
        log('Output      : %s' % (out if not args.dry_run else '(dry run: nothing is written)'))
        log('Join with   : %s' % ('lipo' if use_lipo else 'nothing (dry run)' if args.dry_run else
                                  'the script\'s own fat-file writer (--no-lipo)'))
        pl, folders = plan(roots)
    except (MergeError, OSError) as e:
        log('::error title=Universal merge::%s' % e if gha() else 'error: %s' % e)
        return 1

    log('')
    log('Mach-O files joined (arm64 + x86_64): %d' % pl.counts['lipo'])
    log('Identical in both builds, copied once: %d files, %d symbolic links' % (pl.counts['copy'], pl.counts['link']))
    groups = collections.OrderedDict()
    for act in pl.actions:
        if act.what == 'take':
            groups.setdefault(act.detail, []).append(act.path)
    if groups:
        log('Taken from one build (%d files):' % pl.counts['take'])
        for detail, paths in groups.items():
            log('  [%s]' % detail)
            for p in paths[:12]:
                log('      %s' % p)
            if len(paths) > 12:
                log('      ... and %d more' % (len(paths) - 12))
    if args.report:
        with open(args.report, 'w', encoding='utf-8', newline='\n') as f:
            f.write('action\tpath\tfrom\tdetail\n')
            for act in pl.actions:
                f.write('%s\t%s\t%s\t%s\n' % (act.what, act.path, act.source or 'both', act.detail))
            for msg in pl.problems:
                f.write('ERROR\t%s\t\t\n' % msg)
        log('Report: %s' % args.report)
    if pl.problems:
        log('')
        for msg in pl.problems:
            log('::error title=Universal merge::%s' % msg if gha() else 'error: %s' % msg)
        log('Universal merge FAILED: %d problem(s); nothing was written.' % len(pl.problems))
        return 1
    # every planned Mach-O pair joins into arm64 + x86_64, and every file
    # taken from one build keeps what the rule says
    if args.dry_run:
        log('')
        log('Dry run: the plan has no problems (%d files, %d folders).' % (len(pl.actions), len(folders)))
        return 0
    try:
        write(pl, folders, out, use_lipo)
        # before the workflow's "Sign the merged build output", whose new
        # seals then cover the edited plists
        edited = fix_arch_priority(pl, out)
        if edited:
            log('')
            log('Edited after the merge, as the bundles\' executables are universal now (%d file%s):'
                % (len(edited), '' if len(edited) == 1 else 's'))
            for path, detail in edited:
                log('  %s: %s' % (detail, path))
            if args.report:
                with open(args.report, 'a', encoding='utf-8', newline='\n') as f:
                    for path, detail in edited:
                        f.write('edit\t%s\tmerged\t%s (the executable holds arm64 and x86_64)\n' % (path, detail))
        log('')
        checked, other, problems = check_tree(out, use_lipo)
    except (MergeError, OSError) as e:
        log('::error title=Universal merge::%s' % e if gha() else 'error: %s' % e)
        return 1
    return report_check(out, checked, other, problems)


if __name__ == '__main__':
    sys.exit(main())
