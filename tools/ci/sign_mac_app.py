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

"""Sign the LiveCode app (or a macOS build output) ad hoc, from the inside
out, and verify the result.

  python tools/ci/sign_mac_app.py TARGET [--dry-run] [--no-verify]

TARGET is an .app bundle (the staged app) or a folder (the
merged Release tree, whose binaries archive is published). Every piece of
macOS code in it is signed with

  codesign --force --sign - --timestamp=none <path>

deepest first, so that everything a bundle holds is signed before the
bundle is sealed:

  bundles     every .app, .bundle, .framework, .plugin, .xpc and .appex
              folder whose executable is macOS code, as a bundle: the
              runtimes' Standalone*.app, the externals and database
              drivers, revpdfprinter.bundle (codesign signs the bundle's
              executable with it and writes its resource seal);
  Mach-O      every other Mach-O file of macOS code that can carry a
              signature (executables, dylibs, loadable bundles): the
              toolchain, revsecurity.dylib, the extensions' libraries
              (the six xTalk Suite dylibs and the time zone library);
  the app     last, when TARGET is a bundle: its executable and the seal
              over everything else (Contents/Tools is sealed as its
              resources).

Nothing is signed with --deep: it would sign only what sits in the nested
code locations (Contents/MacOS, Frameworks, PlugIns), not the code under
Contents/Tools, and in an order codesign chooses. Every file has to be
signed again: the build's Mach-O files carry the ad-hoc signatures Xcode
made before tools/extract-debug-symbols.sh stripped them, so their page
hashes are stale (Apple Silicon kills a process at the first page that
does not match), and a lipo-merged file keeps each slice's old signature.
No --preserve-metadata: all the build's signatures hold is the ad-hoc
flag, identifiers that Xcode made up (an app's comes from its Info.plist
again), and, on executables, the get-task-allow debugging entitlement of
Xcode's "Sign to Run Locally", which a release must not carry (notarization
rejects it).

Skipped and listed: code for other platforms (the time zone library's iOS
and simulator files from the runtimes asset, which the IDE only copies
into iOS standalones, whose builder signs them), object files (tz.lcext,
which cannot carry a signature) and .dSYM bundles (debug information).
Extended attributes are removed first (xattr -cr): codesign refuses
Finder information and resource forks ("detritus"), and quarantine or
provenance attributes do not belong in a package.

Then, unless --no-verify: every signed bundle and file must pass
"codesign --verify --strict", and an .app TARGET also
"codesign --verify --deep --strict --verbose=2" (--deep reaches only the
nested code locations; the runtimes' Standalone*.app bundles under
Contents/Tools are checked one by one above).

--dry-run prints the order without signing (and works on any host).
Exit status 0 success, 1 a codesign or verify failure (its output is
printed), 2 usage errors.

Only the Python 3 standard library is used (3.8 or later).
"""

import argparse
import os
import plistlib
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), 'oxt'))
sys.path.insert(0, HERE)
import binfmt  # noqa: E402
import merge_universal  # noqa: E402

BUNDLE_SUFFIXES = ('.app', '.bundle', '.framework', '.plugin', '.xpc', '.appex')
# Mach-O file types that carry a signature: MH_EXECUTE, MH_DYLIB, MH_BUNDLE
SIGNABLE = {2: 'executable', 6: 'dylib', 8: 'bundle'}
SIGN = ['codesign', '--force', '--sign', '-', '--timestamp=none']


class SignError(Exception):
    pass


def log(msg=''):
    print(msg)
    sys.stdout.flush()


def gha():
    return os.environ.get('GITHUB_ACTIONS') == 'true'


def bundle_executable(path):
    """The executable of a bundle folder (from its Info.plist), or None."""
    for info, exe_dir in (('Contents/Info.plist', 'Contents/MacOS'),        # macOS app, bundle
                          ('Resources/Info.plist', ''),                       # framework (Versions/Current)
                          ('Versions/Current/Resources/Info.plist', 'Versions/Current'),
                          ('Info.plist', '')):                               # iOS style, flat
        p = os.path.join(path, *info.split('/'))
        if os.path.isfile(p):
            try:
                with open(p, 'rb') as f:
                    name = plistlib.load(f).get('CFBundleExecutable')
            except (OSError, ValueError, plistlib.InvalidFileException):
                return None
            if not name:
                return None
            return os.path.join(path, *(exe_dir.split('/') if exe_dir else []), name)
    return None


def macho_kind(path):
    """(file type, macOS code or not) of a Mach-O file, or None."""
    info = merge_universal.macho_info(path)
    if info is None:
        return None
    data = binfmt.read_file(path)
    b = binfmt.parse_macho(data)
    return b.filetype, info[1]


def plan(target):
    """(steps, skipped): steps are (kind, path) in signing order, deepest
    first; skipped are (path, why)."""
    bundles, files, skipped = [], [], []
    main_exes = set()
    for dirpath, dirnames, filenames in os.walk(target):
        # package.txt component Ext: the mergExt bundle, signed by its
        # maker and installed byte for byte as LiveCode's installers have it
        # (Installer/legacyoxt/ext)
        parts = os.path.normpath(dirpath).split(os.sep)
        if parts[-2:] == ['Tools', 'Ext']:
            skipped.append((dirpath, 'LiveCode\'s mergExt bundle (package.txt Ext), kept as it is'))
            dirnames[:] = []
            continue
        # .dSYM bundles hold debug information, not code
        for d in [d for d in dirnames if d.endswith('.dSYM')]:
            skipped.append((os.path.join(dirpath, d), 'debug symbols (.dSYM), not code'))
        dirnames[:] = sorted(d for d in dirnames
                             if not d.endswith('.dSYM') and not os.path.islink(os.path.join(dirpath, d)))
        for d in dirnames:
            if d.endswith(BUNDLE_SUFFIXES):
                path = os.path.join(dirpath, d)
                exe = bundle_executable(path)
                # a framework's executable is a link into Versions/Current
                exe = os.path.realpath(exe) if exe else None
                kind = macho_kind(exe) if exe and os.path.isfile(exe) else None
                if kind is None:
                    skipped.append((path, 'a bundle without a Mach-O executable'))
                elif not kind[1]:
                    skipped.append((path, 'a bundle of code for another platform (iOS)'))
                else:
                    bundles.append(path)
                    main_exes.add(exe)
        for name in sorted(filenames):
            path = os.path.join(dirpath, name)
            if os.path.islink(path):
                continue
            kind = macho_kind(path)
            if kind is None:
                continue
            if not kind[1]:
                skipped.append((path, 'code for another platform (iOS or a simulator)'))
            elif kind[0] not in SIGNABLE:
                skipped.append((path, 'Mach-O file type %d (not an executable, dylib or bundle: an object file '
                                      'cannot carry a signature)' % kind[0]))
            else:
                files.append(path)
    target_is_bundle = os.path.basename(os.path.normpath(target)).endswith(BUNDLE_SUFFIXES)
    top = os.path.normpath(target)
    top_exe = bundle_executable(target) if target_is_bundle else None
    if top_exe:
        # signed with the app, at the end
        main_exes.add(os.path.realpath(top_exe))
    steps = [('bundle', b) for b in bundles if os.path.normpath(b) != top]
    steps += [('file', f) for f in files if os.path.realpath(f) not in main_exes]
    # deepest first; a bundle's own folder has fewer components than
    # anything inside it, so its contents come before it
    steps.sort(key=lambda s: (-s[1].count(os.sep), s[1]))
    if target_is_bundle:
        exe = top_exe
        if not exe or not os.path.isfile(exe):
            raise SignError('%s is a bundle without an executable (Contents/Info.plist CFBundleExecutable)' % target)
        steps.append(('app' if target.endswith('.app') else 'bundle', top))
    return steps, skipped


def run(cmd):
    r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    return r.returncode, r.stdout.decode('utf-8', 'replace').strip()


def main(argv=None):
    ap = argparse.ArgumentParser(description='Sign the LiveCode app or a macOS build output ad hoc, from the inside '
                                             'out, and verify it.')
    ap.add_argument('target', help='the .app bundle, or a build output folder')
    ap.add_argument('--dry-run', action='store_true', help='print the signing order only')
    ap.add_argument('--no-verify', action='store_true', help='do not run codesign --verify afterwards')
    args = ap.parse_args(argv)
    target = os.path.abspath(args.target)
    if not os.path.isdir(target):
        ap.error('%s is not a folder' % args.target)
    if not args.dry_run and (sys.platform != 'darwin' or not shutil.which('codesign')):
        ap.error('codesign runs on macOS only; use --dry-run to see the order elsewhere')
    try:
        steps, skipped = plan(target)
    except (SignError, merge_universal.MergeError, binfmt.FormatError, OSError) as e:
        log('::error title=Signing::%s' % e if gha() else 'error: %s' % e)
        return 1

    rel = lambda p: os.path.relpath(p, os.path.dirname(target))   # noqa: E731
    counts = {}
    for kind, _ in steps:
        counts[kind] = counts.get(kind, 0) + 1
    log('Target : %s' % target)
    log('Signing: ad hoc (codesign --force --sign - --timestamp=none), deepest first: %d bundles, %d Mach-O '
        'files%s' % (counts.get('bundle', 0), counts.get('file', 0), ', then the app' if counts.get('app') else ''))
    if skipped:
        log('Not signed (%d):' % len(skipped))
        for path, why in skipped:
            log('  %s: %s' % (rel(path), why))
    if args.dry_run:
        log('')
        for i, (kind, path) in enumerate(steps, 1):
            log('%4d  %-6s %s' % (i, kind, rel(path)))
        return 0

    t0 = time.time()
    code, out = run(['xattr', '-cr', target])
    if code:
        log('::error title=Signing::xattr -cr %s failed: %s' % (target, out))
        return 1
    for i, (kind, path) in enumerate(steps, 1):
        code, out = run(SIGN + [path])
        if code:
            log(out)
            msg = 'codesign failed (exit status %d) on %s (step %d of %d, %s)' % (code, rel(path), i, len(steps), kind)
            log('::error title=Signing::%s' % msg if gha() else 'error: %s' % msg)
            return 1
    log('Signed %d items in %.0f s.' % (len(steps), time.time() - t0))
    if args.no_verify:
        return 0

    failures = []
    for kind, path in steps:
        code, out = run(['codesign', '--verify', '--strict', path])
        if code:
            failures.append((rel(path), out))
    if target.endswith('.app'):
        code, out = run(['codesign', '--verify', '--deep', '--strict', '--verbose=2', target])
        log(out)
        if code:
            failures.append(('%s (--deep)' % rel(target), out))
    for path, out in failures:
        log('%s:\n%s' % (path, out))
        msg = 'codesign --verify --strict failed on %s' % path
        log('::error title=Signing::%s' % msg if gha() else 'error: %s' % msg)
    if failures:
        return 1
    log('Verified: every signed item passes codesign --verify --strict%s.'
        % (', and the app --deep' if target.endswith('.app') else ''))
    return 0


if __name__ == '__main__':
    sys.exit(main())
