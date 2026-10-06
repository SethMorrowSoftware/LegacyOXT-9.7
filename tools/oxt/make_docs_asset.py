#!/usr/bin/env python3
"""Build a legacyoxt-docs-<label>.zip release asset: the IDE's documentation
data (the Dictionary's API database and data, and the guides' data), as
LiveCode's docs builder writes it (tools/ci/build_docs.sh, which the Linux
package job runs and uploads as the artifact LegacyOXT-docs).

  python tools/oxt/make_docs_asset.py --data DIR --label LABEL --out DIR
      [--commit SHA] [--run URL] [--update-manifest [FILE]]

--data is that artifact's folder: the contents of
ide/Documentation/html_viewer/resources/data (api, api_livecode_script,
api_livecode_builder, guide). Installer/package.txt installs them into
Documentation/html_viewer/resources/data, and LiveCode's repositories never
kept them, so every package that does not build them itself takes them from
this asset: tools/oxt/package.py installs its files at their paths (the
Linux package builds its own, so the asset's "exclude" leaves them out
there).

Writes <out>/<name>.zip (every file under <name>/, then <name>/PROVENANCE.md
listing every file with its size and SHA-256), <out>/PROVENANCE.md and
<out>/<name>.manifest.json, the entry for tools/oxt/external-assets.json;
--update-manifest puts that entry in place of the manifest's
legacyoxt-docs-* entries. Only the Python 3 standard library is used.
"""

import argparse
import calendar
import collections
import hashlib
import json
import os
import re
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_URL = 'https://github.com/SethMorrowSoftware/LegacyOXT-9.7'
ASSET_PREFIX = 'legacyoxt-docs-'
RELEASE_TAG_FMT = 'assets-{label}'
DEFAULT_MANIFEST = os.path.join(HERE, 'external-assets.json')
DEST = 'Documentation/html_viewer/resources/data'
# What the docs builder must have written (docs_builder.livecodescript)
REQUIRED = ('api', 'api_livecode_script', 'api_livecode_builder', 'guide')
# The packages that build the data themselves
OWN = collections.OrderedDict([('linux-x86_64', [DEST + '/**'])])
ZIP_EPOCH = 315532800


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for b in iter(lambda: f.read(1 << 20), b''):
            h.update(b)
    return h.hexdigest()


def zip_info(name, mtime):
    t = max(int(mtime), ZIP_EPOCH)
    zi = zipfile.ZipInfo(name, date_time=tuple(__import__('time').gmtime(t))[:6])
    zi.compress_type = zipfile.ZIP_DEFLATED
    zi.create_system = 3
    zi.external_attr = (0o100644 << 16)
    return zi


def engine_version(repo):
    with open(os.path.join(repo, 'version'), encoding='utf-8') as f:
        for line in f:
            m = re.match(r'^\s*BUILD_SHORT_VERSION\s*=\s*(\S+)', line)
            if m:
                return m.group(1)
    raise SystemExit('error: no BUILD_SHORT_VERSION in the file version')


def collect(data):
    files = []
    for dirpath, dirnames, filenames in os.walk(data):
        dirnames.sort()
        for name in sorted(filenames):
            full = os.path.join(dirpath, name)
            if os.path.islink(full):
                raise SystemExit('error: %s is a symbolic link' % full)
            rel = os.path.relpath(full, data).replace(os.sep, '/')
            files.append({'path': DEST + '/' + rel, 'source': full, 'size': os.path.getsize(full),
                          'sha256': sha256_file(full), 'mtime': int(os.stat(full).st_mtime)})
    return files


def provenance(name, version, commit, run, files):
    L = ['# %s: documentation data for LegacyOXT' % name, '',
         'The IDE\'s documentation data, as LiveCode\'s docs builder writes it',
         '(`builder/builder_tool.livecodescript --stage docs`, run by `tools/ci/build_docs.sh`',
         'with an engine built from LiveCode\'s source in %s):' % REPO_URL,
         'the Dictionary\'s API database (`api/api.sqlite`) and data for LiveCode Script and',
         'LiveCode Builder, and the guides\' data. `Installer/package.txt` installs them into',
         '`%s`; LiveCode\'s repositories never kept them.' % DEST, '',
         '- Source: %s%s' % (REPO_URL, ', commit `%s`' % commit if commit else ''),
         '- LiveCode Community `%s` (`BUILD_SHORT_VERSION`)' % version]
    if run:
        L.append('- Built by %s' % run)
    L += ['', 'The dictionary and guides are LiveCode Ltd\'s documentation, under the GNU GPL v3',
          'like the rest of LiveCode Community. As in LiveCode\'s own Community builds, the',
          'builder also documents the externals of LiveCode\'s mergExt and tsNet bundles of the',
          'Business edition (it reads only their `api.lcdoc` files; the packages ship neither',
          'tsNet nor the Business mergExt externals). `tools/ci/build_docs.sh` says where they',
          'come from.', '', '## Files', '',
          '| path | bytes | SHA-256 |', '|---|---:|---|']
    for f in files:
        L.append('| `%s` | %d | `%s` |' % (f['path'].replace('|', '\\|'), f['size'], f['sha256']))
    L += ['', 'Total: %d files, %s bytes.' % (len(files), '{:,}'.format(sum(f['size'] for f in files))), '']
    return '\n'.join(L)


def manifest_entry(name, label, sha, size, commit):
    return collections.OrderedDict([
        ('id', name),
        ('url', '%s/releases/download/%s/%s.zip' % (REPO_URL, RELEASE_TAG_FMT.format(label=label), name)),
        ('sha256', sha),
        ('size', size),
        ('kind', 'zip'),
        ('strip', 1),
        ('dest', ''),
        ('rename', collections.OrderedDict([('PROVENANCE.md', 'PROVENANCE-%s.md' % name)])),
        ('exclude', OWN),
        ('exclude_note', 'The Linux package builds the documentation data itself (tools/ci/build_docs.sh).'),
        ('description', 'The IDE\'s documentation data (the Dictionary and the guides), written by LiveCode\'s '
                        'docs builder with an engine built from this repository (%s). See PROVENANCE.md in the '
                        'archive.' % ('commit %s' % commit[:12] if commit else 'see PROVENANCE.md')),
        ('licence', 'GPL-3.0 (LiveCode Community\'s documentation)'),
        ('source', 'LegacyOXT\'s CI (tools/ci/build_docs.sh)%s, made with tools/oxt/make_docs_asset.py'
                   % (' at commit %s' % commit if commit else '')),
    ])


def replace_entry(path, entry):
    with open(path, encoding='utf-8') as f:
        data = json.load(f, object_pairs_hook=collections.OrderedDict)
    out, placed = [], False
    for a in data.get('assets', []):
        if str(a.get('id', '')).startswith(ASSET_PREFIX):
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


def main(argv=None):
    ap = argparse.ArgumentParser(description='Build the legacyoxt-docs release asset from the docs builder\'s '
                                             'output.')
    ap.add_argument('--data', required=True, help='the LegacyOXT-docs artifact (the data folder\'s contents)')
    ap.add_argument('--label', required=True, help='the asset\'s label (legacyoxt-docs-<label>)')
    ap.add_argument('--out', required=True, help='folder for the zip, PROVENANCE.md and the manifest entry')
    ap.add_argument('--commit', help='the commit the data was built from')
    ap.add_argument('--run', help='the CI run that built it')
    ap.add_argument('--update-manifest', nargs='?', const=DEFAULT_MANIFEST, metavar='FILE')
    args = ap.parse_args(argv)
    if not re.match(r'^[0-9A-Za-z][0-9A-Za-z._-]*$', args.label):
        ap.error('--label: letters, digits, ".", "_" and "-"')
    data = os.path.abspath(args.data)
    missing = [d for d in REQUIRED if not os.path.isdir(os.path.join(data, d)) or not os.listdir(os.path.join(data, d))]
    if missing:
        raise SystemExit('error: %s has no %s: not the docs builder\'s output' % (data, ', '.join(missing)))
    name = ASSET_PREFIX + args.label
    version = engine_version(os.path.dirname(os.path.dirname(HERE)))
    files = collect(data)
    text = provenance(name, version, args.commit, args.run, files)
    os.makedirs(args.out, exist_ok=True)
    zip_path = os.path.join(args.out, name + '.zip')
    tmp = zip_path + '.tmp'
    newest = max(f['mtime'] for f in files)
    with zipfile.ZipFile(tmp, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for f in files:
            with open(f['source'], 'rb') as s, z.open(zip_info('%s/%s' % (name, f['path']), f['mtime']), 'w') as d:
                d.write(s.read())
        z.writestr(zip_info('%s/PROVENANCE.md' % name, newest), text.encode('utf-8'))
    os.replace(tmp, zip_path)
    with open(os.path.join(args.out, 'PROVENANCE.md'), 'w', encoding='utf-8', newline='\n') as f:
        f.write(text)
    size, sha = os.path.getsize(zip_path), sha256_file(zip_path)
    entry = manifest_entry(name, args.label, sha, size, args.commit)
    with open(os.path.join(args.out, name + '.manifest.json'), 'w', encoding='utf-8', newline='\n') as f:
        json.dump(entry, f, indent=2, ensure_ascii=False)
        f.write('\n')
    counts = collections.Counter(f['path'][len(DEST) + 1:].split('/')[0] for f in files)
    print('%s: %d files, %s bytes uncompressed' % (zip_path, len(files), '{:,}'.format(sum(f['size'] for f in files))))
    for k in sorted(counts):
        print('  %5d  %s' % (counts[k], k))
    print('size   %d' % size)
    print('sha256 %s' % sha)
    if args.update_manifest:
        replace_entry(args.update_manifest, entry)
        print('updated %s' % args.update_manifest)
    return 0


if __name__ == '__main__':
    sys.exit(main())
