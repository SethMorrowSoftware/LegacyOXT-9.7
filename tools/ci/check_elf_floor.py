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

"""Check the oldest glibc and libstdc++ that every ELF file of a build
output needs.

Walks ROOT (for CI, linux-<arch>-bin) and reads the symbol version needs
(.gnu.version_r, the table the dynamic loader checks at load time) of every
ELF file: executables and shared objects alike, in every folder
(Externals/CEF, packaged_extensions/*/code, ...). A file that needs
GLIBC_2.32 does not load on a system with glibc 2.31, whatever the rest of
the product needs, so the highest version any one file needs is the real
minimum of the product. Everything built in the build container can only
need what the container's libraries have; the check catches prebuilt
files (CEF, extension libraries) and a change of build image.

    check_elf_floor.py ROOT --max GLIBC=2.31 --max GLIBCXX=3.4.28
                       [--machine x86_64] [--exclude '*.dbg']
                       [--allow PATH=REASON]

--max NAME=VERSION    no file may need a version NAME_x newer than VERSION
                      (GLIBC for libc, libm, libpthread, libdl and the
                      loader; GLIBCXX and CXXABI for libstdc++; GCC for
                      libgcc_s). A private version of that name (such as
                      GLIBC_PRIVATE) fails too: it ties the file to one
                      build of the library. glibc's loader feature markers
                      count as the release that added them
                      (GLIBC_ABI_DT_RELR as GLIBC_2.36), and
                      CXXABI_FLOAT128, a public version, as CXXABI_1.3.9.
                      Names without --max are only reported.
--machine ARCH        every file must be for ARCH (x86_64, arm64 or
                      aarch64, x86, arm)
--exclude PATTERN     skip matching paths (relative to ROOT, / separators,
                      fnmatch patterns), for example '*.dbg': the debug
                      information that objcopy --only-keep-debug splits
                      off is ELF too, but holds no code
--allow PATH=REASON   report PATH (pattern as for --exclude) but never
                      fail on it; the reason is printed with it

The version needs are read from the section table, or, when a file has
none (sstrip), from the dynamic segment, so neither readelf nor objdump is
needed. Symbolic links are not followed, so every file is read once.

Prints one line per file with the highest version it needs of each name,
then the highest per name over all files. Exit code 0 when every file is
within the limits, 1 when one is not (or when ROOT holds no ELF file at
all, which means a wrong ROOT), 2 on a usage error. Problems go to stderr,
or under GitHub Actions (GITHUB_ACTIONS=true) to stdout as ::error::
annotations.

Only the Python 3 standard library is used (3.8 or newer: the Ubuntu 20.04
build container has 3.8).
"""

import argparse
import fnmatch
import os
import re
import struct
import sys

ELF_MAGIC = b'\x7fELF'
ELFCLASS32, ELFCLASS64 = 1, 2
ELFDATA2LSB, ELFDATA2MSB = 1, 2
SHT_GNU_VERNEED = 0x6FFFFFFE
PT_LOAD, PT_DYNAMIC = 1, 2
DT_NULL, DT_STRTAB, DT_STRSZ = 0, 5, 10
DT_VERNEED, DT_VERNEEDNUM = 0x6FFFFFFE, 0x6FFFFFFF
# readelf's names; DYN is a shared object or a position-independent executable
ELF_TYPES = {1: 'REL', 2: 'EXEC', 3: 'DYN', 4: 'CORE'}
MACHINES = {3: 'x86', 40: 'arm', 62: 'x86_64', 183: 'aarch64'}
MACHINE_ALIASES = {'arm64': 'aarch64', 'amd64': 'x86_64', 'i386': 'x86', 'i686': 'x86'}

# NAME_1.2.3 (glibc, libstdc++, libgcc_s), NAME_1_1_0 (OpenSSL) or
# NAME_0.9.0rc4 (ALSA). A name without a number after it, such as
# GLIBC_PRIVATE, is a private version, but for those of the tables below.
VERSION_RE = re.compile(r'^(?P<name>[A-Za-z][A-Za-z0-9_]*?)_(?P<version>[0-9][0-9A-Za-z._]*)$')

# glibc's unnumbered marker versions: the linker adds one when the output
# uses a loader feature that older glibc lacks, and the loader refuses a
# file that needs one it does not define ("version `GLIBC_ABI_DT_RELR' not
# found"). Each counts as the numbered version of the release that added it
# (upstream; distributions may backport them to older releases). Without
# this, GLIBC_ABI_DT_RELR would be filed under a name GLIBC_ABI_DT that no
# --max limits, and a prebuilt library linked on a new distribution with
# -z pack-relative-relocs would pass whenever its symbols need no newer
# glibc, although glibc 2.31 refuses to load it.
ABI_MARKERS = {
    'GLIBC_ABI_DT_RELR': ('GLIBC', '2.36'),        # ld -z pack-relative-relocs
    'GLIBC_ABI_DT_X86_64_PLT': ('GLIBC', '2.42'),  # ld -z mark-plt
    'GLIBC_ABI_GNU_TLS': ('GLIBC', '2.42'),        # i386 __tls_get_addr
    'GLIBC_ABI_GNU2_TLS': ('GLIBC', '2.42'),       # TLS descriptors
}

# Unnumbered versions that are public all the same, with the numbered
# version of the same name that first came with them: a file that needs one
# is checked as if it needed that. libstdc++ keeps the typeinfo of
# __float128 (typeid(__float128), x86 only) in CXXABI_FLOAT128 since GCC 5
# (CXXABI_1.3.9), so the GCC 10 libstdc++ of Ubuntu 20.04 has it; filed as
# a private version it would fail --max CXXABI for a file that loads there.
PUBLIC_UNNUMBERED = {'CXXABI_FLOAT128': ('CXXABI', '1.3.9')}


class ElfError(Exception):
    pass


def version_key(text):
    """2.31 -> (2, 31); 1_1_0 -> (1, 1, 0); 0.9.0rc4 -> (0, 9, 0, 4)."""
    return tuple(int(p) for p in re.findall(r'[0-9]+', text))


class _Elf(object):
    def __init__(self, f):
        self.f = f
        ident = self.read(0, 16)
        if ident[:4] != ELF_MAGIC:
            raise ElfError('not an ELF file')
        if ident[4] not in (ELFCLASS32, ELFCLASS64) or ident[5] not in (ELFDATA2LSB, ELFDATA2MSB):
            raise ElfError('unknown ELF class %d or data encoding %d' % (ident[4], ident[5]))
        self.is64 = ident[4] == ELFCLASS64
        self.e = '<' if ident[5] == ELFDATA2LSB else '>'
        if self.is64:
            fields = struct.unpack(self.e + 'HHIQQQIHHHHHH', self.read(16, 48))
        else:
            fields = struct.unpack(self.e + 'HHIIIIIHHHHHH', self.read(16, 36))
        (self.type, self.machine, _, _, self.phoff, self.shoff, _, _,
         self.phentsize, self.phnum, self.shentsize, self.shnum, _) = fields

    def read(self, offset, size):
        self.f.seek(offset)
        data = self.f.read(size)
        if len(data) != size:
            raise ElfError('truncated at offset %d' % offset)
        return data

    def sections(self):
        """(type, offset, size, link, info) of every section."""
        if not self.shoff:
            return []
        fmt = self.e + ('IIQQQQIIQQ' if self.is64 else 'IIIIIIIIII')
        count = self.shnum
        if count == 0:
            # Extended numbering: the count is in section 0's sh_size
            count = struct.unpack(fmt, self.read(self.shoff, struct.calcsize(fmt)))[5]
        table = self.read(self.shoff, self.shentsize * count)
        result = []
        for i in range(count):
            _, sh_type, _, _, off, size, link, info, _, _ = struct.unpack_from(fmt, table, i * self.shentsize)
            result.append((sh_type, off, size, link, info))
        return result

    def segments(self):
        """(type, offset, vaddr, filesz) of every program header."""
        if not self.phoff:
            return []
        table = self.read(self.phoff, self.phentsize * self.phnum)
        result = []
        for i in range(self.phnum):
            if self.is64:
                p_type, _, off, vaddr, _, filesz, _, _ = struct.unpack_from(self.e + 'IIQQQQQQ', table,
                                                                           i * self.phentsize)
            else:
                p_type, off, vaddr, _, filesz, _, _, _ = struct.unpack_from(self.e + 'IIIIIIII', table,
                                                                           i * self.phentsize)
            result.append((p_type, off, vaddr, filesz))
        return result

    def verneed(self, data, strtab, count):
        """Walk Elf_Verneed / Elf_Vernaux (the same 16-byte layout in 32- and
        64-bit files). Returns [(library, version name)]."""
        def string(offset):
            end = strtab.find(b'\0', offset)
            if offset >= len(strtab) or end < 0:
                raise ElfError('string offset %d outside the string table' % offset)
            return strtab[offset:end].decode('ascii', 'replace')

        needs = []
        pos = 0
        for _ in range(count):
            if pos + 16 > len(data):
                raise ElfError('version needs run past their section')
            _, cnt, vn_file, vn_aux, vn_next = struct.unpack_from(self.e + 'HHIII', data, pos)
            library = string(vn_file)
            aux = pos + vn_aux
            for _ in range(cnt):
                if aux + 16 > len(data):
                    raise ElfError('version needs run past their section')
                _, _, _, vna_name, vna_next = struct.unpack_from(self.e + 'IHHII', data, aux)
                needs.append((library, string(vna_name)))
                if not vna_next:
                    break
                aux += vna_next
            if not vn_next:
                break
            pos += vn_next
        return needs

    def needs_from_sections(self):
        sections = self.sections()
        for sh_type, off, size, link, info in sections:
            if sh_type == SHT_GNU_VERNEED:
                if link >= len(sections):
                    raise ElfError('.gnu.version_r links to a missing string table')
                _, str_off, str_size, _, _ = sections[link]
                return self.verneed(self.read(off, size), self.read(str_off, str_size), info)
        return None if not sections else []

    def needs_from_dynamic(self):
        segments = self.segments()
        loads = [s for s in segments if s[0] == PT_LOAD]

        def file_range(vaddr):
            """The file offset of vaddr and of the end of its segment's
            file image (the dynamic tags give no size for the table)."""
            for _, off, start, filesz in loads:
                if start <= vaddr < start + filesz:
                    return off + (vaddr - start), off + filesz
            raise ElfError('address 0x%x is not in a loaded segment' % vaddr)

        for p_type, off, _, filesz in segments:
            if p_type != PT_DYNAMIC:
                continue
            fmt = self.e + ('qQ' if self.is64 else 'iI')
            step = struct.calcsize(fmt)
            dyn = self.read(off, filesz)
            tags = {}
            for i in range(0, len(dyn) - step + 1, step):
                tag, val = struct.unpack_from(fmt, dyn, i)
                if tag == DT_NULL:
                    break
                tags.setdefault(tag, val)
            if DT_VERNEED not in tags:
                return []
            str_start, _ = file_range(tags[DT_STRTAB])
            strtab = self.read(str_start, tags[DT_STRSZ])
            start, end = file_range(tags[DT_VERNEED])
            return self.verneed(self.read(start, end - start), strtab, tags.get(DT_VERNEEDNUM, 0))
        return []


def read_elf(path, via_dynamic=False):
    """None for a file that is not ELF, else a dict with the machine, the
    file type and needs: [(library, version name)]. via_dynamic reads the
    dynamic segment even when the file has a section table (for tests)."""
    with open(path, 'rb') as f:
        if f.read(4) != ELF_MAGIC:
            return None
        elf = _Elf(f)
        needs = None if via_dynamic else elf.needs_from_sections()
        if needs is None:
            needs = elf.needs_from_dynamic()
        return {'machine': MACHINES.get(elf.machine, 'machine-%d' % elf.machine),
                'type': ELF_TYPES.get(elf.type, 'type-%d' % elf.type),
                'needs': needs}


def walk(root, exclude):
    """Relative paths (/ separators) of the regular files under root, sorted,
    without symbolic links and excluded paths."""
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = os.path.relpath(dirpath, root).replace(os.sep, '/')
        rel_dir = '' if rel_dir == '.' else rel_dir + '/'
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


def highest_per_name(needs):
    """({name: (version text, version name)}, {name: [private version names]}):
    the highest numbered version needed of each name (GLIBC: ('2.29',
    'GLIBC_2.29'); a marker of ABI_MARKERS counts as its release, GLIBC:
    ('2.36', 'GLIBC_ABI_DT_RELR'), and a version of PUBLIC_UNNUMBERED as
    its numbered one), and the other unnumbered ones such as
    GLIBC_PRIVATE."""
    result = {}
    private = {}
    for _, vname in needs:
        m = VERSION_RE.match(vname)
        if vname in ABI_MARKERS:
            name, version = ABI_MARKERS[vname]
        elif vname in PUBLIC_UNNUMBERED:
            name, version = PUBLIC_UNNUMBERED[vname]
        elif m:
            name, version = m.group('name'), m.group('version')
        else:
            # Filed under the name before the first '_' (GLIBC_PRIVATE and
            # a marker newer than ABI_MARKERS, GLIBC_ABI_*, both under
            # GLIBC), so that --max GLIBC fails every one of them
            name = vname.split('_', 1)[0]
            if vname not in private.setdefault(name, []):
                private[name].append(vname)
            continue
        if name not in result or version_key(version) > version_key(result[name][0]):
            result[name] = (version, vname)
    return result, private


def check(root, maxima, machine=None, exclude=(), allow=None):
    """Check every ELF file under root. maxima: {name: version text}.
    Returns (rows, problems, allowed, highest): rows are (path, machine,
    type, {name: version name}) for the report, problems and allowed are
    lists of (path, message), highest maps each name to (version, path)."""
    allow = allow or {}
    rows, problems, allowed = [], [], []
    highest = {}
    for rel in walk(root, exclude):
        try:
            info = read_elf(os.path.join(root, rel))
        except (OSError, ElfError, struct.error) as e:
            problems.append((rel, 'cannot be read as ELF: %s' % e))
            continue
        if info is None:
            continue
        reason = next((r for p, r in allow.items() if fnmatch.fnmatchcase(rel, p)), None)
        found = []
        if machine and info['machine'] != machine:
            found.append('is for %s, not %s' % (info['machine'], machine))
        per_name, private = highest_per_name(info['needs'])
        rows.append((rel, info['machine'], info['type'],
                     dict([(n, v[1]) for n, v in per_name.items()] +
                          [(v, v) for vs in private.values() for v in vs])))
        for name, (version, vname) in per_name.items():
            if name not in highest or version_key(version) > version_key(highest[name][0]):
                highest[name] = (version, rel)
            if name in maxima and version_key(version) > version_key(maxima[name]):
                libs = sorted(set(lib for lib, v in info['needs'] if v == vname))
                found.append('needs %s (from %s), newer than %s_%s' % (vname, ', '.join(libs), name, maxima[name]))
        for name, vnames in private.items():
            if name in maxima:
                for vname in vnames:
                    found.append('needs %s, which only one build of the library has' % vname)
        for message in found:
            (allowed if reason is not None else problems).append(
                (rel, message + (' (allowed: %s)' % reason if reason is not None else '')))
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
        description='Check the glibc and libstdc++ versions every ELF file under a folder needs.')
    p.add_argument('root', help='folder to check (for example linux-x86_64-bin)')
    p.add_argument('--max', action='append', default=[], metavar='NAME=VERSION',
                   type=lambda s: _pair(s, 'NAME=VERSION'),
                   help='the newest version NAME_x any file may need (repeat per name)')
    p.add_argument('--machine', help='every file must be for this architecture')
    p.add_argument('--exclude', action='append', default=[], metavar='PATTERN',
                   help='skip paths matching PATTERN (relative to root)')
    p.add_argument('--allow', action='append', default=[], metavar='PATH=REASON',
                   type=lambda s: _pair(s, 'PATH=REASON'),
                   help='report PATH (fnmatch pattern, relative to root) but do not fail on it')
    args = p.parse_args(argv)

    if not os.path.isdir(args.root):
        sys.stderr.write('error: %s is not a folder\n' % args.root)
        return 2
    maxima = dict(args.max)
    for name, version in maxima.items():
        if not re.match(r'^[0-9]+(?:[._][0-9]+)*$', version):
            sys.stderr.write('error: --max %s=%s: not a version\n' % (name, version))
            return 2
    machine = MACHINE_ALIASES.get(args.machine, args.machine) if args.machine else None

    rows, problems, allowed, highest = check(args.root, maxima, machine, args.exclude, dict(args.allow))

    names = sorted(set(n for r in rows for n in r[3]), key=lambda n: (n not in maxima, n))
    width = max([len('path')] + [len(r[0]) for r in rows])
    print('%-*s  %-8s  %-4s  %s' % (width, 'path', 'machine', 'type', 'highest version needed'))
    for rel, mach, ftype, per_name in rows:
        print('%-*s  %-8s  %-4s  %s' % (width, rel, mach, ftype,
                                         ' '.join(per_name[n] for n in names if n in per_name) or '-'))
    print('')
    print('%d ELF files checked under %s' % (len(rows), args.root))
    for name in names:
        if name in highest:
            version, rel = highest[name]
            print('Highest %s: %s_%s, in %s%s' % (name, name, version, rel,
                                                  ' (limit %s_%s)' % (name, maxima[name]) if name in maxima else ''))
    for rel, message in allowed:
        print('allowed: %s: %s' % (rel, message))
    if not rows and not problems:
        problems.append((args.root, 'no ELF file found; is this the build output folder?'))
    report_problems(problems, 'ELF floor')
    if problems:
        return 1
    print('OK: no file%s needs more than %s' % (
        ' but the %d allowed above' % len(set(rel for rel, _ in allowed)) if allowed else '',
        ', '.join('%s_%s' % (n, v) for n, v in sorted(maxima.items())) or '(no limits given)'))
    return 0


if __name__ == '__main__':
    sys.exit(main())
