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

"""Put the files of one LegacyOXT release together from the packages of
the three platforms, and check them before and after they are uploaded.

  python3 tools/ci/release_assets.py assemble --version V --artifacts DIR --out DIR
  python3 tools/ci/release_assets.py check-uploaded --dir DIR --assets FILE
  python3 tools/ci/release_assets.py list --version V

The release workflow (.github/workflows/release.yml) downloads the CI
artifacts that hold the packages, each into a folder of its own named
after it, as actions/download-artifact writes a named artifact:

  LegacyOXT-win-x86_64     build-windows.yml, job "Build win-x86_64"
  LegacyOXT-mac-universal  build-macos.yml, job "Package mac-universal"
  LegacyOXT-linux-x86_64   build-linux.yml, job "Package linux-x86_64"

assemble checks each artifact folder of --artifacts:

  - its SHA256SUMS lists every other file of the folder once, and every
    file matches its line (a file that changed or went missing between
    packaging and the release fails here, not on a user's computer);
  - it holds exactly the files ASSETS names for it, where <root> is
    LegacyOXT-<version>: none missing, and none that the table does not
    name (a new kind of file is added to the table on purpose, together
    with its description in tools/ci/release_notes.py);

and across the artifacts that no file name comes from two of them. Nothing else in DIR is looked at (the build
and test logs, the per-architecture build outputs), and nothing is
written when a check fails. Then it links (or, across file systems,
copies) every file except the artifacts' own SHA256SUMS into --out and
writes one SHA256SUMS there over all of them: "<sha256>  <name>" lines,
sorted by name (byte order), LF line endings, as sha256sum writes them,
so that "sha256sum -c SHA256SUMS --ignore-missing" checks any one
download.

check-uploaded compares the files of --dir (the assembled release, with
its SHA256SUMS) with the release's assets as GitHub lists them, one per
line of --assets: name, size in bytes, state and digest, separated by
tabs (the digest "sha256:<hex>" may be empty: GitHub computes it only
for assets uploaded since mid-2025). Every file must be an asset with
the same size, the state "uploaded" and, where GitHub gives one, the
same SHA-256; and no other asset may be there.

list prints the names of the release's files for --version, one per
line (SHA256SUMS last).

Exit status: 0 success, 1 failed checks (listed; under GitHub Actions as
::error:: annotations), 2 usage errors.

Only the Python 3 standard library is used.
"""

import argparse
import collections
import hashlib
import os
import re
import shutil
import sys

PRODUCT = 'LegacyOXT'
SUMS = 'SHA256SUMS'

# The version rule of BUILD_SHORT_VERSION, as the build workflows check it
VERSION_RE = re.compile(r'^[0-9]+(\.[0-9]+){1,3}(-[0-9A-Za-z][0-9A-Za-z.-]*)?$')

# What a release holds, per CI artifact:
# (artifact, platform, the files after "<root>" in their names). Linux arm64
# is built but not packaged, so it has no artifact here.
ASSETS = (
    ('LegacyOXT-win-x86_64', 'win-x86_64', (
        '-win-x86_64-setup.exe',
        '-win-x86_64-portable.zip',
        '-win-x86_64-binaries.zip',
        '-win-x86_64-symbols.zip',
    )),
    ('LegacyOXT-mac-universal', 'mac-universal', (
        '-mac-universal.dmg',
        '-mac-universal.zip',
        '-mac-universal-binaries.tar.xz',
        '-mac-universal-symbols.zip',
    )),
    ('LegacyOXT-linux-x86_64', 'linux-x86_64', (
        '-linux-x86_64.tar.xz',
        '-linux-x86_64-binaries.tar.xz',
        '-linux-x86_64-symbols.tar.xz',
    )),
)

SUM_LINE = re.compile(r'^([0-9A-Fa-f]{64}) [ *](.+)$')


class Problems(list):
    def add(self, where, message):
        self.append('%s: %s' % (where, message))


def package_root(version):
    return '%s-%s' % (PRODUCT, version)


def expected(version):
    """[(artifact, platform, [file names])] in ASSETS order."""
    root = package_root(version)
    return [(artifact, platform, [root + suffix for suffix in suffixes])
            for artifact, platform, suffixes in ASSETS]


def release_files(version):
    """Every file of the release, SHA256SUMS last."""
    return [name for _, _, names in expected(version) for name in names] + [SUMS]


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def read_sums(path, problems, where):
    """{name: sha256} from a sha256sum file; problems for bad or doubled lines."""
    sums = collections.OrderedDict()
    with open(path, 'rb') as f:
        data = f.read()
    try:
        text = data.decode('utf-8')
    except UnicodeDecodeError:
        problems.add(where, '%s is not UTF-8 text' % SUMS)
        return sums
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        m = SUM_LINE.match(line)
        if not m:
            problems.add(where, '%s line %d is not "<sha256>  <file>": %r' % (SUMS, number, line))
            continue
        digest, name = m.group(1).lower(), m.group(2)
        if '/' in name or '\\' in name:
            problems.add(where, '%s line %d names a file in a folder: %s' % (SUMS, number, name))
        elif name in sums:
            problems.add(where, '%s lists %s twice' % (SUMS, name))
        else:
            sums[name] = digest
    return sums


def check_artifact(folder, artifact, names, problems):
    """Checks one artifact folder; returns {name: sha256} of its files."""
    where = artifact
    if not os.path.isdir(folder):
        problems.add(where, 'not downloaded (no folder %s)' % folder)
        return {}
    present = set()
    for entry in sorted(os.listdir(folder)):
        path = os.path.join(folder, entry)
        if os.path.isdir(path) or os.path.islink(path):
            problems.add(where, '%s is not a plain file (the release takes files only)' % entry)
        else:
            present.add(entry)
    if SUMS not in present:
        problems.add(where, 'has no %s' % SUMS)
        return {}
    sums = read_sums(os.path.join(folder, SUMS), problems, where)
    files = present - set([SUMS])
    for name in sorted(set(names) - files):
        problems.add(where, '%s is missing' % name)
    for name in sorted(files - set(names)):
        problems.add(where, '%s is not a file of the release (see ASSETS in %s)'
                     % (name, os.path.basename(__file__)))
    for name in sorted(files - set(sums)):
        problems.add(where, '%s is not listed in its %s' % (name, SUMS))
    for name in sorted(set(sums) - files):
        problems.add(where, '%s lists %s, which is not there' % (SUMS, name))
    digests = {}
    for name in sorted(files & set(sums)):
        digest = sha256(os.path.join(folder, name))
        if digest != sums[name]:
            problems.add(where, '%s does not match its %s line (%s, expected %s)'
                         % (name, SUMS, digest, sums[name]))
        else:
            digests[name] = digest
    return digests


def link_or_copy(src, dst):
    if os.path.lexists(dst):
        os.remove(dst)
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)


def assemble(version, artifacts_dir, out, log=print):
    problems = Problems()
    if not VERSION_RE.match(version):
        problems.add('--version', '%r is not a version such as 0.1.0 or 0.1.0-beta.1' % version)
        return problems, None
    found = collections.OrderedDict()   # name -> (artifact, folder, sha256)
    for artifact, platform, names in expected(version):
        folder = os.path.join(artifacts_dir, artifact)
        digests = check_artifact(folder, artifact, names, problems)
        for name, digest in digests.items():
            if name in found:
                problems.add(artifact, '%s comes from %s as well; every file of the release is made once'
                             % (name, found[name][0]))
            else:
                found[name] = (artifact, folder, digest)
        log('%-26s %-14s %d of %d files checked against its %s'
            % (artifact, platform, len(digests), len(names), SUMS))
    if problems:
        return problems, None

    if os.path.isdir(out) and os.listdir(out):
        problems.add('--out', '%s is not empty' % out)
        return problems, None
    os.makedirs(out, exist_ok=True)
    lines = []
    for name in sorted(found):          # byte order: the names are ASCII
        artifact, folder, digest = found[name]
        link_or_copy(os.path.join(folder, name), os.path.join(out, name))
        lines.append('%s  %s' % (digest, name))
    with open(os.path.join(out, SUMS), 'w', encoding='utf-8', newline='\n') as f:
        f.write('\n'.join(lines) + '\n')
    log('')
    log('Release files in %s:' % out)
    total = 0
    for name in sorted(found):
        size = os.path.getsize(os.path.join(out, name))
        total += size
        log('  %-52s %10.1f MB  %s' % (name, size / 1048576.0, found[name][2]))
    log('  %s (%d files)' % (SUMS, len(lines)))
    log('  %d files, %.1f MB' % (len(found) + 1, total / 1048576.0))
    return problems, lines


def read_assets(path, problems):
    """[(name, size, state, digest)] from check-uploaded's --assets file."""
    rows = []
    with open(path, encoding='utf-8') as f:
        for number, line in enumerate(f, 1):
            line = line.rstrip('\r\n')
            if not line:
                continue
            fields = line.split('\t')
            if len(fields) < 3 or not fields[1].isdigit():
                problems.add(path, 'line %d is not "name<TAB>size<TAB>state[<TAB>digest]": %r' % (number, line))
                continue
            fields += [''] * (4 - len(fields))
            rows.append((fields[0], int(fields[1]), fields[2], fields[3]))
    return rows


def check_uploaded(folder, assets_path, log=print):
    problems = Problems()
    if not os.path.isfile(os.path.join(folder, SUMS)):
        problems.add(folder, 'has no %s; run assemble first' % SUMS)
        return problems
    sums = read_sums(os.path.join(folder, SUMS), problems, folder)
    sums[SUMS] = sha256(os.path.join(folder, SUMS))
    local = dict((name, os.path.getsize(os.path.join(folder, name))) for name in sums
                 if os.path.isfile(os.path.join(folder, name)))
    for name in sorted(set(sums) - set(local)):
        problems.add(folder, '%s is listed in %s but not there' % (name, SUMS))
    remote = collections.OrderedDict()
    for name, size, state, digest in read_assets(assets_path, problems):
        if name in remote:
            problems.add('release', 'has two assets named %s' % name)
        remote[name] = (size, state, digest)
    for name in sorted(local):
        if name not in remote:
            problems.add('release', '%s was not uploaded' % name)
            continue
        size, state, digest = remote[name]
        if size != local[name]:
            problems.add('release', '%s has %d bytes, the file %d' % (name, size, local[name]))
        if state != 'uploaded':
            problems.add('release', '%s is in the state "%s", not "uploaded"' % (name, state))
        if digest:
            if not digest.startswith('sha256:'):
                problems.add('release', '%s has the digest "%s", not a sha256' % (name, digest))
            elif digest[len('sha256:'):].lower() != sums[name]:
                problems.add('release', '%s has the SHA-256 %s on GitHub, %s here'
                             % (name, digest[len('sha256:'):], sums[name]))
    for name in sorted(set(remote) - set(local)):
        problems.add('release', 'has the asset %s, which is not a file of this release' % name)
    checked = sum(1 for name in local if name in remote and remote[name][2])
    log('%d files compared with the release\'s %d assets (sizes and states; SHA-256 of %d, where GitHub '
        'gives a digest)' % (len(local), len(remote), checked))
    return problems


def report(problems, title):
    for p in problems:
        if os.environ.get('GITHUB_ACTIONS') == 'true':
            print('::error title=%s::%s' % (title, p))
        else:
            sys.stderr.write('error: %s\n' % p)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='command')
    a = sub.add_parser('assemble', help='check the artifacts and write the release folder')
    a.add_argument('--version', required=True, help='product version (BUILD_SHORT_VERSION)')
    a.add_argument('--artifacts', required=True, help='folder with one folder per downloaded artifact')
    a.add_argument('--out', required=True, help='release folder to write (new or empty)')
    c = sub.add_parser('check-uploaded', help='compare the release folder with the uploaded assets')
    c.add_argument('--dir', required=True, help='the release folder that assemble wrote')
    c.add_argument('--assets', required=True, help='name, size, state, digest per line (tabs)')
    lst = sub.add_parser('list', help='print the names of the release files')
    lst.add_argument('--version', required=True)
    args = ap.parse_args(argv)

    if args.command == 'assemble':
        problems, _ = assemble(args.version, args.artifacts, args.out)
        report(problems, 'Release files')
        if problems:
            print('%d problem(s); no release folder was written' % len(problems))
            return 1
        return 0
    if args.command == 'check-uploaded':
        problems = check_uploaded(args.dir, args.assets)
        report(problems, 'Uploaded release files')
        return 1 if problems else 0
    if args.command == 'list':
        if not VERSION_RE.match(args.version):
            sys.stderr.write('error: %r is not a version\n' % args.version)
            return 2
        print('\n'.join(release_files(args.version)))
        return 0
    ap.print_usage(sys.stderr)
    return 2


if __name__ == '__main__':
    sys.exit(main())
