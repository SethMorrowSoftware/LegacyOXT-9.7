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

"""Check the oldest macOS that every Mach-O file of a build output needs.

Walks ROOT (for CI, _build/mac/Release) and reads every Mach-O file, thin or
universal, every slice of it: the architecture, and the minimum macOS
("minos") and SDK version that the linker recorded in LC_BUILD_VERSION, or
in LC_VERSION_MIN_MACOSX for binaries linked for macOS 10.13 and older. The
loader refuses to run a binary whose minos is newer than the Mac, so the
highest minos in the tree is the real minimum macOS of the product, not
the deployment target that config/mac.gypi asks for; a library that
records a newer one (a prebuilt, or a target that sets its own
MACOSX_DEPLOYMENT_TARGET) would silently raise it. The SDK version selects
AppKit's compatibility behaviour, so every file must record the same one.

    check_macho_floor.py ROOT --minos arm64=11.0 --minos x86_64=10.13
                         [--sdk 11.0] [--arch arm64] [--allow PATH=REASON]
                         [--only-arch PATH=ARCH] [--file-minos PATH=ARCH:VERSION]

--minos ARCH=VERSION  every slice of ARCH must record exactly this minos;
                      a slice of an architecture with no --minos fails
                      (arm64, arm64e, x86_64, i386, ...)
--sdk VERSION         every slice must record this SDK version
--arch ARCH           every file must hold a slice of ARCH (the
                      architecture this build is for)
--only-arch PATH=ARCH
                      files matching PATH must hold exactly one slice, of
                      ARCH, instead of a slice of --arch (a target built
                      for one architecture whatever the build is for)
--file-minos PATH=ARCH:VERSION
                      ARCH slices of files matching PATH must record
                      VERSION instead of --minos's; every other check
                      (SDK, platform, the other architectures) still
                      applies to them
--allow PATH=REASON   PATH is reported but never fails; the reason is
                      printed with it, so a log says why. Prefer
                      --only-arch and --file-minos, which excuse one
                      known difference and keep every other check
--exclude PATTERN     skip matching paths altogether

PATH and PATTERN are relative to ROOT, with / separators, and are
fnmatch patterns. An --only-arch or --file-minos PATH that matches no
file is an error: the exception it describes no longer exists.

dSYM companions (file type MH_DSYM) are skipped: they hold debug
information, not code, and record whatever their binary records.
Symbolic links are not followed, so every file is read once; static
libraries (ar archives) are not Mach-O files and are skipped too.

Prints one line per file and slice, then the highest minos per
architecture. Exit code 0 when every file is as expected, 1 when one is
not (or when ROOT holds no Mach-O file at all, which means a wrong ROOT),
2 on a usage error. Problems go to stderr, or under GitHub Actions
(GITHUB_ACTIONS=true) to stdout as ::error:: annotations.

Only the Python 3 standard library is used (3.8 or newer), so it runs
on any build host and can be tested on a Linux or Windows copy of the
build output.
"""

import argparse
import fnmatch
import os
import struct
import sys

# <mach-o/fat.h>: a universal file's header is big-endian on every host
FAT_MAGIC = 0xCAFEBABE
FAT_MAGIC_64 = 0xCAFEBABF
# <mach-o/loader.h>, as read big-endian from the first four bytes
MH_MAGIC = 0xFEEDFACE
MH_MAGIC_64 = 0xFEEDFACF
MH_CIGAM = 0xCEFAEDFE
MH_CIGAM_64 = 0xCFFAEDFE

MH_DSYM = 0xA
FILE_TYPES = {1: 'object', 2: 'executable', 6: 'dylib', 7: 'dylinker', 8: 'bundle', 0xA: 'dSYM'}

LC_VERSION_MIN_MACOSX = 0x24
LC_VERSION_MIN_IPHONEOS = 0x25
LC_VERSION_MIN_TVOS = 0x2F
LC_VERSION_MIN_WATCHOS = 0x30
LC_BUILD_VERSION = 0x32
PLATFORM_MACOS = 1
PLATFORMS = {1: 'macOS', 2: 'iOS', 3: 'tvOS', 4: 'watchOS', 5: 'bridgeOS', 6: 'Mac Catalyst',
             7: 'iOS simulator', 8: 'tvOS simulator', 9: 'watchOS simulator', 10: 'DriverKit',
             11: 'visionOS', 12: 'visionOS simulator'}

CPU_ARCH_ABI64 = 0x01000000
CPU_ARCH_ABI64_32 = 0x02000000
CPU_TYPE_X86 = 7
CPU_TYPE_ARM = 12
CPU_TYPE_POWERPC = 18
CPU_SUBTYPE_MASK = 0x00FFFFFF
CPU_SUBTYPE_ARM64E = 2

AR_MAGIC = b'!<arch>\n'

# A Java class file starts with the same CAFEBABE as a universal Mach-O;
# there the next word is the class file version (45 or more), here the
# number of slices, which no real file has more than a handful of.
MAX_FAT_ARCHS = 30


class MachOError(Exception):
    pass


def arch_name(cputype, cpusubtype):
    base = cputype & ~(CPU_ARCH_ABI64 | CPU_ARCH_ABI64_32)
    if base == CPU_TYPE_X86:
        return 'x86_64' if cputype & CPU_ARCH_ABI64 else 'i386'
    if base == CPU_TYPE_ARM:
        if cputype & CPU_ARCH_ABI64_32:
            return 'arm64_32'
        if cputype & CPU_ARCH_ABI64:
            return 'arm64e' if (cpusubtype & CPU_SUBTYPE_MASK) == CPU_SUBTYPE_ARM64E else 'arm64'
        return 'arm'
    if base == CPU_TYPE_POWERPC:
        return 'ppc64' if cputype & CPU_ARCH_ABI64 else 'ppc'
    return 'cputype-0x%x' % cputype


def version_string(v):
    """xxxx.yy.zz nibbles, as ld64 writes them; 11.0.0 prints as 11.0."""
    if v == 0:
        return 'n/a'
    major, minor, patch = v >> 16, (v >> 8) & 0xFF, v & 0xFF
    return '%d.%d.%d' % (major, minor, patch) if patch else '%d.%d' % (major, minor)


def parse_version(text):
    parts = text.split('.')
    if not 1 <= len(parts) <= 3 or not all(p.isdigit() for p in parts):
        raise ValueError('not a version: %r' % text)
    nums = [int(p) for p in parts] + [0] * (3 - len(parts))
    return (nums[0] << 16) | (nums[1] << 8) | nums[2]


def _read(f, offset, size):
    f.seek(offset)
    data = f.read(size)
    if len(data) != size:
        raise MachOError('truncated at offset %d' % offset)
    return data


def read_slice(f, offset, size):
    """One thin Mach-O image at offset: its architecture, file type and the
    minimum-OS load commands. Returns a dict."""
    magic = struct.unpack('>I', _read(f, offset, 4))[0]
    if magic in (MH_MAGIC, MH_MAGIC_64):
        e = '>'
    elif magic in (MH_CIGAM, MH_CIGAM_64):
        e = '<'
    else:
        raise MachOError('no Mach-O header at offset %d' % offset)
    is64 = magic in (MH_MAGIC_64, MH_CIGAM_64)
    hsize = 32 if is64 else 28
    cputype, cpusubtype, filetype, ncmds, sizeofcmds = struct.unpack(e + 'iiIII', _read(f, offset + 4, 20))
    if size is not None and hsize + sizeofcmds > size:
        raise MachOError('load commands run past the end of the slice')
    cmds = _read(f, offset + hsize, sizeofcmds)
    versions = []   # (platform, minos, sdk, load command)
    pos = 0
    for _ in range(ncmds):
        if pos + 8 > len(cmds):
            raise MachOError('load command %d runs past sizeofcmds' % len(versions))
        cmd, cmdsize = struct.unpack_from(e + 'II', cmds, pos)
        if cmdsize < 8 or pos + cmdsize > len(cmds):
            raise MachOError('bad load command size %d' % cmdsize)
        if cmd == LC_BUILD_VERSION:
            platform, minos, sdk = struct.unpack_from(e + 'III', cmds, pos + 8)
            versions.append((platform, minos, sdk, 'LC_BUILD_VERSION'))
        elif cmd == LC_VERSION_MIN_MACOSX:
            minos, sdk = struct.unpack_from(e + 'II', cmds, pos + 8)
            versions.append((PLATFORM_MACOS, minos, sdk, 'LC_VERSION_MIN_MACOSX'))
        elif cmd in (LC_VERSION_MIN_IPHONEOS, LC_VERSION_MIN_TVOS, LC_VERSION_MIN_WATCHOS):
            minos, sdk = struct.unpack_from(e + 'II', cmds, pos + 8)
            platform = {LC_VERSION_MIN_IPHONEOS: 2, LC_VERSION_MIN_TVOS: 3, LC_VERSION_MIN_WATCHOS: 4}[cmd]
            versions.append((platform, minos, sdk, 'LC_VERSION_MIN'))
        pos += cmdsize
    return {'arch': arch_name(cputype, cpusubtype), 'filetype': filetype, 'versions': versions}


def read_macho(path):
    """The slices of a Mach-O file (a list of dicts, see read_slice), or None
    when the file is not a Mach-O file. Raises MachOError when it starts like
    one but cannot be read."""
    with open(path, 'rb') as f:
        head = f.read(8)
        if len(head) < 8:
            return None
        magic, nfat = struct.unpack('>II', head)
        if magic in (FAT_MAGIC, FAT_MAGIC_64):
            if not 0 < nfat <= MAX_FAT_ARCHS:
                return None   # a Java class file
            fsize = os.fstat(f.fileno()).st_size
            entry, fmt = (32, '>iiQQ') if magic == FAT_MAGIC_64 else (20, '>iiII')
            table = _read(f, 8, entry * nfat)
            slices = []
            for i in range(nfat):
                _, _, offset, size = struct.unpack_from(fmt, table, i * entry)
                if offset + size > fsize:
                    raise MachOError('slice %d runs past the end of the file' % i)
                if _read(f, offset, min(size, 8)) == AR_MAGIC:
                    return None   # a universal static library: object files, never loaded
                slices.append(read_slice(f, offset, size))
            return slices
        if magic in (MH_MAGIC, MH_MAGIC_64, MH_CIGAM, MH_CIGAM_64):
            return [read_slice(f, 0, None)]
    return None


def walk(root, exclude):
    """Relative paths (/ separators) of the regular files under root, sorted,
    without symbolic links and excluded paths."""
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = os.path.relpath(dirpath, root).replace(os.sep, '/')
        rel_dir = '' if rel_dir == '.' else rel_dir + '/'
        # Do not descend into excluded folders (for example '*.dSYM')
        dirnames[:] = sorted(d for d in dirnames
                             if not os.path.islink(os.path.join(dirpath, d))
                             and not any(fnmatch.fnmatchcase(rel_dir + d, p) for p in exclude))
        for name in filenames:
            rel = rel_dir + name
            full = os.path.join(dirpath, name)
            if os.path.islink(full) or not os.path.isfile(full):
                continue
            if any(fnmatch.fnmatchcase(rel, p) for p in exclude):
                continue
            found.append(rel)
    return sorted(found)


def check(root, minos, sdk=None, arch=None, allow=None, exclude=(), only_arch=None, file_minos=()):
    """Check every Mach-O file under root. minos: {arch: encoded version};
    sdk: encoded version or None; arch: required architecture or None;
    allow: {pattern: reason}; only_arch: {pattern: arch}; file_minos:
    [(pattern, arch, encoded version)]. Returns (rows, problems, allowed,
    highest): rows are (path, arch, file type, platform, minos, sdk,
    source) for the report, problems and allowed are lists of (path,
    message), and highest maps each architecture to (minos, path)."""
    allow = allow or {}
    only_arch = only_arch or {}
    rows, problems, allowed = [], [], []
    highest = {}
    used = set()
    for rel in walk(root, exclude):
        try:
            slices = read_macho(os.path.join(root, rel))
        except (OSError, MachOError, struct.error) as e:
            problems.append((rel, 'cannot be read as Mach-O: %s' % e))
            continue
        if slices is None or all(s['filetype'] == MH_DSYM for s in slices):
            continue
        reason = next((r for p, r in allow.items() if fnmatch.fnmatchcase(rel, p)), None)
        found = []
        archs = [s['arch'] for s in slices]
        single = next(((p, a) for p, a in only_arch.items() if fnmatch.fnmatchcase(rel, p)), None)
        if single is not None:
            used.add(('--only-arch', single[0]))
            if archs != [single[1]]:
                found.append('holds %s, expected only %s' % (', '.join(archs), single[1]))
        elif arch and arch not in archs:
            found.append('has no %s slice (only %s)' % (arch, ', '.join(archs)))
        want = dict(minos)
        for p, a, v in file_minos:
            if fnmatch.fnmatchcase(rel, p):
                used.add(('--file-minos', p))
                want[a] = v
        for s in slices:
            ftype = FILE_TYPES.get(s['filetype'], 'type-%d' % s['filetype'])
            mac = [v for v in s['versions'] if v[0] == PLATFORM_MACOS]
            if not s['versions']:
                rows.append((rel, s['arch'], ftype, '-', '-', '-', '-'))
                found.append('%s: records no minimum OS version (no LC_BUILD_VERSION or '
                             'LC_VERSION_MIN_MACOSX)' % s['arch'])
                continue
            for platform, v_min, v_sdk, source in s['versions']:
                rows.append((rel, s['arch'], ftype, PLATFORMS.get(platform, str(platform)),
                             version_string(v_min), version_string(v_sdk), source))
            if not mac:
                found.append('%s: built for %s, not macOS' % (
                    s['arch'], ', '.join(PLATFORMS.get(v[0], str(v[0])) for v in s['versions'])))
                continue
            _, v_min, v_sdk, _ = mac[0]
            if s['arch'] not in highest or v_min > highest[s['arch']][0]:
                highest[s['arch']] = (v_min, rel)
            if s['arch'] not in want:
                found.append('%s: unexpected architecture' % s['arch'])
            elif v_min != want[s['arch']]:
                found.append('%s: minos %s, expected %s' % (
                    s['arch'], version_string(v_min), version_string(want[s['arch']])))
            if sdk is not None and v_sdk != sdk:
                found.append('%s: sdk %s, expected %s' % (s['arch'], version_string(v_sdk), version_string(sdk)))
        for message in found:
            (allowed if reason is not None else problems).append(
                (rel, message + (' (allowed: %s)' % reason if reason is not None else '')))
    for option, p in sorted(set([('--only-arch', p) for p in only_arch] +
                                [('--file-minos', p) for p, _, _ in file_minos]) - used):
        problems.append((root, '%s %s matches no Mach-O file' % (option, p)))
    return rows, problems, allowed, highest


def _pair(text, what):
    key, sep, value = text.partition('=')
    if not sep or not key or not value:
        raise argparse.ArgumentTypeError('expected %s, got %r' % (what, text))
    return key, value


def report_problems(problems, title):
    """As error annotations under GitHub Actions (on stdout, after the
    report, so that the log keeps its order), else on stderr."""
    sys.stdout.flush()
    for rel, message in problems:
        if os.environ.get('GITHUB_ACTIONS') == 'true':
            print('::error title=%s::%s: %s' % (title, rel, message))
        else:
            sys.stderr.write('error: %s: %s\n' % (rel, message))
    sys.stdout.flush()


def main(argv=None):
    p = argparse.ArgumentParser(
        description='Check the minimum macOS and SDK version of every Mach-O file under a folder.')
    p.add_argument('root', help='folder to check (for example _build/mac/Release)')
    p.add_argument('--minos', action='append', default=[], metavar='ARCH=VERSION',
                   type=lambda s: _pair(s, 'ARCH=VERSION'),
                   help='the minos every ARCH slice must record (repeat per architecture)')
    p.add_argument('--sdk', metavar='VERSION', help='the SDK version every slice must record')
    p.add_argument('--arch', help='every file must hold a slice of this architecture')
    p.add_argument('--allow', action='append', default=[], metavar='PATH=REASON',
                   type=lambda s: _pair(s, 'PATH=REASON'),
                   help='report PATH (fnmatch pattern, relative to root) but do not fail on it')
    p.add_argument('--only-arch', action='append', default=[], metavar='PATH=ARCH',
                   type=lambda s: _pair(s, 'PATH=ARCH'),
                   help='files matching PATH must hold exactly one slice, of ARCH (instead of --arch)')
    p.add_argument('--file-minos', action='append', default=[], metavar='PATH=ARCH:VERSION',
                   type=lambda s: _pair(s, 'PATH=ARCH:VERSION'),
                   help='ARCH slices of files matching PATH must record VERSION (instead of --minos)')
    p.add_argument('--exclude', action='append', default=[], metavar='PATTERN',
                   help='skip paths matching PATTERN (relative to root)')
    args = p.parse_args(argv)

    if not os.path.isdir(args.root):
        sys.stderr.write('error: %s is not a folder\n' % args.root)
        return 2
    try:
        minos = {a: parse_version(v) for a, v in args.minos}
        sdk = parse_version(args.sdk) if args.sdk else None
        file_minos = []
        for p, value in args.file_minos:
            a, sep, v = value.partition(':')
            if not sep or not a or not v:
                raise ValueError('--file-minos expects PATH=ARCH:VERSION, got %s=%s' % (p, value))
            file_minos.append((p, a, parse_version(v)))
    except ValueError as e:
        sys.stderr.write('error: %s\n' % e)
        return 2

    rows, problems, allowed, highest = check(args.root, minos, sdk, args.arch, dict(args.allow), args.exclude,
                                             dict(args.only_arch), file_minos)

    widths = [max([len(h)] + [len(r[i]) for r in rows]) for i, h in enumerate(('path', 'arch', 'type'))]
    print('%-*s  %-*s  %-*s  platform  minos   sdk     from' % (widths[0], 'path', widths[1], 'arch',
                                                                  widths[2], 'type'))
    for r in rows:
        print('%-*s  %-*s  %-*s  %-8s  %-6s  %-6s  %s' % (widths[0], r[0], widths[1], r[1], widths[2], r[2],
                                                          r[3], r[4], r[5], r[6]))
    files = len(set(r[0] for r in rows))
    print('')
    print('%d Mach-O files checked under %s' % (files, args.root))
    for a in sorted(highest):
        print('Highest minos (%s): %s, in %s' % (a, version_string(highest[a][0]), highest[a][1]))
    for rel, message in allowed:
        print('allowed: %s: %s' % (rel, message))
    if not rows and not problems:
        problems.append((args.root, 'no Mach-O file found; is this the build output folder?'))
    report_problems(problems, 'Mach-O floor')
    if problems:
        return 1
    expected = ', '.join('%s %s' % (a, version_string(v)) for a, v in sorted(minos.items()))
    print('OK: every file%s records minos %s%s' % (
        ' but the %d allowed above' % len(set(rel for rel, _ in allowed)) if allowed else '',
        expected or '(not checked)', ', sdk %s' % version_string(sdk) if sdk is not None else ''))
    return 0


if __name__ == '__main__':
    sys.exit(main())
