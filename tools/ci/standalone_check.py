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

"""Check the standalone runtimes of an installed OpenXTalk-Lite, and build
standalones from them and run them.

  python tools/ci/standalone_check.py (--install DIR | --package FILE)
      [--platform P] [--targets own|all] [--log FILE] [--timeout SECONDS]
  python tools/ci/standalone_check.py --engine FILE --runtime PATH
      [--platform P] [--log FILE] [--timeout SECONDS]

--install is an installed layout, as for tools/ci/run_livecode_check.py
(the folder that holds OpenXTalk-Lite.app on macOS, the program folder on
Windows and Linux), tested from a neutral path; --package extracts a
package (the portable zip, the Linux tar.xz) to a temporary folder first.
--platform names the layout (default: this machine's). --engine and
--runtime name a development engine and a standalone engine (or, on macOS,
a Standalone*.app) directly, for a build that is not packaged (the 32-bit
Windows and Linux builds) or to try the deploy step elsewhere (for example
with a Linux build in WSL); --platform then names the runtime's platform
(win-x86, linux-x86, ...).

The targets are the runtimes that the IDE's standalone builder deploys
(revsblibrary revSBEnginePath), by the tools/oxt/package.py platform whose
tables describe their folders (RUNTIMES):

  mac-universal   Runtime/Mac OS X/x64-ARM64/Standalone-blank.app
  win-x86_64      Runtime/Windows/x86-64/Standalone
  win-x86         Runtime/Windows/x86-32/Standalone
  linux-x86_64    Runtime/Linux/x86-64/Standalone
  linux-x86       Runtime/Linux/x86-32/Standalone

--targets own (the default) takes the layout's own platform's runtime;
--targets all every one of them that the layout has (a package's runtimes
for the other platforms come from the runtimes asset, see
tools/oxt/make_runtimes_asset.py). Android standalones are built with the
Android SDK and are not checked here.

1. The runtime folders.

   macOS (the layout's own, tools/oxt/package.py MAC_RUNTIMES): each
   folder holds its Standalone*.app with Contents/MacOS/Standalone-
   Community (the name revSBEnginePath builds from the edition) holding
   every architecture of the layout, an Info.plist whose
   LSMinimumSystemVersion is the lowest minimum macOS of that engine (the
   builder copies it into every standalone), the icons the builder copies
   into every Mac standalone (x86-32: Standalone.icns and
   StandaloneDoc.icns), its Support files, and Externals whose
   Externals.txt and Database Drivers.txt name only bundles that are
   there; each Standalone*.app passes "codesign --verify --strict" when
   codesign exists.

   Windows and Linux (package.py's tables of that platform): the
   Standalone engine, its Support files (revpdfprinter, revsecurity), every
   external and database driver that Externals.txt and Database
   Drivers.txt name and, when the browser external is listed, the CEF
   library are there, and each is a PE (Windows) or ELF (Linux) file built
   for the runtime's architecture.

2. A standalone from each target's runtime: the layout's development
   engine runs tools/ci/standalone-deploy.livecodescript, which saves a
   one-handler stack and attaches it to a copy of the runtime with
   "_internal deploy" (macosx, windows or linux), the command
   revStandaloneDeployWithParams uses. Any engine deploys for every
   platform, so a Linux layout also builds its Windows standalones. The
   Mac deployer accepts only the load commands it knows how to move (no
   LC_RPATH, no chained fixups), so this is where a runtime that no
   standalone can be made from fails. The standalone must be a file of
   the runtime's format and architecture (Mach-O: every slice of the
   runtime). On macOS the copy is then signed as the IDE signs it
   (codesign --deep --force --sign -, performAdHocCodesign) and must pass
   "codesign --verify --deep --strict". It runs when this machine can run
   it (the same operating system; on Windows both architectures, on Linux
   this machine's), with -ui, and must report the engine's version, the
   processor it was built for (for Mach-O, the slice that ran), its
   platform (MacOS, Win32 or Linux) and the environment "command line"
   (what a standalone reports without a user interface; the development
   engine says "development command line").

What this does not do is run the IDE's builder itself
(revSaveAsStandalone), which also copies the externals and database
drivers the stack needs from the runtime's Externals folder and warns when
one is missing; that needs the IDE's libraries loaded in a headless
engine, and is left for later (see tools/oxt/README.md).

Exit status 0 when everything passes, 1 otherwise. Under GitHub Actions
it writes annotations and a job summary. Only the Python 3 standard
library is used (3.8 or later).
"""

import argparse
import collections
import os
import platform as host
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), 'oxt'))
sys.path.insert(0, HERE)
import binfmt  # noqa: E402
import check_native_deps  # noqa: E402
import package  # noqa: E402
import run_livecode_check as rlc  # noqa: E402

DEPLOY_SCRIPT = os.path.join(HERE, 'standalone-deploy.livecodescript')
# The runtime the IDE deploys for each target, in the tools folder (the Mac
# target's "IntelArmUniversal" button selects MacOSX x64-ARM64), by the
# package.py platform whose tables describe its folder
RUNTIMES = collections.OrderedDict([
    ('mac-universal', 'Runtime/Mac OS X/x64-ARM64/Standalone-blank.app'),
    ('win-x86_64', 'Runtime/Windows/x86-64/Standalone'),
    ('win-x86', 'Runtime/Windows/x86-32/Standalone'),
    ('linux-x86_64', 'Runtime/Linux/x86-64/Standalone'),
    ('linux-x86', 'Runtime/Linux/x86-32/Standalone'),
])
# the deploy command's platform, what "the platform" says in a standalone,
# and the binary format (binfmt.arch_of), by package.py family
DEPLOY_PLATFORM = {'mac': 'macosx', 'windows': 'windows', 'linux': 'linux'}
REPORTED_PLATFORM = {'mac': 'MacOS', 'windows': 'Win32', 'linux': 'Linux'}
BINARY_FORMAT = {'mac': 'macho', 'windows': 'pe', 'linux': 'elf'}
MAC_EXE = 'Contents/MacOS/Standalone-Community'


def describe(kind, archs):
    """'ELF x86', 'PE x86_64', or 'not a binary'."""
    return '%s %s' % ({'macho': 'Mach-O'}.get(kind, kind.upper()), ' '.join(archs)) if kind else 'not a binary'


def log(msg=''):
    rlc.log(msg)


def gha():
    return os.environ.get('GITHUB_ACTIONS') == 'true'


def run(cmd, timeout=120):
    try:
        r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout)
    except subprocess.TimeoutExpired:
        return None, '(did not finish within %d seconds)' % timeout
    except OSError as e:
        return None, str(e)
    return r.returncode, r.stdout.decode('utf-8', 'replace').strip()


def _version(v):
    parts = [int(x) for x in re.findall(r'[0-9]+', str(v))] if not isinstance(v, tuple) else list(v)
    while len(parts) > 1 and parts[-1] == 0:
        parts.pop()
    return tuple(parts)


def slice_floors(path):
    data = binfmt.read_file(path)
    return [binfmt.parse_macho(data[o:o + n]).floors.get('macOS') for _, o, n in binfmt.macho_slices(data)]


def own_target(p):
    """The target of a layout's own platform (every macOS layout has the
    universal runtime)."""
    return 'mac-universal' if p.family == 'mac' else p.name


def can_run(tp):
    """Whether this machine can run a standalone of package.py platform tp:
    the same operating system, and on Linux the same architecture (64-bit
    Windows runs 32-bit programs too; a Mac standalone holds the slices of
    its runtime)."""
    t = package.PLATFORMS[tp]
    fam = check_native_deps.host_family()
    if t.family != fam:
        return False
    if fam != 'linux':
        return True
    machine = host.machine().lower()
    native = {'amd64': 'x86_64', 'i386': 'x86', 'i486': 'x86', 'i586': 'x86', 'i686': 'x86',
              'aarch64': 'arm64'}.get(machine, machine)
    return t.arch == native


def check_mac_runtimes(tools, p):
    """Problems of the macOS runtime folders (see the module notes), and
    lines describing what was checked."""
    problems, lines = [], []
    codesign = shutil.which('codesign') if sys.platform == 'darwin' else None
    for r in p.runtimes:
        folder = os.path.join(tools, *r['folder'].split('/'))
        if not os.path.isdir(folder):
            problems.append('%s is missing' % r['folder'])
            continue
        if r['standalone']:
            app = os.path.join(folder, r['standalone'][1])
            exe = os.path.join(app, *MAC_EXE.split('/'))
            where = '%s/%s' % (r['folder'], r['standalone'][1])
            if not os.path.isfile(exe):
                problems.append('%s has no %s (revSBEnginePath\'s engine)' % (where, MAC_EXE))
                continue
            archs = binfmt.macho_archs(exe) or []
            if sorted(archs) != sorted(p.mac_archs):
                problems.append('%s/%s holds %s, not %s' % (where, MAC_EXE, ' '.join(archs) or 'nothing',
                                                            ' and '.join(p.mac_archs)))
            try:
                with open(os.path.join(app, 'Contents', 'Info.plist'), 'rb') as f:
                    info = plistlib.load(f)
            except (OSError, ValueError, plistlib.InvalidFileException) as e:
                problems.append('%s/Contents/Info.plist: %s' % (where, e))
                info = {}
            floors = [f for f in slice_floors(exe) if f]
            declared = info.get('LSMinimumSystemVersion')
            if info.get('CFBundleExecutable') != 'Standalone-Community':
                problems.append('%s/Contents/Info.plist: CFBundleExecutable is %r' % (where,
                                                                                    info.get('CFBundleExecutable')))
            if floors and (not declared or _version(declared) != _version(min(floors))):
                problems.append('%s/Contents/Info.plist declares LSMinimumSystemVersion %s; its engine runs from '
                                'macOS %s' % (where, declared, binfmt.version_text(min(floors))))
            for icon in ('Standalone.icns', 'StandaloneDoc.icns'):
                if not os.path.isfile(os.path.join(app, 'Contents', 'Resources', icon)):
                    problems.append('%s has no Contents/Resources/%s (the builder copies it into every Mac '
                                    'standalone)' % (where, icon))
            if codesign:
                code, out = run(['codesign', '--verify', '--strict', app])
                if code != 0:
                    problems.append('codesign --verify --strict %s: %s' % (where, out))
            lines.append('%s: %s, macOS %s and later, %s' % (
                where, ' '.join(archs), declared, 'signature verified' if codesign else 'signature not checked here'))
        for name in r['support']:
            if not os.path.exists(os.path.join(folder, 'Support', name)):
                problems.append('%s/Support/%s is missing' % (r['folder'], name))
        if r['externals']:
            for lst, sub in (('Externals.txt', ''), ('Database Drivers.txt', 'Database Drivers')):
                path = os.path.join(folder, 'Externals', sub, lst) if sub else os.path.join(folder, 'Externals', lst)
                try:
                    with open(path, 'rb') as f:
                        entries = [x for x in f.read().decode('utf-8').split('\n') if x]
                except OSError as e:
                    problems.append('%s: %s' % (os.path.relpath(path, tools), e))
                    continue
                for e in entries:
                    name = e.split(',', 1)[-1]
                    if '\r' in e or not os.path.isdir(os.path.join(os.path.dirname(path), name)):
                        problems.append('%s names %r, which is not a bundle next to it'
                                        % (os.path.relpath(path, tools), e))
            lines.append('%s/Externals: %s' % (r['folder'], 'externals and database drivers listed and present'))
    return problems, lines


def check_runtime_folder(tools, tp):
    """Problems of the Windows or Linux runtime folder of package.py
    platform tp (see the module notes), and lines describing what was
    checked. The IDE reads the lists with url "file:", which takes CRLF
    line endings as well as LF on every platform."""
    t = package.PLATFORMS[tp]
    r = t.runtimes[0]
    folder = os.path.join(tools, *r['folder'].split('/'))
    fmt = BINARY_FORMAT[t.family]
    problems, checked = [], []

    def binary(rel):
        path = os.path.join(folder, *rel.split('/'))
        if not os.path.isfile(path):
            problems.append('%s/%s is missing' % (r['folder'], rel))
            return
        kind, archs = binfmt.arch_of(path)
        if kind != fmt or archs != [t.arch]:
            problems.append('%s/%s is %s, expected %s' % (r['folder'], rel, describe(kind, archs),
                                                          describe(fmt, [t.arch])))
        checked.append(rel)

    binary(r['standalone'][1])
    for name in r['support']:
        binary('Support/' + name)
    if r['externals']:
        for lst, sub in (('Externals.txt', 'Externals/'), ('Database Drivers.txt', 'Externals/Database Drivers/')):
            path = os.path.join(folder, *(sub + lst).split('/'))
            try:
                with open(path, 'rb') as f:
                    entries = [x.rstrip('\r') for x in f.read().decode('utf-8').split('\n') if x.strip()]
            except FileNotFoundError:
                # Worded without the path on this machine, so that the
                # baseline can name it
                problems.append('%s/%s%s is missing' % (r['folder'], sub, lst))
                continue
            except OSError as e:
                problems.append('%s/%s%s: %s' % (r['folder'], sub, lst, e))
                continue
            if not entries:
                problems.append('%s/%s%s lists nothing' % (r['folder'], sub, lst))
            for e in entries:
                name = e.split(',', 1)[-1]
                binary(sub + name)
                if name.startswith('revbrowser.'):
                    binary('Externals/CEF/libcef.' + name.rsplit('.', 1)[-1])
    return problems, ['%s: %d binaries checked (%s)' % (r['folder'], len(checked), describe(fmt, [t.arch]))]


def deploy_and_run(engine, runtime, tp, work, timeout, expect_version):
    """Build the probe standalone for package.py platform tp from runtime
    with engine, check it and run it when this machine can. Returns
    (problems, lines)."""
    t = package.PLATFORMS[tp]
    family = t.family
    problems, lines = [], []
    stack = os.path.join(work, 'OXTStandaloneProbe-%s.livecode' % tp)
    if family == 'mac':
        app = os.path.join(work, 'OXTProbe-%s.app' % tp)
        # a copy of the runtime bundle, whose executable the deploy
        # replaces, as revSaveAsMacStandalone copies the runtime's app
        shutil.copytree(runtime, app, symlinks=True)
        runtime_exe = os.path.join(runtime, *MAC_EXE.split('/'))
        output = os.path.join(app, *MAC_EXE.split('/'))
        os.remove(output)
    else:
        app, runtime_exe = None, runtime
        output = os.path.join(work, 'OXTProbe-%s%s' % (tp, '.exe' if family == 'windows' else ''))
    env = dict(OXT_DEPLOY_PLATFORM=DEPLOY_PLATFORM[family], OXT_DEPLOY_ENGINE=runtime_exe, OXT_DEPLOY_OUTPUT=output,
               OXT_DEPLOY_STACK=stack, OXT_DEPLOY_ARCHS=None)
    code, out, err = rlc.run_engine(engine, DEPLOY_SCRIPT, env, work, timeout)
    for line in out + err:
        log('  deploy (%s): %s' % (tp, line))
    if code != 0 or not any(x.startswith('DEPLOY OK') for x in out):
        why = next((x for x in out if x.startswith('DEPLOY FAILED')), None) or \
            ('the engine did not finish within %d seconds' % timeout if code is None else
             'exit status %s, no DEPLOY line' % code)
        problems.append('%s: building the standalone failed: %s' % (tp, why))
        return problems, lines
    lines.append('%s: built %s from %s' % (tp, os.path.relpath(app or output, work), runtime_exe))
    if family == 'mac':
        # the deploy keeps every slice of the runtime (no "architectures")
        if sorted(binfmt.macho_archs(output) or []) != sorted(binfmt.macho_archs(runtime_exe) or []):
            problems.append('%s: the standalone holds %s, the runtime %s' % (
                tp, ' '.join(binfmt.macho_archs(output) or ['nothing']),
                ' '.join(binfmt.macho_archs(runtime_exe) or [])))
            return problems, lines
        # what the IDE does after building (performAdHocCodesign): the
        # deploy changed the executable, so its signature no longer holds
        if can_run(tp):
            for cmd in (['xattr', '-cr', app], ['codesign', '--force', '--deep', '--sign', '-', app],
                        ['codesign', '--verify', '--deep', '--strict', '--verbose=2', app]):
                c, text = run(cmd)
                log('  %s: %s' % (' '.join(cmd[:4]), text or 'ok'))
                if c != 0:
                    problems.append('%s: %s failed: %s' % (tp, ' '.join(cmd[:-1]), text))
                    return problems, lines
            lines.append('%s: signed ad hoc as the IDE signs a standalone, and verified' % tp)
    else:
        kind, archs = binfmt.arch_of(output)
        if kind != BINARY_FORMAT[family] or archs != [t.arch]:
            problems.append('%s: the standalone is %s, expected %s' % (tp, describe(kind, archs),
                                                                       describe(BINARY_FORMAT[family], [t.arch])))
            return problems, lines
    if not can_run(tp):
        lines.append('%s: not run here (this machine does not run %s programs)' % (tp, tp))
        return problems, lines
    archs = binfmt.arch_of(output)[1] or ['?']
    processor = rlc.engine_processor(output)
    try:
        r = subprocess.run([output, '-ui'], cwd=work, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
        code, out, err = r.returncode, r.stdout.decode('utf-8', 'replace').splitlines(), \
            r.stderr.decode('utf-8', 'replace').splitlines()
    except subprocess.TimeoutExpired:
        code, out, err = None, [], []
    except OSError as e:
        problems.append('%s: cannot run the standalone %s: %s' % (tp, output, e))
        return problems, lines
    for line in out + err:
        log('  standalone (%s): %s' % (tp, line))
    probe = next((m for m in (re.match(r'^PROBE version=(\S*) processor=(\S*) platform=(\S*) environment=(.*)$', x)
                              for x in out) if m), None)
    if code is None:
        problems.append('%s: the standalone did not finish within %d seconds' % (tp, timeout))
    elif probe is None:
        problems.append('%s: the standalone wrote no PROBE line (exit status %s)%s'
                        % (tp, code, rlc._missing_library_hint(err)))
    else:
        version, got, plat, environment = probe.groups()
        # without a user interface a standalone's environment is "command
        # line" (a development engine's "development command line")
        expected = {'version': (version, expect_version), 'processor': (got, processor),
                    'platform': (plat, REPORTED_PLATFORM[family]),
                    'environment': (environment.strip(), 'command line')}
        for key, (have, want) in expected.items():
            if have != want:
                problems.append('%s: the standalone reports %s %r, expected %r' % (tp, key, have, want))
        if code != 0:
            problems.append('%s: the standalone exited with status %s' % (tp, code))
        lines.append('%s: ran it (%s; the processor it reports: %s): %s' % (tp, ' '.join(archs), got,
                                                                          probe.group(0)))
    return problems, lines


# Problems known to be in what the check looks at, one message per line as
# the check words it; "#" starts a comment. In this repository they are
# faults of OpenXTalk Lite 1.15's own runtimes (the asset oxt-runtimes-1.15),
# which are kept as Tom Perry shipped them: reported, but not failures.
BASELINE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'standalone-baseline.txt')


def read_baseline(path=BASELINE):
    try:
        with open(path, encoding='utf-8') as f:
            return [line.split('#', 1)[0].strip() for line in f if line.split('#', 1)[0].strip()]
    except FileNotFoundError:
        return []


def main(argv=None):
    ap = argparse.ArgumentParser(description='Check the standalone runtimes of an installed OpenXTalk-Lite, and build '
                                             'standalones from them and run them.')
    ap.add_argument('--install', metavar='DIR', help='installed layout (the folder that holds OpenXTalk-Lite.app on '
                                                     'macOS, the program folder elsewhere)')
    ap.add_argument('--package', metavar='FILE', help='a package (the portable zip, the Linux tar.xz) to extract '
                                                      'and check like --install')
    ap.add_argument('--engine', metavar='FILE', help='development engine (with --runtime, instead of --install)')
    ap.add_argument('--runtime', metavar='PATH', help='standalone engine, or on macOS a Standalone*.app')
    ap.add_argument('--platform', choices=list(package.PLATFORMS),
                    help='the layout, or with --runtime the runtime\'s platform (default: this machine\'s, %s)'
                         % rlc.default_platform())
    ap.add_argument('--targets', choices=('own', 'all'), default='own',
                    help='the layout\'s own runtime (default), or every runtime it has (see RUNTIMES)')
    ap.add_argument('--log', metavar='FILE', help='write the output here too')
    ap.add_argument('--timeout', type=int, default=180, help='seconds per engine run (default: %(default)s)')
    args = ap.parse_args(argv)
    p = package.PLATFORMS[args.platform or rlc.default_platform()]
    layout_given = bool(args.install) + bool(args.package)
    if layout_given > 1 or bool(layout_given) == bool(args.engine or args.runtime) or \
            bool(args.engine) != bool(args.runtime):
        ap.error('pass --install or --package, or --engine with --runtime')
    if args.runtime and args.targets != 'own':
        ap.error('--targets all goes with --install or --package')
    if own_target(p) not in RUNTIMES:
        ap.error('%s has no standalone runtime of its own' % p.name)

    problems, lines, temp_dirs = [], [], []
    try:
        expect_version, _ = rlc.read_expectations(rlc.REPO)
        base = os.environ.get('RUNNER_TEMP') or tempfile.gettempdir()
        work = tempfile.mkdtemp(prefix='oxt-standalone-', dir=base)
        temp_dirs.append(work)
        trap = package.repository_mode_trap(work, p)
        if trap:
            raise rlc.CheckError('the work folder %s has a folder named %s in its path' % (work, trap))
        if layout_given:
            args.bin_dir = None
            lay = rlc.find_layout(args, p, temp_dirs)
            engine, tools = lay.engine, lay.tools
            log('Layout  : %s' % lay.root)
            if p.family == 'mac':
                found, what = check_mac_runtimes(tools, p)
                problems += found
                for line in what:
                    log('Runtime : ' + line)
            if args.targets == 'own':
                targets = [own_target(p)]
            else:
                targets = [tp for tp, rel in RUNTIMES.items() if os.path.exists(os.path.join(tools, *rel.split('/')))]
                if own_target(p) not in targets:
                    targets.insert(0, own_target(p))
            jobs = []
            for tp in targets:
                runtime = os.path.join(tools, *RUNTIMES[tp].split('/'))
                if not os.path.exists(runtime):
                    problems.append('%s: no runtime at %s' % (tp, RUNTIMES[tp]))
                    continue
                if package.PLATFORMS[tp].family != 'mac':
                    found, what = check_runtime_folder(tools, tp)
                    problems += found
                    for line in what:
                        log('Runtime : ' + line)
                    lines += what
                jobs.append((tp, runtime))
        else:
            engine = os.path.abspath(args.engine)
            jobs = [(own_target(p), os.path.abspath(args.runtime))]
            if not os.path.exists(jobs[0][1]):
                raise rlc.CheckError('no runtime at %s' % jobs[0][1])
        log('Engine  : %s' % engine)
        for tp, runtime in jobs:
            log('Target  : %s (%s)' % (tp, runtime))
            found, what = deploy_and_run(engine, runtime, tp, work, args.timeout, expect_version)
            problems += found
            lines += what
    except (rlc.CheckError, OSError, binfmt.FormatError) as e:
        problems.append(str(e))
    finally:
        for d in temp_dirs:
            shutil.rmtree(d, ignore_errors=True)
    log('')
    for line in lines:
        log('Standalone: ' + line)
    baseline = read_baseline()
    known = [m for m in problems if m in baseline]
    problems = [m for m in problems if m not in baseline]
    for msg in known:
        log('::notice title=Standalone check (known)::%s' % msg if gha() else 'known: %s' % msg)
        lines.append('known (tools/ci/standalone-baseline.txt): ' + msg)
    if layout_given and args.targets == 'all':
        for msg in baseline:
            if msg not in known:
                log('Baseline entry that did not occur (remove it from tools/ci/standalone-baseline.txt): %s' % msg)
    for msg in problems:
        log('::error title=Standalone check::%s' % msg if gha() else 'error: %s' % msg)
    result = 'passed' if not problems else 'FAILED (%d problems)' % len(problems)
    log('Standalone check %s.' % result)
    rlc._append('GITHUB_STEP_SUMMARY', '### Standalone check (%s, targets: %s)\n\n%s\n\nResult: **%s**\n\n'
                % (p.name, args.targets, '\n'.join('- ' + x for x in lines + ['problem: ' + m for m in problems]),
                   result))
    if args.log:
        with open(args.log, 'w', encoding='utf-8', newline='\n') as f:
            f.write('\n'.join(lines + problems + [result]) + '\n')
    return 1 if problems else 0


if __name__ == '__main__':
    sys.exit(main())
