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

"""Pin, fetch and build the xTalk Suite extensions that OpenXTalk-Lite ships
built in (tools/oxt/xtalk-extensions.json).

The extensions are consumed from their member repositories
(github.com/SethMorrowSoftware/<Repo>) at pinned commits; nothing of them is
kept in this repository. The manifest lists, per member, the repository, the
commit, a version and the exact files taken, each with the SHA-256 and size
of its git blob (LF line endings; a CRLF working copy has other bytes), and
the extensions made from them:

  lcb  a LiveCode Builder library: the .lcb source is compiled with the
       build's lc-compile into module.lcm and manifest.xml, and the member's
       native libraries (src/code/<platform-id>/<token>.<ext>, all platform
       ids the manifest lists) go into code/<platform-id>/;
  lcs  a LiveCode Script library (a script-only stack): the file is shipped
       with a 'script "<stack>"' header when it has none and an appended
       extensionInitialize / extensionFinalize pair, which the IDE sends to
       put the library into the message path; its manifest.xml is written
       from the JSON.

Every extension folder also gets the member's licence files in licenses/,
and one that ships Visual C++ runtime DLLs also a notice of Microsoft's
terms for them (licenses/Microsoft-Visual-C++-Runtime.txt).

Subcommands

  pin    [--member NAME] [--ref REF [--allow-off-branch]]
         resolve the member's commit (default: HEAD of its default branch,
         through git ls-remote or the GitHub API; it must be on the default
         branch, which --allow-off-branch waives for a branch or tag named
         by --ref, never for a SHA-1), download every listed file at that
         commit and rewrite the manifest with the commit, the version
         (version_from) and each file's sha256, size and, for Windows
         libraries, the DLLs it imports. Files with their own
         "repository" and "commit" (the OpenSSL licence text) keep their
         commit and are only re-hashed. This is how maintainers take a newer
         version of a member.
  fetch  [--cache DIR] [--offline] [--platforms LIST] [--member NAME]
         download every pinned file into the cache and verify its size and
         SHA-256 (a mismatch is fatal and the file is deleted), then check
         the native libraries against the member's src/code/MANIFEST.sha256.
  build  --bin DIR --out DIR [--platforms LIST] [--vc-redist DIR]
         [--allow-unpinned-vc-runtime] [--cache DIR] [--offline]
         fetch, then compile and assemble <out>/<folder>/ for every
         extension and write <out>/XTALK-EXTENSIONS.txt. Folders this tool
         made before (listed in the old XTALK-EXTENSIONS.txt) are replaced;
         nothing else in <out> is touched.
  pin-vc-runtime --vc-redist DIR
         pin the Visual C++ runtime DLLs that the Windows libraries need
         (from their recorded "imports", and what those DLLs import in
         turn) to their SHA-256 and size in that redistributable folder:
         rewrites the manifest's "vc_runtime" block.
  export --out FILE.zip [--cache DIR] [--offline]
         fetch, then write every pinned file (all members and platforms)
         into a reproducible zip in the cache layout <Repo>/<commit>/<path>,
         with a copy of the manifest. Tag builds attach it to the release,
         so that the release can be rebuilt ("build --cache <unzipped>
         --offline") even if a member repository loses the pinned commit.
  list   print the members, their extensions and probes.

The cache folder is --cache, else the environment variable OXT_XTALK_CACHE,
else the xtalk folder of the external-assets cache (OXT_ASSETS_CACHE, else
<repo>/prebuilt/fetched-assets, ignored by git), the same folder that
package.py uses; a file lives at <cache>/<Repo>/<commit>/<path>. Downloads
use tools/oxt/fetch_assets.py (HTTPS only, retries with backoff, verified
before they are moved into place).

The Visual C++ runtime. enetxt.dll and box2dxt.dll are built with the
dynamic CRT and import MSVCP140 / VCRUNTIME140 / VCRUNTIME140_1, which
Windows does not have without the Visual C++ Redistributable and which the
OpenXTalk-Lite engine (static CRT) does not ship. With --vc-redist (Visual
Studio's redistributable folder, VCToolsRedistDir, which has
x64\\Microsoft.VC14x.CRT and x86\\Microsoft.VC14x.CRT) build copies every
DLL that a library in code/x86_64-win32 or code/x86-win32 imports and that
is neither a Windows system DLL (SYSTEM_DLLS) nor already in that folder
into the folder (app-local deployment; the engine loads extension libraries
with LOAD_WITH_ALTERED_SEARCH_PATH, so Windows looks for their DLLs next to
them first). Every copy must be the one "vc_runtime" in the manifest pins
(SHA-256 and size per platform and DLL), so that the package does not
depend on which Visual Studio the build machine has; with
--allow-unpinned-vc-runtime (never for a release) another copy only
warns. It checks that the copies export every function the library
imports from them, which catches a redistributable that lacks a function
but not every older one, and warns when a copy's file version is older
than the MSVC linker that built the library (Microsoft supports only a
runtime at least as new as the newest toolset used). Without --vc-redist
it warns and lists the libraries that cannot load on a PC without the
redistributable.

Only the Python 3 standard library is used.
"""

import argparse
import collections
import hashlib
import http.client
import json
import os
import re
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
import textwrap
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import fetch_assets  # noqa: E402

DEFAULT_MANIFEST = os.path.join(HERE, 'xtalk-extensions.json')
CACHE_ENV = 'OXT_XTALK_CACHE'
RAW_URL = 'https://raw.githubusercontent.com/%s/%s/%s'
API_COMMIT_URL = 'https://api.github.com/repos/%s/commits/%s'
API_COMPARE_URL = 'https://api.github.com/repos/%s/compare/%s...HEAD?per_page=1'
# The compare statuses of <commit>...HEAD that mean the default branch is at
# the commit or after it, so the commit is one of its ancestors
ON_DEFAULT_BRANCH = ('ahead', 'identical')
STAMP_NAME = 'XTALK-EXTENSIONS.txt'
LICENCE_DIR = 'licenses'
VC_RUNTIME_NOTICE = 'Microsoft-Visual-C++-Runtime.txt'

ROLES = ('source', 'library', 'code-manifest', 'licence')
KINDS = ('lcb', 'lcs')
EXTRACTS = ('leading-comment',)

# Windows platform ids -> (Visual C++ redistributable architecture folder,
# PE machine of the libraries in it)
WINDOWS_PLATFORMS = collections.OrderedDict([
    ('x86_64-win32', ('x64', 0x8664)),
    ('x86-win32', ('x86', 0x14c)),
])

# DLLs every supported Windows has: an import of one of these needs nothing
# shipped. api-ms-win-crt-* is the Universal CRT, part of Windows 10 and
# later (and of Windows 7/8.1 with KB2999226). msvcrt.dll is the system C
# runtime that MinGW builds (coinxt) use, not the Visual C++ runtime.
SYSTEM_DLLS = frozenset((
    'kernel32.dll', 'user32.dll', 'advapi32.dll', 'ws2_32.dll', 'winmm.dll',
    'crypt32.dll', 'bcrypt.dll', 'iphlpapi.dll', 'mswsock.dll', 'msvcrt.dll',
))
SYSTEM_DLL_PREFIXES = ('api-ms-win-crt-',)

HTTP_ATTEMPTS = 4
HTTP_TIMEOUT = 60

# The pair of handlers appended to a script library. It follows
# extensions/script-libraries/diff/diff.livecodescript (the pattern of the
# script libraries LiveCode ships): the IDE loads a script library extension
# with revInternal__LoadLibrary, which sends extensionInitialize to the
# stack; a stack that does not handle it would be in memory but never in
# the message path. The guard keeps a second extensionInitialize from
# inserting the script twice. Appended at the end so that no declaration
# of the library moves (script-level declarations resolve by position) and
# its line numbers stay those of the member repository.
LCS_WRAPPER = '''
-- OpenXTalk-Lite: added when this library was bundled as an extension (see
-- tools/oxt/xtalk_extensions.py in the OpenXTalk-Lite repository). The IDE
-- sends extensionInitialize to a script library extension when it loads it
-- and extensionFinalize when it unloads it.
on extensionInitialize
   if the target is not me then
      pass extensionInitialize
   end if

   if the long id of me is not among the lines of the backScripts then
      insert the script of me into back
   end if

   if the environment contains "development" then
      set the _ideoverride of me to true
   end if
end extensionInitialize

on extensionFinalize
   if the target is not me then
      pass extensionFinalize
   end if

   remove the script of me from back
end extensionFinalize
'''


class XtalkError(Exception):
    pass


class HttpError(XtalkError):
    """An HTTP error status that is not worth retrying (a 404, say)."""
    def __init__(self, code, message):
        XtalkError.__init__(self, message)
        self.code = code


# ---------------------------------------------------------------------------
# Manifest

def load_manifest(path=DEFAULT_MANIFEST, pinned=True):
    """Return the manifest as ordered dicts, validated. With pinned=False the
    commits, hashes and sizes may still be empty (before the first pin)."""
    try:
        with open(path, encoding='utf-8') as f:
            data = json.load(f, object_pairs_hook=collections.OrderedDict)
    except (OSError, ValueError) as e:
        raise XtalkError('%s: %s' % (path, e))
    validate(data, path, pinned)
    return data


def _need(cond, where, msg):
    if not cond:
        raise XtalkError('%s: %s' % (where, msg))


def _is_sha1(value):
    return isinstance(value, str) and re.match(r'^[0-9a-f]{40}$', value) is not None


def _is_repository(value):
    """owner/name as GitHub allows it. cache_file uses the name as a
    folder, so '.' and '..' (which GitHub refuses anyway) must not pass,
    or the cache path leaves the cache (fetch and 'pin --cache' write
    there). A leading dot is legitimate (owner/.github)."""
    return (isinstance(value, str)
            and re.match(r'^[A-Za-z0-9-]+/[A-Za-z0-9._-]+$', value) is not None
            and value.split('/')[1] not in ('.', '..'))


def _safe_name(value):
    """A single path component that Windows can store and that does not
    start with a dot (the IDE skips such folders)."""
    return (isinstance(value, str) and value not in ('', '.', '..')
            and not value.startswith('.')
            and re.search(r'[\\/<>:"|?*\x00-\x1f]', value) is None
            and not value.endswith((' ', '.')))


def validate(data, where='manifest', pinned=True):
    _need(isinstance(data, dict), where, 'expected a JSON object')
    platforms = data.get('platforms')
    _need(isinstance(platforms, list) and platforms, where, '"platforms" must be a non-empty list')
    for p in platforms:
        _need(isinstance(p, str) and re.match(r'^[a-z0-9_]+-[a-z0-9]+$', p), where, 'bad platform id %r' % (p,))
    members = data.get('members')
    _need(isinstance(members, list) and members, where, '"members" must be a non-empty list')
    names, folders, ids = set(), {}, {}
    for m in members:
        _need(isinstance(m, dict), where, 'every member must be an object')
        name = m.get('name')
        w = '%s: member %s' % (where, name)
        _need(isinstance(name, str) and re.match(r'^[A-Za-z0-9][A-Za-z0-9._-]*$', name), where, 'bad member name %r' % (name,))
        _need(name.lower() not in names, where, 'duplicate member %s' % name)
        names.add(name.lower())
        _need(_is_repository(m.get('repository')), w, 'bad repository')
        _need(_is_sha1(m.get('commit')) or (not pinned and m.get('commit') == ''), w,
              'commit must be a full 40-digit SHA-1 (run "xtalk_extensions.py pin")')
        _need(isinstance(m.get('version'), str) and (m['version'] or not pinned), w, 'version missing')
        files = m.get('files')
        _need(isinstance(files, list) and files, w, '"files" must be a non-empty list')
        seen = set()
        code_manifests = []
        for f in files:
            _validate_file(f, m, w, platforms, pinned)
            key = (f.get('repository', m['repository']).lower(), f['path'].lower())
            _need(key not in seen, w, 'file %s is listed twice' % f['path'])
            seen.add(key)
            if f['role'] == 'code-manifest':
                code_manifests.append(f)
        libraries = member_files(m, 'library')
        _need(len(code_manifests) <= 1, w, 'more than one code-manifest file')
        if libraries:
            _need(code_manifests, w, 'native libraries need the member\'s code-manifest (MANIFEST.sha256)')
            code_dir = posix_dirname(code_manifests[0]['path'])
            for f in libraries:
                _need(posix_dirname(posix_dirname(f['path'])) == code_dir, w,
                      '%s is not in %s/<platform>/' % (f['path'], code_dir))
        vf = m.get('version_from')
        _need(isinstance(vf, dict) and isinstance(vf.get('path'), str) and isinstance(vf.get('pattern'), str),
              w, '"version_from" needs "path" and "pattern"')
        _need(any(f['path'] == vf['path'] and 'repository' not in f for f in files), w,
              'version_from.path %s is not one of its files' % vf['path'])
        try:
            _need(re.compile(vf['pattern']).groups >= 1, w, 'version_from.pattern needs a group')
        except re.error as e:
            raise XtalkError('%s: version_from.pattern: %s' % (w, e))
        exts = m.get('extensions')
        _need(isinstance(exts, list) and exts, w, '"extensions" must be a non-empty list')
        lcb_count = 0
        for e in exts:
            _validate_extension(e, m, w)
            lcb_count += e['kind'] == 'lcb'
            key = e['folder'].lower()
            _need(key not in folders, w, 'extension folder %s is used twice' % e['folder'])
            folders[key] = e['folder']
            _need(e['id'].lower() not in ids, w, 'extension id %s is used twice' % e['id'])
            ids[e['id'].lower()] = e
        _need(lcb_count <= 1, w, 'more than one lcb extension (the native libraries go into the one)')
        _need(lcb_count == 1 or not libraries, w, 'native libraries but no lcb extension to put them in')
    if 'vc_runtime' in data:
        _validate_vc_runtime(data['vc_runtime'], where, platforms)
    # requires: known ids, no cycles
    for m in members:
        for e in m['extensions']:
            for r in e.get('requires', []):
                _need(r.lower() in ids, where, 'extension %s requires unknown extension %s' % (e['id'], r))
    dependency_order(data)


def _validate_vc_runtime(vc, where, platforms):
    """The "vc_runtime" block: file_version, redist, and files as
    {Windows platform id: {lower-case DLL name: {sha256, size}}}."""
    w = '%s: vc_runtime' % where
    _need(isinstance(vc, dict), w, 'must be an object')
    files = vc.get('files')
    _need(isinstance(files, dict), w, '"files" must be an object: platform id -> DLL name -> {sha256, size}')
    fv = vc.get('file_version')
    _need(isinstance(fv, str) and (re.match(r'^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$', fv) is not None
                                   or (fv == '' and not any(files.values()))),
          w, 'file_version must be the DLLs\' file version, four numbers (run "xtalk_extensions.py pin-vc-runtime")')
    _need(isinstance(vc.get('redist', ''), str), w, 'redist must be a string')
    for platform, dlls in files.items():
        _need(platform in platforms and platform in WINDOWS_PLATFORMS, w,
              'platform id %s is not a Windows platform id of "platforms"' % platform)
        _need(isinstance(dlls, dict), w, 'files of %s must be an object: DLL name -> {sha256, size}' % platform)
        for name, pin in dlls.items():
            wd = '%s: %s/%s' % (w, platform, name)
            _need(isinstance(name, str) and re.match(r'^[a-z0-9_.-]+\.dll$', name) is not None, wd,
                  'the DLL name must be a lower-case file name ending in .dll')
            _need(not is_system_dll(name), wd, 'a Windows system DLL is never copied')
            _need(isinstance(pin, dict) and isinstance(pin.get('sha256'), str)
                  and re.match(r'^[0-9a-f]{64}$', pin['sha256']) is not None, wd,
                  'sha256 must be 64 lowercase hex digits')
            _need(isinstance(pin.get('size'), int) and not isinstance(pin['size'], bool) and pin['size'] > 0, wd,
                  'size must be a positive integer')


def _validate_file(f, m, w, platforms, pinned):
    _need(isinstance(f, dict), w, 'every file must be an object')
    path = f.get('path')
    try:
        _need(isinstance(path, str) and fetch_assets.safe_relpath(path) == path, w, 'bad file path %r' % (path,))
    except fetch_assets.AssetError as e:
        raise XtalkError('%s: %s' % (w, e))
    wf = '%s: file %s' % (w, path)
    _need(f.get('role') in ROLES, wf, 'role must be one of %s' % ', '.join(ROLES))
    if 'repository' in f or 'commit' in f:
        _need(_is_repository(f.get('repository')), wf, 'bad repository')
        _need(_is_sha1(f.get('commit')), wf, 'a file with its own repository needs a full commit SHA-1')
        _need(f['role'] == 'licence', wf, 'only licence files may come from another repository')
    if pinned:
        _need(isinstance(f.get('sha256'), str) and re.match(r'^[0-9a-f]{64}$', f['sha256']), wf,
              'sha256 must be 64 lowercase hex digits (run "xtalk_extensions.py pin")')
        _need(isinstance(f.get('size'), int) and f['size'] > 0, wf, 'size must be a positive integer')
    if f['role'] == 'library':
        _need(len(path.split('/')) >= 3 and path.split('/')[-3] == 'code', wf,
              'a library must be at <dir>/code/<platform-id>/<file>')
        platform = library_platform(f)
        _need(platform in platforms, wf, 'platform id %s is not in "platforms"' % platform)
        if platform in WINDOWS_PLATFORMS and pinned:
            _need(isinstance(f.get('imports'), list) and all(isinstance(x, str) for x in f['imports']), wf,
                  'a Windows library needs its "imports" (run "xtalk_extensions.py pin")')
    if 'name' in f:
        _need(f['role'] == 'licence' and _safe_name(f['name']), wf, 'bad name %r' % (f['name'],))
    if 'extract' in f:
        _need(f['role'] == 'licence' and f['extract'] in EXTRACTS, wf,
              'extract must be one of %s, on a licence file' % ', '.join(EXTRACTS))


def _validate_extension(e, m, w):
    _need(isinstance(e, dict), w, 'every extension must be an object')
    we = '%s: extension %s' % (w, e.get('id'))
    _need(e.get('kind') in KINDS, we, 'kind must be one of %s' % ', '.join(KINDS))
    _need(isinstance(e.get('id'), str) and re.match(r'^[A-Za-z][A-Za-z0-9_.-]*$', e['id']), we, 'bad id')
    _need(_safe_name(e.get('folder')), we, 'bad folder %r' % (e.get('folder'),))
    main = e.get('main')
    _need(any(f['path'] == main and f['role'] == 'source' and 'repository' not in f for f in m['files']),
          we, 'main %r is not one of the member\'s source files' % (main,))
    _need(isinstance(e.get('probe'), str) and e['probe'].strip(), we, 'probe missing')
    _need('\t' not in e['probe'] and '\n' not in e['probe'], we, 'the probe must be one line without tabs')
    _need(isinstance(e.get('expect'), str) and '\t' not in e['expect'], we, 'expect missing')
    try:
        re.compile(e['expect'])
    except re.error as err:
        raise XtalkError('%s: expect: %s' % (we, err))
    requires = e.get('requires', [])
    _need(isinstance(requires, list) and all(isinstance(r, str) for r in requires), we, 'requires must be a list')
    if e['kind'] == 'lcb':
        _need(main.endswith('.lcb'), we, 'the main file of an lcb extension must be a .lcb')
        _need(not requires, we, 'the requires of an lcb extension come from its "use" clauses')
    else:
        # The IDE takes the id of a script library from its stack name and
        # loads it by "item 1 of <file name>" with "." as the delimiter, so
        # the stack name, the file name and the folder must agree and must
        # not contain dots.
        stack = e.get('stack')
        _need(isinstance(stack, str) and re.match(r'^[A-Za-z][A-Za-z0-9_-]*$', stack), we,
              'stack must be a name without dots')
        _need(e['id'] == stack and e['folder'] == stack, we, 'id and folder must equal the stack name')
        _need(main.endswith('.livecodescript'), we, 'the main file of an lcs extension must be a .livecodescript')
        for k in ('title', 'author'):
            _need(isinstance(e.get(k), str) and e[k], we, '%s missing' % k)


def posix_dirname(path):
    return path.rsplit('/', 1)[0] if '/' in path else ''


def member_files(member, role=None):
    return [f for f in member['files'] if role is None or f['role'] == role]


def library_platform(f):
    return f['path'].split('/')[-2]


def file_source(member, f):
    """(repository, commit) a file is taken from."""
    return f.get('repository', member['repository']), f.get('commit', member['commit'])


def file_url(member, f):
    repo, commit = file_source(member, f)
    return RAW_URL % (repo, commit, urllib.parse.quote(f['path']))


def all_extensions(data):
    for m in data['members']:
        for e in m['extensions']:
            yield m, e


def dependency_order(data):
    """[(member, extension)]: every lcb extension first (in manifest order),
    then the lcs extensions, each after the ones it requires."""
    pairs = list(all_extensions(data))
    by_id = {e['id'].lower(): (m, e) for m, e in pairs}
    order, state = [], {}

    def visit(pair, trail):
        key = pair[1]['id'].lower()
        if state.get(key) == 'done':
            return
        if state.get(key) == 'visiting':
            raise XtalkError('circular requires: %s' % ' -> '.join(trail + [pair[1]['id']]))
        state[key] = 'visiting'
        for r in pair[1].get('requires', []):
            if r.lower() in by_id:
                visit(by_id[r.lower()], trail + [pair[1]['id']])
        state[key] = 'done'
        order.append(pair)

    for pair in [p for p in pairs if p[1]['kind'] == 'lcb'] + [p for p in pairs if p[1]['kind'] == 'lcs']:
        visit(pair, [])
    return order


def selected_platforms(data, platforms):
    if not platforms:
        return list(data['platforms'])
    chosen = []
    for item in platforms:
        for p in item.split(','):
            p = p.strip()
            if not p:
                continue
            if p not in data['platforms']:
                raise XtalkError('unknown platform id %s (the manifest has %s)' % (p, ', '.join(data['platforms'])))
            if p not in chosen:
                chosen.append(p)
    return [p for p in data['platforms'] if p in chosen]


def wanted_files(member, platforms):
    return [f for f in member['files'] if f['role'] != 'library' or library_platform(f) in platforms]


# Rewriting the manifest: two-space indentation like json.dump, but every
# entry of "files" and "extensions" on one line, as the file is written by
# hand, so that a pin changes only the values and diffs stay readable.
COMPACT_LISTS = ('files', 'extensions')


def dump_manifest(data):
    return _dump(data, 0, None) + '\n'


def _dump(value, level, key):
    pad = '  ' * level
    if isinstance(value, dict) and isinstance(key, str) and key.lower().endswith('.dll'):
        return json.dumps(value, ensure_ascii=False)   # a pinned runtime DLL: {sha256, size}
    if isinstance(value, dict):
        if not value:
            return '{}'
        rows = ['%s  %s: %s' % (pad, json.dumps(k, ensure_ascii=False), _dump(v, level + 1, k))
                for k, v in value.items()]
        return '{\n' + ',\n'.join(rows) + '\n' + pad + '}'
    if isinstance(value, list):
        if not value:
            return '[]'
        if key in COMPACT_LISTS:
            rows = [pad + '  ' + json.dumps(x, ensure_ascii=False) for x in value]
        else:
            rows = [pad + '  ' + _dump(x, level + 1, None) for x in value]
        return '[\n' + ',\n'.join(rows) + '\n' + pad + ']'
    return json.dumps(value, ensure_ascii=False)


def write_manifest(path, data):
    text = dump_manifest(data)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8', newline='\n') as f:
        f.write(text)
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# PE images (Windows DLLs): machine, imported DLLs and functions, exports

class PeInfo(object):
    __slots__ = ('machine', 'imports', 'exports', 'linker', 'file_version')

    def __init__(self):
        self.machine = None
        self.imports = collections.OrderedDict()   # DLL name -> [function names; '#<n>' for ordinals]
        self.exports = set()
        self.linker = None          # (major, minor) of the optional header
        self.file_version = None    # (a, b, c, d) of VS_FIXEDFILEINFO, or None


def pe_file_version(data):
    """FileVersion (four numbers) from the VS_FIXEDFILEINFO of a PE image's
    VS_VERSION_INFO resource, or None. The fixed info follows the
    UTF-16 key "VS_VERSION_INFO" (padded to 32 bits) and starts with the
    signature 0xFEEF04BD; looking for the key first keeps a stray
    signature elsewhere in the image from being taken. WHY: the name of a
    redistributable folder (VC\\Redist\\MSVC\\14.44.35112) is the toolset's
    redist version, not the DLLs' (14.44.35211.0 there), and whoever checks
    which Microsoft runtime build shipped needs the DLLs' own."""
    i = data.find('VS_VERSION_INFO\0'.encode('utf-16-le'))
    j = data.find(struct.pack('<I', 0xFEEF04BD), i, i + 64) if i >= 0 else -1
    if j < 0 or j + 16 > len(data):
        return None
    ms, ls = struct.unpack_from('<II', data, j + 8)   # dwFileVersionMS, dwFileVersionLS
    return (ms >> 16, ms & 0xffff, ls >> 16, ls & 0xffff)


def version_text(version):
    return '.'.join(str(n) for n in version) if version else 'unknown'


def pe_info(data, what='image'):
    """Parse a PE image's headers, import table, delay-load import table and
    export names. Raises XtalkError for anything that is not a valid PE."""
    def u16(off):
        return struct.unpack_from('<H', data, off)[0]

    def u32(off):
        return struct.unpack_from('<I', data, off)[0]

    def u64(off):
        return struct.unpack_from('<Q', data, off)[0]

    try:
        if data[:2] != b'MZ':
            raise ValueError('no MZ header')
        pe = u32(0x3c)
        if data[pe:pe + 4] != b'PE\0\0':
            raise ValueError('no PE signature')
        info = PeInfo()
        info.machine = u16(pe + 4)
        nsections = u16(pe + 6)
        optsize = u16(pe + 20)
        opt = pe + 24
        magic = u16(opt)
        # MajorLinkerVersion, MinorLinkerVersion: MSVC's link.exe writes
        # its toolset version (14.51 for Visual Studio 2026's first
        # toolset), GNU ld its own (2.41 for CoinXT's MinGW build)
        info.linker = (data[opt + 2], data[opt + 3])
        if magic == 0x10b:
            wide, image_base, dirs = False, u32(opt + 28), opt + 96
        elif magic == 0x20b:
            wide, image_base, dirs = True, u64(opt + 24), opt + 112
        else:
            raise ValueError('unknown optional header magic 0x%x' % magic)
        ndirs = u32(dirs - 4)
        sections = []
        for i in range(nsections):
            s = opt + optsize + 40 * i
            vsize, va, rawsize, rawptr = struct.unpack_from('<IIII', data, s + 8)
            sections.append((va, max(vsize, rawsize), rawptr, rawsize))

        def off(rva):
            for va, size, rawptr, rawsize in sections:
                if va <= rva < va + size:
                    if rva - va >= rawsize:
                        raise ValueError('RVA 0x%x is in uninitialised data' % rva)
                    return rva - va + rawptr
            raise ValueError('RVA 0x%x is outside every section' % rva)

        def cstr(rva):
            o = off(rva)
            end = data.index(b'\0', o)
            return data[o:end].decode('ascii')

        def thunks(rva):
            names = []
            if not rva:
                return names
            o = off(rva)
            step, flag = (8, 1 << 63) if wide else (4, 1 << 31)
            while True:
                v = u64(o) if wide else u32(o)
                if not v:
                    return names
                names.append('#%d' % (v & 0xffff) if v & flag else cstr((v & 0x7fffffff) + 2))
                o += step

        def add(dll, names):
            for known in info.imports:
                if known.lower() == dll.lower():
                    info.imports[known].extend(names)
                    return
            info.imports[dll] = list(names)

        if ndirs > 1:
            rva = u32(dirs + 8)
            if rva:
                o = off(rva)
                while True:
                    olt, _, _, name, first = struct.unpack_from('<IIIII', data, o)
                    if not (olt or name or first):
                        break
                    add(cstr(name), thunks(olt or first))
                    o += 20
        if ndirs > 13:
            rva = u32(dirs + 13 * 8)
            if rva:
                o = off(rva)
                while True:
                    attrs, name, _, _, names_rva = struct.unpack_from('<IIIII', data, o)
                    if not name:
                        break
                    # Old-style (attrs bit 0 clear) descriptors hold VAs
                    base = 0 if attrs & 1 else image_base
                    add(cstr(name - base), thunks(names_rva - base if names_rva else 0))
                    o += 32
        if ndirs > 0:
            rva = u32(dirs)
            if rva:
                o = off(rva)
                count, names_rva = u32(o + 24), u32(o + 32)
                if count:
                    n = off(names_rva)
                    for i in range(count):
                        info.exports.add(cstr(u32(n + 4 * i)))
        info.file_version = pe_file_version(data)
        return info
    except (ValueError, struct.error, IndexError, UnicodeDecodeError) as e:
        raise XtalkError('%s is not a valid PE image: %s' % (what, e))


def is_system_dll(name):
    n = name.lower()
    return n in SYSTEM_DLLS or n.startswith(SYSTEM_DLL_PREFIXES)


# ---------------------------------------------------------------------------
# Network

def _http_get(url, log, accept='application/octet-stream'):
    """Bytes at an HTTPS URL; transient errors are retried with backoff."""
    opener = urllib.request.build_opener(fetch_assets._HttpsOnlyRedirects())
    delay = 5
    for attempt in range(1, HTTP_ATTEMPTS + 1):
        request = urllib.request.Request(url, headers={'User-Agent': fetch_assets.USER_AGENT, 'Accept': accept})
        try:
            with opener.open(request, timeout=HTTP_TIMEOUT) as response:
                if urllib.parse.urlsplit(response.geturl()).scheme != 'https':
                    raise XtalkError('%s ended at a non-HTTPS URL' % url)
                length = response.headers.get('Content-Length')
                body = response.read()
                if length and length.isdigit() and int(length) != len(body):
                    raise OSError('connection closed after %d of %s bytes' % (len(body), length))
                return body
        except urllib.error.HTTPError as e:
            if e.code in (408, 425, 429) or e.code >= 500:
                problem = 'HTTP %d %s' % (e.code, e.reason)
            else:
                raise HttpError(e.code, 'HTTP %d %s for %s' % (e.code, e.reason, url))
        except fetch_assets.AssetError as e:
            raise XtalkError(str(e))
        except (urllib.error.URLError, OSError, http.client.HTTPException) as e:
            problem = str(getattr(e, 'reason', e))
        if attempt == HTTP_ATTEMPTS:
            raise XtalkError('download of %s failed: %s' % (url, problem))
        log('  %s: %s; retrying in %d s' % (url, problem, delay))
        time.sleep(delay)
        delay *= 2
    raise XtalkError('download of %s failed' % url)


def resolve_commit(repository, ref, log):
    """Full commit SHA-1 of ref (a branch, a tag, HEAD for the default
    branch, or a full SHA-1) in github.com/<repository>. A SHA-1, and any
    answer of the GitHub API, must be on the default branch (see
    on_default_branch); a branch or tag that git ls-remote resolves is a
    ref of the repository itself."""
    if _is_sha1(ref):
        return on_default_branch(repository, ref, log)
    git = shutil.which('git')
    if git:
        url = 'https://github.com/%s.git' % repository
        patterns = ['HEAD'] if ref == 'HEAD' else ['refs/heads/' + ref, 'refs/tags/' + ref, 'refs/tags/' + ref + '^{}']
        try:
            p = subprocess.run([git, 'ls-remote', url] + patterns, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, timeout=180,
                               env=dict(os.environ, GIT_TERMINAL_PROMPT='0'))
        except (OSError, subprocess.SubprocessError) as e:
            p = None
            log('  git ls-remote %s failed (%s); asking the GitHub API' % (url, e))
        if p is not None and p.returncode == 0:
            refs = {}
            for line in p.stdout.decode('utf-8', 'replace').splitlines():
                parts = line.split('\t')
                if len(parts) == 2 and _is_sha1(parts[0]):
                    refs[parts[1]] = parts[0]
            for name in ('HEAD', 'refs/tags/%s^{}' % ref, 'refs/heads/' + ref, 'refs/tags/' + ref):
                if name in refs and (name == 'HEAD') == (ref == 'HEAD'):
                    return refs[name]
            raise XtalkError('%s has no branch or tag %s' % (url, ref))
        if p is not None:
            log('  git ls-remote %s failed (%s); asking the GitHub API'
                % (url, p.stderr.decode('utf-8', 'replace').strip()))
    body = _http_get(API_COMMIT_URL % (repository, urllib.parse.quote(ref, safe='')), log,
                     accept='application/vnd.github.sha')
    sha = body.decode('ascii', 'replace').strip()
    if not _is_sha1(sha):
        raise XtalkError('the GitHub API gave no commit for %s %s' % (repository, ref))
    # The API also resolves a short SHA-1, to any commit of the fork network
    return on_default_branch(repository, sha, log)


_compare_status = {}


def default_branch_status(repository, commit, log):
    """How commit relates to the head of the default branch of
    github.com/<repository>, from one call of the GitHub compare API:
    'identical' or 'ahead' (ON_DEFAULT_BRANCH) when the default branch is
    at it or after it, 'behind' or 'diverged' when it is not on the
    default branch, or a note when the API answers 404 (no history in
    common, or no such commit). The answer is kept for the rest of the
    run, so that pin asks once per commit."""
    key = (repository.lower(), commit)
    if key not in _compare_status:
        try:
            body = _http_get(API_COMPARE_URL % (repository, commit), log, accept='application/vnd.github+json')
        except HttpError as e:
            if e.code not in (404, 422):
                raise
            status = 'HTTP %d, no history in common or no such commit' % e.code
        else:
            try:
                status = json.loads(body.decode('utf-8')).get('status')
            except (ValueError, AttributeError):
                status = None
        _compare_status[key] = status
    return _compare_status[key]


def on_default_branch(repository, commit, log):
    """commit, if the default branch of github.com/<repository> is at it or
    after it; else raise. raw.githubusercontent.com/<repository>/<commit>/
    and the commits API serve every commit of the repository's fork
    network, so a commit pushed to anybody's fork, or the head of an
    unmerged pull request, downloads under the member's name: pinned, it
    would ship as the member's code (the SHA-256s only prove that it is
    what was pinned), and in a manifest diff its SHA-1 looks like any
    other. A name that git ls-remote resolved is a ref of the repository
    itself and needs no check here (cmd_pin still wants it on the default
    branch, so that it stays reachable)."""
    status = default_branch_status(repository, commit, log)
    if status not in ON_DEFAULT_BRANCH:
        raise XtalkError('%s is not on the default branch of github.com/%s (compare: %s); it may be a '
                         'commit of a fork or an unmerged pull request. Pin a branch or tag of the '
                         'repository by name (--ref NAME, with --allow-off-branch if it is not on the '
                         'default branch)' % (commit, repository, status))
    return commit


# ---------------------------------------------------------------------------
# MANIFEST.sha256 (the members' list of their native libraries)

def parse_sha256_list(data, what):
    """{relative path: sha256} from sha256sum output; CR, blank lines, '#'
    comments and the binary-mode '*' are tolerated (a CRLF checkout of the
    file must parse the same as the LF blob)."""
    out = {}
    for n, line in enumerate(data.decode('utf-8', 'replace').splitlines(), 1):
        line = line.strip().rstrip('\r')
        if not line or line.startswith('#'):
            continue
        m = re.match(r'^([0-9a-fA-F]{64}) [ *](.+)$', line)
        if not m:
            raise XtalkError('%s line %d is not "<sha256>  <path>": %r' % (what, n, line))
        out[m.group(2).strip().replace('\\', '/').lstrip('./')] = m.group(1).lower()
    return out


def check_code_manifest(member, data, platforms_all, checked_platforms):
    """Compare the member's libraries with its MANIFEST.sha256 (data): each
    library of checked_platforms must be listed with its pinned SHA-256, and
    every listed file of a platform the manifest takes must be taken."""
    cm = member_files(member, 'code-manifest')[0]
    code_dir = posix_dirname(cm['path'])
    listed = parse_sha256_list(data, '%s %s' % (member['name'], cm['path']))
    taken = {}
    for f in member_files(member, 'library'):
        taken[f['path'][len(code_dir) + 1:]] = f
    problems, notes = [], []
    for rel, f in sorted(taken.items()):
        if library_platform(f) not in checked_platforms:
            continue
        if rel not in listed:
            problems.append('%s is not in %s' % (f['path'], cm['path']))
        elif listed[rel] != f['sha256']:
            problems.append('%s: %s lists SHA-256 %s, the pin is %s' % (f['path'], cm['path'], listed[rel], f['sha256']))
    for rel in sorted(listed):
        if rel in taken:
            continue
        if rel.split('/')[0] in platforms_all:
            problems.append('%s lists %s/%s, which xtalk-extensions.json does not take; add it to "files"'
                            % (cm['path'], code_dir, rel))
        else:
            notes.append('%s lists %s/%s for a platform id that is not in "platforms"' % (cm['path'], code_dir, rel))
    if problems:
        raise XtalkError('%s at %s: %s' % (member['name'], member['commit'][:12], '; '.join(problems)))
    return notes


# ---------------------------------------------------------------------------
# Checks on sources

def lcb_module_id(text):
    m = re.search(r'^\s*(?:library|widget|module)\s+([A-Za-z0-9_.]+)\s*$', text, re.M)
    return m.group(1) if m else None


def lcs_header(data):
    """The stack name in a 'script "<name>"' first line, or None."""
    text = data[3:] if data.startswith(b'\xef\xbb\xbf') else data
    first = text.split(b'\n', 1)[0].rstrip(b'\r')
    m = re.match(rb'^\s*script\s+"([^"]*)"\s*$', first, re.I)
    return m.group(1).decode('utf-8', 'replace') if m else None


def check_sources(member, contents):
    """Raise if a pinned source no longer matches its extension entry
    (module id of an lcb, script header name of an lcs)."""
    for e in member['extensions']:
        data = contents[e['main']]
        if e['kind'] == 'lcb':
            found = lcb_module_id(data.decode('utf-8', 'replace'))
            if found != e['id']:
                raise XtalkError('%s: %s declares module %s, the manifest says %s (the folder name and '
                                 'the id users see follow it; update the extension entry)'
                                 % (member['name'], e['main'], found, e['id']))
        else:
            name = lcs_header(data)
            if name is not None and name.lower() != e['stack'].lower():
                raise XtalkError('%s: %s starts with script "%s", the manifest says stack %s'
                                 % (member['name'], e['main'], name, e['stack']))


# ---------------------------------------------------------------------------
# pin

def pin_member(data, member, commit, cache, log):
    """Point member at commit and re-hash its files. Returns a list of
    (path, old sha256, new sha256) for the files that changed."""
    old_commit = member['commit']
    member['commit'] = commit
    contents, changes = {}, []
    for f in member['files']:
        url = file_url(member, f)
        body = _http_get(url, log)
        digest = hashlib.sha256(body).hexdigest()
        if f.get('sha256') != digest:
            changes.append((f['path'], f.get('sha256') or '-', digest))
        f['sha256'] = digest
        f['size'] = len(body)
        if f['role'] == 'library':
            platform = library_platform(f)
            if platform in WINDOWS_PLATFORMS:
                info = pe_info(body, '%s %s' % (member['name'], f['path']))
                if info.machine != WINDOWS_PLATFORMS[platform][1]:
                    raise XtalkError('%s %s: PE machine 0x%04x, expected 0x%04x for %s'
                                     % (member['name'], f['path'], info.machine,
                                        WINDOWS_PLATFORMS[platform][1], platform))
                f['imports'] = list(info.imports)
            else:
                f.pop('imports', None)
        if 'repository' not in f:
            contents[f['path']] = body
        if cache:
            _store(cache_file(cache, member, f), body)
        log('  %-52s %10d  %s' % (f['path'] if 'repository' not in f else
                                  '%s:%s' % (f['repository'], f['path']), len(body), digest[:16]))
    vf = member['version_from']
    m = re.search(vf['pattern'], contents[vf['path']].decode('utf-8', 'replace'), re.M)
    if not m:
        raise XtalkError('%s: version_from.pattern does not match %s at %s' % (member['name'], vf['path'], commit))
    member['version'] = m.group(1)
    cms = member_files(member, 'code-manifest')
    if cms:
        for note in check_code_manifest(member, contents[cms[0]['path']], data['platforms'], data['platforms']):
            log('  note: ' + note)
    check_sources(member, contents)
    if old_commit and old_commit != commit:
        log('  %s: %s -> %s' % (member['name'], old_commit[:12], commit[:12]))
    return changes


def _store(path, body):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + '.part'
    with open(tmp, 'wb') as f:
        f.write(body)
    os.replace(tmp, path)


def cmd_pin(args, log=print):
    data = load_manifest(args.manifest, pinned=False)
    members = data['members']
    if args.member:
        wanted = {n.lower() for n in args.member}
        unknown = wanted - {m['name'].lower() for m in members}
        if unknown:
            raise XtalkError('unknown member: %s' % ', '.join(sorted(unknown)))
        members = [m for m in members if m['name'].lower() in wanted]
    if args.ref and len(members) != 1:
        raise XtalkError('--ref needs exactly one --member')
    cache = cache_dir(args.repo, args.cache) if not args.no_cache else None
    total = 0
    for m in members:
        ref = args.ref or 'HEAD'
        commit = resolve_commit(m['repository'], ref, log)
        log('%s: %s %s is %s' % (m['name'], m['repository'], ref, commit))
        # The pinned files are fetched live from raw.githubusercontent.com,
        # so the commit must stay reachable for as long as a release that
        # pins it may be rebuilt. The members publish by fast-forwarding
        # their default branch, never force-pushed, so its commits stay; a
        # commit reachable only from another branch, or from a tag, which
        # can be moved, disappears when that goes. (A SHA-1, and a ref the
        # GitHub API resolved, were checked by resolve_commit already.)
        if ref != 'HEAD' and not args.allow_off_branch:
            status = default_branch_status(m['repository'], commit, log)
            if status not in ON_DEFAULT_BRANCH:
                raise XtalkError('%s: %s (%s) is not on the default branch of github.com/%s (compare: %s); '
                                 'it can disappear when its branch is deleted or its tag is moved. Pin a '
                                 'commit of the default branch, or pass --allow-off-branch to pin it anyway'
                                 % (m['name'], commit, ref, m['repository'], status))
        changes = pin_member(data, m, commit, cache, log)
        log('  version %s; %d file(s) changed' % (m['version'], len(changes)))
        total += len(changes)
    validate(data, args.manifest, pinned=True)
    with open(args.manifest, encoding='utf-8') as f:
        before = f.read()
    after = dump_manifest(data)
    if before == after:
        log('%s is unchanged' % args.manifest)
    else:
        write_manifest(args.manifest, data)
        log('rewrote %s (%d file hash(es) changed); review and commit it' % (args.manifest, total))
    return 0


# ---------------------------------------------------------------------------
# fetch

def cache_dir(repo_root=None, override=None, assets_cache=None):
    """--cache, else OXT_XTALK_CACHE, else the xtalk folder of the
    external-assets cache (assets_cache, else fetch_assets' own default:
    OXT_ASSETS_CACHE, else <repo>/prebuilt/fetched-assets). The CLI and
    package.py must agree, or 'fetch' online and then 'package.py
    --offline' look in different folders when OXT_ASSETS_CACHE is set."""
    if override:
        return os.path.abspath(override)
    env = os.environ.get(CACHE_ENV)
    if env:
        return os.path.abspath(env)
    if not assets_cache:
        assets_cache = fetch_assets.cache_dir(repo_root)
    return os.path.join(os.path.abspath(assets_cache), 'xtalk')


def cache_file(cache, member, f):
    repo, commit = file_source(member, f)
    return os.path.join(cache, repo.split('/')[1], commit, *f['path'].split('/'))


def fetch(data, cache, offline=False, platforms=None, members=None, log=print):
    """Make sure every pinned file (of the given platforms and members) is
    in the cache and verified. Returns {(member name, repository, path):
    local file}."""
    platforms = platforms or list(data['platforms'])
    local = {}
    for m in data['members']:
        if members and m['name'].lower() not in members:
            continue
        downloaded = cached = 0
        for f in wanted_files(m, platforms):
            path = cache_file(cache, m, f)
            if os.path.isfile(path):
                problem = fetch_assets.check_file(f, path)
                if problem is None:
                    local[(m['name'], file_source(m, f)[0], f['path'])] = path
                    cached += 1
                    continue
                log('%s %s: the cached file does not match the pin (%s); deleting it' % (m['name'], f['path'], problem))
                os.remove(path)
            asset = collections.OrderedDict([('id', '%s %s' % (m['name'], f['path'])), ('url', file_url(m, f)),
                                             ('sha256', f['sha256']), ('size', f['size'])])
            try:
                got = fetch_assets.fetch(asset, os.path.dirname(path), offline, log)
            except fetch_assets.AssetError as e:
                raise XtalkError(str(e))
            if os.path.normcase(os.path.abspath(got)) != os.path.normcase(os.path.abspath(path)):
                raise XtalkError('%s %s was saved as %s, expected %s' % (m['name'], f['path'], got, path))
            local[(m['name'], file_source(m, f)[0], f['path'])] = path
            downloaded += 1
        cms = member_files(m, 'code-manifest')
        if cms:
            with open(local[(m['name'], m['repository'], cms[0]['path'])], 'rb') as fh:
                check_code_manifest(m, fh.read(), data['platforms'], platforms)
        log('%s %s at %s: %d file(s) verified%s' % (m['name'], m['version'], m['commit'][:12],
                                                     downloaded + cached,
                                                     ' (%d downloaded)' % downloaded if downloaded else ''))
    return local


def cmd_fetch(args, log=print):
    data = load_manifest(args.manifest)
    platforms = selected_platforms(data, args.platforms)
    members = _member_filter(data, args.member)
    cache = cache_dir(args.repo, args.cache)
    log('xTalk extensions cache: %s' % cache)
    fetch(data, cache, args.offline, platforms, members, log)
    return 0


def _member_filter(data, names):
    if not names:
        return None
    wanted = {n.lower() for n in names}
    unknown = wanted - {m['name'].lower() for m in data['members']}
    if unknown:
        raise XtalkError('unknown member: %s' % ', '.join(sorted(unknown)))
    return wanted


# ---------------------------------------------------------------------------
# build

def lc_compile_path(bin_dir):
    for name in ('lc-compile.exe', 'lc-compile'):
        path = os.path.join(bin_dir, name)
        if os.path.isfile(path):
            return path
    raise XtalkError('no lc-compile in %s' % bin_dir)


def _lci_snapshot(folder):
    out = {}
    for name in os.listdir(folder):
        st = os.stat(os.path.join(folder, name))
        out[name] = (st.st_size, st.st_mtime)
    return out


def _rmtree(path):
    # Read-only files (a copy of a read-only checkout, say) are made
    # writable and removed again; onerror is deprecated from Python 3.12
    def retry(func, p, error):
        try:
            os.chmod(p, stat.S_IWRITE)
            func(p)
        except OSError:
            raise error
    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=retry)
    else:
        shutil.rmtree(path, onerror=lambda func, p, exc_info: retry(func, p, exc_info[1]))


def find_vc_redist(folder):
    """{'x64': CRT folder, 'x86': CRT folder, 'version': name} from Visual
    Studio's redistributable folder (VCToolsRedistDir,
    ...\\VC\\Redist\\MSVC\\<version>)."""
    folder = os.path.abspath(folder)
    result = {'folder': folder, 'version': os.path.basename(folder.rstrip('\\/'))}
    for arch in ('x64', 'x86'):
        base = os.path.join(folder, arch)
        found = sorted(d for d in (os.listdir(base) if os.path.isdir(base) else [])
                       if re.match(r'^Microsoft\.VC1[0-9]+\.CRT$', d, re.I) and os.path.isdir(os.path.join(base, d)))
        if not found:
            raise XtalkError('--vc-redist %s has no %s\\Microsoft.VC14x.CRT folder (expected Visual Studio\'s '
                             'VC\\Redist\\MSVC\\<version> folder, VCToolsRedistDir)' % (folder, arch))
        result[arch] = os.path.join(base, found[-1])
    return result


# What complete_windows_runtime did in one code folder: the runtime DLLs it
# copied (file names), [(library, [DLLs it needs])] when there is no
# redistributable, [(library, 'major.minor' of its MSVC linker, [(copied
# DLL, its file version)])] for libraries built by a newer toolset than the
# copies they import, and the copies that are not the pinned ones (only
# with allow_unpinned)
RuntimeResult = collections.namedtuple('RuntimeResult', 'copied missing stale unpinned')


def runtime_pin_problem(path, name, pins):
    """None if the copy at path is the one pins ({lower-case name:
    {sha256, size}}, or None) lists, else what is wrong with it."""
    digest, size = fetch_assets.file_digest(path)
    pin = (pins or {}).get(name.lower())
    if pin is None:
        return 'it is not pinned (SHA-256 %s, %d bytes; "vc_runtime" lists no %s for this platform)' % (
            digest, size, name.lower())
    if digest != pin['sha256'] or size != pin['size']:
        return 'SHA-256 %s, %d bytes; the pin is SHA-256 %s, %d bytes' % (digest, size, pin['sha256'], pin['size'])
    return None


def complete_windows_runtime(folder, platform, redist, where, log, pins=None, pinned_version=None,
                             allow_unpinned=False):
    """Copy into folder, from redist, every DLL that a DLL in folder
    imports and that is neither a Windows system DLL nor in the folder,
    until nothing is missing (a copied runtime DLL can import another).
    Every copy must match pins (the manifest's vc_runtime files of this
    platform); with allow_unpinned another copy only warns. Returns a
    RuntimeResult; missing is non-empty only without redist."""
    arch, machine = WINDOWS_PLATFORMS[platform]
    copied, unpinned = [], []
    while True:
        present = {n.lower(): n for n in os.listdir(folder)}
        unresolved = collections.OrderedDict()
        infos = {}
        for name in sorted(present.values()):
            if not name.lower().endswith('.dll'):
                continue
            with open(os.path.join(folder, name), 'rb') as f:
                info = pe_info(f.read(), '%s/%s' % (where, name))
            if info.machine != machine:
                raise XtalkError('%s/%s: PE machine 0x%04x, expected 0x%04x' % (where, name, info.machine, machine))
            infos[name] = info
            for dll in info.imports:
                if not is_system_dll(dll) and dll.lower() not in present:
                    unresolved.setdefault(name, []).append(dll)
        if not unresolved:
            break
        if redist is None:
            return RuntimeResult(copied, list(unresolved.items()), [], [])
        source_dir = redist[arch]
        available = {n.lower(): n for n in os.listdir(source_dir)}
        for lib, dlls in unresolved.items():
            for dll in dlls:
                if dll.lower() in {n.lower() for n in os.listdir(folder)}:
                    continue
                if dll.lower() not in available:
                    raise XtalkError('%s/%s imports %s, which is neither a Windows system DLL nor in %s'
                                     % (where, lib, dll, source_dir))
                name = available[dll.lower()]
                target = os.path.join(folder, name)
                shutil.copyfile(os.path.join(source_dir, name), target)
                copied.append(name)
                log('  %s: added %s from %s (imported by %s)' % (where, name, source_dir, lib))
                # Pinned, so that the same pins give the same package
                # whichever Visual Studio (and redistributable) the machine
                # has; the redistributable folder is otherwise the only
                # input that is not pinned
                problem = runtime_pin_problem(target, name, pins)
                if problem:
                    message = ('%s/%s, copied from %s: %s. Use the pinned Visual C++ redistributable (file '
                               'version %s) or re-pin with "xtalk_extensions.py pin-vc-runtime --vc-redist DIR"'
                               % (where, name, source_dir, problem, pinned_version or '(none pinned)'))
                    if not allow_unpinned:
                        raise XtalkError(message)
                    log('  WARNING: %s; kept (--allow-unpinned-vc-runtime)' % message)
                    unpinned.append(name)
    # Every function a DLL of the folder imports from a copied DLL must be
    # exported by it. This catches a redistributable that lacks a function
    # the library needs, not every older one: a runtime with the same
    # exports passes whatever its version.
    lowered = {c.lower() for c in copied}
    for name, info in infos.items():
        for dll, functions in info.imports.items():
            if dll.lower() not in lowered:
                continue
            target = next(n for n in infos if n.lower() == dll.lower())
            missing = [fn for fn in functions if not fn.startswith('#') and fn not in infos[target].exports]
            if missing:
                raise XtalkError('%s/%s imports %s from %s, which the copy from %s does not export (the '
                                 'redistributable lacks functions the library needs)'
                                 % (where, name, ', '.join(missing[:8]), dll, redist[arch]))
    # Microsoft supports mixing binaries of different toolsets only when
    # the runtime is at least as new as the newest toolset used
    # (learn.microsoft.com/cpp/porting/binary-compat-2015-2017), and MSVC's
    # link.exe writes its toolset version as the linker version. The
    # copies are app-local, so they are loaded even on a PC with a newer
    # runtime installed. A warning, not an error: the windows-2022 CI image
    # has only Visual Studio 2022's 14.44 runtime, older than the 14.51
    # toolset that built enetxt.dll and box2dxt.dll.
    stale = []
    for name, info in infos.items():
        if name.lower() in lowered or not info.linker or info.linker[0] != 14:
            continue
        older = []
        for dll in info.imports:
            if dll.lower() not in lowered:
                continue
            target = next(n for n in infos if n.lower() == dll.lower())
            fv = infos[target].file_version
            if fv and tuple(fv[:2]) < tuple(info.linker):
                older.append((target, version_text(fv)))
        if older:
            stale.append((name, '%d.%d' % info.linker, older))
    return RuntimeResult(copied, [], stale, unpinned)


def extract_leading_comment(data, what):
    text = data.decode('utf-8', 'replace').replace('\r\n', '\n')
    stripped = text.lstrip()
    if not stripped.startswith('/*') or '*/' not in stripped:
        raise XtalkError('%s does not start with a /* ... */ comment' % what)
    return stripped[:stripped.index('*/') + 2] + '\n'


def _xml(text):
    return (text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
            .replace('"', '&quot;'))


def lcs_manifest(member, e):
    """manifest.xml of a script library, in the element order LiveCode's
    extension-utils writes (extensionGenerateManifestForLCSExtension)."""
    lines = ['<package version="0.0">',
             '  <name>%s</name>' % _xml(e['stack']),
             '  <title>%s</title>' % _xml(e['title']),
             '  <author>%s</author>' % _xml(e['author']),
             '  <version>%s</version>' % _xml(member['version']),
             '  <type>library</type>',
             '  <license>community</license>']
    for r in e.get('requires', []):
        lines.append('  <requires name="%s"/>' % _xml(r))
    lines.append('</package>')
    return ('\n'.join(lines) + '\n').encode('utf-8')


def wrap_lcs(data, stack, what):
    """The script library as shipped: the pinned bytes with a script header
    if there is none, plus LCS_WRAPPER, or the bytes unchanged if the
    library handles both messages itself."""
    header = lcs_header(data)
    if header is not None and header.lower() != stack.lower():
        raise XtalkError('%s starts with script "%s", the manifest says stack %s' % (what, header, stack))
    text = data.decode('utf-8', 'replace')
    has_init = re.search(r'^\s*(?:on|command)\s+extensionInitialize\b', text, re.M | re.I) is not None
    has_fini = re.search(r'^\s*(?:on|command)\s+extensionFinalize\b', text, re.M | re.I) is not None
    if has_init != has_fini:
        raise XtalkError('%s handles only one of extensionInitialize and extensionFinalize' % what)
    out = data
    if header is None:
        bom = b'\xef\xbb\xbf' if data.startswith(b'\xef\xbb\xbf') else b''
        out = bom + ('script "%s"\n' % stack).encode('ascii') + data[len(bom):]
    if not has_init:
        if not out.endswith(b'\n'):
            out += b'\n'
        out += LCS_WRAPPER.encode('ascii')
    return out, header is None, not has_init


def build(data, bin_dir, out, cache, platforms=None, vc_redist=None, offline=False, log=print,
          allow_unpinned_vc_runtime=False):
    """Fetch, compile and assemble every extension into out. Returns a
    summary dict (see the end of this function). A Visual C++ runtime
    DLL that is not the one the manifest's "vc_runtime" pins is an error,
    unless allow_unpinned_vc_runtime."""
    platforms = platforms or list(data['platforms'])
    bin_dir = os.path.abspath(bin_dir)
    lc_compile = lc_compile_path(bin_dir)
    lci_dir = os.path.join(bin_dir, 'modules', 'lci')
    if not os.path.isdir(lci_dir) or not any(n.endswith('.lci') for n in os.listdir(lci_dir)):
        raise XtalkError('%s has no module interfaces (*.lci)' % lci_dir)
    redist = find_vc_redist(vc_redist) if vc_redist else None
    vc_pins = data.get('vc_runtime') or {}
    out = os.path.abspath(out)
    local = fetch(data, cache, offline, platforms, None, log)
    os.makedirs(out, exist_ok=True)

    # Clear what this tool made before: the folders the old stamp lists and
    # the folders of the current manifest. Nothing else in out is touched.
    owned = {e['folder'] for _, e in all_extensions(data)}
    stamp = os.path.join(out, STAMP_NAME)
    if os.path.isfile(stamp):
        with open(stamp, encoding='utf-8', errors='replace') as f:
            for line in f:
                first = line.rstrip('\r\n').split('\t')[0]
                if line.strip() and not line.startswith('#') and _safe_name(first):
                    owned.add(first)
        os.remove(stamp)
    for name in os.listdir(out):
        path = os.path.join(out, name)
        if os.path.isdir(path) and (name in owned or name.startswith('.building-')):
            _rmtree(path)

    lci_before = _lci_snapshot(lci_dir)
    scratch = tempfile.mkdtemp(prefix='oxt-xtalk-lci-')
    built, runtime_copies, missing_runtime, stale_runtime = [], [], [], []
    try:
        for m, e in dependency_order(data):
            log('')
            log('%s (%s, %s %s)' % (e['folder'], e['kind'], m['name'], m['version']))
            tmp = os.path.join(out, '.building-' + e['folder'])
            os.makedirs(tmp)
            main_path = local[(m['name'], m['repository'], e['main'])]
            with open(main_path, 'rb') as f:
                main_data = f.read()
            if e['kind'] == 'lcb':
                _build_lcb(m, e, tmp, main_data, lc_compile, lci_dir, scratch, log)
                for f in member_files(m, 'library'):
                    platform = library_platform(f)
                    if platform not in platforms:
                        continue
                    dest = os.path.join(tmp, 'code', platform)
                    os.makedirs(dest, exist_ok=True)
                    shutil.copyfile(local[(m['name'], m['repository'], f['path'])],
                                    os.path.join(dest, posix_basename(f['path'])))
                for platform in WINDOWS_PLATFORMS:
                    folder = os.path.join(tmp, 'code', platform)
                    if platform not in platforms or not os.path.isdir(folder):
                        continue
                    _check_recorded_imports(m, folder, platform)
                    where = '%s/code/%s' % (e['folder'], platform)
                    result = complete_windows_runtime(folder, platform, redist, where, log,
                                                      (vc_pins.get('files') or {}).get(platform),
                                                      vc_pins.get('file_version'), allow_unpinned_vc_runtime)
                    for name in result.copied:
                        path = os.path.join(folder, name)
                        with open(path, 'rb') as fh:
                            version = version_text(pe_file_version(fh.read()))
                        runtime_copies.append((where + '/' + name, _sha256_file(path), version,
                                               name not in result.unpinned))
                    for lib, dlls in result.missing:
                        missing_runtime.append((where + '/' + lib, dlls))
                    for lib, linker, older in result.stale:
                        stale_runtime.append((where + '/' + lib, linker, older))
            else:
                shipped, headed, wrapped = wrap_lcs(main_data, e['stack'], '%s %s' % (m['name'], e['main']))
                if lcs_header(shipped) is None:
                    raise XtalkError('%s: the shipped script has no script header' % e['folder'])
                with open(os.path.join(tmp, e['stack'] + '.livecodescript'), 'wb') as f:
                    f.write(shipped)
                with open(os.path.join(tmp, 'manifest.xml'), 'wb') as f:
                    f.write(lcs_manifest(m, e))
                log('  %s.livecodescript: %s%s' % (e['stack'], 'script header added, ' if headed else '',
                                                    'extensionInitialize/extensionFinalize appended'
                                                    if wrapped else 'load handlers already present'))
            _add_licences(m, tmp, local)
            # The runtime DLLs are Microsoft's Distributable Code, under
            # neither the member's licence nor the GPL: their terms go with
            # them, in licenses/ (not in code/, whose every file the IDE
            # maps and the standalone builder copies)
            ext_copies = [(c[0][len(e['folder']) + 1:], c[2]) for c in runtime_copies
                          if c[0].startswith(e['folder'] + '/')]
            if ext_copies:
                _write_vc_runtime_notice(tmp, ext_copies, redist['version'], log)
            final = os.path.join(out, e['folder'])
            os.rename(tmp, final)
            files = []
            for dirpath, dirnames, filenames in os.walk(final):
                dirnames.sort()
                for name in sorted(filenames):
                    files.append(os.path.relpath(os.path.join(dirpath, name), out).replace(os.sep, '/'))
            built.append(collections.OrderedDict([
                ('folder', e['folder']), ('kind', e['kind']), ('id', e['id']), ('member', m['name']),
                ('version', m['version']), ('repository', m['repository']), ('commit', m['commit']),
                ('probe', e['probe']), ('expect', e['expect']), ('requires', list(e.get('requires', []))),
                ('stack', e.get('stack', '')), ('files', files)]))
            log('  %d files' % len(files))
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    if _lci_snapshot(lci_dir) != lci_before:
        raise XtalkError('%s changed while compiling; lc-compile must write the interfaces elsewhere' % lci_dir)

    if missing_runtime:
        log('')
        log('WARNING: ' + '=' * 70)
        log('WARNING: no --vc-redist: the Microsoft Visual C++ runtime is NOT bundled. These')
        log('WARNING: libraries cannot load on a PC without the Visual C++ Redistributable:')
        for lib, dlls in missing_runtime:
            log('WARNING:   %s needs %s' % (lib, ', '.join(dlls)))
        log('WARNING: ' + '=' * 70)
    unpinned = [c[0] for c in runtime_copies if not c[3]]
    if unpinned:
        log('')
        log('WARNING: ' + '=' * 70)
        log('WARNING: --allow-unpinned-vc-runtime: these Visual C++ runtime DLLs are NOT the')
        log('WARNING: pinned ones (file version %s, "vc_runtime" in the manifest):' % vc_pins.get('file_version'))
        for path in unpinned:
            log('WARNING:   %s' % path)
        log('WARNING: ' + '=' * 70)
    if stale_runtime:
        log('')
        log('WARNING: ' + '=' * 70)
        log('WARNING: the bundled Visual C++ runtime is older than the build tools of these')
        log('WARNING: libraries (Microsoft supports only a redistributable at least as new as the')
        log('WARNING: newest toolset used; pass a newer --vc-redist to fix it):')
        for lib, linker, older in stale_runtime:
            log('WARNING:   %s (MSVC %s): %s' % (lib, linker, ', '.join('%s %s' % o for o in older)))
        log('WARNING: ' + '=' * 70)
    _write_stamp(stamp, built, redist, runtime_copies, missing_runtime, stale_runtime, platforms,
                 vc_pins.get('file_version'))
    return collections.OrderedDict([
        ('out', out), ('stamp', STAMP_NAME), ('extensions', built), ('platforms', platforms),
        ('vc_redist', redist['folder'] if redist else None),
        ('vc_redist_version', redist['version'] if redist else None),
        # True unless a copy is not the pinned one (--allow-unpinned-vc-runtime)
        ('vc_runtime_pinned', not unpinned),
        ('vc_runtime_pinned_file_version', vc_pins.get('file_version')),
        ('vc_runtime_files', [collections.OrderedDict([('path', p), ('sha256', h), ('file_version', v),
                                                       ('pinned', ok)])
                              for p, h, v, ok in runtime_copies]),
        ('missing_runtime', [collections.OrderedDict([('library', lib), ('needs', dlls)])
                             for lib, dlls in missing_runtime]),
        ('vc_runtime_older_than_build_tools', [
            collections.OrderedDict([('library', lib), ('linker', linker),
                                     ('runtime', [collections.OrderedDict([('dll', d), ('file_version', v)])
                                                  for d, v in older])])
            for lib, linker, older in stale_runtime]),
    ])


def posix_basename(path):
    return path.rsplit('/', 1)[-1]


def _sha256_file(path):
    return fetch_assets.file_digest(path)[0]


def _check_recorded_imports(member, folder, platform):
    """The DLLs a library imports must be the ones the manifest records
    (they decide which runtime DLLs are shipped)."""
    for f in member_files(member, 'library'):
        if library_platform(f) != platform:
            continue
        with open(os.path.join(folder, posix_basename(f['path'])), 'rb') as fh:
            actual = list(pe_info(fh.read(), f['path']).imports)
        if sorted(x.lower() for x in actual) != sorted(x.lower() for x in f['imports']):
            raise XtalkError('%s %s imports %s, the manifest records %s; run "xtalk_extensions.py pin"'
                             % (member['name'], f['path'], ', '.join(actual), ', '.join(f['imports'])))


def _build_lcb(member, e, folder, source, lc_compile, lci_dir, scratch, log):
    # The source goes next to module.lcm under its own name, and lc-compile
    # runs in that folder with relative paths: module.lcm embeds the source
    # path exactly as given, so the module is the same wherever it is built
    # and the IDE finds the source that matches it. --interface keeps the
    # .lci out of --modulepath (lc-compile writes it into the first
    # --modulepath otherwise). No -Werror: org.openxtalk.box2dxt has a digit
    # in a namespace component, which is a warning that cannot be fixed
    # without changing the id.
    name = posix_basename(e['main'])
    with open(os.path.join(folder, name), 'wb') as f:
        f.write(source)
    cmd = [lc_compile, '--modulepath', lci_dir, '--interface', os.path.join(scratch, e['id'] + '.lci'),
           '--manifest', 'manifest.xml', '--output', 'module.lcm', name]
    try:
        p = subprocess.run(cmd, cwd=folder, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=600)
    except (OSError, subprocess.SubprocessError) as err:
        raise XtalkError('%s: could not run %s: %s' % (e['folder'], lc_compile, err))
    output = p.stdout.decode('utf-8', 'replace').strip()
    for line in output.splitlines():
        log('  lc-compile: ' + line.rstrip())
    if p.returncode != 0:
        raise XtalkError('%s: lc-compile failed with exit code %d' % (e['folder'], p.returncode))
    for produced in ('module.lcm', 'manifest.xml'):
        if not os.path.isfile(os.path.join(folder, produced)):
            raise XtalkError('%s: lc-compile wrote no %s' % (e['folder'], produced))
    try:
        root = ET.parse(os.path.join(folder, 'manifest.xml')).getroot()
    except ET.ParseError as err:
        raise XtalkError('%s: manifest.xml: %s' % (e['folder'], err))
    if root.tag != 'package' or (root.findtext('name') or '') != e['id']:
        raise XtalkError('%s: manifest.xml names %r, expected %s' % (e['folder'], root.findtext('name'), e['id']))
    if (root.findtext('version') or '') != member['version']:
        raise XtalkError('%s: manifest.xml has version %r, the manifest %s'
                         % (e['folder'], root.findtext('version'), member['version']))
    log('  compiled %s: module.lcm %d bytes, %d handlers'
        % (name, os.path.getsize(os.path.join(folder, 'module.lcm')), len(root.findall('handler'))))


def _add_licences(member, folder, local):
    dest = os.path.join(folder, LICENCE_DIR)
    os.makedirs(dest, exist_ok=True)
    for f in member_files(member, 'licence'):
        repo, commit = file_source(member, f)
        target = os.path.join(dest, f.get('name') or posix_basename(f['path']))
        if os.path.exists(target):
            raise XtalkError('%s: two licence files are named %s; give one a "name"'
                             % (member['name'], os.path.basename(target)))
        path = local[(member['name'], repo, f['path'])]
        if f.get('extract') == 'leading-comment':
            with open(path, 'rb') as fh:
                comment = extract_leading_comment(fh.read(), f['path'])
            text = ('The licence notice at the top of %s in github.com/%s at commit %s,\n'
                    'which is compiled into the native libraries of this extension:\n\n%s'
                    % (f['path'], repo, commit, comment))
            with open(target, 'wb') as fh:
                fh.write(text.encode('utf-8'))
        else:
            shutil.copyfile(path, target)


# Microsoft's terms for the runtime DLLs, for the notice written next to
# them: (lowest, highest major.minor of their file version, the Visual
# Studio release whose licence covers them, its licence terms, its REDIST
# list). The runtime of Visual Studio 2022 is 14.30 to 14.4x. A runtime
# outside every range gets a notice without a release and a warning:
# review the terms and add its release here before pinning it.
VC_RUNTIME_LICENCES = [
    ((14, 30), (14, 49), 'Visual Studio 2022', 'https://visualstudio.microsoft.com/license-terms/',
     'https://learn.microsoft.com/visualstudio/releases/2022/redistribution'),
]
VC_REDIST_DOWNLOAD = 'https://learn.microsoft.com/cpp/windows/latest-supported-vc-redist'


def _write_vc_runtime_notice(folder, copies, redist_version, log):
    """Write licenses/Microsoft-Visual-C++-Runtime.txt (CRLF, UTF-8) into
    an extension folder for its copied runtime DLLs, copies being [(path
    in the extension, file version)]. A paraphrase of Microsoft's terms
    with links to them, not the terms themselves."""
    target = os.path.join(folder, LICENCE_DIR, VC_RUNTIME_NOTICE)
    if os.path.exists(target):
        raise XtalkError('%s: a licence file of the member is named %s, which the Visual C++ runtime notice needs'
                         % (os.path.basename(folder), VC_RUNTIME_NOTICE))
    versions = [tuple(int(n) for n in v.split('.')) if re.match(r'^[0-9.]+$', v) else None for _, v in copies]
    licence = next((l for l in VC_RUNTIME_LICENCES if all(v and l[0] <= v[:2] <= l[1] for v in versions)), None)
    if licence:
        product, terms, redist_list = licence[2], licence[3], licence[4]
        source = '%s\'s VC\\Redist folder (VC\\Redist\\MSVC\\%s)' % (product, redist_version)
        under = ('the Distributable Code section of the Microsoft Software License Terms for %s (%s) and its '
                 'REDIST list (%s).' % (product, terms, redist_list))
    else:
        log('  WARNING: %s: the runtime\'s file version (%s) is not one VC_RUNTIME_LICENCES knows; the notice '
            'names no Visual Studio release. Review Microsoft\'s terms for it and add it there'
            % (VC_RUNTIME_NOTICE, ', '.join(sorted({v for _, v in copies}))))
        source = 'a Visual Studio VC\\Redist folder (VC\\Redist\\MSVC\\%s)' % redist_version
        under = ('the Distributable Code section of the Microsoft Software License Terms for the Visual Studio '
                 'release they come from (https://visualstudio.microsoft.com/license-terms/) and its REDIST list.')

    def para(text, indent=''):
        return textwrap.wrap(text, 72, initial_indent=indent, subsequent_indent=' ' * len(indent),
                             break_long_words=False, break_on_hyphens=False)

    width = max(len(path) for path, _ in copies)
    lines = ['Microsoft Visual C++ runtime', '============================', '']
    lines += para('The Windows libraries of this extension are built with the dynamic Visual C++ runtime, so '
                  'OpenXTalk-Lite ships these Microsoft files next to them:') + ['']
    lines += ['  %s  (file version %s)' % (path.ljust(width), v) for path, v in copies] + ['']
    lines += para('Copyright (c) Microsoft Corporation. Microsoft Visual C++ runtime, copied unmodified from '
                  '%s.' % source) + ['']
    lines += para('These files are Microsoft Distributable Code. They are covered neither by this extension\'s '
                  'licence (the other files in this folder) nor by OpenXTalk-Lite\'s licence (the GNU General Public '
                  'License version 3). They are distributed under %s This file summarises some of those terms; '
                  'it does not reproduce them. Read them.' % under) + ['']
    lines += para('Anyone who distributes these files further, including in a standalone application (the '
                  'standalone builder copies them into its Externals folder together with this extension\'s '
                  'library), must comply with those terms. Among other conditions, they require you to:') + ['']
    for item in ('distribute the files only unmodified, and only as part of a program that adds significant '
                 'primary functionality to them;',
                 'require distributors and end users to agree to terms that protect the files at least as much '
                 'as Microsoft\'s terms do;',
                 'not use Microsoft\'s trademarks in a way that suggests your program comes from or is endorsed '
                 'by Microsoft;',
                 'not make the files subject to a licence that requires their source code to be disclosed or '
                 'distributed, or that lets others modify them.'):
        lines += para(item, '  - ')
    lines += ['']
    lines += para('If you do not want to redistribute them, delete them from your standalone\'s Externals '
                  'folder and have the PCs that run it install the Microsoft Visual C++ Redistributable instead: '
                  '%s' % VC_REDIST_DOWNLOAD)
    with open(target, 'wb') as f:
        f.write(('\r\n'.join(lines) + '\r\n').encode('utf-8'))


def _write_stamp(path, built, redist, runtime_copies, missing_runtime, stale_runtime, platforms,
                 pinned_version=None):
    lines = [
        '# xTalk Suite extensions bundled with OpenXTalk-Lite',
        '#',
        '# Built by tools/oxt/xtalk_extensions.py from tools/oxt/xtalk-extensions.json',
        '# in the OpenXTalk-Lite repository. Each extension comes unchanged from its',
        '# repository at the commit below (script libraries get a script header if',
        '# they have none and an extensionInitialize/extensionFinalize pair; LCB',
        '# libraries are compiled with this release\'s lc-compile). The licence',
        '# files of each extension are in its licenses folder; THIRD-PARTY-NOTICES.md',
        '# at the root of the program folder lists what each one contains.',
        '#',
        '# Native library platforms: %s' % ', '.join(platforms),
        '#',
        '# folder\tkind\tid\tmember\tversion\tsource',
    ]
    for b in built:
        lines.append('\t'.join([b['folder'], b['kind'], b['id'], b['member'], b['version'],
                                'https://github.com/%s/tree/%s' % (b['repository'], b['commit'])]))
    lines.append('#')
    if runtime_copies:
        lines.append('# Microsoft Visual C++ runtime, copied unchanged from Visual Studio\'s')
        lines.append('# redistributable folder VC\\Redist\\MSVC\\%s next to the libraries that import it.'
                     % redist['version'])
        if all(ok for _, _, _, ok in runtime_copies):
            lines.append('# These are the PINNED runtime, file version %s (tools/oxt/xtalk-extensions.json,'
                         % pinned_version)
            lines.append('# "vc_runtime"), listed with their SHA-256 and file version:')
        else:
            lines.append('# WARNING: NOT the pinned runtime (file version %s, "vc_runtime" in'
                         % (pinned_version or '(none pinned)'))
            lines.append('# tools/oxt/xtalk-extensions.json): packaged with --allow-unpinned-vc-runtime.')
            lines.append('# The files marked NOT PINNED differ from the pins:')
        for p, h, v, ok in runtime_copies:
            lines.append('#   %s  %s  (file version %s)%s' % (p, h, v, '' if ok else '  NOT PINNED'))
        lines.append('# They are Microsoft Distributable Code, not under the extensions\' licences or the')
        lines.append('# GPL: see %s/%s in each of these extensions.' % (LICENCE_DIR, VC_RUNTIME_NOTICE))
        for lib, linker, older in stale_runtime:
            lines.append('# WARNING: %s was built with MSVC %s, the runtime copied for it is older (%s);'
                         % (lib, linker, ', '.join('%s %s' % o for o in older)))
            lines.append('#   Microsoft supports only a runtime at least as new as the newest toolset used.')
    elif missing_runtime:
        lines.append('# WARNING: the Microsoft Visual C++ runtime is not bundled. These libraries')
        lines.append('# cannot load on a PC without the Visual C++ Redistributable:')
        for lib, dlls in missing_runtime:
            lines.append('#   %s needs %s' % (lib, ', '.join(dlls)))
    else:
        lines.append('# No library needs the Microsoft Visual C++ runtime.')
    with open(path, 'wb') as f:
        f.write(('\r\n'.join(lines) + '\r\n').encode('utf-8'))


def cmd_build(args, log=print):
    data = load_manifest(args.manifest)
    platforms = selected_platforms(data, args.platforms)
    cache = cache_dir(args.repo, args.cache)
    log('xTalk extensions cache: %s' % cache)
    if not args.vc_redist:
        log('WARNING: no --vc-redist; libraries that need the Visual C++ runtime get no copy of it')
    t0 = time.time()
    result = build(data, args.bin, args.out, cache, platforms, args.vc_redist, args.offline, log,
                   args.allow_unpinned_vc_runtime)
    log('')
    log('built %d extensions into %s in %.0f s' % (len(result['extensions']), result['out'], time.time() - t0))
    if args.summary_json:
        with open(args.summary_json, 'w', encoding='utf-8', newline='\n') as f:
            json.dump(result, f, indent=2)
            f.write('\n')
    return 0


# ---------------------------------------------------------------------------
# pin-vc-runtime

VC_RUNTIME_COMMENT = [
    'The Microsoft Visual C++ runtime DLLs that build copies next to the',
    'Windows libraries importing them (enetxt, Box2Dxt), pinned by SHA-256 and',
    'size so that a package does not depend on which Visual Studio the',
    'machine that makes it has: build and package.py stop at any other copy',
    '(--allow-unpinned-vc-runtime only warns; never for a release). "redist"',
    'names the redistributable folder they were pinned from (VC\\Redist\\MSVC\\',
    '<version>; 14.44.35112 is Visual Studio 2022 17.14\'s, the one on the',
    'windows-2022 CI image) and "file_version" their file version.',
    '"xtalk_extensions.py pin-vc-runtime --vc-redist DIR" rewrites "redist",',
    '"file_version" and "files".',
]


def runtime_pins(data, redist, log):
    """({platform: {lower-case DLL name: {sha256, size}}}, file version):
    for each Windows platform of the manifest, every DLL of the
    redistributable that a member's library there imports (its recorded
    "imports", less Windows system DLLs and the member's own files), and
    what those DLLs import in turn, as build copies them."""
    files = collections.OrderedDict()
    versions = set()
    for platform in [p for p in data['platforms'] if p in WINDOWS_PLATFORMS]:
        arch, machine = WINDOWS_PLATFORMS[platform]
        source_dir = redist[arch]
        available = {n.lower(): n for n in os.listdir(source_dir)}
        pins = {}
        for m in data['members']:
            libraries = [f for f in member_files(m, 'library') if library_platform(f) == platform]
            own = {posix_basename(f['path']).lower() for f in libraries}
            queue = [(posix_basename(f['path']), d) for f in libraries for d in f['imports']]
            while queue:
                importer, dll = queue.pop(0)
                key = dll.lower()
                if is_system_dll(dll) or key in own or key in pins:
                    continue
                if key not in available:
                    raise XtalkError('%s %s/%s imports %s, which is neither a Windows system DLL nor in %s'
                                     % (m['name'], platform, importer, dll, source_dir))
                path = os.path.join(source_dir, available[key])
                with open(path, 'rb') as fh:
                    body = fh.read()
                info = pe_info(body, path)
                if info.machine != machine:
                    raise XtalkError('%s: PE machine 0x%04x, expected 0x%04x' % (path, info.machine, machine))
                version = version_text(info.file_version)
                versions.add(version)
                pins[key] = collections.OrderedDict([('sha256', hashlib.sha256(body).hexdigest()),
                                                     ('size', len(body))])
                log('  %-32s %s %9d  file version %s  (imported by %s)'
                    % (platform + '/' + key, pins[key]['sha256'], len(body), version, importer))
                queue.extend((available[key], d) for d in info.imports)
        if pins:
            files[platform] = collections.OrderedDict(sorted(pins.items()))
    if len(versions) > 1:
        raise XtalkError('the DLLs taken from %s have different file versions (%s); pin one redistributable '
                         'folder whose DLLs all have the same version' % (redist['folder'], ', '.join(sorted(versions))))
    return files, (versions.pop() if versions else '')


def cmd_pin_vc_runtime(args, log=print):
    data = load_manifest(args.manifest)
    redist = find_vc_redist(args.vc_redist)
    log('Visual C++ redistributable: %s' % redist['folder'])
    files, version = runtime_pins(data, redist, log)
    old = data.get('vc_runtime') or {}
    block = collections.OrderedDict([
        ('comment', old.get('comment', VC_RUNTIME_COMMENT)),
        ('redist', redist['version']),
        ('file_version', version),
        ('files', files)])
    # The block goes after "platforms" the first time; later it stays where it is
    if 'vc_runtime' in data:
        data['vc_runtime'] = block
    else:
        items = list(data.items())
        at = [k for k, _ in items].index('platforms') + 1
        data = collections.OrderedDict(items[:at] + [('vc_runtime', block)] + items[at:])
    validate(data, args.manifest, pinned=True)
    with open(args.manifest, encoding='utf-8') as f:
        before = f.read()
    if before == dump_manifest(data):
        log('%s is unchanged' % args.manifest)
    else:
        write_manifest(args.manifest, data)
        log('rewrote "vc_runtime" in %s (file version %s, from %s); review and commit it, and update its comment '
            'if it names another redistributable' % (args.manifest, version, redist['version']))
    return 0


# ---------------------------------------------------------------------------
# export

# Every entry of the export zip gets this date (the earliest a zip can
# hold), so the zip depends only on the pinned files, not on when they
# were downloaded
EXPORT_DATE = (1980, 1, 1, 0, 0, 0)
EXPORT_README = '''xTalk Suite extension sources of an OpenXTalk-Lite release
=====================================================

Every file that tools/oxt/xtalk-extensions.json (the copy in this zip)
pins, exactly as tools/oxt/xtalk_extensions.py downloaded and verified it
from https://raw.githubusercontent.com/<repository>/<commit>/<path>, in the
layout of its download cache: <repository name>/<commit>/<path>.

The packages fetch these files from the member repositories when they are
made. This copy keeps a release reproducible if a member repository later
loses a pinned commit (a rewritten history, a renamed or private
repository). To rebuild the release, check out its tag, extract this zip
into an empty folder and package with that folder as the cache, offline:

  package-windows.ps1 -XtalkCache <folder> ...     (tools/ci)
  python tools/oxt/package.py --xtalk-cache <folder> --offline ...
  python tools/oxt/xtalk_extensions.py build --cache <folder> --offline ...

Every file is still checked against the size and SHA-256 in the manifest.
The licence of each file is that of its repository (see its licenses/
folder in the program's Extensions folder and THIRD-PARTY-NOTICES.md).
'''


def export_entries(data, cache):
    """[(name in the zip, local file)] for every pinned file, sorted and
    without duplicates (two members may pin the same file, OpenSSL's
    licence text)."""
    entries = {}
    for m in data['members']:
        for f in m['files']:
            path = cache_file(cache, m, f)
            name = os.path.relpath(path, cache).replace(os.sep, '/')
            entries.setdefault(name, path)
    return sorted(entries.items())


def _zip_info(name):
    zi = zipfile.ZipInfo(name, date_time=EXPORT_DATE)
    zi.compress_type = zipfile.ZIP_DEFLATED
    zi.create_system = 3
    zi.external_attr = (0o100644 & 0xFFFF) << 16
    return zi


def export(data, manifest_bytes, cache, out, offline=False, log=print):
    """Fetch every pinned file and write out (a zip). Returns (files,
    bytes of the files, sha256 of the zip)."""
    fetch(data, cache, offline, None, None, log)
    entries = export_entries(data, cache)
    out = os.path.abspath(out)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    tmp = out + '.part'
    total = 0
    with zipfile.ZipFile(tmp, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        # The manifest with LF line endings, as git stores it, so that a
        # CRLF checkout exports the same zip
        z.writestr(_zip_info('xtalk-extensions.json'), manifest_bytes.replace(b'\r\n', b'\n'))
        z.writestr(_zip_info('README.txt'), EXPORT_README.encode('utf-8'))
        for name, path in entries:
            with open(path, 'rb') as src, z.open(_zip_info(name), 'w') as dst:
                while True:
                    b = src.read(1 << 20)
                    if not b:
                        break
                    dst.write(b)
                    total += len(b)
    os.replace(tmp, out)
    return len(entries), total, _sha256_file(out)


def cmd_export(args, log=print):
    data = load_manifest(args.manifest)
    with open(args.manifest, 'rb') as f:
        manifest_bytes = f.read()
    cache = cache_dir(args.repo, args.cache)
    log('xTalk extensions cache: %s' % cache)
    count, size, digest = export(data, manifest_bytes, cache, args.out, args.offline, log)
    log('wrote %s: %d pinned files (%s bytes), SHA-256 %s'
        % (os.path.abspath(args.out), count, '{:,}'.format(size), digest))
    return 0


# ---------------------------------------------------------------------------
# list

def cmd_list(args, log=print):
    data = load_manifest(args.manifest, pinned=False)
    for m in data['members']:
        log('%s %s  %s@%s' % (m['name'], m['version'] or '?', m['repository'], m['commit'] or '(not pinned)'))
        size = sum(f.get('size') or 0 for f in m['files'])
        log('  %d files, %s bytes' % (len(m['files']), '{:,}'.format(size)))
        for e in m['extensions']:
            requires = (' requires ' + ', '.join(e['requires'])) if e.get('requires') else ''
            log('  %-4s Extensions/%s%s' % (e['kind'], e['folder'], requires))
            log('       probe %s  expect /%s/' % (e['probe'], e['expect']))
        for f in member_files(m, 'library'):
            if f.get('imports'):
                runtime = [d for d in f['imports'] if not is_system_dll(d)]
                if runtime:
                    log('  %s needs %s' % (f['path'], ', '.join(runtime)))
    return 0


# ---------------------------------------------------------------------------
# Command line

def main(argv=None):
    parser = argparse.ArgumentParser(
        description='Pin, fetch and build the xTalk Suite extensions listed in tools/oxt/xtalk-extensions.json.')
    parser.add_argument('--manifest', default=DEFAULT_MANIFEST, help='manifest (default: %(default)s)')
    parser.add_argument('--repo', help='repository root (default: two levels above this script)')
    sub = parser.add_subparsers(dest='command')
    sub.required = True

    p = sub.add_parser('pin', help='pin members to a commit and re-hash their files')
    p.add_argument('--member', action='append', help='only this member (repeatable; default: all)')
    p.add_argument('--ref', help='branch, tag or commit to pin (default: HEAD of the default branch); '
                                 'needs exactly one --member. A commit given by its SHA-1 must be on the '
                                 'default branch; so must a branch or tag, unless --allow-off-branch')
    p.add_argument('--allow-off-branch', action='store_true',
                   help='pin a --ref branch or tag whose commit is not on the default branch (it '
                        'disappears when that branch is deleted)')
    p.add_argument('--cache', metavar='DIR', help='also store the downloads in this cache '
                                                  '(default: as for fetch)')
    p.add_argument('--no-cache', action='store_true', help='do not store the downloads')
    p.set_defaults(func=cmd_pin)

    p = sub.add_parser('fetch', help='download and verify the pinned files')
    p.add_argument('--cache', metavar='DIR', help='cache folder (default: $%s, else <asset cache>/xtalk, the '
                                                  'asset cache being $%s, else <repo>/%s)'
                                                  % (CACHE_ENV, fetch_assets.CACHE_ENV,
                                                     fetch_assets.DEFAULT_CACHE_REL.replace(os.sep, '/')))
    p.add_argument('--offline', action='store_true', help='use the cache only')
    p.add_argument('--platforms', action='append', metavar='LIST',
                   help='only the native libraries of these platform ids (comma-separated; default: all)')
    p.add_argument('--member', action='append', help='only this member (repeatable)')
    p.set_defaults(func=cmd_fetch)

    p = sub.add_parser('build', help='compile and assemble the extension folders')
    p.add_argument('--bin', required=True, help='engine build output with lc-compile and modules/lci')
    p.add_argument('--out', required=True, help='folder to write the extension folders into')
    p.add_argument('--cache', metavar='DIR', help='cache folder (as for fetch)')
    p.add_argument('--offline', action='store_true', help='use the cache only')
    p.add_argument('--platforms', action='append', metavar='LIST',
                   help='only the native libraries of these platform ids (comma-separated; default: all)')
    p.add_argument('--vc-redist', metavar='DIR',
                   help='Visual Studio\'s VC\\Redist\\MSVC\\<version> folder (VCToolsRedistDir): '
                        'bundle the Visual C++ runtime DLLs the Windows libraries import')
    p.add_argument('--allow-unpinned-vc-runtime', action='store_true',
                   help='only warn when a Visual C++ runtime DLL is not the one "vc_runtime" pins (never for '
                        'a release)')
    p.add_argument('--summary-json', metavar='FILE', help='write what was built as JSON')
    p.set_defaults(func=cmd_build)

    p = sub.add_parser('pin-vc-runtime', help='pin the Visual C++ runtime DLLs to those of a redistributable '
                                              'folder')
    p.add_argument('--vc-redist', metavar='DIR', required=True,
                   help='Visual Studio\'s VC\\Redist\\MSVC\\<version> folder (VCToolsRedistDir)')
    p.set_defaults(func=cmd_pin_vc_runtime)

    p = sub.add_parser('export', help='write every pinned file into a zip in the cache layout, to keep with '
                                      'a release')
    p.add_argument('--out', required=True, metavar='FILE.zip', help='zip to write')
    p.add_argument('--cache', metavar='DIR', help='cache folder (as for fetch)')
    p.add_argument('--offline', action='store_true', help='use the cache only')
    p.set_defaults(func=cmd_export)

    p = sub.add_parser('list', help='list the members, extensions and probes')
    p.set_defaults(func=cmd_list)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (XtalkError, fetch_assets.AssetError) as e:
        sys.stderr.write('error: %s\n' % e)
        return 1


if __name__ == '__main__':
    sys.exit(main())
