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

"""Read what packaging and the CI checks need from ELF (Linux) and Mach-O
(macOS) binaries, with the Python standard library only, so that the same
checks run on any host (a Windows or Linux machine can check a macOS
build, which has no otool there). Of Windows (PE) files only the machine
is read (pe_arch), for the checks of the Windows standalone runtimes:

  the architecture(s)     ELF e_machine; Mach-O cputype of each slice of a
                          universal ("fat") file
  the libraries needed    ELF DT_NEEDED; Mach-O LC_LOAD_DYLIB,
                          LC_LOAD_WEAK_DYLIB, LC_REEXPORT_DYLIB,
                          LC_LAZY_LOAD_DYLIB and LC_LOAD_UPWARD_DYLIB
  the run paths           ELF DT_RPATH / DT_RUNPATH; Mach-O LC_RPATH
  its own name            ELF DT_SONAME; Mach-O LC_ID_DYLIB
  the OS floor            ELF: the newest symbol version needed per
                          prefix (GLIBC_2.38, GLIBCXX_3.4.30, OPENSSL_3.0.0)
                          from DT_VERNEED; Mach-O: the minimum macOS of
                          LC_BUILD_VERSION or LC_VERSION_MIN_MACOSX

WHY a parser of our own: readelf and otool are not on every machine that
packages (otool only exists on macOS), and a check that silently needs a
tool the runner lacks would pass by doing nothing.

  python tools/oxt/binfmt.py FILE...     prints what it reads, one block
                                         per file (for looking at a build)
"""

import collections
import struct
import sys

ELF_MAGIC = b'\x7fELF'
# e_machine -> the architecture names used in platform ids and folders
ELF_MACHINES = {3: 'x86', 62: 'x86_64', 40: 'arm', 183: 'arm64'}

# PE (COFF) Machine -> the same names as ELF_MACHINES
PE_MACHINES = {0x14c: 'x86', 0x8664: 'x86_64', 0xaa64: 'arm64', 0x1c4: 'arm'}

# Mach-O cputype -> name (lipo -archs spelling)
MACHO_CPUS = {7: 'i386', 0x01000007: 'x86_64', 12: 'arm', 0x0100000c: 'arm64', 0x0200000c: 'arm64_32',
              18: 'ppc', 0x01000012: 'ppc64'}
MH_MAGIC, MH_MAGIC_64 = 0xfeedface, 0xfeedfacf
FAT_MAGIC, FAT_MAGIC_64 = 0xcafebabe, 0xcafebabf

LC_ID_DYLIB = 0xd
LC_DYLIB_LOADS = {0xc: 'load', 0x80000018: 'weak', 0x8000001f: 'reexport', 0x20: 'lazy', 0x80000023: 'upward'}
LC_RPATH = 0x8000001c
LC_VERSION_MIN_MACOSX = 0x24
LC_BUILD_VERSION = 0x32
LC_DYLD_CHAINED_FIXUPS = 0x80000034
PLATFORM_MACOS = 1

DT_NULL, DT_NEEDED, DT_STRTAB, DT_STRSZ, DT_SONAME, DT_RPATH, DT_RUNPATH = 0, 1, 5, 10, 14, 15, 29
DT_VERNEED, DT_VERNEEDNUM = 0x6ffffffe, 0x6fffffff
PT_LOAD, PT_DYNAMIC = 1, 2


class FormatError(Exception):
    pass


class Binary(object):
    """What was read from one file. For a universal Mach-O file, archs
    lists every slice and the other fields are the union over the slices
    (floors: the highest of them), since each slice must load on its
    own; slices holds one Binary per slice with that slice's own fields,
    for checks that must not mix them (an @rpath library found through an
    LC_RPATH of another slice does not load). A thin file or an ELF file
    has one slice, itself."""
    __slots__ = ('format', 'archs', 'needed', 'rpaths', 'own_name', 'floors', 'filetype', 'chained_fixups',
                 'slices')

    def __init__(self, fmt):
        self.format = fmt                 # 'elf' or 'macho'
        self.archs = []                   # ['x86_64'], ['x86_64', 'arm64']
        self.needed = []                  # library names or install names, in order, no duplicates
        self.rpaths = []
        self.own_name = None              # DT_SONAME or LC_ID_DYLIB
        self.floors = collections.OrderedDict()  # 'GLIBC' -> (2, 38); 'macOS' -> (11, 0)
        self.filetype = None              # ELF e_type / Mach-O filetype
        self.chained_fixups = False       # Mach-O LC_DYLD_CHAINED_FIXUPS (the Mac deployer rejects it)
        self.slices = [self]


def sniff(data):
    """'elf', 'macho', 'pe' or None from the first bytes of a file."""
    if data[:4] == ELF_MAGIC:
        return 'elf'
    if len(data) >= 8:
        be = struct.unpack_from('>I', data, 0)[0]
        le = struct.unpack_from('<I', data, 0)[0]
        if le in (MH_MAGIC, MH_MAGIC_64) or be in (MH_MAGIC, MH_MAGIC_64):
            return 'macho'
        # 0xcafebabe is also a Java class file's magic, whose next word is
        # its version (45 and up); a fat header's is its slice count
        if be in (FAT_MAGIC, FAT_MAGIC_64) and 0 < struct.unpack_from('>I', data, 4)[0] < 16:
            return 'macho'
    if data[:2] == b'MZ':
        return 'pe'
    return None


def read_file(path, limit=None):
    with open(path, 'rb') as f:
        return f.read() if limit is None else f.read(limit)


def parse(path):
    """Binary for an ELF or Mach-O file; FormatError for anything else."""
    data = read_file(path)
    kind = sniff(data)
    if kind == 'elf':
        return parse_elf(data)
    if kind == 'macho':
        return parse_macho(data)
    raise FormatError('not an ELF or Mach-O file')


def _cstr(data, off):
    end = data.find(b'\0', off)
    if off < 0 or off >= len(data) or end < 0:
        raise FormatError('string offset 0x%x out of range' % off)
    return data[off:end].decode('utf-8', 'replace')


def _version_tuple(text):
    try:
        return tuple(int(x) for x in text.split('.'))
    except ValueError:
        return None


def version_text(version):
    return '.'.join(str(x) for x in version) if version else ''


# ---------------------------------------------------------------------------
# ELF

def parse_elf(data):
    try:
        return _parse_elf(data)
    except (struct.error, IndexError) as e:
        raise FormatError('truncated or malformed ELF file: %s' % e)


def _parse_elf(data):
    if data[:4] != ELF_MAGIC:
        raise FormatError('no ELF header')
    wide = {1: False, 2: True}.get(data[4])
    order = {1: '<', 2: '>'}.get(data[5])
    if wide is None or order is None:
        raise FormatError('unknown ELF class or byte order')
    info = Binary('elf')
    e_type, machine = struct.unpack_from(order + 'HH', data, 16)
    info.filetype = e_type
    info.archs = [ELF_MACHINES.get(machine, 'machine-%d' % machine)]
    if wide:
        phoff = struct.unpack_from(order + 'Q', data, 32)[0]
        phentsize, phnum = struct.unpack_from(order + 'HH', data, 54)
    else:
        phoff = struct.unpack_from(order + 'I', data, 28)[0]
        phentsize, phnum = struct.unpack_from(order + 'HH', data, 42)

    loads, dynamic = [], None
    for i in range(phnum):
        o = phoff + i * phentsize
        if wide:
            p_type, _, p_offset, p_vaddr, _, p_filesz = struct.unpack_from(order + 'IIQQQQ', data, o)
        else:
            p_type, p_offset, p_vaddr, _, p_filesz = struct.unpack_from(order + 'IIIII', data, o)
        if p_type == PT_LOAD:
            loads.append((p_vaddr, p_offset, p_filesz))
        elif p_type == PT_DYNAMIC:
            dynamic = (p_offset, p_filesz)
    if dynamic is None:
        return info      # static executable: needs nothing

    def offset(vaddr):
        for va, off, size in loads:
            if va <= vaddr < va + size:
                return vaddr - va + off
        raise FormatError('address 0x%x is in no loaded segment' % vaddr)

    entry = struct.Struct(order + ('qQ' if wide else 'iI'))
    tags = []
    off, end = dynamic[0], dynamic[0] + dynamic[1]
    while off + entry.size <= end:
        tag, val = entry.unpack_from(data, off)
        if tag == DT_NULL:
            break
        tags.append((tag, val))
        off += entry.size
    values = dict(tags)
    if DT_STRTAB not in values:
        raise FormatError('dynamic section without a string table')
    strtab = offset(values[DT_STRTAB])
    for tag, val in tags:
        if tag == DT_NEEDED:
            name = _cstr(data, strtab + val)
            if name not in info.needed:
                info.needed.append(name)
        elif tag in (DT_RPATH, DT_RUNPATH):
            for p in _cstr(data, strtab + val).split(':'):
                if p and p not in info.rpaths:
                    info.rpaths.append(p)
        elif tag == DT_SONAME:
            info.own_name = _cstr(data, strtab + val)

    # Symbol versions needed (Elf_Verneed, then its Elf_Vernaux chain):
    # the newest version per prefix is the floor the library sets, which a
    # plain DT_NEEDED list does not show (glibc 2.38 and libssl.so.3 both
    # read as "libc.so.6" and "libssl.so.3")
    if DT_VERNEED in values:
        o = offset(values[DT_VERNEED])
        for _ in range(values.get(DT_VERNEEDNUM, 0)):
            _, cnt, _, aux, nxt = struct.unpack_from(order + 'HHIII', data, o)
            a = o + aux
            for _ in range(cnt):
                _, _, _, name, anext = struct.unpack_from(order + 'IHHII', data, a)
                _add_floor(info, _cstr(data, strtab + name))
                if not anext:
                    break
                a += anext
            if not nxt:
                break
            o += nxt
    return info


def _add_floor(info, symver):
    # GLIBC_2.38, GLIBCXX_3.4.30, CXXABI_1.3.13, GCC_7.0.0, OPENSSL_3.0.0;
    # GLIBC_PRIVATE and other names without a number say nothing
    prefix, _, number = symver.rpartition('_')
    version = _version_tuple(number)
    if not prefix or version is None:
        return
    if version > info.floors.get(prefix, ()):
        info.floors[prefix] = version


# ---------------------------------------------------------------------------
# Mach-O

def parse_macho(data):
    try:
        return _parse_macho(data)
    except (struct.error, IndexError) as e:
        raise FormatError('truncated or malformed Mach-O file: %s' % e)


def macho_slices(data):
    """[(cputype, offset, size)] of a thin or universal Mach-O file."""
    be = struct.unpack_from('>I', data, 0)[0]
    if be in (FAT_MAGIC, FAT_MAGIC_64):
        n = struct.unpack_from('>I', data, 4)[0]
        out = []
        for i in range(n):
            if be == FAT_MAGIC:
                cpu, _, off, size, _ = struct.unpack_from('>iiIII', data, 8 + 20 * i)
            else:
                cpu, _, off, size, _, _ = struct.unpack_from('>iiQQII', data, 8 + 32 * i)
            out.append((cpu & 0xffffffff, off, size))
        return out
    return [(None, 0, len(data))]


def _parse_macho(data):
    slices = [_parse_macho_slice(data[off:off + size], off) for _, off, size in macho_slices(data)]
    if len(slices) == 1:
        return slices[0]
    info = Binary('macho')
    info.slices = slices
    info.filetype = slices[0].filetype
    for sl in slices:
        info.archs.extend(sl.archs)
        for name in sl.needed:
            if name not in info.needed:
                info.needed.append(name)
        for path in sl.rpaths:
            if path not in info.rpaths:
                info.rpaths.append(path)
        if sl.own_name is not None:
            info.own_name = sl.own_name
        for key, version in sl.floors.items():
            if version > info.floors.get(key, ()):
                info.floors[key] = version
        info.chained_fixups = info.chained_fixups or sl.chained_fixups
    return info


def _parse_macho_slice(s, off):
    """Binary for one thin Mach-O image (off: its offset in the file, for
    messages)."""
    info = Binary('macho')
    magic_le = struct.unpack_from('<I', s, 0)[0]
    if magic_le in (MH_MAGIC, MH_MAGIC_64):
        order = '<'
    elif struct.unpack_from('>I', s, 0)[0] in (MH_MAGIC, MH_MAGIC_64):
        order = '>'
    else:
        raise FormatError('slice at 0x%x has no Mach-O header' % off)
    magic, cputype, _, filetype, ncmds, _ = struct.unpack_from(order + 'IiiIII', s, 0)
    cputype &= 0xffffffff
    info.archs.append(MACHO_CPUS.get(cputype, 'cpu-0x%x' % cputype))
    info.filetype = filetype
    o = 32 if magic == MH_MAGIC_64 else 28
    for _ in range(ncmds):
        cmd, cmdsize = struct.unpack_from(order + 'II', s, o)
        if cmdsize < 8:
            raise FormatError('load command of size %d' % cmdsize)
        if cmd in LC_DYLIB_LOADS:
            name = _cstr(s, o + struct.unpack_from(order + 'I', s, o + 8)[0])
            if name not in info.needed:
                info.needed.append(name)
        elif cmd == LC_ID_DYLIB:
            info.own_name = _cstr(s, o + struct.unpack_from(order + 'I', s, o + 8)[0])
        elif cmd == LC_RPATH:
            path = _cstr(s, o + struct.unpack_from(order + 'I', s, o + 8)[0])
            if path not in info.rpaths:
                info.rpaths.append(path)
        elif cmd == LC_BUILD_VERSION:
            platform, minos = struct.unpack_from(order + 'II', s, o + 8)
            if platform == PLATFORM_MACOS:
                _add_macos_floor(info, minos)
        elif cmd == LC_VERSION_MIN_MACOSX:
            _add_macos_floor(info, struct.unpack_from(order + 'I', s, o + 8)[0])
        elif cmd == LC_DYLD_CHAINED_FIXUPS:
            info.chained_fixups = True
        o += cmdsize
    return info


def _add_macos_floor(info, packed):
    version = (packed >> 16, (packed >> 8) & 0xff) + (((packed & 0xff),) if packed & 0xff else ())
    if version > info.floors.get('macOS', ()):
        info.floors['macOS'] = version


def macho_archs(path):
    """lipo -archs of a Mach-O file (read from the headers only), or None
    when it is not one."""
    data = read_file(path, 4096)
    if sniff(data) != 'macho':
        return None
    try:
        slices = macho_slices(data)
        if slices[0][0] is None:
            # thin: the cputype follows the magic, in the file's byte order
            order = '<' if struct.unpack_from('<I', data, 0)[0] in (MH_MAGIC, MH_MAGIC_64) else '>'
            slices = [(struct.unpack_from(order + 'i', data, 4)[0] & 0xffffffff, 0, 0)]
        return [MACHO_CPUS.get(c, 'cpu-0x%x' % c) for c, _, _ in slices]
    except (struct.error, IndexError):
        return None


def elf_arch(path):
    """Architecture of an ELF file from its header, or None."""
    data = read_file(path, 64)
    if data[:4] != ELF_MAGIC or len(data) < 20:
        return None
    order = {1: '<', 2: '>'}.get(data[5], '<')
    machine = struct.unpack_from(order + 'H', data, 18)[0]
    return ELF_MACHINES.get(machine, 'machine-%d' % machine)


def pe_arch(path):
    """Architecture of a Windows PE file (an .exe or .dll) from its COFF
    header, or None when it is not one: the "MZ" header's e_lfanew leads
    to "PE\0\0" and the Machine field after it."""
    data = read_file(path, 4096)
    if data[:2] != b'MZ' or len(data) < 64:
        return None
    off = struct.unpack_from('<I', data, 0x3c)[0]
    if off + 6 > len(data):
        data = read_file(path, off + 6)
    if len(data) < off + 6 or data[off:off + 4] != b'PE\0\0':
        return None
    machine = struct.unpack_from('<H', data, off + 4)[0]
    return PE_MACHINES.get(machine, 'machine-0x%x' % machine)


def arch_of(path):
    """(format, architectures) of an ELF, Mach-O or PE file, from its
    headers; (None, []) for anything else."""
    kind = sniff(read_file(path, 64))
    if kind == 'elf':
        return kind, [elf_arch(path)]
    if kind == 'macho':
        return kind, macho_archs(path) or []
    if kind == 'pe':
        a = pe_arch(path)
        return (kind, [a]) if a else (None, [])
    return None, []


def main(argv=None):
    paths = sys.argv[1:] if argv is None else argv
    if not paths:
        sys.stderr.write('usage: binfmt.py FILE...\n')
        return 2
    status = 0
    for p in paths:
        try:
            b = parse(p)
        except (OSError, FormatError) as e:
            print('%s: %s' % (p, e))
            status = 1
            continue
        print('%s: %s %s' % (p, b.format, ' '.join(b.archs)))
        if b.own_name:
            print('  name     %s' % b.own_name)
        for n in b.needed:
            print('  needs    %s' % n)
        for r in b.rpaths:
            print('  rpath    %s' % r)
        for k, v in b.floors.items():
            print('  floor    %s %s' % (k, version_text(v)))
        if b.chained_fixups:
            print('  chained fixups (LC_DYLD_CHAINED_FIXUPS)')
    return status


if __name__ == '__main__':
    sys.exit(main())
