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

"""Write the distribution archives of a layout that tools/oxt/package.py
staged, the build output's binaries and symbols archives, and SHA256SUMS.

  python tools/oxt/package_dist.py (--summary FILE | --platform P --stage DIR)
      (--bin DIR | --bin-tar FILE | --no-binaries) [--symbols-tar FILE]
      --out DIR [--repo DIR] [--xtalk-sources [--xtalk-cache DIR] [--assets-cache DIR]]
      [--dmg] [--zip-level N] [--xz-preset N] [--no-hardlinks]
      [--summary-json FILE]

--summary is package.py's --summary-json, which gives the platform, the
stage folder and the build output (a --bin folder or a --bin-tar tarball);
the options override it. DIR of --stage is the LegacyOXT-<version> folder.
Written to --out, where <root> is LegacyOXT-<version>:

  win-x86_64
    <root>-win-x86_64-portable.zip   every staged file under <root>/, then
                                     an entry for every empty folder
    <root>-win-x86_64-binaries.zip   win-x86_64-bin/ without *.pdb, and the
                                     staged licence files at the top
    <root>-win-x86_64-symbols.zip    the *.pdb files under win-x86_64-bin/
  linux-<arch>
    <root>-linux-<arch>.tar.xz           the staged folder as <root>/
    <root>-linux-<arch>-binaries.tar.xz  linux-<arch>-bin/ without *.dbg and
                                         the build's own tools (see below),
                                         and the licence files at the top
    <root>-linux-<arch>-symbols.tar.xz   the *.dbg files under linux-<arch>-bin/
                                         (not the build tools')
  mac-<arch>
    <root>-mac-<arch>.dmg                with --dmg (macOS only): a disk
                                         image (hdiutil, UDZO, HFS+, volume
                                         "LegacyOXT <version>") holding
                                         the app and a link to
                                         /Applications to drag it onto
    <root>-mac-<arch>.zip                the app: on macOS written by
                                         "ditto -c -k --sequesterRsrc
                                         --keepParent", elsewhere by this
                                         script as ditto stores it
    <root>-mac-<arch>-binaries.tar.xz    Release/ (the build's _build/mac/
                                         Release, or the universal merge of
                                         two) without *.dSYM and the build's
                                         own tools (see below), and the
                                         licence files at the top
    <root>-mac-<arch>-symbols.zip        the *.dSYM bundles under Release/
                                         (not the build tools')
  all
    <root>-xtalk-sources.zip         with --xtalk-sources: every file the
                                     xTalk manifest pins (xtalk_extensions.py
                                     export, from the cache only)
    SHA256SUMS                       "<sha256>  <file>" for each archive, in
                                     the order binaries, disk image,
                                     package, symbols, sources; LF line
                                     endings

The Windows archives have the names and the entries (names, bytes, file
dates, folder entries) that tools/ci/package-windows.ps1 writes with .NET's
ZipArchive; the order of the entries can differ (PowerShell sorts by
culture rules, this by lower-case name), and so can the compressed bytes:
.NET's Optimal is Windows' own zlib build, which no level of Python's zlib
reproduces (level 9, the default here, comes closest in size). An empty
folder's entry gets the folder's date (.NET stamps the time it writes the
entry). The Windows CI keeps using package-windows.ps1 for now, which also
finds the Visual C++ runtime with vswhere.

Unix archives keep what package.py staged: modes, symbolic links and empty
folders, owner and group 0 with no names, file dates in whole seconds
(clamped to SOURCE_DATE_EPOCH when it is set), entries sorted. In a Linux
package, staged files of 64 KiB or more with the same content and mode are
stored once and hard-linked (--no-hardlinks stores each): the runtime's
copies of libcef.so and the rest of CEF are the same files as the IDE's.
Such a tarball must be extracted onto a file system that has hard links
(not FAT, which could not hold the package's modes anyway). The macOS
zip is ditto's own on macOS (it keeps everything the signed app has; a
signature lives inside its files, so nothing needs the "__MACOSX" side
files that --sequesterRsrc would write for extended attributes, which
tools/ci/sign_mac_app.py removes); elsewhere, or with OXT_PYTHON_ZIP=1,
this script writes one that stores folders, symbolic links (as links)
and each file's mode, as ditto does, so that the app keeps its
executables and bundle structure where the zip is extracted (ditto -x -k,
Archive Utility or unzip). They are written on Linux or macOS only; in
WSL the stage must be under a Linux path, since a Windows drive reports
every file as 0777 (a probe file next to the stage,
package.unix_tree_problem, refuses one).

--dmg (a macOS layout, on macOS) also writes the disk image: the app is
copied with ditto into a temporary folder next to a symbolic link to
/Applications, and "hdiutil create -format UDZO -fs HFS+" makes the
image of that folder (HFS+ rather than APFS: every macOS the app runs on
reads it, and it matches the case-insensitive names package.py checks
for). hdiutil sometimes fails with "Resource busy" while something
still scans the new files, so it is tried up to four times; then
"hdiutil verify" checks the image's checksum. Sign the app before this
step: the image and the zip hold it as it is.

A tarball given as --bin-tar is read once, as a stream: each member goes
to the binaries or the symbols archive under the platform's folder name
(win-x86_64-bin, linux-<arch>-bin, Release), without macOS tar's "._"
AppleDouble files; modes and links are kept, owners dropped. The CI keeps
the debug symbols out of its bin tarball, in a tarball of their own (the
same folder with only the *.dbg files, or the *.dSYM bundles):
--symbols-tar adds its files to the symbols archive, and fails on anything
in it that is not a debug symbol file or that the build output has too.

In the Linux package no path may have a folder named like a build output
(package.repository_mode_trap: _build, linux-bin, linux-<arch>-bin,
build-linux-<arch>), which would switch an engine on that path into
repository mode.

The Linux and macOS build outputs also hold the tools the build makes to
build itself (package.BUILD_PROGRAMS: gentle-target, reflex-target,
perfect-target, the lc-compile bootstrap stages, zic, lcidlc), which
package.py never installs. Their files, .dSYM bundles and .dbg files are
left out of the binaries and symbols archives, from a folder and from a
tarball alike, and counted in the log: gentle-target and reflex-target
are GENTLE 97, whose licence forbids redistributing it
(THIRD-PARTY-NOTICES.md "GENTLE"), and the Windows binaries zip holds
none of them either. Any entry named gentle-* or reflex-* that still
reaches an archive is an error, and no archive is written.

Under GitHub Actions it writes the step outputs version, package-root,
platform, dist-dir, package (the main archive), dmg (with --dmg) and
sha256sums, and a table of the archives to the job summary.

Only the Python 3 standard library is used.
"""

import argparse
import collections
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import package  # noqa: E402
import layout  # noqa: E402
import fetch_assets  # noqa: E402
import xtalk_extensions  # noqa: E402

HARDLINK_MIN_SIZE = 64 * 1024
Levels = collections.namedtuple('Levels', 'zip xz')

# Names that must never reach an archive, whatever the tables say: GENTLE
# 97 (gentle-target, and its reflex-target) may not be redistributed
GENTLE_PREFIXES = ('gentle-', 'reflex-')


class DistError(Exception):
    pass


def debug_file(family, parts):
    """True for the debug symbols that go into the symbols archive."""
    if family == 'windows':
        return parts[-1].lower().endswith('.pdb')
    if family == 'linux':
        return parts[-1].endswith('.dbg')
    return any(p.endswith('.dSYM') for p in parts)


def build_tool(rel):
    """True for a path of a Linux or macOS build output that belongs to
    one of the build's own tools (package.BUILD_PROGRAMS, at the top of
    the build output: gentle-target, reflex-target, perfect-target, the
    lc-compile bootstrap stages, zic, lcidlc), or to their debug symbols
    (X.dSYM/..., X.dbg). The binaries and symbols archives leave them out:
    the Windows binaries zip has no such tools either (package-windows.ps1
    takes the build's dist files), and GENTLE's licence forbids
    redistributing gentle-target and reflex-target."""
    top = rel.split('/')[0]
    names = [top]
    for suffix in ('.dSYM', '.dbg'):
        if top.endswith(suffix):
            names.append(top[:-len(suffix)])
    return any(layout._glob_match(pat, n) for pat, _ in package.BUILD_PROGRAMS for n in names)


def bin_top(p):
    """Folder name of the build output inside the archives: what a build
    of this repository makes (package-windows.ps1 always uses
    win-x86_64-bin), so an archive extracts to a build's layout."""
    return {'windows': '%s-bin' % p.name, 'linux': '%s-bin' % p.name}.get(p.family, 'Release')


def archive_names(p, root):
    """{kind: file name} in SHA256SUMS order (package-windows.ps1's)."""
    base = '%s-%s' % (root, p.name)
    binaries, package_, symbols = {
        'windows': ('-binaries.zip', '-portable.zip', '-symbols.zip'),
        'linux': ('-binaries.tar.xz', '.tar.xz', '-symbols.tar.xz'),
        'mac': ('-binaries.tar.xz', '.zip', '-symbols.zip'),
    }[p.family]
    names = collections.OrderedDict([('binaries', base + binaries), ('package', base + package_),
                                     ('symbols', base + symbols)])
    if p.family == 'mac':
        # the disk image (--dmg) goes before the zip of the same app
        names['dmg'] = base + '.dmg'
        names.move_to_end('dmg', last=False)
        names.move_to_end('binaries', last=False)
    return names


def _sort_key(name):
    return (name.lower(), name)


def walk(root):
    """(files, empty folders, links, folders) below root, as sorted
    relative paths with "/" separators; a link to a folder is not
    followed."""
    files, empty, links, folders = [], [], [], []
    for dirpath, dirnames, filenames in os.walk(root):
        rel = os.path.relpath(dirpath, root)
        rel = '' if rel == '.' else rel.replace(os.sep, '/') + '/'
        dlinks = [d for d in dirnames if os.path.islink(os.path.join(dirpath, d))]
        dirnames[:] = [d for d in dirnames if d not in dlinks]
        if rel:
            folders.append(rel.rstrip('/'))
        if rel and not dirnames and not filenames and not dlinks:
            empty.append(rel.rstrip('/'))
        for name in filenames + dlinks:
            (links if os.path.islink(os.path.join(dirpath, name)) else files).append(rel + name)
    return (sorted(files, key=_sort_key), sorted(empty, key=_sort_key), sorted(links, key=_sort_key),
            sorted(folders, key=_sort_key))


def _epoch():
    value = os.environ.get('SOURCE_DATE_EPOCH', '')
    return int(value) if value.isdigit() else None


def _mtime(st_mtime):
    t = int(st_mtime)
    epoch = _epoch()
    return min(t, epoch) if epoch is not None else t


# ---------------------------------------------------------------------------
# Zip

def _set_level(zi, level):
    """ZipFile.open(zinfo, 'w') compresses at the ZipInfo's own level, not
    the ZipFile's (Python 3.7-3.12 call it _compresslevel, 3.13 and later
    compress_level)."""
    try:
        zi.compress_level = level
    except AttributeError:
        zi._compresslevel = level
    return zi


def _zip_info_windows(path, name, level):
    """What .NET's CreateEntryFromFile records: the name, the file's local
    date, deflate. The size is known, so zipfile writes zip64 records only
    for a file that needs them, as .NET does."""
    zi = zipfile.ZipInfo.from_file(path, name)
    zi.compress_type = zipfile.ZIP_DEFLATED
    return _set_level(zi, level)


def write_zip_windows(out, entries, folders, level):
    """entries: [(file, name)]; folders: [(folder, name ending in '/')]."""
    tmp = out + '.part'
    with zipfile.ZipFile(tmp, 'w', zipfile.ZIP_DEFLATED, allowZip64=True, compresslevel=level) as z:
        for path, name in entries:
            with open(path, 'rb') as src, z.open(_zip_info_windows(path, name, level), 'w') as dst:
                _copy(src, dst)
        for path, name in folders:
            z.writestr(zipfile.ZipInfo(name, time.localtime(max(os.stat(path).st_mtime, 315532800))[:6]), b'')
    os.replace(tmp, out)
    return len(entries) + len(folders)


def _unix_zip_info(name, mode, mtime, kind, level=6, size=0):
    zi = zipfile.ZipInfo(name, time.localtime(max(mtime, 315532800))[:6])
    zi.create_system = 3
    zi.file_size = size
    _set_level(zi, level)
    if kind == 'dir':
        zi.external_attr = ((stat.S_IFDIR | mode) << 16) | 0x10
        zi.compress_type = zipfile.ZIP_STORED
    elif kind == 'link':
        zi.external_attr = (stat.S_IFLNK | 0o755) << 16
        zi.compress_type = zipfile.ZIP_STORED
    else:
        zi.external_attr = (stat.S_IFREG | mode) << 16
        zi.compress_type = zipfile.ZIP_DEFLATED
    return zi


class UnixZip(object):
    """A zip that keeps what ditto -c -k keeps: folder entries, each file's
    mode and symbolic links (the link target is the entry's data)."""

    def __init__(self, out, level):
        self.out = out
        self.tmp = out + '.part'
        self.level = level
        self.count = 0
        self.z = zipfile.ZipFile(self.tmp, 'w', zipfile.ZIP_DEFLATED, allowZip64=True, compresslevel=level)

    def add_path(self, path, name):
        st = os.lstat(path)
        mtime = _mtime(st.st_mtime)
        if stat.S_ISLNK(st.st_mode):
            self.z.writestr(_unix_zip_info(name, 0o755, mtime, 'link'), os.readlink(path).encode('utf-8'))
            self.count += 1
        elif stat.S_ISDIR(st.st_mode):
            self.z.writestr(_unix_zip_info(name.rstrip('/') + '/', stat.S_IMODE(st.st_mode), mtime, 'dir'), b'')
            self.count += 1
        else:
            with open(path, 'rb') as src:
                self.add_stream(src, name, stat.S_IMODE(st.st_mode), mtime, st.st_size)

    def add_stream(self, src, name, mode, mtime, size):
        with self.z.open(_unix_zip_info(name, mode, mtime, 'file', self.level, size), 'w') as dst:
            _copy(src, dst)
        self.count += 1

    def add_member(self, m, name, src):
        if m.isdir():
            self.z.writestr(_unix_zip_info(name.rstrip('/') + '/', m.mode & 0o7777, _mtime(m.mtime), 'dir'), b'')
            self.count += 1
        elif m.issym():
            self.z.writestr(_unix_zip_info(name, 0o755, _mtime(m.mtime), 'link'), m.linkname.encode('utf-8'))
            self.count += 1
        else:
            self.add_stream(src, name, m.mode & 0o7777, _mtime(m.mtime), m.size)

    def close(self):
        self.z.close()
        os.replace(self.tmp, self.out)

    def discard(self):
        try:
            self.z.close()
        except Exception:   # noqa: BLE001 - the archive is being thrown away
            pass
        _remove(self.tmp)


def _remove(path):
    try:
        os.remove(path)
    except OSError:
        pass


def _copy(src, dst):
    while True:
        b = src.read(1 << 20)
        if not b:
            return
        dst.write(b)


# ---------------------------------------------------------------------------
# Tar

class UnixTar(object):
    """A tar.xz in PAX format with owners 0 and no owner names, whole-second
    dates and modes as on disk; hard links for what add_path is told.

    The tar stream goes through the xz program with -T0 when there is one
    (Linux; macOS has none by default): Python's lzma uses one core, which
    makes the 1 GB Linux package and its symbols take many minutes. The
    result is a standard .tar.xz either way. OXT_PYTHON_XZ=1 forces
    Python's lzma."""

    def __init__(self, out, level):
        self.out = out
        self.tmp = out + '.part'
        self.proc = self.f = None
        xz = None if os.environ.get('OXT_PYTHON_XZ') == '1' else shutil.which('xz')
        if xz:
            self.f = open(self.tmp, 'wb')
            self.proc = subprocess.Popen([xz, '-T0', '-%d' % level, '-c'], stdin=subprocess.PIPE, stdout=self.f)
            self.t = tarfile.open(fileobj=self.proc.stdin, mode='w|', format=tarfile.PAX_FORMAT)
        else:
            self.t = tarfile.open(self.tmp, 'w:xz', format=tarfile.PAX_FORMAT, preset=level)
        self.names = set()
        self.count = 0

    def _info(self, name, mode, mtime):
        ti = tarfile.TarInfo(name)
        ti.mode = mode
        ti.mtime = _mtime(mtime)
        ti.uid = ti.gid = 0
        ti.uname = ti.gname = ''
        return ti

    def add_path(self, path, name, hardlink_to=None):
        st = os.lstat(path)
        ti = self._info(name, stat.S_IMODE(st.st_mode), st.st_mtime)
        if stat.S_ISLNK(st.st_mode):
            ti.type = tarfile.SYMTYPE
            ti.linkname = os.readlink(path)
            ti.mode = 0o777
            self.t.addfile(ti)
        elif stat.S_ISDIR(st.st_mode):
            ti.type = tarfile.DIRTYPE
            self.t.addfile(ti)
        elif hardlink_to is not None:
            ti.type = tarfile.LNKTYPE
            ti.linkname = hardlink_to
            self.t.addfile(ti)
        else:
            ti.size = st.st_size
            with open(path, 'rb') as f:
                self.t.addfile(ti, f)
        self.names.add(name)
        self.count += 1

    def add_member(self, m, name, src, linkname=None):
        """A member of another tarball under a new name (linkname: the new
        name of a hard link's target)."""
        ti = self._info(name, m.mode & 0o7777, m.mtime)
        ti.type = m.type
        if m.issym():
            ti.linkname = m.linkname
        elif m.islnk():
            if linkname not in self.names:
                raise DistError('hard link %s -> %s: its target is not in %s'
                                % (m.name, m.linkname, os.path.basename(self.out)))
            ti.linkname = linkname
        elif m.isfile():
            ti.size = m.size
        self.t.addfile(ti, src if m.isfile() else None)
        self.names.add(name)
        self.count += 1

    def close(self):
        self.t.close()
        if self.proc:
            # tarfile does not close a stream it was given
            self.proc.stdin.close()
            status = self.proc.wait()
            self.f.close()
            if status:
                raise DistError('xz failed with exit status %d writing %s' % (status, self.out))
        os.replace(self.tmp, self.out)

    def discard(self):
        """Stop and remove a half-written archive (after an error)."""
        if self.proc:
            self.proc.kill()
            self.proc.wait()
            self.f.close()
        else:
            try:
                self.t.close()
            except Exception:   # noqa: BLE001 - the archive is being thrown away
                pass
        _remove(self.tmp)


def hardlink_plan(stage, files, min_size=HARDLINK_MIN_SIZE):
    """{relative path: relative path of the first file with the same
    content and mode} for staged files of at least min_size bytes. (The
    date of a linked copy is the first file's; package.py stages the
    copies of one build output with the same date.)"""
    by_key = collections.defaultdict(list)
    for rel in files:
        st = os.lstat(os.path.join(stage, *rel.split('/')))
        if st.st_size >= min_size:
            # a hard link extracts with its target's mode (and date): link
            # only copies that will have the same mode, or a 0755 library
            # linked to a 0644 copy of the same bytes would lose its x bit
            by_key[(st.st_size, stat.S_IMODE(st.st_mode))].append(rel)
    out = {}
    for rels in by_key.values():
        if len(rels) < 2:
            continue
        first = {}
        for rel in rels:
            digest = _sha256(os.path.join(stage, *rel.split('/')))
            if digest in first:
                out[rel] = first[digest]
            else:
                first[digest] = rel
    return out


def _sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for b in iter(lambda: f.read(1 << 20), b''):
            h.update(b)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Archives

def write_package(p, stage, out, levels, hardlinks, log):
    """The platform's package of the staged folder. Returns the number of
    entries."""
    root = os.path.basename(stage)
    files, empty, links, folders = walk(stage)
    if p.family == 'windows':
        pdbs = [f for f in files if f.lower().endswith('.pdb')]
        if pdbs:
            raise DistError('a *.pdb file was staged: %s' % pdbs[0])
        if links:
            raise DistError('a Windows layout has a symbolic link: %s' % links[0])
        return write_zip_windows(out, [(os.path.join(stage, *f.split('/')), root + '/' + f) for f in files],
                                 [(os.path.join(stage, *d.split('/')), root + '/' + d + '/') for d in empty],
                                 levels.zip)
    if p.family == 'linux':
        # The tarball is extracted anywhere, and a folder of it named like a
        # build output would put the engine of an install into repository
        # mode if it were ever on the engine's path; there is none today
        for rel in folders + files + links:
            trap = package.repository_mode_trap(root + '/' + rel, p)
            if trap:
                raise DistError('%s has a folder named %s (%s): the package must have no folder name that '
                                'switches the engine into repository mode' % (stage, trap, rel))
        same = hardlink_plan(stage, files) if hardlinks else {}
        if same:
            saved = sum(os.path.getsize(os.path.join(stage, *r.split('/'))) for r in same)
            log('  %d staged files are stored as hard links to identical ones (%s bytes)'
                % (len(same), '{:,}'.format(saved)))
        t = UnixTar(out, levels.xz)
        try:
            t.add_path(stage, root)
            # Folders first, then files and links in name order: an
            # extractor creates a folder's mode before its content
            for d in folders:
                t.add_path(os.path.join(stage, *d.split('/')), root + '/' + d)
            for rel in sorted(files + links, key=_sort_key):
                target = same.get(rel)
                t.add_path(os.path.join(stage, *rel.split('/')), root + '/' + rel,
                           hardlink_to=(root + '/' + target) if target else None)
        except BaseException:
            t.discard()
            raise
        t.close()
        return t.count
    # macOS: the app alone, as ditto -c -k --keepParent <app> does
    app = os.path.join(stage, p.engine)
    if not os.path.isdir(app):
        raise DistError('%s has no %s' % (stage, p.engine))
    prefix = p.engine + '/'
    others = [f for f in files + links if not f.startswith(prefix)]
    if others:
        log('WARNING: not in the zip (outside %s): %s' % (p.engine, ', '.join(others[:5])))
    if use_ditto():
        # Apple's own writer on macOS, which is what the workflow and
        # Archive Utility users expect; it keeps what a signed app needs
        tmp = out + '.part'
        _remove(tmp)
        r = subprocess.run(['ditto', '-c', '-k', '--sequesterRsrc', '--keepParent', app, tmp],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        if r.returncode:
            _remove(tmp)
            raise DistError('ditto -c -k failed (exit status %d): %s'
                            % (r.returncode, r.stdout.decode('utf-8', 'replace').strip()))
        os.replace(tmp, out)
        with zipfile.ZipFile(out) as zf:
            names = zf.namelist()
        side = [n for n in names if n.startswith('__MACOSX/')]
        if side:
            log('WARNING: %d extended-attribute side files in %s (__MACOSX/, from --sequesterRsrc), for '
                'example %s: sign the app with tools/ci/sign_mac_app.py first, which removes them'
                % (len(side), os.path.basename(out), side[0]))
        return len(names)
    z = UnixZip(out, levels.zip)
    try:
        z.add_path(app, prefix)
        for d in folders:
            if d.startswith(prefix):
                z.add_path(os.path.join(stage, *d.split('/')), d + '/')
        for rel in sorted(files + links, key=_sort_key):
            if rel.startswith(prefix):
                z.add_path(os.path.join(stage, *rel.split('/')), rel)
    except BaseException:
        z.discard()
        raise
    z.close()
    return z.count


def use_ditto():
    """True on macOS with ditto (unless OXT_PYTHON_ZIP=1)."""
    return sys.platform == 'darwin' and shutil.which('ditto') is not None and os.environ.get('OXT_PYTHON_ZIP') != '1'


def write_dmg(p, stage, out, version, log, attempts=4):
    """The disk image of the staged app (see the module notes). Returns the
    number of files and links it holds."""
    for tool in ('ditto', 'hdiutil'):
        if sys.platform != 'darwin' or not shutil.which(tool):
            raise DistError('--dmg needs macOS (%s is not on PATH)' % tool)
    app = os.path.join(stage, p.engine)
    if not os.path.isdir(app):
        raise DistError('%s has no %s' % (stage, p.engine))
    volume = '%s %s' % (package.PRODUCT, version)
    src = tempfile.mkdtemp(prefix='oxt-dmg-')
    tmp = out[:-len('.dmg')] + '.part.dmg'     # hdiutil wants the suffix
    try:
        r = subprocess.run(['ditto', app, os.path.join(src, p.engine)], stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT)
        if r.returncode:
            raise DistError('ditto %s failed: %s' % (app, r.stdout.decode('utf-8', 'replace').strip()))
        os.symlink('/Applications', os.path.join(src, 'Applications'))
        cmd = ['hdiutil', 'create', '-volname', volume, '-srcfolder', src, '-fs', 'HFS+', '-format', 'UDZO',
               '-imagekey', 'zlib-level=9', '-ov', tmp]
        for attempt in range(1, attempts + 1):
            _remove(tmp)
            r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            text = r.stdout.decode('utf-8', 'replace').strip()
            if not r.returncode:
                break
            log('hdiutil create failed (attempt %d of %d, exit status %d): %s' % (attempt, attempts, r.returncode,
                                                                                  text))
            if attempt == attempts:
                raise DistError('hdiutil create failed %d times; the last time: %s' % (attempts, text))
            time.sleep(10 * attempt)
        r = subprocess.run(['hdiutil', 'verify', tmp], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        if r.returncode:
            raise DistError('hdiutil verify %s failed: %s' % (tmp, r.stdout.decode('utf-8', 'replace').strip()))
        os.replace(tmp, out)
        files, _, links, _ = walk(src)
        return len(files) + len(links)
    finally:
        _remove(tmp)
        shutil.rmtree(src, ignore_errors=True)


def licence_entries(p, stage):
    """The licence files of the binaries archives, from the repository root
    (the installed layout has LiveCode's own License Agreement.txt
    instead): [(file, name at the archive's top)]."""
    out = []
    for name in package.LICENCE_FILES:
        path = os.path.join(package.REPO, name)
        if not os.path.isfile(path):
            raise DistError('%s is missing from the repository root' % name)
        out.append((path, name))
    return out


class _Router(object):
    """Sends build output entries to the binaries or the symbols archive."""

    def __init__(self, p, bin_out, sym_out, levels):
        self.p = p
        self.top = bin_top(p)
        self.counts = collections.Counter()
        self.names = []                        # every entry's name, for the GENTLE guard
        self.symbol_paths = set()      # relative paths sent to the symbols archive
        if p.family == 'windows':
            self.bins, self.syms = [], []      # written at the end, in name order
            self.bin_out, self.sym_out, self.level = bin_out, sym_out, levels.zip
        else:
            self.bin = UnixTar(bin_out, levels.xz)
            self.sym = UnixTar(sym_out, levels.xz) if p.family == 'linux' else UnixZip(sym_out, levels.zip)

    def route(self, parts):
        return 'symbols' if debug_file(self.p.family, parts) else 'binaries'

    def add_path(self, path, rel):
        where = self.route(rel.split('/'))
        name = self.top + '/' + rel
        self.counts[where] += 0 if os.path.isdir(path) and not os.path.islink(path) else 1
        self.names.append(name)
        if where == 'symbols':
            self.symbol_paths.add(rel)
        if self.p.family == 'windows':
            (self.syms if where == 'symbols' else self.bins).append((path, name))
        else:
            (self.sym if where == 'symbols' else self.bin).add_path(path, name)

    def add_member(self, m, rel, src, linkname=None):
        where = self.route(rel.split('/'))
        name = self.top + '/' + rel if rel else self.top
        self.counts[where] += 0 if m.isdir() else 1
        self.names.append(name)
        if where == 'symbols':
            self.symbol_paths.add(rel)
        target = self.sym if where == 'symbols' else self.bin
        if isinstance(target, UnixZip):
            if m.islnk():
                raise DistError('hard link %s in the symbols of a macOS build' % m.name)
            target.add_member(m, name, src)
        else:
            target.add_member(m, name, src, linkname)

    def finish(self, licences):
        """Write what is left and return (binaries entries, symbols
        entries)."""
        if self.p.family == 'windows':
            return (write_zip_windows(self.bin_out, self.bins + licences, [], self.level),
                    write_zip_windows(self.sym_out, self.syms, [], self.level))
        for path, name in licences:
            self.bin.add_path(path, name)
        self.bin.close()
        self.sym.close()
        return self.bin.count, self.sym.count

    def abort(self):
        if self.p.family != 'windows':
            for a in (self.bin, self.sym):
                a.discard()


def add_symbols_tar(r, path):
    """The debug symbols of a CI symbols tarball (LegacyOXT-linux-<arch>-
    symbols.tar.xz: the build output folder with only its *.dbg files, for
    extracting over the bin tarball; the macOS one holds the *.dSYM
    bundles) into the symbols archive, under the platform's folder name.
    Anything but debug symbols in it is an error, and so is a file that is
    in the build output (--bin, --bin-tar) as well. The debug symbols of
    the build's own tools (build_tool: GENTLE among them) are left out, as
    write_binaries leaves out the tools. Returns (files added, files left
    out)."""
    tops = set()
    seen = set()
    skipped = 0
    with tarfile.open(path, 'r|*') as tf:
        for m in tf:
            name = _member_name(m.name)
            parts = name.split('/') if name else []
            if not parts or any(x.startswith('._') for x in parts) or parts[-1] == '.DS_Store':
                continue
            if '..' in parts or '' in parts or m.name.startswith('/'):
                raise DistError('%s: unsafe member name %r' % (path, m.name))
            tops.add(parts[0])
            if len(tops) > 1:
                raise DistError('%s: more than one top-level folder (%s); a CI symbols tarball has the build '
                                'output\'s' % (path, ', '.join(sorted(tops))))
            rel = '/'.join(parts[1:])
            if not rel:
                continue
            if not debug_file(r.p.family, parts):
                if m.isdir():
                    continue
                raise DistError('%s: %s is not a debug symbol file' % (path, m.name))
            if m.islnk():
                raise DistError('%s: %s is a hard link; a symbols tarball holds plain files' % (path, m.name))
            if build_tool(rel):
                if not m.isdir():
                    skipped += 1
                continue
            if rel in seen:
                raise DistError('%s: %s is in it twice' % (path, m.name))
            if rel in r.symbol_paths:
                raise DistError('%s: %s is also in the build output' % (path, m.name))
            seen.add(rel)
            r.add_member(m, rel, tf.extractfile(m) if m.isfile() else None)
    if not seen:
        raise DistError('%s holds no debug symbols' % path)
    return len(seen), skipped


def write_binaries(p, bin_dir, bin_tar, bin_out, sym_out, licences, levels, log, symbols_tar=None):
    """The binaries and symbols archives of the build output (a folder or a
    CI tarball), and the symbols of symbols_tar (a CI symbols tarball, see
    add_symbols_tar). Returns (binaries entries, symbols entries). A Linux or
    macOS build output goes in without the build's own tools and their
    debug symbols (build_tool: GENTLE among them), which are logged."""
    r = _Router(p, bin_out, sym_out, levels)
    unix = p.family != 'windows'
    left_out = collections.Counter()          # top-level name -> entries left out

    def tool(rel):
        if unix and build_tool(rel):
            left_out[rel.split('/')[0]] += 1
            return True
        return False

    try:
        if bin_dir:
            files, empty, links, folders = walk(bin_dir)
            skip = (lambda rel: False) if not unix else \
                (lambda rel: any(x.startswith('._') or x == '.DS_Store' for x in rel.split('/')) or tool(rel))
            if unix:
                # folder entries keep empty folders and folder modes
                for d in folders:
                    if not skip(d):
                        r.add_path(os.path.join(bin_dir, *d.split('/')), d)
            for rel in sorted(files + links, key=_sort_key):
                if not skip(rel):
                    r.add_path(os.path.join(bin_dir, *rel.split('/')), rel)
        else:
            renamed = {}
            with tarfile.open(bin_tar, 'r|*') as tf:
                for m in tf:
                    name = _member_name(m.name)
                    parts = name.split('/') if name else []
                    if not parts or any(x.startswith('._') for x in parts) or parts[-1] == '.DS_Store':
                        continue
                    if '..' in parts or '' in parts or m.name.startswith('/'):
                        raise DistError('%s: unsafe member name %r' % (bin_tar, m.name))
                    rel = '/'.join(parts[1:])
                    renamed[name] = r.top + ('/' + rel if rel else '')
                    # (a hard link to a tool left out then fails in UnixTar:
                    # its target is not in the archive)
                    if not rel or tool(rel):
                        continue
                    link = renamed.get(_member_name(m.linkname)) if m.islnk() else None
                    r.add_member(m, rel, tf.extractfile(m) if m.isfile() else None, link)
        if symbols_tar:
            n, skipped = add_symbols_tar(r, symbols_tar)
            log('  %d debug symbol files from %s%s' % (n, os.path.basename(symbols_tar),
                                                      ' (%d of the build\'s own tools left out)' % skipped
                                                      if skipped else ''))
        # The guard behind build_tool, before the archives are finished (the
        # abort below then removes them): nothing of GENTLE may go out,
        # whatever a later build calls its tools or wherever it puts them
        gentle = [n for n in r.names if n.rsplit('/', 1)[-1].startswith(GENTLE_PREFIXES)]
        if gentle:
            raise DistError('GENTLE 97 may not be redistributed (toolchain/gentle/LICENSE), but the binaries or '
                            'symbols archive would hold %d entr%s named gentle-* or reflex-*: %s%s; leave them '
                            'out with package.BUILD_PROGRAMS (package_dist.build_tool)'
                            % (len(gentle), 'y' if len(gentle) == 1 else 'ies', ', '.join(gentle[:5]),
                               ', ...' if len(gentle) > 5 else ''))
        counts = r.finish(licences)
    except BaseException:
        r.abort()
        raise
    if left_out:
        log('Left out of the binaries and symbols archives: %d entries of the build\'s own tools (%s), see '
            'THIRD-PARTY-NOTICES.md "GENTLE"' % (sum(left_out.values()), ', '.join(sorted(left_out))))
    if not r.counts['symbols']:
        log('WARNING: the build output has no debug symbols; %s is empty' % os.path.basename(sym_out))
    return counts


def _member_name(name):
    """A tar member name without "./" prefixes and a trailing "/"."""
    while name.startswith('./'):
        name = name[2:]
    return name.rstrip('/')


def main(argv=None):
    ap = argparse.ArgumentParser(description='Write the distribution archives of a staged LegacyOXT layout.')
    ap.add_argument('--summary', metavar='FILE', help='package.py --summary-json output')
    ap.add_argument('--platform', choices=list(package.PLATFORMS))
    ap.add_argument('--stage', metavar='DIR', help='the staged LegacyOXT-<version> folder')
    ap.add_argument('--bin', dest='bin_dir', metavar='DIR', help='build output folder')
    ap.add_argument('--bin-tar', metavar='FILE', help='build output as a CI tarball')
    ap.add_argument('--symbols-tar', metavar='FILE',
                    help='Linux and macOS: the CI\'s debug symbols tarball (LegacyOXT-<platform>-symbols.tar.xz), '
                         'whose symbols join those of the build output in the symbols archive')
    ap.add_argument('--no-binaries', action='store_true', help='write no binaries or symbols archive')
    ap.add_argument('--out', required=True, help='folder for the archives (created; files of the same '
                                                 'names are replaced)')
    ap.add_argument('--repo', default=os.path.dirname(os.path.dirname(HERE)),
                    help='repository root (default: two levels above this script)')
    ap.add_argument('--xtalk-sources', action='store_true',
                    help='also write <root>-xtalk-sources.zip from the xTalk cache')
    ap.add_argument('--xtalk-cache', metavar='DIR', help='xTalk extensions cache (as package.py)')
    ap.add_argument('--assets-cache', metavar='DIR', help='asset cache (for the default xTalk cache)')
    ap.add_argument('--zip-level', type=int, choices=range(10), default=9,
                    help='zlib level of the zips (default: 9, the closest to .NET\'s Optimal)')
    ap.add_argument('--xz-preset', type=int, choices=range(10), default=6,
                    help='xz preset of the tarballs (default: 6, as xz and tar -J)')
    ap.add_argument('--no-hardlinks', action='store_true',
                    help='Linux: store identical staged files separately')
    ap.add_argument('--dmg', action='store_true',
                    help='macOS layouts, on macOS: also write the disk image <root>-mac-<arch>.dmg (hdiutil)')
    ap.add_argument('--summary-json', metavar='FILE', help='write the list of archives as JSON')
    args = ap.parse_args(argv)
    log = print

    try:
        summary = {}
        if args.summary:
            with open(args.summary, encoding='utf-8') as f:
                summary = json.load(f)
        platform = args.platform or summary.get('platform') or package.DEFAULT_PLATFORM
        p = package.PLATFORMS[platform]
        stage = args.stage or summary.get('stage_dir')
        if not stage or not os.path.isdir(stage):
            raise DistError('no staged folder (--stage or --summary): %s' % stage)
        stage = os.path.abspath(stage)
        root = os.path.basename(stage)
        m = re.match(r'^%s-([0-9A-Za-z][0-9A-Za-z.+_-]*)$' % re.escape(package.PRODUCT), root)
        if not m:
            raise DistError('%s is not an %s-<version> folder' % (stage, package.PRODUCT))
        version = m.group(1)
        if not os.path.lexists(os.path.join(stage, *p.engine_executable.split('/'))):
            raise DistError('%s has no %s: not a %s layout' % (stage, p.engine_executable, p.name))
        bin_dir = args.bin_dir or (None if args.bin_tar else summary.get('bin_dir'))
        bin_tar = args.bin_tar or (None if args.bin_dir else summary.get('bin_tar'))
        if not args.no_binaries and not (bin_dir or bin_tar):
            raise DistError('pass --bin or --bin-tar (or --no-binaries)')
        if bin_dir and not os.path.isdir(bin_dir):
            raise DistError('--bin %s is not a folder' % bin_dir)
        if bin_tar and not os.path.isfile(bin_tar):
            raise DistError('--bin-tar %s is not a file' % bin_tar)
        if bin_tar and p.family == 'windows' and not args.no_binaries:
            raise DistError('win-x86_64 takes the build output as a folder (--bin), as package-windows.ps1 does')
        if args.dmg and p.family != 'mac':
            raise DistError('--dmg is for the macOS layouts, not %s' % p.name)
        if args.dmg and (sys.platform != 'darwin' or not shutil.which('hdiutil')):
            raise DistError('--dmg needs macOS, whose hdiutil makes the disk image')
        if args.symbols_tar:
            if args.no_binaries or p.family == 'windows':
                raise DistError('--symbols-tar adds to the symbols archive of a Linux or macOS build output; it '
                                'cannot go with --no-binaries or win-x86_64')
            if not os.path.isfile(args.symbols_tar):
                raise DistError('--symbols-tar %s is not a file' % args.symbols_tar)
        if p.unix and os.name == 'nt':
            raise DistError('write the %s archives on Linux or macOS: a Windows file system does not keep '
                            'the modes and links of the staged tree' % p.name)
        if p.unix:
            # In WSL a stage on a Windows drive passes the test above, and
            # its archives would store every file as 0777 (package.py
            # refuses to stage there; this catches a stage copied there).
            # The probe goes next to the stage, not into it, so that the
            # stage folder's date, which the tar records, stays as it was;
            # a folder that cannot be written cannot be probed.
            try:
                why = package.unix_tree_problem(os.path.dirname(stage), p.case_sensitive)
            except OSError:
                why = None
            if why:
                raise DistError('the file system of %s %s (in WSL: a Windows drive such as /mnt/c), so the '
                                'staged modes are lost; stage and archive the %s layout under a Linux path '
                                'such as /tmp' % (stage, why, p.name))
        out = os.path.abspath(args.out)
        os.makedirs(out, exist_ok=True)
        names = archive_names(p, root)
        written = collections.OrderedDict()

        log('Platform : %s' % p.name)
        log('Stage    : %s' % stage)
        log('Build    : %s' % (bin_dir or bin_tar or '(no binaries)'))
        if args.symbols_tar:
            log('Symbols  : %s' % args.symbols_tar)
        log('Output   : %s' % out)
        sums = os.path.join(out, 'SHA256SUMS')
        if os.path.exists(sums):
            os.remove(sums)

        levels = Levels(args.zip_level, args.xz_preset)
        t0 = time.time()
        entries = {}
        if not args.no_binaries:
            log('Writing %s and %s ...' % (names['binaries'], names['symbols']))
            entries['binaries'], entries['symbols'] = write_binaries(
                p, bin_dir and os.path.abspath(bin_dir), bin_tar, os.path.join(out, names['binaries']),
                os.path.join(out, names['symbols']), licence_entries(p, stage), levels, log,
                args.symbols_tar and os.path.abspath(args.symbols_tar))
        if args.dmg:
            log('Writing %s ...' % names['dmg'])
            entries['dmg'] = write_dmg(p, stage, os.path.join(out, names['dmg']), version, log)
        log('Writing %s ...' % names['package'])
        entries['package'] = write_package(p, stage, os.path.join(out, names['package']), levels,
                                           not args.no_hardlinks, log)
        for key in names:
            if key in entries:
                written[key] = os.path.join(out, names[key])

        if args.xtalk_sources:
            path = os.path.join(out, '%s-xtalk-sources.zip' % root)
            log('Writing %s ...' % os.path.basename(path))
            manifest = xtalk_extensions.DEFAULT_MANIFEST
            with open(manifest, 'rb') as f:
                manifest_bytes = f.read()
            cache = xtalk_extensions.cache_dir(args.repo, args.xtalk_cache,
                                               fetch_assets.cache_dir(args.repo, args.assets_cache))
            xtalk_extensions.export(xtalk_extensions.load_manifest(manifest), manifest_bytes, cache, path,
                                    True, log)
            with zipfile.ZipFile(path) as z:
                entries['sources'] = len(z.infolist())
            written['sources'] = path
        log('Archives written in %.0f s' % (time.time() - t0))

        rows = []
        lines = []
        for key, path in written.items():
            digest = _sha256(path)
            lines.append('%s  %s' % (digest, os.path.basename(path)))
            rows.append(collections.OrderedDict([('kind', key), ('name', os.path.basename(path)),
                                                 ('bytes', os.path.getsize(path)),
                                                 ('entries', entries[key]), ('sha256', digest)]))
        with open(sums, 'w', encoding='utf-8', newline='\n') as f:
            f.write('\n'.join(lines) + '\n')

        log('')
        log('Archives in %s:' % out)
        for r in rows:
            log('  %-52s %10.1f MB  %6d entries' % (r['name'], r['bytes'] / 1048576.0, r['entries']))
        log('  SHA256SUMS')
        for line in lines:
            log('    ' + line)

        if args.summary_json:
            with open(args.summary_json, 'w', encoding='utf-8', newline='\n') as f:
                json.dump(collections.OrderedDict([('platform', p.name), ('version', version),
                                                   ('package_root', root), ('dist_dir', out),
                                                   ('archives', rows)]), f, indent=2)
                f.write('\n')
        if os.environ.get('GITHUB_OUTPUT'):
            with open(os.environ['GITHUB_OUTPUT'], 'a', encoding='utf-8', newline='\n') as f:
                f.write('version=%s\npackage-root=%s\nplatform=%s\ndist-dir=%s\npackage=%s\nsha256sums=%s\n'
                        % (version, root, p.name, out, written['package'], sums))
                if 'dmg' in written:
                    f.write('dmg=%s\n' % written['dmg'])
        if os.environ.get('GITHUB_STEP_SUMMARY'):
            md = ['### Packages (%s)' % p.name, '', '| File | Size | Entries | SHA-256 |', '| --- | --- | --- | --- |']
            md += ['| %s | %.1f MB | %d | `%s` |' % (r['name'], r['bytes'] / 1048576.0, r['entries'], r['sha256'])
                   for r in rows]
            with open(os.environ['GITHUB_STEP_SUMMARY'], 'a', encoding='utf-8', newline='\n') as f:
                f.write('\n'.join(md) + '\n\n')
    except (DistError, OSError, tarfile.TarError, zipfile.BadZipFile, xtalk_extensions.XtalkError,
            fetch_assets.AssetError) as e:
        sys.stderr.write('error: %s\n' % e)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
