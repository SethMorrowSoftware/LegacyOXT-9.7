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

"""Map an installed OpenXTalk Lite / LiveCode 9.x Windows folder to this
repository's layout, and back.

The installed program folder (for example "C:\\Program Files\\OpenXTalk\\
OpenXTalk Lite", or the contents of openxtalk-lite-1.15-win-noinstaller.7z)
is assembled by the upstream packager from several places.
Installer/package.txt, run by builder/tools_builder.livecodescript and
builder/package_compiler.livecodescript, says where each part comes from:

  Toolset/**                         rfolder ide:Toolset (names starting
                                     with "." are skipped)
  Toolset/libraries/<11 files>       stack/file ide-support:<name>
  Plugins/*, Resources/**            listed ide:Plugins and ide:Resources items
  Runtime/Windows/<arch>/Support/Sample Icons/*
                                     file ide:Resources/Sample Icons/*
  Resources/Mobile Examples/**       ide:Resources/Mobile Examples (Mac only)
  Documentation/**                   listed ide:Documentation items, plus
                                     repo:docs/guides, the docs builder output
                                     and a generated PDF
  License Agreement.txt, about.txt,
  Open Source Licenses.txt           textfile ide:<name>
  Release Notes.pdf                  generated (repo:LiveCodeNotes-<ver>.pdf)
  edition.txt                        written by the packager
  <ProductName>.exe, *.dll,
  Externals/**, Toolchain/**,
  Runtime/**                         engine builds
  Extensions/com.livecode.*          extensions built from extensions/
                                     (the timezone library's zoneinfo data
                                     and native code come from the macOS
                                     and other platform builds)
  Ext/**                             the mergExt collection (downloaded)

OpenXTalk Lite ships its own versions of Toolset, Plugins, Resources and
Documentation, extra root files (about.dat, .version, .buildnumber, an icon;
up to 1.07 also its own Release Notes.pdf) and extensions that are not built
from this repository. This tool gives every installed path one class:

  ide       (a) IDE content, kept in the repository. The repository path is
                given (ide/..., ide-support/...). Extensions that the IDE
                ships but this repository does not build go to ide/Extensions/.
  build     (b) produced by building or packaging this repository: never
                imported.
  external  (c) binaries for other platforms or third-party collections that
                this repository's Windows build does not produce (mergExt,
                other-platform runtimes, timezone code and zoneinfo data).
                Not kept in git; package.py adds them from the release
                assets in external-assets.json (mergExt is not
                redistributed).
  junk      (d) files that are not part of the product (shortcuts left by an
                install script, zero-byte test databases).
  excluded  (e) shipped by OXT Lite but not redistributed by this project
                because of their licences (see NOT_REDISTRIBUTABLE): never
                imported, and removed from the repository by import.
  xtalk     (f) the xTalk Suite extensions OpenXTalk-Lite ships built in
                (Extensions/<folder> for every folder in
                xtalk-extensions.json, and Extensions/XTALK-EXTENSIONS.txt):
                built by tools/oxt/xtalk_extensions.py from their own
                repositories at pinned commits. Never imported; package.py
                adds them.
  unknown       no rule matches: the rules need updating.

All rules are in RULES below; everything else derives from them.

Subcommands
  classify <installed-root>             one TSV line per file:
                                        class, installed path, repo path,
                                        size, reason
  import <installed-root> <repo-root>   make the managed repository files
                                        mirror the install: copy new and
                                        changed files, delete managed files
                                        the install does not have
  assemble <repo-root> <out-dir>        write the IDE part (class a) of the
                                        installed layout from the repository
  verify <installed-root> <repo-root>   assemble into a temporary folder and
                                        compare with the install's class (a)
                                        files; exit status 1 on any difference

"Managed" repository files are the files under the repository side of the
class (a) rules (ide/Toolset, ide/Plugins, ide/Resources, ide/Documentation,
ide/Extensions, the listed ide/ root files and the 11 ide-support files),
except names starting with "." (upstream never packages them) and
ide/Resources/Mobile Examples (installed on macOS only, so a Windows install
says nothing about them). Nothing outside that set is ever written or
deleted.

Text files (see TEXT_EXTENSIONS) are compared with line endings normalised,
because git stores them with LF and may check them out with CRLF; every other
file is compared byte for byte. import writes the installed bytes unchanged.

Only the Python 3 standard library is used.
"""

import argparse
import collections
import json
import os
import re
import shutil
import sys
import tempfile

# ---------------------------------------------------------------------------
# Classes

IDE = 'ide'            # (a)
BUILD = 'build'        # (b)
EXTERNAL = 'external'  # (c)
JUNK = 'junk'          # (d)
EXCLUDED = 'excluded'  # (e)
XTALK = 'xtalk'        # (f)
UNKNOWN = 'unknown'

CLASS_ORDER = (IDE, BUILD, EXTERNAL, XTALK, JUNK, EXCLUDED, UNKNOWN)

# ---------------------------------------------------------------------------
# Inputs for the rules

# Installer/package.txt, component Toolset: these ide-support files are
# placed into Toolset/libraries.
IDE_SUPPORT_FILES = (
    'revdeploylibraryandroid.livecodescript',
    'revdeploylibraryemscripten.livecodescript',
    'revdeploylibraryios.livecodescript',
    'revdocsparser.livecodescript',
    'revhtml5urllibrary.livecodescript',
    'revliburl.livecodescript',
    'revsaveasandroidstandalone.livecodescript',
    'revsaveasemscriptenstandalone.livecodescript',
    'revsaveasiosstandalone.livecodescript',
    'revsaveasstandalone.livecodescript',
    'revsblibrary.livecodescript',
)

# Installer/package.txt, components Extensions and TimeZone: extensions
# packaged from this repository's extensions/ folder.
REPO_BUILT_EXTENSIONS = (
    'com.livecode.library.androidaudiorecorder',
    'com.livecode.library.androidbgaudio',
    'com.livecode.library.androidutils',
    'com.livecode.library.diff',
    'com.livecode.library.drawing',
    'com.livecode.library.dropbox',
    'com.livecode.library.extension-utils',
    'com.livecode.library.getopt',
    'com.livecode.library.httpd',
    'com.livecode.library.iconsvg',
    'com.livecode.library.json',
    'com.livecode.library.messageauthentication',
    'com.livecode.library.mime',
    'com.livecode.library.native.mac.statusmenu',
    'com.livecode.library.oauth2',
    'com.livecode.library.objectrepository',
    'com.livecode.library.qr',
    'com.livecode.library.scriptitems',
    'com.livecode.library.timezone',
    'com.livecode.library.toast',
    'com.livecode.library.widgetutils',
    'com.livecode.widget.browser',
    'com.livecode.widget.clock',
    'com.livecode.widget.colorswatch',
    'com.livecode.widget.gradientrampeditor',
    'com.livecode.widget.headerbar',
    'com.livecode.widget.iconpicker',
    'com.livecode.widget.linegraph',
    'com.livecode.widget.native.android.button',
    'com.livecode.widget.native.android.field',
    'com.livecode.widget.native.emscripten.button',
    'com.livecode.widget.native.ios.button',
    'com.livecode.widget.native.mac.button',
    'com.livecode.widget.native.mac.textfield',
    'com.livecode.widget.navbar',
    'com.livecode.widget.paletteactions',
    'com.livecode.widget.segmented',
    'com.livecode.widget.spinner',
    'com.livecode.widget.svgpath',
    'com.livecode.widget.switchbutton',
    'com.livecode.widget.tile',
    'com.livecode.widget.treeview',
)

# The xTalk Suite extensions (class xtalk): their folders are the ones
# tools/oxt/xtalk-extensions.json lists, read here so that the manifest
# stays the only list. They must be classified before the Extensions/**
# rule below, which would otherwise make them IDE content that "import"
# copies into ide/Extensions/.
XTALK_MANIFEST = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'xtalk-extensions.json')
XTALK_STAMP = 'XTALK-EXTENSIONS.txt'


def _xtalk_extensions(path=XTALK_MANIFEST):
    """[(folder, member, repository, commit)] from the manifest; empty
    when there is no manifest."""
    try:
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
    except FileNotFoundError:
        return []
    out = []
    for m in data.get('members', []):
        for e in m.get('extensions', []):
            out.append((e['folder'], m['name'], m['repository'], m.get('commit', '')))
    return out


XTALK_EXTENSIONS = _xtalk_extensions()

# Root files of the install that are IDE content, kept at the root of ide/.
ROOT_IDE_FILES = (
    ('.buildnumber', 'OXT build number, read by the updater'),
    ('.version', 'OXT version, read by the IDE (revmenubar) and the updater'),
    ('about.dat', 'OXT About text (replaces about.txt)'),
    ('about.txt', 'package.txt Misc: textfile ide:about.txt'),
    ('License Agreement.txt', 'package.txt Misc: textfile ide:License Agreement.txt'),
    ('Open Source Licenses.txt', 'package.txt Misc: textfile ide:Open Source Licenses.txt'),
    ('OpenXTalk-lite_1024.ico', 'OXT application icon'),
    ('Release Notes.pdf', 'OXT Lite 1.07 and earlier ship their own release '
     'notes (Terry Little); upstream generates the file from '
     'repo:LiveCodeNotes-<version>.pdf'),
)

# The repository's own guides (docs/guides), which package.txt copies into
# Documentation/guides: their paths below docs/guides, from the checkout
def _repo_guides():
    base = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                        'docs', 'guides')
    out = []
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith('.'))
        for name in sorted(filenames):
            if not name.startswith('.'):
                out.append(os.path.relpath(os.path.join(dirpath, name), base).replace(os.sep, '/'))
    return tuple(out)


REPO_GUIDES = _repo_guides()

# Files OXT Lite shipped that this project does not redistribute, with the
# reason. They are left out of the repository and its packages.
NOT_REDISTRIBUTABLE = (
    ('Documentation/linked_files/apple-human-interface-guidelines-2005.pdf',
     'Apple Inc. copyright; no licence to redistribute'),
    ('Documentation/linked_files/animationEngine6.zip',
     'third-party library (animationEngine) with no licence in the release'),
)


class Rule(object):
    """One classification rule.

    pattern: installed path, relative, with "/" separators. "*" matches
             within one path component and "**" (only as the last
             component) matches everything below a folder.
    cls:     class of matching files.
    repo:    for class ide only: the repository path. For a pattern that
             ends in "/**" it is a folder ending in "/", and the part of
             the installed path below the pattern's folder is appended.
    note:    why (usually the package.txt line).
    """

    def __init__(self, pattern, cls, repo=None, note=''):
        self.pattern = pattern
        self.cls = cls
        self.repo = repo
        self.note = note
        self.is_tree = pattern.endswith('/**')
        self.prefix = pattern[:-2] if self.is_tree else pattern
        if cls == IDE:
            assert repo is not None, pattern
            assert self.is_tree == repo.endswith('/'), pattern
        rx = []
        i = 0
        while i < len(pattern):
            if pattern.startswith('**', i):
                rx.append('.*')
                i += 2
            elif pattern[i] == '*':
                rx.append('[^/]*')
                i += 1
            elif pattern[i] == '?':
                rx.append('[^/]')
                i += 1
            else:
                rx.append(re.escape(pattern[i]))
                i += 1
        self.regex = re.compile(''.join(rx) + r'\Z')

    def matches(self, path):
        return self.regex.match(path) is not None

    def repo_path(self, path):
        if self.cls != IDE:
            return None
        if self.is_tree:
            return self.repo + path[len(self.prefix):]
        return self.repo


def _rules():
    R = []
    add = R.append

    # --- Root of the install -------------------------------------------
    for name, note in ROOT_IDE_FILES:
        add(Rule(name, IDE, 'ide/' + name, note))
    add(Rule('edition.txt', BUILD, None,
             'package.txt Toolset: emit variable TargetEdition to edition.txt'))
    add(Rule('*.exe', BUILD, None,
             'package.txt Engine.Windows: LiveCode-<Edition>.exe as '
             '<ProductName>.exe (.setup.exe: Uninstaller component)'))
    add(Rule('revpdfprinter.dll', BUILD, None, 'package.txt Engine.Windows'))
    add(Rule('revsecurity.dll', BUILD, None, 'package.txt Engine.Windows'))
    add(Rule('*.lnk', JUNK, None,
             'Windows shortcut left by an install script (installers create '
             'their own); OXT Lite 1.15\'s points at the 1.07 name '
             '"OpenXTalk Lite.exe" and embeds a machine name and user SID'))
    add(Rule('test.db', JUNK, None,
             'zero-byte SQLite file from testing (dated 2026-02-10)'))
    for pattern in ('**/.*', '**/.*/**'):
        add(Rule(pattern, JUNK, None,
                 'name starting with "." below the root: upstream packaging '
                 'never installs these (package_compiler skips them), e.g. '
                 '.DS_Store'))

    # --- Toolset ---------------------------------------------------------
    for name in IDE_SUPPORT_FILES:
        add(Rule('Toolset/libraries/' + name, IDE, 'ide-support/' + name,
                 'package.txt Toolset: ide-support:' + name))
    add(Rule('Toolset/palettes/dictionary/api.sqlite', JUNK, None,
             'zero-byte file that no script refers to; the dictionary '
             'database is Documentation/html_viewer/resources/data/api/'
             'api.sqlite'))
    add(Rule('Toolset/**', IDE, 'ide/Toolset/', 'package.txt Toolset: rfolder ide:Toolset'))

    # --- Not redistributed (before the folder rules that would take them) --
    for path, note in NOT_REDISTRIBUTABLE:
        add(Rule(path, EXCLUDED, None, note))

    # --- Plugins, Resources, Documentation -------------------------------
    add(Rule('Plugins/**', IDE, 'ide/Plugins/', 'package.txt Plugins: ide:Plugins'))
    add(Rule('Resources/Sample Icons/**', UNKNOWN, None,
             'package.txt installs Sample Icons only under '
             'Runtime/Windows/<arch>/Support/Sample Icons'))
    add(Rule('Resources/Mobile Examples/**', IDE, 'ide/Resources/Mobile Examples/',
             'package.txt Mobile.MacOSX: macOS installs only'))
    add(Rule('Resources/**', IDE, 'ide/Resources/', 'package.txt Resources: ide:Resources'))
    # package.txt Documentation also copies the repository's own guides
    # (rfolder "repo:docs/guides") into Documentation/guides, next to the
    # IDE's (rfolder "ide:Documentation/guides"); no name is in both
    for rel in REPO_GUIDES:
        add(Rule('Documentation/guides/' + rel, IDE, 'docs/guides/' + rel,
                 'package.txt Documentation: rfolder repo:docs/guides'))
    add(Rule('Documentation/**', IDE, 'ide/Documentation/',
             'package.txt Documentation: ide:Documentation'))

    # --- Extensions ------------------------------------------------------
    add(Rule('Extensions/com.livecode.library.timezone/code/x86_64-win32/**', BUILD, None,
             'package.txt TimeZone: win-x86_64 packaged_extensions'))
    add(Rule('Extensions/com.livecode.library.timezone/code/**', EXTERNAL, None,
             'package.txt TimeZone: native code built for other platforms'))
    add(Rule('Extensions/com.livecode.library.timezone/resources/**', EXTERNAL, None,
             'package.txt TimeZone: zoneinfo compiled by zic from '
             'extensions/libraries/timezone/tz; only macOS and Linux builds '
             'make it (tz.gyp target tzdata), not the Windows build'))
    for ext in REPO_BUILT_EXTENSIONS:
        add(Rule('Extensions/%s/**' % ext, BUILD, None,
                 'package.txt Extensions: packaged_extensions built from extensions/'))
    add(Rule('Extensions/' + XTALK_STAMP, XTALK, None,
             'list of the bundled xTalk Suite extensions (tools/oxt/xtalk_extensions.py)'))
    for folder, member, repository, commit in XTALK_EXTENSIONS:
        add(Rule('Extensions/%s/**' % folder, XTALK, None,
                 'xTalk Suite extension from %s at %s, built by tools/oxt/xtalk_extensions.py'
                 % (repository, commit[:12] or '(not pinned)')))
    add(Rule('Extensions/**', IDE, 'ide/Extensions/',
             'extension shipped with the IDE but not built from extensions/'))

    # --- Runtime ---------------------------------------------------------
    for arch in ('x86-64', 'x86-32'):
        add(Rule('Runtime/Windows/%s/Support/Sample Icons/**' % arch, IDE,
                 'ide/Resources/Sample Icons/',
                 'package.txt Runtime.Windows: file ide:Resources/Sample Icons/*'))
    add(Rule('Runtime/Windows/x86-64/**', BUILD, None,
             'package.txt Runtime.Windows x86-64: standalone engine build'))
    add(Rule('Runtime/**', EXTERNAL, None,
             'standalone engines for other platforms or architectures '
             '(not built by this repository\'s Windows x86-64 build)'))

    # --- Engine outputs and third-party collections ----------------------
    add(Rule('Externals/**', BUILD, None,
             'package.txt Externals, Databases, Externals.CEF.Windows, Mobile.Windows'))
    add(Rule('Toolchain/**', BUILD, None, 'package.txt Toolchain.Windows'))
    add(Rule('Ext/**', EXTERNAL, None,
             'package.txt Ext: mergExt collection downloaded by the builder'))
    return R


RULES = _rules()

# Repository paths (folders end in "/") inside the managed set that are
# never deleted by import nor written by assemble for a Windows layout.
MANAGED_EXCLUDE = (
    ('ide/Resources/Mobile Examples/',
     'package.txt Mobile.MacOSX: installed on macOS only'),
    # In LiveCode's ide/ but not in its packages (builder/package.txt does
    # not list them; the stock 9.6.3 install has none of them): the IDE's
    # own test interface and the documentation sources
    ('ide/Plugins/livecodeTestInterface.livecode',
     'LiveCode does not package it (package.txt Plugins)'),
    ('ide/Documentation/Docs Helper.livecode',
     'LiveCode does not package it (package.txt Documentation)'),
    ('ide/Documentation/dictionary/',
     'LiveCode does not package it (package.txt Documentation)'),
    ('ide/Documentation/specs/',
     'LiveCode does not package it (package.txt Documentation)'),
)

# Differences that are expected when a stock LiveCode install is verified
# against the stock ide/ tree (verify --upstream). Paths are installed
# paths. "install": present in the install only; "repo": assembled from the
# repository but not packaged by upstream.
UPSTREAM_DIFFERENCES = (
    ('install', 'Release Notes.pdf',
     'generated release notes (package.txt Misc: repo:LiveCodeNotes-<version>.pdf)'),
    ('install', 'Documentation/guides/**',
     'package.txt also copies repo:docs/guides and docs:guides (docs '
     'builder output) into Documentation/guides'),
    ('install', 'Documentation/html_viewer/resources/data/**',
     'docs builder output (api.sqlite, *.js); ignored by ide/.gitignore upstream'),
    ('install', 'Documentation/pdf/**',
     'generated user guide (repo:LiveCodeUserGuide-<version>.pdf)'),
    ('repo', 'Plugins/livecodeTestInterface.livecode',
     'in ide/Plugins but not listed in package.txt Plugins'),
    ('repo', 'Documentation/Docs Helper.livecode',
     'in ide/Documentation but not listed in package.txt Documentation'),
    ('repo', 'Documentation/dictionary/**',
     'in ide/Documentation but not listed in package.txt Documentation'),
    ('repo', 'Documentation/specs/**',
     'in ide/Documentation but not listed in package.txt Documentation'),
)

# Files compared as text (line endings normalised). This matches how git
# stores them (ide/.gitattributes and the root .gitattributes, plus
# auto-detection): as text with LF line endings.
TEXT_EXTENSIONS = frozenset((
    '.buildnumber', '.css', '.csv', '.dat', '.htm', '.html', '.js', '.json',
    '.lc', '.lcb', '.lcdoc', '.lci', '.livecodescript', '.map', '.md',
    '.mlc', '.snippet', '.svg', '.template', '.tsv', '.txt', '.version',
    '.xml', '.yaml', '.yml',
))


# ---------------------------------------------------------------------------
# Rule evaluation

def classify_path(path):
    """Return (class, repo path or None, rule) for an installed path."""
    for rule in RULES:
        if rule.matches(path):
            return rule.cls, rule.repo_path(path), rule
    return UNKNOWN, None, None


def _managed_roots():
    """Repository-side folders (ending in "/") and files of the ide rules."""
    trees, files = set(), set()
    for rule in RULES:
        if rule.cls != IDE:
            continue
        (trees if rule.is_tree else files).add(rule.repo)
    return trees, files


def is_managed(repo_path, include_excluded=False):
    """True if the repository path belongs to the managed set.

    With include_excluded, paths under MANAGED_EXCLUDE count as managed
    (import compares them when an install contains them, but never
    deletes them).
    """
    if not include_excluded:
        for prefix, _ in MANAGED_EXCLUDE:
            if repo_path.startswith(prefix):
                return False
    trees, files = _managed_roots()
    if repo_path in files:
        return True
    for tree in trees:
        if repo_path.startswith(tree):
            rest = repo_path[len(tree):]
            return not any(part.startswith('.') for part in rest.split('/'))
    return False


def install_paths_for(repo_path):
    """Installed paths a repository file is assembled to (empty if none).

    Only the rules whose repository side is the most specific match are
    used; several results are mirrors (the same file placed twice, as
    package.txt does for Sample Icons). Each result is checked to classify
    back to the same repository path.
    """
    best, found = -1, []
    for rule in RULES:
        if rule.cls != IDE:
            continue
        if rule.is_tree:
            if not repo_path.startswith(rule.repo):
                continue
            length = len(rule.repo)
            candidate = rule.prefix + repo_path[len(rule.repo):]
        else:
            if repo_path != rule.repo:
                continue
            length = len(rule.repo) + 1
            candidate = rule.pattern
        if length > best:
            best, found = length, [candidate]
        elif length == best:
            found.append(candidate)
    result = []
    for candidate in found:
        cls, back, _ = classify_path(candidate)
        if cls == IDE and back == repo_path:
            result.append(candidate)
    return result


def is_text(path):
    name = path.rsplit('/', 1)[-1]
    ext = os.path.splitext(name)[1].lower() or name.lower()
    return ext in TEXT_EXTENSIONS


def _normalise(data):
    return re.sub(b'\r+\n', b'\n', data)


def same_content(path, a, b):
    """Compare two files; path (any side) decides text or binary."""
    if os.path.getsize(a) == os.path.getsize(b):
        with open(a, 'rb') as fa, open(b, 'rb') as fb:
            if fa.read() == fb.read():
                return True
        if not is_text(path):
            return False
    elif not is_text(path):
        return False
    with open(a, 'rb') as fa, open(b, 'rb') as fb:
        return _normalise(fa.read()) == _normalise(fb.read())


def _glob_match(pattern, path):
    return Rule(pattern, JUNK).matches(path)


# ---------------------------------------------------------------------------
# File system helpers

def walk_files(root):
    """Relative paths ("/" separators) of all files below root, sorted."""
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        rel = os.path.relpath(dirpath, root)
        rel = '' if rel == '.' else rel.replace(os.sep, '/') + '/'
        for name in filenames:
            out.append(rel + name)
    out.sort()
    return out


def empty_dirs(root):
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        if not dirnames and not filenames:
            rel = os.path.relpath(dirpath, root).replace(os.sep, '/')
            if rel != '.':
                out.append(rel)
    return sorted(out)


def native(root, rel):
    return os.path.join(root, *rel.split('/'))


def managed_repo_files(repo_root, include_excluded=False):
    """Managed files that exist in the repository working tree."""
    trees, files = _managed_roots()
    found = set()
    for rel in files:
        if os.path.isfile(native(repo_root, rel)) and is_managed(rel, include_excluded):
            found.add(_actual_case(repo_root, rel))
    for tree in trees:
        base = native(repo_root, tree.rstrip('/'))
        if not os.path.isdir(base):
            continue
        for rel in walk_files(base):
            path = tree + rel
            if is_managed(path, include_excluded):
                found.add(path)
    return sorted(found)


def _actual_case(root, rel):
    """rel with the letter case used on disk (case-insensitive systems)."""
    parts = rel.split('/')
    current = root
    out = []
    for part in parts:
        try:
            names = os.listdir(current)
        except OSError:
            return rel
        match = part if part in names else next(
            (n for n in names if n.lower() == part.lower()), part)
        out.append(match)
        current = os.path.join(current, match)
    return '/'.join(out)


def area_of(repo_path):
    parts = repo_path.split('/')
    if parts[0] == 'ide-support':
        return 'ide-support'
    if len(parts) == 2:
        return 'ide (root files)'
    return '/'.join(parts[:2])


def group_of(path):
    """Grouping key for summaries of installed paths."""
    parts = path.split('/')
    if len(parts) == 1:
        return '(root)'
    if parts[0] == 'Runtime':
        return '/'.join(parts[:min(3, len(parts) - 1)])
    if parts[0] == 'Extensions' and len(parts) > 2:
        if parts[1] == 'com.livecode.library.timezone' and parts[2] == 'code' and len(parts) > 4:
            return '/'.join(parts[:4])
        return '/'.join(parts[:2])
    return parts[0]


# ---------------------------------------------------------------------------
# classify

def classify_tree(install_root, paths=None):
    """List of (class, path, repo path, size, note) for an install."""
    if paths is None:
        paths = walk_files(install_root)
    rows = []
    for path in paths:
        cls, repo, rule = classify_path(path)
        size = ''
        if install_root is not None:
            size = os.path.getsize(native(install_root, path))
        rows.append((cls, path, repo or '', size, rule.note if rule else 'no rule matches'))
    return rows


def cmd_classify(args):
    if args.from_list:
        with open(args.from_list, encoding='utf-8') as f:
            paths = sorted({line.rstrip('\r\n').split('\t')[0] for line in f if line.strip()})
        rows = classify_tree(None, paths)
    else:
        _require_dir(args.installed_root)
        rows = classify_tree(args.installed_root)
    out = open(args.out, 'w', encoding='utf-8', newline='\n') if args.out else sys.stdout
    try:
        out.write('class\tinstalled_path\trepo_path\tsize\treason\n')
        for row in rows:
            out.write('\t'.join(str(x) for x in row) + '\n')
    finally:
        if args.out:
            out.close()
    _print_class_summary(rows, sys.stderr if not args.out else sys.stdout)
    return 1 if any(r[0] == UNKNOWN for r in rows) else 0


def _print_class_summary(rows, stream):
    by_class = collections.Counter()
    size_class = collections.Counter()
    groups = collections.defaultdict(lambda: [0, 0])
    for cls, path, repo, size, note in rows:
        by_class[cls] += 1
        size_class[cls] += size or 0
        g = groups[(cls, group_of(path))]
        g[0] += 1
        g[1] += size or 0
    stream.write('\nSummary: %d files\n' % len(rows))
    for cls in CLASS_ORDER:
        if by_class[cls]:
            stream.write('  %-9s %6d files %14s bytes\n' % (cls, by_class[cls], '{:,}'.format(size_class[cls])))
    stream.write('\nBy class and folder:\n')
    for (cls, group) in sorted(groups, key=lambda k: (CLASS_ORDER.index(k[0]), k[1])):
        n, s = groups[(cls, group)]
        stream.write('  %-9s %6d %14s  %s\n' % (cls, n, '{:,}'.format(s), group))
    unknown = [r[1] for r in rows if r[0] == UNKNOWN]
    if unknown:
        stream.write('\nUnclassified paths (add a rule):\n')
        for p in unknown:
            stream.write('  ' + p + '\n')


# ---------------------------------------------------------------------------
# import

def plan_import(install_root, repo_root):
    """Return (actions, problems).

    actions: list of (action, repo path, installed path, old repo path);
    action is add, modify, same, delete or rename-case (the letter case of
    the path changes; old repo path is the name on disk).
    """
    rows = classify_tree(install_root)
    problems = []
    unknown = [r[1] for r in rows if r[0] == UNKNOWN]
    if unknown:
        problems.append('unclassified installed paths (add rules first):\n  ' + '\n  '.join(unknown))
    wanted = collections.OrderedDict()
    for cls, path, repo, size, note in rows:
        if cls != IDE:
            continue
        wanted.setdefault(repo, []).append(path)
    for repo, sources in wanted.items():
        for other in sources[1:]:
            if not same_content(repo, native(install_root, sources[0]), native(install_root, other)):
                problems.append('%s: mirrored installed files differ: %s' % (repo, ', '.join(sources)))
    existing = managed_repo_files(repo_root)
    existing_lower = {p.lower(): p for p in managed_repo_files(repo_root, True)}
    actions = []
    for repo, sources in wanted.items():
        src = sources[0]
        on_disk = existing_lower.get(repo.lower())
        if on_disk is None:
            if os.path.exists(native(repo_root, repo)):
                problems.append('%s exists but is not a managed file' % repo)
            actions.append(('add', repo, src, ''))
        elif on_disk != repo:
            actions.append(('rename-case', repo, src, on_disk))
        elif same_content(repo, native(install_root, src), native(repo_root, repo)):
            actions.append(('same', repo, src, ''))
        else:
            actions.append(('modify', repo, src, ''))
    wanted_lower = {p.lower() for p in wanted}
    for repo in existing:
        if repo.lower() not in wanted_lower:
            actions.append(('delete', repo, '', ''))
    return actions, problems


def cmd_import(args):
    _require_dir(args.installed_root)
    _require_repo(args.repo_root)
    actions, problems = plan_import(args.installed_root, args.repo_root)
    if problems:
        for p in problems:
            sys.stderr.write('error: ' + p + '\n')
        return 2
    repo_root = args.repo_root
    if not args.dry_run:
        # Remove first, so that a folder whose name changes case is
        # recreated with the new case.
        for action, repo, src, old in actions:
            if action in ('delete', 'rename-case'):
                gone = old or repo
                os.remove(native(repo_root, gone))
                _prune_empty_parents(repo_root, gone)
        for action, repo, src, old in actions:
            if action in ('add', 'modify', 'rename-case'):
                dst = native(repo_root, repo)
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copyfile(native(args.installed_root, src), dst)
    if args.log:
        with open(args.log, 'w', encoding='utf-8', newline='\n') as f:
            f.write('action\trepo_path\tinstalled_path\told_repo_path\n')
            for row in actions:
                f.write('\t'.join(row) + '\n')
    _print_import_summary(actions, args.dry_run)
    renames = [(old, repo) for action, repo, src, old in actions if action == 'rename-case']
    if renames:
        print('\nnote: %d paths change only in letter case. On a case-insensitive file\n'
              'system git does not see this by itself; record each rename with\n'
              '  git rm --cached -q -- <old>  and  git add -- <new>' % len(renames))
        for old, new in renames:
            print('  %s -> %s' % (old, new))
    return 0


def _prune_empty_parents(repo_root, repo_path):
    trees, _ = _managed_roots()
    parts = repo_path.split('/')[:-1]
    while parts:
        folder = '/'.join(parts)
        if (folder + '/') in trees or len(parts) < 2:
            break
        path = native(repo_root, folder)
        if os.path.isdir(path) and not os.listdir(path):
            os.rmdir(path)
            parts.pop()
        else:
            break


def _print_import_summary(actions, dry_run):
    counts = collections.defaultdict(collections.Counter)
    for action, repo, src, old in actions:
        counts[area_of(repo)][action] += 1
    kinds = ('add', 'modify', 'delete', 'rename-case', 'same')
    print('%simport summary (repository paths):' % ('dry-run ' if dry_run else ''))
    print('  %-22s %7s %7s %7s %7s %7s' % (('area',) + ('added', 'modified', 'deleted', 'case', 'same')))
    total = collections.Counter()
    for area in sorted(counts):
        c = counts[area]
        total.update(c)
        print('  %-22s %7d %7d %7d %7d %7d' % ((area,) + tuple(c[k] for k in kinds)))
    print('  %-22s %7d %7d %7d %7d %7d' % (('total',) + tuple(total[k] for k in kinds)))


# ---------------------------------------------------------------------------
# assemble

def plan_assemble(repo_root, include=()):
    """Return (list of (installed path, repo path), problems).

    include lists MANAGED_EXCLUDE prefixes to assemble all the same: a
    Windows layout has none of them, but package.py stages the macOS
    layout with ide/Resources/Mobile Examples/ (package.txt
    Mobile.MacOSX)."""
    pairs, problems = [], []
    for prefix in include:
        if prefix not in [p for p, _ in MANAGED_EXCLUDE]:
            raise ValueError('%s is not in MANAGED_EXCLUDE' % prefix)
    repo_files = managed_repo_files(repo_root)
    if include:
        repo_files = sorted(set(repo_files) | {
            p for p in managed_repo_files(repo_root, include_excluded=True)
            if any(p.startswith(prefix) for prefix in include)})
    for repo in repo_files:
        targets = install_paths_for(repo)
        if not targets:
            problems.append('%s has no installed location (shadowed by a more specific rule?)' % repo)
        for t in targets:
            pairs.append((t, repo))
    seen = {}
    for t, repo in pairs:
        if t in seen and seen[t] != repo:
            problems.append('%s is assembled from both %s and %s' % (t, seen[t], repo))
        seen[t] = repo
    return sorted(pairs), problems


def assemble(repo_root, out_dir, eol='keep'):
    pairs, problems = plan_assemble(repo_root)
    if problems:
        return pairs, problems
    for target, repo in pairs:
        dst = native(out_dir, target)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        src = native(repo_root, repo)
        if eol != 'keep' and is_text(repo):
            with open(src, 'rb') as f:
                data = _normalise(f.read())
            if eol == 'crlf':
                data = data.replace(b'\n', b'\r\n')
            with open(dst, 'wb') as f:
                f.write(data)
        else:
            shutil.copyfile(src, dst)
    return pairs, problems


def cmd_assemble(args):
    _require_repo(args.repo_root)
    if os.path.exists(args.out_dir) and os.listdir(args.out_dir) and not args.force:
        sys.stderr.write('error: %s is not empty (use --force to write into it)\n' % args.out_dir)
        return 2
    pairs, problems = assemble(args.repo_root, args.out_dir, args.eol)
    if problems:
        for p in problems:
            sys.stderr.write('error: ' + p + '\n')
        return 2
    counts = collections.Counter(group_of(t) for t, _ in pairs)
    print('assembled %d files into %s' % (len(pairs), args.out_dir))
    for g in sorted(counts):
        print('  %6d  %s' % (counts[g], g))
    return 0


# ---------------------------------------------------------------------------
# verify

def cmd_verify(args):
    _require_dir(args.installed_root)
    _require_repo(args.repo_root)
    rows = classify_tree(args.installed_root)
    unknown = [r[1] for r in rows if r[0] == UNKNOWN]
    expected = {r[1] for r in rows if r[0] == IDE}
    temp = tempfile.mkdtemp(prefix='oxt-verify-')
    try:
        pairs, problems = assemble(args.repo_root, temp)
        if problems:
            for p in problems:
                sys.stderr.write('error: ' + p + '\n')
            return 2
        assembled = {t: repo for t, repo in pairs}
        only_install = sorted(expected - set(assembled))
        only_repo = sorted(set(assembled) - expected)
        differ, same = [], 0
        for t in sorted(expected & set(assembled)):
            if same_content(t, native(args.installed_root, t), native(temp, t)):
                same += 1
            else:
                differ.append(t)
        tolerated = []
        if args.upstream:
            only_install, t1 = _split_tolerated(only_install, 'install')
            only_repo, t2 = _split_tolerated(only_repo, 'repo')
            tolerated = t1 + t2
        install_empty = [d for d in empty_dirs(args.installed_root)
                         if classify_path(d + '/x')[0] == IDE]
    finally:
        if args.keep_temp:
            print('assembled tree kept in ' + temp)
        else:
            shutil.rmtree(temp, ignore_errors=True)

    print('installed class (a) files: %d; assembled from repository: %d' % (len(expected), len(assembled)))
    print('identical: %d (text files compared with line endings normalised)' % same)
    _report_list('different content', differ, lambda t: '%s  <- %s' % (t, assembled[t]))
    _report_list('in the install only', only_install)
    _report_list('assembled from the repository only', only_repo, lambda t: '%s  <- %s' % (t, assembled[t]))
    _report_list('unclassified installed paths', unknown)
    if tolerated:
        print('expected upstream packaging differences (--upstream): %d' % len(tolerated))
        for side, t, why in tolerated:
            print('  [%s only] %s  (%s)' % (side, t, why))
    if install_empty:
        print('note: empty folders in the install (git cannot store them): %d' % len(install_empty))
        for d in install_empty:
            print('  ' + d)
    failed = differ or only_install or only_repo or unknown
    print('VERIFY %s' % ('FAILED' if failed else 'PASSED'))
    return 1 if failed else 0


def _split_tolerated(paths, side):
    keep, tolerated = [], []
    for p in paths:
        why = next((w for s, pat, w in UPSTREAM_DIFFERENCES if s == side and _glob_match(pat, p)), None)
        if why:
            tolerated.append((side, p, why))
        else:
            keep.append(p)
    return keep, tolerated


def _report_list(title, items, fmt=None):
    if not items:
        return
    print('%s: %d' % (title, len(items)))
    for t in items:
        print('  ' + (fmt(t) if fmt else t))


# ---------------------------------------------------------------------------
# Command line

def _require_dir(path):
    if not os.path.isdir(path):
        raise SystemExit('error: %s is not a folder' % path)


def _require_repo(path):
    if not os.path.isdir(os.path.join(path, 'ide')):
        raise SystemExit('error: %s has no ide/ folder (not the repository root?)' % path)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='Map an installed OpenXTalk Lite / LiveCode Windows folder '
                    'to the repository layout and back.')
    sub = parser.add_subparsers(dest='command', required=True)

    p = sub.add_parser('classify', help='classify every file of an install')
    p.add_argument('installed_root', nargs='?')
    p.add_argument('--out', help='write the TSV here instead of standard output')
    p.add_argument('--from-list', metavar='FILE',
                   help='classify the installed paths listed in FILE (first '
                        'TSV column) instead of reading a folder')
    p.set_defaults(func=cmd_classify)

    p = sub.add_parser('import', help='mirror an install into the repository')
    p.add_argument('installed_root')
    p.add_argument('repo_root')
    p.add_argument('--dry-run', action='store_true', help='report only')
    p.add_argument('--log', metavar='FILE', help='write every action as TSV')
    p.set_defaults(func=cmd_import)

    p = sub.add_parser('assemble', help='write the IDE part of the installed layout')
    p.add_argument('repo_root')
    p.add_argument('out_dir')
    p.add_argument('--eol', choices=('keep', 'lf', 'crlf'), default='keep',
                   help='line endings for text files (default: as in the working tree)')
    p.add_argument('--force', action='store_true', help='allow a non-empty out_dir')
    p.set_defaults(func=cmd_assemble)

    p = sub.add_parser('verify', help='compare an install with the repository')
    p.add_argument('installed_root')
    p.add_argument('repo_root')
    p.add_argument('--upstream', action='store_true',
                   help='accept the documented differences of a stock LiveCode '
                        'package (UPSTREAM_DIFFERENCES)')
    p.add_argument('--keep-temp', action='store_true', help='keep the assembled tree')
    p.set_defaults(func=cmd_verify)

    args = parser.parse_args(argv)
    if args.command == 'classify' and not args.installed_root and not args.from_list:
        parser.error('classify needs installed_root or --from-list')
    return args.func(args)


if __name__ == '__main__':
    sys.exit(main())
