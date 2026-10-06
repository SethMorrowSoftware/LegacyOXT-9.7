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

"""Download, cache and verify the external assets that packaging adds to
the installed layout (tools/oxt/external-assets.json).

External assets are files that packaging takes from a published archive
rather than from the build: the standalone runtimes for other platforms,
taken unchanged from OpenXTalk Lite 1.15 (oxt-runtimes-1.15, made by
make_runtimes_asset.py from Tom Perry's Windows release). Each asset is
one archive with a fixed URL, size and SHA-256:

  {
    "id":          unique name, used in messages and reports
    "url":         https:// URL of the archive
    "sha256":      lowercase hex SHA-256 of the archive
    "size":        archive size in bytes
    "kind":        "zip" (the only kind so far)
    "strip":       number of leading path components removed from every
                   archive member (0 keeps the paths as they are)
    "dest":        folder, relative to the installed root, that the
                   remaining paths are placed in ("" or "." for the root)
    "rename":      optional {"<member path after strip>": "<path relative
                   to dest>"} for members that go somewhere else
    "platforms":   optional list of the package.py platforms (--platform
                   names, or fnmatch patterns such as "mac-*") the asset is
                   for; without it, every platform gets it
    "exclude":     optional {"<platform pattern>": ["<glob>", ...]}: on a
                   platform that a pattern matches, members whose installed
                   path (after strip, rename and dest) matches a glob are
                   left out, and so are folder entries inside such a tree.
                   A glob is an installed path in which "*" matches within
                   one path component and "**" anything (as in layout.py).
                   This is how an asset stops supplying files that the
                   platform's own build produces: package.py treats a path
                   that comes from two places as an error.
    "exclude_note": optional text saying why (for people)
    "description", "licence", "source": text for people and reports
  }

The cache folder is --assets-cache, else the environment variable
OXT_ASSETS_CACHE, else <repo>/prebuilt/fetched-assets (ignored by git).
An archive is used from the cache when a file with the URL's file name is
there and its size and SHA-256 match; otherwise it is downloaded (HTTPS
only, redirects followed, transient errors retried) to a temporary name and
moved into place once it has been verified. A completed download whose
size or SHA-256 does not match the manifest is an error, never retried.

  python tools/oxt/fetch_assets.py [--repo DIR] [--manifest FILE]
                                   [--assets-cache DIR] [--offline]
                                   [--id ID ...] [--list]

Only the Python 3 standard library is used.
"""

import argparse
import fnmatch
import hashlib
import http.client
import json
import os
import posixpath
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_MANIFEST = os.path.join(HERE, 'external-assets.json')
DEFAULT_CACHE_REL = os.path.join('prebuilt', 'fetched-assets')
CACHE_ENV = 'OXT_ASSETS_CACHE'

REQUIRED_FIELDS = ('id', 'url', 'sha256', 'size', 'kind', 'strip', 'dest',
                   'description', 'licence', 'source')
KINDS = ('zip',)

ATTEMPTS = 4
TIMEOUT = 60
USER_AGENT = 'OpenXTalk-Lite-packager (+https://github.com/SethMorrowSoftware/OpenXTalk-Lite-1.15)'


class AssetError(Exception):
    pass


# ---------------------------------------------------------------------------
# Manifest

def load_manifest(path=DEFAULT_MANIFEST):
    """Return the list of asset dicts from the manifest, validated."""
    with open(path, encoding='utf-8') as f:
        data = json.load(f)
    assets = data.get('assets') if isinstance(data, dict) else None
    if not isinstance(assets, list):
        raise AssetError('%s: expected an object with an "assets" list' % path)
    seen = set()
    for a in assets:
        validate_asset(a, path)
        if a['id'] in seen:
            raise AssetError('%s: duplicate asset id %s' % (path, a['id']))
        seen.add(a['id'])
    return assets


def validate_asset(a, where='manifest'):
    if not isinstance(a, dict):
        raise AssetError('%s: every asset must be an object' % where)
    missing = [k for k in REQUIRED_FIELDS if k not in a]
    if missing:
        raise AssetError('%s: asset %s lacks %s' % (where, a.get('id', '?'), ', '.join(missing)))
    name = a['id']
    if not re.match(r'^[A-Za-z0-9][A-Za-z0-9._-]*$', str(name)):
        raise AssetError('%s: bad asset id %r' % (where, name))
    if a['kind'] not in KINDS:
        raise AssetError('%s: asset %s: kind must be one of %s' % (where, name, ', '.join(KINDS)))
    if urllib.parse.urlsplit(a['url']).scheme != 'https':
        raise AssetError('%s: asset %s: the URL must use https' % (where, name))
    if not re.match(r'^[0-9a-f]{64}$', str(a['sha256'])):
        raise AssetError('%s: asset %s: sha256 must be 64 lowercase hex digits' % (where, name))
    if not isinstance(a['size'], int) or a['size'] <= 0:
        raise AssetError('%s: asset %s: size must be a positive integer' % (where, name))
    if not isinstance(a['strip'], int) or a['strip'] < 0:
        raise AssetError('%s: asset %s: strip must be an integer >= 0' % (where, name))
    safe_relpath(a['dest'], allow_empty=True, what='asset %s dest' % name)
    rename = a.get('rename', {})
    if not isinstance(rename, dict):
        raise AssetError('%s: asset %s: rename must be an object' % (where, name))
    for k, v in rename.items():
        safe_relpath(k, what='asset %s rename source' % name)
        safe_relpath(v, what='asset %s rename target' % name)
    platforms = a.get('platforms')
    if platforms is not None and (not isinstance(platforms, list) or not platforms or
                                  not all(_is_platform_pattern(p) for p in platforms)):
        raise AssetError('%s: asset %s: platforms must be a non-empty list of platform names or patterns'
                         % (where, name))
    exclude = a.get('exclude', {})
    if not isinstance(exclude, dict):
        raise AssetError('%s: asset %s: exclude must be an object: platform pattern -> list of globs'
                         % (where, name))
    for pattern, globs in exclude.items():
        if not _is_platform_pattern(pattern):
            raise AssetError('%s: asset %s: exclude key %r is not a platform name or pattern'
                             % (where, name, pattern))
        if not isinstance(globs, list) or not globs:
            raise AssetError('%s: asset %s: exclude %s must be a non-empty list of globs' % (where, name, pattern))
        for g in globs:
            safe_glob(g, what='asset %s exclude %s' % (name, pattern))


def safe_glob(glob, what='glob'):
    """safe_relpath for an exclude glob: the same rules (checked with the
    wildcards replaced by a letter), but "*" and "?" are wildcards, not
    characters Windows cannot store. Returns the normalised glob."""
    if not isinstance(glob, str):
        raise AssetError('%s: not a string' % what)
    safe_relpath(re.sub(r'[*?]', 'x', glob), what=what)
    return '/'.join(x for x in glob.replace('\\', '/').split('/') if x not in ('', '.'))


def _is_platform_pattern(value):
    return isinstance(value, str) and re.match(r'^[a-z0-9_*?\[\]-]+$', value) is not None


def for_platform(asset, platform):
    """True when the asset is for the package.py platform (every asset is,
    unless its "platforms" says otherwise; platform None means any)."""
    patterns = asset.get('platforms')
    return platform is None or patterns is None or any(fnmatch.fnmatchcase(platform, p) for p in patterns)


def exclusions(asset, platform):
    """The exclude globs of the asset that apply on the platform (none
    when platform is None)."""
    if platform is None:
        return []
    out = []
    for pattern, globs in asset.get('exclude', {}).items():
        if fnmatch.fnmatchcase(platform, pattern):
            out.extend(safe_glob(g) for g in globs)
    return out


def glob_regex(glob):
    """A compiled regex for an installed-path glob: "*" within one path
    component, "?" one character in it, "**" anything (layout.py's Rule
    patterns; fetch_assets.py does not import layout.py)."""
    rx, i = [], 0
    while i < len(glob):
        if glob.startswith('**', i):
            rx.append('.*')
            i += 2
        elif glob[i] == '*':
            rx.append('[^/]*')
            i += 1
        elif glob[i] == '?':
            rx.append('[^/]')
            i += 1
        else:
            rx.append(re.escape(glob[i]))
            i += 1
    return re.compile(''.join(rx) + r'\Z')


def safe_relpath(path, allow_empty=False, what='path'):
    """Normalise a relative "/" path and reject anything that could leave
    the target folder. Returns '' for the root when allow_empty."""
    if not isinstance(path, str):
        raise AssetError('%s: not a string' % what)
    p = path.replace('\\', '/')
    if p in ('', '.'):
        if allow_empty:
            return ''
        raise AssetError('%s: empty path' % what)
    if p.startswith('/') or re.match(r'^[A-Za-z]:', p) or '\0' in p:
        raise AssetError('%s: %r is not a relative path' % (what, path))
    parts = [x for x in p.split('/') if x not in ('', '.')]
    if any(x == '..' for x in parts):
        raise AssetError('%s: %r contains ".."' % (what, path))
    if any(re.search(r'[<>:"|?*\x00-\x1f]', x) or x.endswith((' ', '.')) for x in parts):
        raise AssetError('%s: %r is not a valid Windows path' % (what, path))
    return '/'.join(parts)


def asset_file_name(asset):
    name = posixpath.basename(urllib.parse.unquote(urllib.parse.urlsplit(asset['url']).path))
    if not name or name in ('.', '..') or re.search(r'[\\/:*?"<>|]', name):
        raise AssetError('asset %s: cannot take a file name from %s' % (asset['id'], asset['url']))
    return name


# ---------------------------------------------------------------------------
# Cache and download

def cache_dir(repo_root=None, override=None):
    if override:
        return os.path.abspath(override)
    env = os.environ.get(CACHE_ENV)
    if env:
        return os.path.abspath(env)
    if repo_root is None:
        repo_root = os.path.dirname(os.path.dirname(HERE))
    return os.path.join(os.path.abspath(repo_root), DEFAULT_CACHE_REL)


def file_digest(path):
    h = hashlib.sha256()
    size = 0
    with open(path, 'rb') as f:
        while True:
            b = f.read(1 << 20)
            if not b:
                break
            h.update(b)
            size += len(b)
    return h.hexdigest(), size


def check_file(asset, path):
    """None if path matches the manifest entry, else the reason."""
    digest, size = file_digest(path)
    if size != asset['size']:
        return 'size %d, expected %d' % (size, asset['size'])
    if digest != asset['sha256']:
        return 'SHA-256 %s, expected %s' % (digest, asset['sha256'])
    return None


class _HttpsOnlyRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urllib.parse.urlsplit(newurl).scheme != 'https':
            raise AssetError('refusing a redirect to a non-HTTPS URL: %s' % newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class _Transient(Exception):
    pass


def _download_once(asset, part, log):
    opener = urllib.request.build_opener(_HttpsOnlyRedirects())
    request = urllib.request.Request(asset['url'], headers={
        'User-Agent': USER_AGENT, 'Accept': 'application/octet-stream'})
    try:
        response = opener.open(request, timeout=TIMEOUT)
    except urllib.error.HTTPError as e:
        if e.code in (408, 425, 429) or e.code >= 500:
            raise _Transient('HTTP %d %s' % (e.code, e.reason))
        hint = ' (not published yet?)' if e.code == 404 else ''
        raise AssetError('asset %s: HTTP %d %s for %s%s' % (asset['id'], e.code, e.reason, asset['url'], hint))
    except (urllib.error.URLError, OSError, http.client.HTTPException) as e:
        raise _Transient(str(getattr(e, 'reason', e)))
    with response:
        final = response.geturl()
        if urllib.parse.urlsplit(final).scheme != 'https':
            raise AssetError('asset %s: the download ended at a non-HTTPS URL: %s' % (asset['id'], final))
        length = response.headers.get('Content-Length')
        expected = int(length) if length and length.isdigit() else None
        if expected is not None and expected != asset['size']:
            raise AssetError('asset %s: %s is %d bytes, the manifest says %d'
                             % (asset['id'], asset['url'], expected, asset['size']))
        h = hashlib.sha256()
        got = 0
        last = time.time()
        try:
            with open(part, 'wb') as out:
                while True:
                    b = response.read(1 << 20)
                    if not b:
                        break
                    out.write(b)
                    h.update(b)
                    got += len(b)
                    if got > asset['size']:
                        raise AssetError('asset %s: %s is larger than the %d bytes in the manifest'
                                         % (asset['id'], asset['url'], asset['size']))
                    if time.time() - last > 10:
                        log('  %s: %.1f of %.1f MB' % (asset['id'], got / 1e6, asset['size'] / 1e6))
                        last = time.time()
        except (OSError, urllib.error.URLError, http.client.HTTPException) as e:
            raise _Transient('interrupted after %d bytes: %s' % (got, e))
    if got < asset['size'] and (expected is None or got < expected):
        raise _Transient('connection closed after %d of %d bytes' % (got, asset['size']))
    return h.hexdigest(), got


def fetch(asset, cache, offline=False, log=print):
    """Return the path of the verified archive in the cache, downloading it
    when needed. Raises AssetError."""
    name = asset_file_name(asset)
    path = os.path.join(cache, name)
    if os.path.isfile(path):
        problem = check_file(asset, path)
        if problem is None:
            log('asset %s: using %s (SHA-256 verified)' % (asset['id'], path))
            return path
        log('asset %s: cached %s does not match the manifest (%s)' % (asset['id'], path, problem))
    if offline:
        raise AssetError('asset %s: %s is not in the cache %s and downloads are disabled'
                         % (asset['id'], name, cache))
    os.makedirs(cache, exist_ok=True)
    part = path + '.part'
    delay = 5
    for attempt in range(1, ATTEMPTS + 1):
        log('asset %s: downloading %s (attempt %d of %d)' % (asset['id'], asset['url'], attempt, ATTEMPTS))
        try:
            digest, size = _download_once(asset, part, log)
        except _Transient as e:
            _remove(part)
            if attempt == ATTEMPTS:
                raise AssetError('asset %s: download failed: %s' % (asset['id'], e))
            log('  %s; retrying in %d s' % (e, delay))
            time.sleep(delay)
            delay *= 2
            continue
        except BaseException:
            _remove(part)
            raise
        if size != asset['size'] or digest != asset['sha256']:
            _remove(part)
            raise AssetError('asset %s: the download from %s does not match the manifest: '
                             'size %d (expected %d), SHA-256 %s (expected %s)'
                             % (asset['id'], asset['url'], size, asset['size'], digest, asset['sha256']))
        os.replace(part, path)
        log('asset %s: saved %s (%d bytes, SHA-256 verified)' % (asset['id'], path, size))
        return path
    raise AssetError('asset %s: download failed' % asset['id'])


def _remove(path):
    try:
        os.remove(path)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Archive members

def plan_members(asset, archive, platform=None, stats=None):
    """Return (files, folders) of an archive after strip, rename and dest:
    files is [(installed path, member name)], folders the installed paths
    of its directory entries (so that empty folders are kept). Other
    folders are created as needed for the files. With a package.py
    platform name, the members that the asset's "exclude" leaves out on it
    are dropped; stats (a dict) then gets "excluded" (the number of files
    dropped) and "unused" (the globs that matched nothing, which the
    caller should report: a stale glob excludes nothing)."""
    if asset['kind'] != 'zip':
        raise AssetError('asset %s: unsupported kind %s' % (asset['id'], asset['kind']))
    dest = safe_relpath(asset['dest'], allow_empty=True)
    rename = {safe_relpath(k): safe_relpath(v) for k, v in asset.get('rename', {}).items()}
    globs = [(g, glob_regex(g)) for g in exclusions(asset, platform)]
    matched, excluded = set(), 0
    used = set()
    files, folders = [], []
    with zipfile.ZipFile(archive) as z:
        for info in z.infolist():
            is_dir = info.filename.endswith('/')
            rel = safe_relpath(info.filename.rstrip('/'), what='asset %s member' % asset['id'])
            parts = rel.split('/')
            if len(parts) <= asset['strip']:
                if is_dir:
                    continue
                raise AssetError('asset %s: member %s has fewer than %d folders to strip'
                                 % (asset['id'], info.filename, asset['strip']))
            rel = '/'.join(parts[asset['strip']:])
            if not is_dir and rel in rename:
                used.add(rel)
                rel = rename[rel]
            target = (dest + '/' + rel) if dest else rel
            # A folder entry goes with the tree it is in or is the root of
            hits = [g for g, rx in globs if rx.match(target) or (is_dir and rx.match(target + '/'))]
            if hits:
                matched.update(hits)
                excluded += 0 if is_dir else 1
                continue
            if is_dir:
                folders.append(target)
            else:
                files.append((target, info.filename))
    if stats is not None:
        stats['excluded'] = excluded
        stats['unused'] = [g for g, _ in globs if g not in matched]
    unused = sorted(set(rename) - used)
    if unused:
        raise AssetError('asset %s: rename lists paths the archive does not have: %s'
                         % (asset['id'], ', '.join(unused)))
    seen = {}
    for target, member in files:
        key = target.lower()
        if key in seen:
            raise AssetError('asset %s: %s and %s both go to %s'
                             % (asset['id'], seen[key], member, target))
        seen[key] = member
    return files, sorted(set(folders))


# ---------------------------------------------------------------------------
# Command line

def main(argv=None):
    parser = argparse.ArgumentParser(
        description='Download and verify the external assets listed in '
                    'tools/oxt/external-assets.json.')
    parser.add_argument('--repo', help='repository root (default: two levels above this script)')
    parser.add_argument('--manifest', default=DEFAULT_MANIFEST, help='asset manifest (default: %(default)s)')
    parser.add_argument('--assets-cache', metavar='DIR',
                        help='cache folder (default: $%s, else <repo>/%s)'
                             % (CACHE_ENV, DEFAULT_CACHE_REL.replace(os.sep, '/')))
    parser.add_argument('--id', action='append', help='only this asset (repeatable)')
    parser.add_argument('--offline', action='store_true', help='use the cache only')
    parser.add_argument('--list', action='store_true', help='list the assets and exit')
    args = parser.parse_args(argv)
    try:
        assets = load_manifest(args.manifest)
        if args.id:
            unknown = sorted(set(args.id) - {a['id'] for a in assets})
            if unknown:
                raise AssetError('unknown asset id: ' + ', '.join(unknown))
            assets = [a for a in assets if a['id'] in args.id]
        if args.list:
            for a in assets:
                print('%s  %s  %d bytes  %s' % (a['id'], a['sha256'], a['size'], a['url']))
            return 0
        cache = cache_dir(args.repo, args.assets_cache)
        for a in assets:
            fetch(a, cache, args.offline)
    except AssetError as e:
        sys.stderr.write('error: %s\n' % e)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
