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

"""Build an oxt-runtimes-<label>.zip release asset: the standalone runtimes
that OpenXTalk-Lite's packages install for platforms other than their own, so
that every package can build standalones for all of them. tools/oxt/
package.py installs the asset's files at their paths in the installed
layout, without the parts the package's own build makes (the asset's
"exclude" in tools/oxt/external-assets.json). There are two modes.

From this repository's builds (--builds; the current asset)
-----------------------------------------------------------

  python tools/oxt/make_runtimes_asset.py --builds --label LABEL --out DIR
      --bin win-x86=PATH --bin win-x86_64=PATH
      --bin linux-x86=PATH --bin linux-x86_64=PATH
      [--carry ZIP] [--assets-cache DIR] [--commit SHA]
      [--run BUILDS=URL]... [--update-manifest [FILE]]

Each PATH is a build output folder or the CI archive that holds one
(OpenXTalk-Lite-win-x86-bin.zip, OpenXTalk-Lite-<ver>-win-x86_64-binaries.zip,
OpenXTalk-Lite-linux-<arch>-bin.tar.xz), which is extracted to a temporary
folder first. From each build the asset takes what package.py installs as
that platform's own runtime, with package.py's tables
(package.plan_runtimes), and the timezone library's code for it:

  Runtime/Windows/x86-32/**      win-x86 (the IDE's "Windows" target)
  Runtime/Windows/x86-64/**      win-x86_64
  Runtime/Linux/x86-32/**        linux-x86 (the IDE's "Linux" target)
  Runtime/Linux/x86-64/**        linux-x86_64
  Extensions/com.livecode.library.timezone/code/<platform id>/**
                                 x86-win32, x86_64-win32, x86-linux,
                                 x86_64-linux
  Extensions/com.livecode.library.timezone/resources/**
                                 the zoneinfo data, from linux-x86_64 (the
                                 Windows builds do not make it)

What this repository does not build yet is carried over unchanged from the
earlier asset oxt-runtimes-1.15 (OpenXTalk Lite 1.15's files): Runtime/
Android/** and the timezone library's Android code. That archive is
downloaded into the assets cache (prebuilt/fetched-assets, or
--assets-cache) unless --carry names a copy; either way its SHA-256 must
be the one recorded here (CARRY_ASSET). Its PROVENANCE.md goes into the
new archive as PROVENANCE-oxt-runtimes-1.15.md.

Every .exe, .dll, .so and engine must be a PE or ELF file of its build's
architecture, and every Standalone must carry this source tree's engine
version (BUILD_SHORT_VERSION in the file "version"), so that a mislabelled
or stale file cannot get in. --commit and --run (BUILDS: build names
separated by commas, URL: the CI run they come from) are recorded in
PROVENANCE.md. The release tag is runtimes-<LABEL>; --update-manifest
replaces the oxt-runtimes-* entries of the manifest with this asset's,
whose "exclude" leaves out what each package builds itself (OWN_PARTS).
.github/workflows/runtimes.yml runs this on the artifacts of a Windows and
a Linux CI run and can publish the result.

From an installed OpenXTalk Lite folder (how oxt-runtimes-1.15 was made)
------------------------------------------------------------------------

The contents of openxtalk-lite-1.15-win-noinstaller.7z. The asset holds the
files of the installed layout that this repository's Windows x86-64 build
did not produce but OXT Lite ships, so that OpenXTalk-Lite packages could offer
the same standalone targets:

  Runtime/Windows/x86-32/**      (except Support/Sample Icons, which
                                  package.py takes from ide/Resources)
  Runtime/Linux/**
  Runtime/Android/**
  Extensions/com.livecode.library.timezone/code/<platform>/**
                                 for every platform except x86_64-win32
  Extensions/com.livecode.library.timezone/resources/**
                                 the zoneinfo data (built only on macOS and
                                 Linux, see tools/oxt/layout.py)

Every file is class "external" in layout.py. Ext/ (the mergExt collection)
is not included: its licence does not allow redistribution as far as this
project knows.

The files are stored unchanged under one top folder <name>/ together with a
generated <name>/PROVENANCE.md that lists every file with its size and
SHA-256. With --stock-setup (the .setup.exe of LiveCode Community 9.6.3 for
Windows, only read) each file is also compared with the stock 9.6.3 package
payload, and PROVENANCE.md says which files are identical to it. The zip is
reproducible: entries are sorted, their dates are the files' modification
times (UTC) and PROVENANCE.md gets the newest of them.

  python tools/oxt/make_runtimes_asset.py <installed-root> --out DIR
      [--stock-setup PATH] [--source-archive NAME] [--name NAME]
      [--update-manifest [FILE]]

--update-manifest writes the archive's size and SHA-256 into the asset
entry of the same name in tools/oxt/external-assets.json (creating the entry
if needed). Publishing the zip as a GitHub Release asset is a separate,
manual step for the maintainer.

Only the Python 3 standard library is used.
"""

import argparse
import calendar
import collections
import datetime
import hashlib
import io
import json
import mmap
import os
import re
import shutil
import struct
import sys
import tarfile
import tempfile
import time
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import binfmt  # noqa: E402
import fetch_assets  # noqa: E402
import layout  # noqa: E402
import package  # noqa: E402

REPO_URL = 'https://github.com/SethMorrowSoftware/OpenXTalk-Lite-1.15'
RELEASE_TAG_FMT = 'runtimes-{version}'
DEFAULT_MANIFEST = os.path.join(HERE, 'external-assets.json')

# (prefix, group title). Every included file is under one of these and is
# class "external" in layout.py.
INCLUDE = (
    ('Runtime/Windows/x86-32/', 'Runtime/Windows/x86-32'),
    ('Runtime/Linux/x86-32/', 'Runtime/Linux/x86-32'),
    ('Runtime/Linux/x86-64/', 'Runtime/Linux/x86-64'),
    ('Runtime/Linux/', 'Runtime/Linux (other)'),
    ('Runtime/Android/', 'Runtime/Android'),
    ('Extensions/com.livecode.library.timezone/code/', 'timezone library: native code'),
    ('Extensions/com.livecode.library.timezone/resources/', 'timezone library: zoneinfo data'),
)

VERSION_RX = re.compile(rb'(?<![0-9.])(?:9\.[0-9]\.[0-9](?:-(?:dp|rc|gm)-[0-9]+|-OXT)?)(?![0-9.])')


# ---------------------------------------------------------------------------
# Stock LiveCode Community 9.6.3 package payload (read only)

class _Slice(io.RawIOBase):
    def __init__(self, f, start, end):
        self.f, self.start, self.end, self.pos = f, start, end, 0

    def seekable(self):
        return True

    def readable(self):
        return True

    def seek(self, off, whence=0):
        if whence == 0:
            self.pos = off
        elif whence == 1:
            self.pos += off
        else:
            self.pos = (self.end - self.start) + off
        return self.pos

    def tell(self):
        return self.pos

    def readinto(self, b):
        n = min(len(b), (self.end - self.start) - self.pos)
        if n <= 0:
            return 0
        self.f.seek(self.start + self.pos)
        d = self.f.read(n)
        b[:len(d)] = d
        self.pos += len(d)
        return len(d)


def stock_digests(setup_path, wanted):
    """{installed path: sha256} for the wanted paths that the stock package
    installs. The payload is the package compiler's zip appended to the
    installer (builder/package_compiler.livecodescript); its manifest.txt
    lines are "file|executable <TAB> [[installFolder]]/<path> <TAB> <item>
    <TAB> [<base>]"."""
    f = open(setup_path, 'rb')
    try:
        m = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
        try:
            eocd = m.rfind(b'PK\x05\x06')
            if eocd < 0:
                raise SystemExit('error: no zip payload found in %s' % setup_path)
            _, _, _, _, _, cd_size, cd_offset, comment = struct.unpack('<IHHHHIIH', m[eocd:eocd + 22])
        finally:
            m.close()
        start = eocd - cd_size - cd_offset
        end = eocd + 22 + comment
        z = zipfile.ZipFile(io.BufferedReader(_Slice(f, start, end), 1 << 20))
        items = {}
        for line in z.read('manifest.txt').decode('utf-8').splitlines():
            parts = line.split('\t')
            if parts[0] not in ('file', 'executable') or len(parts) < 3:
                continue
            # A fourth field names a base file the item is a binary diff
            # against (iOS engines only); those items are not whole files.
            if len(parts) > 3 and parts[3]:
                continue
            target = parts[1]
            prefix = '[[installFolder]]/'
            if target.startswith(prefix):
                items[target[len(prefix):]] = parts[2]
        cache, out = {}, {}
        for path in wanted:
            item = items.get(path)
            if item is None:
                continue
            if item not in cache:
                h = hashlib.sha256()
                with z.open(item) as src:
                    while True:
                        b = src.read(1 << 20)
                        if not b:
                            break
                        h.update(b)
                cache[item] = h.hexdigest()
            out[path] = cache[item]
        return out
    finally:
        f.close()


# ---------------------------------------------------------------------------
# Selection

def select(root):
    """Return (included paths, empty folders, left-out external paths)."""
    included, left_out = [], []
    for path in layout.walk_files(root):
        cls, _, _ = layout.classify_path(path)
        if cls != layout.EXTERNAL:
            continue
        if any(path.startswith(p) for p, _ in INCLUDE):
            included.append(path)
        else:
            left_out.append(path)
    empty = [d for d in layout.empty_dirs(root)
             if layout.classify_path(d + '/x')[0] == layout.EXTERNAL
             and any((d + '/').startswith(p) for p, _ in INCLUDE)]
    return included, empty, left_out


def group_title(path):
    for prefix, title in INCLUDE:
        if path.startswith(prefix):
            return title
    return '(other)'


def read_text(root, name):
    try:
        with open(os.path.join(root, name), encoding='utf-8') as f:
            return f.read().strip()
    except OSError:
        return ''


def file_info(root, path):
    full = layout.native(root, path)
    h = hashlib.sha256()
    with open(full, 'rb') as f:
        head = f.read(4)
        h.update(head)
        while True:
            b = f.read(1 << 20)
            if not b:
                break
            h.update(b)
    st = os.stat(full)
    return {'path': path, 'size': st.st_size, 'sha256': h.hexdigest(),
            'mtime': int(st.st_mtime), 'elf': head == b'\x7fELF'}


def version_strings(root, path):
    """Engine version strings in a file: the tagged ones (9.7.1-OXT,
    9.6.3-rc-3) if there are any, else plain 9.x.y strings."""
    with open(layout.native(root, path), 'rb') as f:
        data = f.read()
    found = {m.group(0).decode('ascii') for m in VERSION_RX.finditer(data)}
    tagged = {v for v in found if '-' in v}
    return sorted(tagged or found)


# ---------------------------------------------------------------------------
# PROVENANCE.md

def utc(ts):
    return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc)


def md_escape(text):
    return text.replace('|', '\\|')


def provenance(name, root, infos, empty, stock, stock_setup, source_archive, left_out):
    version = read_text(root, '.version') or '?'
    build = read_text(root, '.buildnumber') or '?'
    groups = collections.OrderedDict((title, []) for _, title in INCLUDE)
    for i in infos:
        groups.setdefault(group_title(i['path']), []).append(i)
    L = []
    add = L.append
    add('# %s: prebuilt files from OpenXTalk Lite %s' % (name, version))
    add('')
    add('This archive is an external asset of OpenXTalk-Lite (%s). OpenXTalk-Lite\'s' % REPO_URL)
    add('packager (`tools/oxt/package.py`) adds its files to the installed layout at the')
    add('same paths, so that OpenXTalk-Lite offers the standalone targets OpenXTalk Lite')
    add('offers. OpenXTalk-Lite\'s own Windows x86-64 build does not produce these files.')
    add('')
    add('## Source')
    add('')
    src = 'the Windows release of OpenXTalk Lite %s' % version
    if source_archive:
        src += ' (`%s`)' % source_archive
    add('Every file was taken unchanged from %s,' % src)
    add('`.version` %s, `.buildnumber` %s. OpenXTalk Lite was started by Terry Little' % (version, build))
    add('and built and maintained by Tom Perry (tperry2x) up to 1.15, with contributions')
    add('from Paul McClernan (OpenXTalkPaul) and others (https://openxtalk.org).')
    add('')
    add('Not included: `Ext/` (the mergExt collection; OpenXTalk-Lite does not redistribute')
    add('it because its licence is unclear) and `Runtime/Windows/x86-32/Support/Sample')
    add('Icons` (OpenXTalk-Lite installs those from its own `ide/Resources/Sample Icons`).')
    if left_out:
        add('Other files of the release that are not built by OpenXTalk-Lite and are not in')
        add('this archive: %d (%s).' % (len(left_out), ', '.join(sorted({layout.group_of(p) for p in left_out}))))
    add('')
    if stock is not None:
        add('"Stock 9.6.3" below compares each file with the file that the stock LiveCode')
        add('Community 9.6.3 Windows x86-64 installer installs at the same path: **same**')
        add('(byte-identical), **differs**, or **not in 9.6.3**. The comparison read the')
        add('package payload of that installer as kept by an installed copy (`%s`,' % os.path.basename(stock_setup))
        add('%s bytes, SHA-256 `%s`).' % ('{:,}'.format(os.path.getsize(stock_setup)), sha256_file(stock_setup)))
    else:
        add('The files were not compared with the stock LiveCode Community 9.6.3 package.')
    add('')
    add('## Folders')
    add('')
    if stock is not None:
        add('| folder | files | bytes | same as stock 9.6.3 | differs | not in 9.6.3 | newest file (UTC) |')
        add('|---|---:|---:|---:|---:|---:|---|')
    else:
        add('| folder | files | bytes | newest file (UTC) |')
        add('|---|---:|---:|---|')
    for title, items in groups.items():
        if not items:
            continue
        size = sum(i['size'] for i in items)
        newest = utc(max(i['mtime'] for i in items)).strftime('%Y-%m-%d')
        if stock is not None:
            c = collections.Counter(stock_status(i, stock) for i in items)
            add('| `%s` | %d | %s | %d | %d | %d | %s |' % (
                title, len(items), '{:,}'.format(size), c['same'], c['differs'], c['not in 9.6.3'], newest))
        else:
            add('| `%s` | %d | %s | %s |' % (title, len(items), '{:,}'.format(size), newest))
    add('')
    add('## Notes on the folders')
    add('')
    for title, items in groups.items():
        if not items:
            continue
        add('### `%s`' % title)
        add('')
        engines = [i for i in items if i['path'].rsplit('/', 1)[-1] == 'Standalone']
        for e in engines:
            vs = version_strings(root, e['path'])
            add('- `%s` (%s bytes, dated %s UTC): version strings found in the file: %s.' % (
                e['path'], '{:,}'.format(e['size']), utc(e['mtime']).strftime('%Y-%m-%d'),
                ', '.join('`%s`' % v for v in vs) if vs else 'none'))
        if stock is not None:
            c = collections.Counter(stock_status(i, stock) for i in items)
            if c['same'] == len(items):
                add('- All %d files are byte-identical to stock LiveCode Community 9.6.3.' % len(items))
            else:
                if c['same']:
                    add('- %d of %d files are byte-identical to stock LiveCode Community 9.6.3.' % (c['same'], len(items)))
                differ = [i['path'] for i in items if stock_status(i, stock) == 'differs']
                if differ:
                    add('- Different from the stock 9.6.3 file at the same path: %s.' % ', '.join('`%s`' % p for p in differ))
                missing = [i['path'] for i in items if stock_status(i, stock) == 'not in 9.6.3']
                libs = [p for p in missing if '/lib/' in p]
                other = [p for p in missing if '/lib/' not in p]
                if libs:
                    folder = libs[0][:libs[0].index('/lib/') + 4]
                    add('- `%s/` holds %d shared library files of other projects that stock 9.6.3' % (folder, len(libs)))
                    add('  does not install (listed below under Files).')
                if other:
                    add('- Not installed by stock 9.6.3: %s.' % ', '.join('`%s`' % p for p in other))
        add('')
    if stock is not None:
        add('Files that are not identical to stock LiveCode Community 9.6.3 are as Tom Perry')
        add('shipped them in OpenXTalk Lite (engine builds, edited templates, bundled')
        add('libraries). This archive records their bytes; how each one was built is not')
        add('recorded here.')
        add('')
    add('## Corresponding source and licences')
    add('')
    add('- LiveCode Community 9.6.3 (engine, externals, Android templates, timezone')
    add('  library): GNU GPL v3; source at https://github.com/livecode/livecode, tag')
    add('  `9.6.3` (engines reporting `9.6.3-rc-3`: tag `9.6.3-rc-3`). The history of')
    add('  %s contains the LiveCode commits up to `9.6.3-rc-3`.' % REPO_URL)
    add('- Tom Perry\'s OpenXTalk Lite engine (version string `9.7.1-OXT`): GNU GPL v3;')
    add('  his source archives (Windows, macOS and Linux) are published at')
    add('  https://www.openxtalk.net/OXT-lite-source/ . The Windows engine changes are')
    add('  in the history of %s.' % REPO_URL)
    add('- Timezone data (`resources/zoneinfo`): compiled with `zic` from the IANA time')
    add('  zone database, which is in the public domain; the sources are in')
    add('  `extensions/libraries/timezone/tz` of the LiveCode and OpenXTalk-Lite repositories.')
    add('- Shared libraries in `Runtime/Linux/*/lib/`: each under the licence of its own')
    add('  project (for example LGPL for glibc, GLib, GTK and Pango; MIT or BSD-style')
    add('  licences for several X11, compression and codec libraries). Their licence texts')
    add('  and sources are available from those projects; they are not part of this archive.')
    add('')
    add('## Files')
    add('')
    add('Times are the files\' modification times in the release (UTC).')
    add('')
    if stock is not None:
        add('| path | bytes | SHA-256 | modified | stock 9.6.3 |')
        add('|---|---:|---|---|---|')
    else:
        add('| path | bytes | SHA-256 | modified |')
        add('|---|---:|---|---|')
    for i in infos:
        row = '| `%s` | %d | `%s` | %s |' % (md_escape(i['path']), i['size'], i['sha256'],
                                           utc(i['mtime']).strftime('%Y-%m-%d %H:%M'))
        if stock is not None:
            row += ' %s |' % stock_status(i, stock)
        add(row)
    add('')
    add('Total: %d files, %s bytes.' % (len(infos), '{:,}'.format(sum(i['size'] for i in infos))))
    if empty:
        add('')
        add('Empty folders in the release, kept as folder entries: %s.' % ', '.join('`%s`' % d for d in empty))
    add('')
    return '\n'.join(L)


def stock_status(info, stock):
    d = stock.get(info['path'])
    if d is None:
        return 'not in 9.6.3'
    return 'same' if d == info['sha256'] else 'differs'


# ---------------------------------------------------------------------------
# Zip

def zip_info(name, mtime, executable=False):
    t = time.gmtime(max(mtime, 315532800))  # zip dates start in 1980
    zi = zipfile.ZipInfo(name, date_time=t[:6])
    zi.compress_type = zipfile.ZIP_DEFLATED
    zi.create_system = 3
    zi.external_attr = ((0o100755 if executable else 0o100644) & 0xFFFF) << 16
    return zi


def write_zip(path, name, root, infos, empty, provenance_text):
    tmp = path + '.tmp'
    newest = max(i['mtime'] for i in infos)
    with zipfile.ZipFile(tmp, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for i in infos:
            zi = zip_info('%s/%s' % (name, i['path']), i['mtime'], i['elf'])
            with open(layout.native(root, i['path']), 'rb') as src, z.open(zi, 'w') as dst:
                while True:
                    b = src.read(1 << 20)
                    if not b:
                        break
                    dst.write(b)
        for d in empty:
            zi = zipfile.ZipInfo('%s/%s/' % (name, d), date_time=time.gmtime(newest)[:6])
            zi.create_system = 3
            zi.external_attr = (0o40755 << 16) | 0x10
            z.writestr(zi, b'')
        z.writestr(zip_info('%s/PROVENANCE.md' % name, newest), provenance_text.encode('utf-8'),
                   compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    os.replace(tmp, path)


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        while True:
            b = f.read(1 << 20)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Manifest update

def manifest_entry(name, version, sha, size):
    tag = RELEASE_TAG_FMT.format(version=version)
    return collections.OrderedDict([
        ('id', name),
        ('url', '%s/releases/download/%s/%s.zip' % (REPO_URL, tag, name)),
        ('sha256', sha),
        ('size', size),
        ('kind', 'zip'),
        ('strip', 1),
        ('dest', ''),
        ('rename', collections.OrderedDict([('PROVENANCE.md', 'PROVENANCE-%s.md' % name)])),
        ('description', 'Standalone runtimes that the Windows x86-64 build does not produce, '
                        'taken unchanged from OpenXTalk Lite %s: Windows x86-32, Linux and '
                        'Android engines and externals, the timezone library\'s code for other '
                        'platforms and its zoneinfo data. See PROVENANCE.md in the archive.' % version),
        ('licence', 'GPL-3.0 (LiveCode Community and OpenXTalk Lite engine builds); '
                    'IANA tz data: public domain; the shared libraries under '
                    'Runtime/Linux/*/lib keep their own licences (see PROVENANCE.md)'),
        ('source', 'OpenXTalk Lite %s for Windows (openxtalk-lite-%s-win-noinstaller.7z), '
                   'built by Tom Perry; made with tools/oxt/make_runtimes_asset.py' % (version, version)),
    ])


def update_manifest(path, entry):
    if os.path.exists(path):
        with open(path, encoding='utf-8') as f:
            data = json.load(f, object_pairs_hook=collections.OrderedDict)
    else:
        data = collections.OrderedDict([('assets', [])])
    assets = data.setdefault('assets', [])
    for n, a in enumerate(assets):
        if a.get('id') == entry['id']:
            merged = collections.OrderedDict(a)
            merged['sha256'] = entry['sha256']
            merged['size'] = entry['size']
            assets[n] = merged
            break
    else:
        assets.append(entry)
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write('\n')


# ---------------------------------------------------------------------------
# --builds: the asset from this repository's builds

TZ = 'Extensions/com.livecode.library.timezone/'
TZ_BUILD = 'packaged_extensions/com.livecode.library.timezone/'

# The builds the asset is made from (package.py platforms): the timezone
# library's code folder each one makes (common.gypi platform_id), and what
# its binaries must be (binfmt.arch_of)
BUILDS = collections.OrderedDict([
    ('win-x86', ('x86-win32', 'pe', 'x86')),
    ('win-x86_64', ('x86_64-win32', 'pe', 'x86_64')),
    ('linux-x86', ('x86-linux', 'elf', 'x86')),
    ('linux-x86_64', ('x86_64-linux', 'elf', 'x86_64')),
])

# The zoneinfo data (tz.gyp target tzdata), which the Linux and macOS
# builds make and the Windows builds do not
TZDATA_BUILD = 'linux-x86_64'

# The earlier asset that the parts this repository does not build yet are
# carried over from, unchanged: the Android runtime and the timezone
# library's Android code, from OpenXTalk Lite 1.15
CARRY_ASSET = collections.OrderedDict([
    ('id', 'oxt-runtimes-1.15'),
    ('url', REPO_URL + '/releases/download/runtimes-1.15/oxt-runtimes-1.15.zip'),
    ('sha256', '6811d5cc028ea15f7e784ff58c03e4bc8994507f7f56e62fa148a27673cf995a'),
    ('size', 199237317),
    ('kind', 'zip'),
    ('strip', 1),
    ('dest', ''),
])
CARRY = ('Runtime/Android/', TZ + 'code/arm64-android/', TZ + 'code/armv7-android/',
         TZ + 'code/x86-android/', TZ + 'code/x86_64-android/')

# What each package's own build makes, which it therefore leaves out of
# the asset (the asset's "exclude" in external-assets.json). linux-arm64
# and macOS make the zoneinfo data; their own runtimes are not in the
# asset at all.
OWN_PARTS = collections.OrderedDict([
    ('win-x86_64', ['Runtime/Windows/x86-64/**', TZ + 'code/x86_64-win32/**']),
    ('linux-x86_64', ['Runtime/Linux/x86-64/**', TZ + 'code/x86_64-linux/**', TZ + 'resources/**']),
    ('linux-arm64', [TZ + 'resources/**']),
    ('mac-*', [TZ + 'resources/**']),
])

# Names of files that must be binaries of their build's architecture
BINARY_NAMES = re.compile(r'(\.exe|\.dll|\.so|\.lcext|/Standalone|-cefprocess)$')
ZIP_EPOCH = 315532800  # zip dates start in 1980


def part_of(path):
    """The part of the asset a path is in: a runtime folder
    (Runtime/Windows/x86-32, Runtime/Android), a timezone code folder, or
    the zoneinfo data."""
    parts = path.split('/')
    if path.startswith(TZ + 'code/'):
        return '/'.join(parts[:4])
    if path.startswith(TZ):
        return '/'.join(parts[:3])
    if parts[0] == 'Runtime' and len(parts) > 3 and parts[1] != 'Android':
        return '/'.join(parts[:3])
    return '/'.join(parts[:2])


def engine_version(repo):
    """BUILD_SHORT_VERSION of the source tree (the file "version")."""
    with open(os.path.join(repo, 'version'), encoding='utf-8') as f:
        for line in f:
            m = re.match(r'^\s*BUILD_SHORT_VERSION\s*=\s*(\S+)', line)
            if m:
                return m.group(1)
    raise SystemExit('error: no BUILD_SHORT_VERSION in %s' % os.path.join(repo, 'version'))


def digest(path):
    """(size, SHA-256, first 4 bytes) of a file."""
    h = hashlib.sha256()
    size = 0
    with open(path, 'rb') as f:
        head = f.read(4)
        h.update(head)
        size += len(head)
        while True:
            b = f.read(1 << 20)
            if not b:
                break
            h.update(b)
            size += len(b)
    return size, h.hexdigest(), head


def file_versions(path):
    """Engine version strings in a file (see version_strings)."""
    with open(path, 'rb') as f:
        data = f.read()
    return sorted({m.group(0).decode('ascii') for m in VERSION_RX.finditer(data)})


def open_build(name, spec, work):
    """The build output folder of a build: spec is that folder, or the CI
    archive that holds it (a zip or a tarball), which is extracted into
    work first. Returns (folder, what to record about the archive, or
    None)."""
    bin_name = package.PLATFORMS[name].bin_default
    if os.path.isdir(spec):
        return os.path.abspath(spec), None
    if not os.path.isfile(spec):
        raise SystemExit('error: --bin %s=%s: no such folder or file' % (name, spec))
    dest = os.path.join(work, name)
    os.makedirs(dest)
    print('%s: extracting %s ...' % (name, spec))
    if zipfile.is_zipfile(spec):
        # the Windows archives: <bin_name>/ (and, in the binaries zip, the
        # licence files beside it), without the .pdb files
        count = 0
        with zipfile.ZipFile(spec) as z:
            for info in z.infolist():
                member = info.filename.replace('\\', '/')
                if member.endswith('/') or not member.startswith(bin_name + '/'):
                    continue
                rel = fetch_assets.safe_relpath(member, what='%s member' % spec)
                parts = rel.split('/')
                if package._is_debug(parts):
                    continue
                target = os.path.join(dest, *parts)
                os.makedirs(os.path.dirname(target), exist_ok=True)
                with z.open(info) as src, open(target, 'wb') as out:
                    shutil.copyfileobj(src, out, 1 << 20)
                # zip times have no zone: the CI's are UTC, and zip_info()
                # writes them back as UTC
                t = calendar.timegm(info.date_time + (0, 0, 0))
                os.utime(target, (t, t))
                count += 1
        if not count:
            raise SystemExit('error: %s has no files under %s/' % (spec, bin_name))
        folder = os.path.join(dest, bin_name)
    else:
        try:
            folder = package.extract_bin_tar(spec, dest, print)
        except (package.PackageError, tarfile.TarError) as e:
            raise SystemExit('error: %s' % e)
        if os.path.basename(folder) != bin_name:
            raise SystemExit('error: %s holds %s/, not %s/' % (spec, os.path.basename(folder), bin_name))
    size, sha, _ = digest(spec)
    return folder, collections.OrderedDict([('archive', os.path.basename(spec)), ('size', size), ('sha256', sha)])


def plan_build_parts(name, folder):
    """(items, problems): what the asset takes from one build, as
    package.Item objects at their installed paths."""
    p = package.PLATFORMS[name]
    pl = package.Planner(p, folder)
    package.plan_runtimes(pl, '')
    code = BUILDS[name][0]
    pl.tree(TZ + 'code/' + code, TZ_BUILD + 'code/' + code, 'TimeZone')
    if name == TZDATA_BUILD:
        pl.tree(TZ + 'resources', TZ_BUILD + 'resources', 'TimeZone')
    return pl.items, pl.problems


def build_infos(name, items):
    """File records (as file_info's, plus 'from' and 'source' or 'data')
    of one build's items. Generated files (the Externals lists) get the
    date of the build's newest file."""
    infos, generated = [], []
    for it in items:
        if it.origin == 'generated':
            generated.append(it)
            continue
        size, sha, head = digest(it.source)
        infos.append({'path': it.target, 'size': size, 'sha256': sha, 'mtime': int(os.stat(it.source).st_mtime),
                      'elf': head == binfmt.ELF_MAGIC, 'from': name, 'source': it.source})
    newest = max([i['mtime'] for i in infos] or [ZIP_EPOCH])
    for it in generated:
        infos.append({'path': it.target, 'size': len(it.data), 'sha256': hashlib.sha256(it.data).hexdigest(),
                      'mtime': newest, 'elf': False, 'from': name, 'data': it.data})
    return infos


def check_build(name, infos, version):
    """Problems with one build's files: a binary of another format or
    architecture, or an engine without this tree's version."""
    _, fmt, arch = BUILDS[name]
    problems = []
    for i in infos:
        if 'source' not in i:
            continue
        kind, archs = binfmt.arch_of(i['source'])
        if kind or BINARY_NAMES.search(i['path']):
            if kind != fmt or archs != [arch]:
                problems.append('%s (from %s) is %s, expected %s %s'
                                % (i['path'], name, ('%s %s' % (kind.upper(), ' '.join(archs))) if kind
                                   else 'not a binary', fmt.upper(), arch))
        if i['path'].rsplit('/', 1)[-1] == 'Standalone':
            found = file_versions(i['source'])
            if version not in found:
                problems.append('%s (from %s) does not carry the engine version %s (found: %s)'
                                % (i['path'], name, version, ', '.join(found) or 'none'))
            i['versions'] = found
    return problems


def carried_infos(path):
    """(file records, PROVENANCE.md bytes) of the parts carried over from
    CARRY_ASSET's archive at path (checked against its SHA-256)."""
    problem = fetch_assets.check_file(CARRY_ASSET, path)
    if problem:
        raise SystemExit('error: %s is not %s: %s' % (path, CARRY_ASSET['id'], problem))
    infos, provenance_text, seen = [], None, set()
    with zipfile.ZipFile(path) as z:
        for info in z.infolist():
            if info.filename.endswith('/'):
                continue
            rel = info.filename.split('/', CARRY_ASSET['strip'])[-1]
            if rel == 'PROVENANCE.md':
                provenance_text = z.read(info)
                continue
            prefix = next((c for c in CARRY if rel.startswith(c)), None)
            if prefix is None:
                continue
            seen.add(prefix)
            h = hashlib.sha256()
            with z.open(info) as src:
                head = src.read(4)
                h.update(head)
                while True:
                    b = src.read(1 << 20)
                    if not b:
                        break
                    h.update(b)
            infos.append({'path': rel, 'size': info.file_size, 'sha256': h.hexdigest(),
                          'mtime': calendar.timegm(info.date_time + (0, 0, 0)), 'elf': head == binfmt.ELF_MAGIC,
                          'from': CARRY_ASSET['id'], 'zipinfo': info})
    missing = [c for c in CARRY if c not in seen]
    if provenance_text is None:
        missing.append('PROVENANCE.md')
    if missing:
        raise SystemExit('error: %s lacks %s' % (path, ', '.join(missing)))
    return infos, provenance_text


def write_builds_zip(path, name, infos, carry_zip, extra):
    """The archive: every record under <name>/, sorted, then the extra
    (file name, bytes, mtime) files. Carried members keep their dates and
    modes; the others get their files' dates (UTC) and 0755 for ELF
    files."""
    tmp = path + '.tmp'
    with zipfile.ZipFile(tmp, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as z, \
            zipfile.ZipFile(carry_zip) as cz:
        for i in sorted(infos, key=lambda x: x['path']):
            target = '%s/%s' % (name, i['path'])
            if 'zipinfo' in i:
                src = i['zipinfo']
                zi = zipfile.ZipInfo(target, date_time=src.date_time)
                zi.compress_type = zipfile.ZIP_DEFLATED
                zi.create_system = src.create_system
                zi.external_attr = src.external_attr
                with cz.open(src) as s, z.open(zi, 'w') as d:
                    shutil.copyfileobj(s, d, 1 << 20)
            elif 'data' in i:
                z.writestr(zip_info(target, i['mtime']), i['data'])
            else:
                with open(i['source'], 'rb') as s, z.open(zip_info(target, i['mtime'], i['elf']), 'w') as d:
                    shutil.copyfileobj(s, d, 1 << 20)
        for file_name, data, mtime in extra:
            z.writestr(zip_info('%s/%s' % (name, file_name), mtime), data)
    os.replace(tmp, path)


def parse_pairs(values, what):
    """{build name: value} from NAME[,NAME...]=VALUE arguments."""
    out = collections.OrderedDict()
    for v in values or ():
        names, sep, value = v.partition('=')
        if not sep or not value:
            raise SystemExit('error: %s %r: expected NAME=VALUE' % (what, v))
        for n in names.split(','):
            if n not in BUILDS:
                raise SystemExit('error: %s %r: %s is not one of %s' % (what, v, n, ', '.join(BUILDS)))
            if n in out:
                raise SystemExit('error: %s: %s is given twice' % (what, n))
            out[n] = value
    return out


def builds_provenance(name, version, commit, runs, sources, infos):
    by_from = collections.OrderedDict((b, []) for b in BUILDS)
    by_from[CARRY_ASSET['id']] = []
    for i in infos:
        by_from[i['from']].append(i)
    L = []
    add = L.append
    add('# %s: standalone runtimes for OpenXTalk-Lite' % name)
    add('')
    add('This archive is an external asset of OpenXTalk-Lite (%s). OpenXTalk-Lite\'s' % REPO_URL)
    add('packager (`tools/oxt/package.py`) adds its files to the installed layout at the')
    add('same paths, so that every OpenXTalk-Lite package can build standalones for these')
    add('platforms, not only for its own. A package leaves out what its own build makes')
    add('(the asset\'s "exclude" in `tools/oxt/external-assets.json`):')
    add('')
    for plat, globs in OWN_PARTS.items():
        add('- `%s`: %s' % (plat, ', '.join('`%s`' % g for g in globs)))
    add('')
    add('## Built from OpenXTalk-Lite\'s source')
    add('')
    add('The Windows and Linux runtimes (engines, support libraries, externals, database')
    add('drivers and, where there is one, the browser\'s CEF files), the timezone')
    add('library\'s code for those platforms and its zoneinfo data were built by')
    add('OpenXTalk-Lite\'s continuous integration and taken from the build outputs by')
    add('`tools/oxt/make_runtimes_asset.py --builds`, which installs each one as')
    add('`tools/oxt/package.py` installs a platform\'s own runtime.')
    add('')
    if commit:
        add('- Source: %s, commit `%s`' % (REPO_URL, commit))
    else:
        add('- Source: %s (the commit was not recorded)' % REPO_URL)
    add('- Engine version `%s` (`BUILD_SHORT_VERSION`), found in every `Standalone` engine' % version)
    add('')
    add('| build | folders in this archive | files | bytes | taken from |')
    add('|---|---|---:|---:|---|')
    for b in BUILDS:
        items = by_from[b]
        folders = sorted({part_of(i['path']) for i in items})
        src = sources.get(b)
        where = ('`%s` (%s bytes, SHA-256 `%s`)' % (src['archive'], '{:,}'.format(src['size']), src['sha256'])
                 if src else 'a build output folder')
        if runs.get(b):
            where += ', %s' % runs[b]
        add('| `%s` | %s | %d | %s | %s |' % (b, ', '.join('`%s`' % f for f in folders), len(items),
                                            '{:,}'.format(sum(i['size'] for i in items)), where))
    add('')
    carried = by_from[CARRY_ASSET['id']]
    add('## Carried over from %s' % CARRY_ASSET['id'])
    add('')
    add('OpenXTalk-Lite does not build the Android runtime yet. `Runtime/Android` and the')
    add('timezone library\'s Android code (`code/*-android`) are taken unchanged from the')
    add('earlier runtimes asset `%s`' % CARRY_ASSET['id'])
    add('(%s, %s bytes,' % (CARRY_ASSET['url'], '{:,}'.format(CARRY_ASSET['size'])))
    add('SHA-256 `%s`): %d files, %s bytes.' % (CARRY_ASSET['sha256'], len(carried),
                                                 '{:,}'.format(sum(i['size'] for i in carried))))
    add('They are OpenXTalk Lite 1.15\'s files, built from LiveCode Community 9.6.3;')
    add('that archive\'s PROVENANCE.md, which records where each one comes from, is')
    add('included here as `PROVENANCE-%s.md`.' % CARRY_ASSET['id'])
    add('')
    add('## Engines')
    add('')
    for i in sorted(infos, key=lambda x: x['path']):
        if 'versions' in i:
            add('- `%s` (%s bytes, from `%s`): version strings found in the file: %s.'
                % (i['path'], '{:,}'.format(i['size']), i['from'], ', '.join('`%s`' % v for v in i['versions'])))
    add('')
    add('## Corresponding source and licences')
    add('')
    add('- The engines, externals, database drivers and timezone library code built')
    add('  from OpenXTalk-Lite: GNU GPL v3, with the exception in LICENSE-EXCEPTION.md; the')
    add('  source is %s at the commit above. The third-party' % REPO_URL)
    add('  libraries compiled into them (OpenSSL, curl, ICU, SQLite, the database client')
    add('  libraries and others) are listed with their licences in THIRD-PARTY-NOTICES.md')
    add('  of the repository and of every package; BUILDING.md says how they are built.')
    add('- `Externals/CEF` of the Windows runtimes and of `Runtime/Linux/x86-64`: the')
    add('  Chromium Embedded Framework binary distribution that `prebuilt/versions/cef`')
    add('  names, as built and published by Spotify (https://cef-builds.spotifycdn.com;')
    add('  SHA-1 pinned in `prebuilt/cef-sha1sums`): BSD-3-Clause, with the licences of')
    add('  Chromium and its components (see THIRD-PARTY-NOTICES.md).')
    add('- Timezone data (`resources/zoneinfo`): compiled with `zic` from the IANA time')
    add('  zone database, which is in the public domain (`extensions/libraries/timezone/tz`).')
    add('- The Android runtime and the timezone library\'s Android code: see')
    add('  `PROVENANCE-%s.md`.' % CARRY_ASSET['id'])
    add('')
    add('## Files')
    add('')
    add('Times are the files\' modification times in the builds or in %s (UTC).' % CARRY_ASSET['id'])
    add('')
    add('| path | bytes | SHA-256 | modified | from |')
    add('|---|---:|---|---|---|')
    for i in sorted(infos, key=lambda x: x['path']):
        add('| `%s` | %d | `%s` | %s | %s |' % (md_escape(i['path']), i['size'], i['sha256'],
                                              utc(i['mtime']).strftime('%Y-%m-%d %H:%M'), i['from']))
    add('')
    add('Total: %d files, %s bytes.' % (len(infos), '{:,}'.format(sum(i['size'] for i in infos))))
    add('')
    return '\n'.join(L)


def builds_manifest_entry(name, label, sha, size, commit):
    tag = RELEASE_TAG_FMT.format(version=label)
    return collections.OrderedDict([
        ('id', name),
        ('url', '%s/releases/download/%s/%s.zip' % (REPO_URL, tag, name)),
        ('sha256', sha),
        ('size', size),
        ('kind', 'zip'),
        ('strip', 1),
        ('dest', ''),
        ('rename', collections.OrderedDict([('PROVENANCE.md', 'PROVENANCE-%s.md' % name)])),
        ('exclude', collections.OrderedDict((k, list(v)) for k, v in OWN_PARTS.items())),
        ('exclude_note', 'What each package\'s own build makes: Windows x86-64 its runtime and the timezone '
                         'library\'s code for it; Linux x86-64 the same and the zoneinfo data (tz.gyp target '
                         'tzdata, which the Windows builds do not make); Linux arm64 and macOS the zoneinfo '
                         'data (their own runtimes are not in this asset).'),
        ('description', 'Standalone runtimes for the platforms other than the package\'s own, built from this '
                        'repository (%s): Windows x86-32 and x86-64 and Linux x86-32 and x86-64 engines with '
                        'their externals, the timezone library\'s code for them and its zoneinfo data; and the '
                        'Android runtime with the timezone library\'s Android code, carried over unchanged from '
                        'oxt-runtimes-1.15 (OpenXTalk Lite 1.15). See PROVENANCE.md in the archive.'
                        % ('commit %s' % commit[:12] if commit else 'see PROVENANCE.md')),
        ('licence', 'GPL-3.0 with the exception in LICENSE-EXCEPTION.md (OpenXTalk-Lite builds); GPL-3.0 '
                    '(the Android runtime from LiveCode Community 9.6.3); CEF: BSD-3-Clause with Chromium\'s '
                    'licences; IANA tz data: public domain; see PROVENANCE.md'),
        ('source', 'OpenXTalk-Lite\'s CI builds win-x86, win-x86_64, linux-x86 and linux-x86_64%s, made with '
                   'tools/oxt/make_runtimes_asset.py --builds; the Android parts from oxt-runtimes-1.15'
                   % (' of commit %s' % commit if commit else '')),
    ])


def replace_runtimes_entry(path, entry):
    """Put entry in place of the manifest's oxt-runtimes-* entries."""
    with open(path, encoding='utf-8') as f:
        data = json.load(f, object_pairs_hook=collections.OrderedDict)
    out, placed = [], False
    for a in data.get('assets', []):
        if str(a.get('id', '')).startswith('oxt-runtimes-'):
            if not placed:
                out.append(entry)
                placed = True
            continue
        out.append(a)
    if not placed:
        out.append(entry)
    data['assets'] = out
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write('\n')


def builds_main(args, p):
    if args.installed_root or args.stock_setup or args.source_archive:
        p.error('--builds takes no installed folder, --stock-setup or --source-archive')
    if not args.label or not re.match(r'^[0-9A-Za-z][0-9A-Za-z._-]*$', args.label):
        p.error('--builds needs --label (letters, digits, ".", "_" and "-")')
    bins = parse_pairs(args.bin, '--bin')
    missing = [b for b in BUILDS if b not in bins]
    if missing:
        p.error('--builds needs --bin for %s' % ', '.join(missing))
    runs = parse_pairs(args.run, '--run')
    name = args.name or 'oxt-runtimes-%s' % args.label
    repo = os.path.dirname(os.path.dirname(HERE))
    version = engine_version(repo)

    work = tempfile.mkdtemp(prefix='oxt-runtimes-')
    try:
        infos, problems, sources = [], [], {}
        for b in BUILDS:
            folder, src = open_build(b, bins[b], work)
            if src:
                sources[b] = src
            items, found = plan_build_parts(b, folder)
            problems += ['%s: %s' % (b, x) for x in found]
            these = build_infos(b, items)
            problems += check_build(b, these, version)
            infos += these
            print('%s: %d files from %s' % (b, len(these), folder))
        carry_path = args.carry
        if not carry_path:
            cache = fetch_assets.cache_dir(repo, args.assets_cache)
            try:
                carry_path = fetch_assets.fetch(CARRY_ASSET, cache, log=print)
            except fetch_assets.AssetError as e:
                raise SystemExit('error: %s' % e)
        carried, carried_provenance = carried_infos(carry_path)
        infos += carried
        print('%s: %d files carried over' % (CARRY_ASSET['id'], len(carried)))
        seen = {}
        for i in infos:
            key = i['path'].lower()
            if key in seen:
                problems.append('%s comes from both %s and %s' % (i['path'], seen[key], i['from']))
            seen[key] = i['from']
        if problems:
            for x in problems:
                print('error: %s' % x)
            raise SystemExit('error: %d problems; no archive written' % len(problems))

        text = builds_provenance(name, version, args.commit, runs, sources, infos)
        newest = max(i['mtime'] for i in infos)
        os.makedirs(args.out, exist_ok=True)
        zip_path = os.path.join(args.out, name + '.zip')
        write_builds_zip(zip_path, name, infos, carry_path,
                         [('PROVENANCE.md', text.encode('utf-8'), newest),
                          ('PROVENANCE-%s.md' % CARRY_ASSET['id'], carried_provenance, newest)])
    finally:
        shutil.rmtree(work, ignore_errors=True)
    with open(os.path.join(args.out, 'PROVENANCE.md'), 'w', encoding='utf-8', newline='\n') as f:
        f.write(text)
    size, sha, _ = digest(zip_path)
    entry = builds_manifest_entry(name, args.label, sha, size, args.commit)
    with open(os.path.join(args.out, name + '.manifest.json'), 'w', encoding='utf-8', newline='\n') as f:
        json.dump(entry, f, indent=2, ensure_ascii=False)
        f.write('\n')

    counts = collections.Counter(part_of(i['path']) for i in infos)
    print('%s: %d files, %s bytes uncompressed' % (zip_path, len(infos), '{:,}'.format(sum(i['size'] for i in infos))))
    for title in sorted(counts):
        print('  %5d  %s' % (counts[title], title))
    print('size   %d' % size)
    print('sha256 %s' % sha)
    if args.update_manifest:
        replace_runtimes_entry(args.update_manifest, entry)
        print('updated %s' % args.update_manifest)
    return 0


# ---------------------------------------------------------------------------

def main(argv=None):
    p = argparse.ArgumentParser(description='Build an oxt-runtimes release asset from this repository\'s builds '
                                            '(--builds) or from an installed OpenXTalk Lite folder.')
    p.add_argument('installed_root', nargs='?', help='installed OpenXTalk Lite folder (without --builds)')
    p.add_argument('--out', required=True, help='folder for the zip')
    p.add_argument('--name', help='asset name (default: oxt-runtimes-<label>, or oxt-runtimes-<.version> of the '
                                  'installed folder)')
    p.add_argument('--builds', action='store_true', help='make the asset from this repository\'s builds')
    p.add_argument('--label', help='--builds: the asset\'s label, as in oxt-runtimes-<label> and the release tag '
                                   'runtimes-<label>')
    p.add_argument('--bin', action='append', metavar='BUILD=PATH',
                   help='--builds: a build output folder or CI archive, for each of %s' % ', '.join(BUILDS))
    p.add_argument('--carry', metavar='ZIP', help='--builds: a copy of %s.zip (default: downloaded into the assets '
                                                  'cache)' % CARRY_ASSET['id'])
    p.add_argument('--assets-cache', metavar='DIR', help='--builds: the assets cache (default: '
                                                         'prebuilt/fetched-assets, or OXT_ASSETS_CACHE)')
    p.add_argument('--commit', help='--builds: the commit the builds were made from, for PROVENANCE.md')
    p.add_argument('--run', action='append', metavar='BUILDS=URL',
                   help='--builds: the CI run that made these builds (names separated by commas), for '
                        'PROVENANCE.md')
    p.add_argument('--stock-setup', metavar='PATH',
                   help='.setup.exe of stock LiveCode Community 9.6.3 for Windows, to compare against (read only)')
    p.add_argument('--source-archive', metavar='NAME', help='file name of the release archive, for PROVENANCE.md')
    p.add_argument('--update-manifest', nargs='?', const=DEFAULT_MANIFEST, metavar='FILE',
                   help='write size and SHA-256 into this manifest (default: %s)' % DEFAULT_MANIFEST)
    args = p.parse_args(argv)
    if args.builds:
        return builds_main(args, p)
    if args.label or args.bin or args.carry or args.assets_cache or args.commit or args.run:
        p.error('--label, --bin, --carry, --assets-cache, --commit and --run go with --builds')

    root = args.installed_root
    if not root:
        p.error('give the installed OpenXTalk Lite folder, or --builds')
    if not os.path.isdir(root):
        raise SystemExit('error: %s is not a folder' % root)
    version = read_text(root, '.version')
    if not re.match(r'^[0-9A-Za-z][0-9A-Za-z._-]*$', version):
        raise SystemExit('error: %s/.version is missing or unusable' % root)
    name = args.name or 'oxt-runtimes-%s' % version

    included, empty, left_out = select(root)
    if not included:
        raise SystemExit('error: no files to include under %s' % root)
    infos = [file_info(root, pth) for pth in included]
    stock = None
    if args.stock_setup:
        print('comparing with the stock package in %s ...' % args.stock_setup)
        stock = stock_digests(args.stock_setup, set(included))
    text = provenance(name, root, infos, empty, stock, args.stock_setup, args.source_archive, left_out)

    os.makedirs(args.out, exist_ok=True)
    zip_path = os.path.join(args.out, name + '.zip')
    write_zip(zip_path, name, root, infos, empty, text)
    with open(os.path.join(args.out, 'PROVENANCE.md'), 'w', encoding='utf-8', newline='\n') as f:
        f.write(text)
    sha = sha256_file(zip_path)
    size = os.path.getsize(zip_path)

    counts = collections.Counter(group_title(i['path']) for i in infos)
    print('%s: %d files, %s bytes uncompressed' % (zip_path, len(infos), '{:,}'.format(sum(i['size'] for i in infos))))
    for title in counts:
        print('  %5d  %s' % (counts[title], title))
    if left_out:
        print('left out (external, not in this asset): %d files in %s'
              % (len(left_out), ', '.join(sorted({layout.group_of(x) for x in left_out}))))
    print('size   %d' % size)
    print('sha256 %s' % sha)
    if args.update_manifest:
        update_manifest(args.update_manifest, manifest_entry(name, version, sha, size))
        print('updated %s' % args.update_manifest)
    return 0


if __name__ == '__main__':
    sys.exit(main())
