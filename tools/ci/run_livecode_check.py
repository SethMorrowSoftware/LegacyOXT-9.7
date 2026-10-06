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

"""Run the headless LiveCode checks of tools/ci with a development engine,
on Windows, Linux or macOS: the Python counterpart of smoke-test.ps1 and
ide-compile-check.ps1 (which the Windows CI keeps using).

  python tools/ci/run_livecode_check.py smoke
      (--bin DIR | --install DIR | --package FILE) [--platform P]
      [--engine FILE] [--log FILE] [--timeout SECONDS]

  python tools/ci/run_livecode_check.py compile
      (--repo-layout --bin DIR | --root DIR | --install DIR | --package FILE)
      [--platform P] [--engine FILE] [--baseline FILE]
      [--update-baseline [--baseline-source TEXT]] [--log FILE]
      [--timeout SECONDS]

The layout to test is one of

  --bin DIR       a build output (win-x86_64-bin, linux-<arch>-bin,
                  _build/mac/Release): the development layout, with the
                  engine (LiveCode-Community, or on macOS
                  LiveCode-Community.app), the externals and the database
                  drivers in one folder.
  --install DIR   a staged or installed layout (tools/oxt/package.py's
                  LegacyOXT-<version> folder): the engine "LiveCode Community.exe",
                  "LiveCode Community.<arch>" or <ProductTitle>.app/Contents/MacOS/LiveCode-Community,
                  and the tools folder (the folder itself, or on macOS
                  <ProductTitle>.app/Contents/Tools) with Externals,
                  Externals/Database Drivers and Extensions.
  --package FILE  a package (the portable zip, the Linux tar.xz or the
                  macOS app zip), extracted to a neutral temporary folder
                  first, keeping modes and symbolic links, and tested as
                  an installed layout: the files users download.

P is a tools/oxt/package.py platform name; the default is this machine's
(win-x86_64, linux-x86_64 or linux-arm64, mac-universal), which is all
that decides the file names. An installed layout must not be tested from a
path with a folder named _build, <platform>-<processor>-bin and so on
(package.repository_mode_trap): the engine would run the IDE of a source
checkout instead.

smoke runs tools/ci/smoke-test.livecodescript with -ui and passes what
differs between platforms in the environment: the processor the engine
reports (from the engine binary: a universal Mac engine runs this
machine's architecture), the externals' suffix (.dll, .so, .bundle) and
the code folders the IDE maps for LCB libraries on this platform
(<processor>-win32*; <processor>-linux*; <processor>-mac* and universal-mac*,
as revideextensionlibrary __MapCodeLibraryForIDE filters them). In an
installed layout with the xTalk Suite extensions of
tools/oxt/xtalk-extensions.json it also loads and calls each of them, in
load order, as smoke-test.ps1 does; the libraries' dependencies come from
tools/ci/check_native_deps.py, and a library that needs something neither
a system library nor next to it is a failed check, since this machine may
have what a user's lacks. It exits with the number of failed checks, or 1
when the engine did not finish.

compile runs tools/ci/ide-compile-check.livecodescript over a layout's
Toolset, Plugins and Extensions and compares the errors with
tools/ci/ide-compile-baseline.txt as ide-compile-check.ps1 does: an error
that is not in the baseline fails the check (exit status 1), a baseline
entry that no longer occurs is a warning, and --update-baseline rewrites
the baseline instead. --repo-layout checks the source checkout itself
before any packaging (the development engine of --bin, in repository
mode): the Toolset, Plugins and Extensions that tools/oxt/layout.py
assembles from ide/ and ide-support/, plus the build's packaged extensions
that package.py installs, are written to a temporary folder with their
installed paths, so that the error records match the baseline's, which was
made from such a layout. --root checks any folder with a Toolset.

Under GitHub Actions both write step outputs (smoke: passed, failed;
compile: scripts, errors, new-errors, fixed), annotations and a job
summary.

Only the Python 3 standard library is used (3.8 or later).
"""

import argparse
import collections
import json
import os
import platform as host
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), 'oxt'))
sys.path.insert(0, HERE)
import binfmt  # noqa: E402
import check_native_deps  # noqa: E402
import layout  # noqa: E402
import package  # noqa: E402

SMOKE_SCRIPT = os.path.join(HERE, 'smoke-test.livecodescript')
COMPILE_SCRIPT = os.path.join(HERE, 'ide-compile-check.livecodescript')
BASELINE = os.path.join(HERE, 'ide-compile-baseline.txt')
XTALK_MANIFEST = os.path.join(REPO, 'tools', 'oxt', 'xtalk-extensions.json')

# The record of a file that crashed the engine during the compile check,
# and how many such files end the check
CRASH_RECORD = '%s | (file) | line 0 | the engine crashed while checking it'
MAX_CRASHES = 5

# Build output: development engine executable, externals suffix
DEV = {'windows': ('LiveCode-Community.exe', '.dll'),
       'linux': ('LiveCode-Community', '.so'),
       'mac': ('LiveCode-Community.app/Contents/MacOS/LiveCode-Community', '.bundle')}


class CheckError(Exception):
    pass


def log(msg=''):
    print(msg)
    sys.stdout.flush()


def default_platform():
    fam = check_native_deps.host_family()
    if fam == 'windows':
        return 'win-x86_64'
    if fam == 'mac':
        return 'mac-universal'
    machine = host.machine().lower()
    if machine in ('aarch64', 'arm64'):
        return 'linux-arm64'
    if machine in ('i386', 'i486', 'i586', 'i686', 'x86'):
        return 'linux-x86'
    return 'linux-x86_64'


def engine_processor(engine):
    """What "the processor" of the engine will be: its ELF architecture, or
    for Mach-O this machine's architecture when the engine has it (a
    universal engine runs the native slice)."""
    if engine.lower().endswith('.exe'):
        return binfmt.pe_arch(engine) or 'x86_64'
    arch = binfmt.elf_arch(engine)
    if arch:
        return arch
    archs = binfmt.macho_archs(engine) or []
    native = 'arm64' if host.machine().lower() in ('arm64', 'aarch64') else 'x86_64'
    return native if native in archs or not archs else archs[0]


def code_folder_patterns(family, processor):
    """The IDE's code folder lookup (revideextensionlibrary
    __MapCodeLibraryForIDE): folders for this platform whose name starts
    with the processor, and on macOS also the universal ones."""
    tag = {'windows': 'win32', 'linux': 'linux', 'mac': 'mac'}[family]
    patterns = ['%s-%s*' % (processor, tag)]
    if family == 'mac':
        patterns.append('universal-mac*')
    return ','.join(patterns)


def read_expectations(repo):
    """(BUILD_SHORT_VERSION, SQLITE_VERSION) from the source tree."""
    version = sqlite = None
    with open(os.path.join(repo, 'version'), encoding='utf-8') as f:
        for line in f:
            m = re.match(r'^\s*BUILD_SHORT_VERSION\s*=\s*(\S+)', line)
            if m:
                version = m.group(1)
                break
    with open(os.path.join(repo, 'thirdparty', 'libsqlite', 'include', 'sqlite3.h'), encoding='utf-8',
              errors='replace') as f:
        for line in f:
            m = re.match(r'^#define\s+SQLITE_VERSION\s+"([^"]+)"', line)
            if m:
                sqlite = m.group(1)
                break
    if not version or not sqlite:
        raise CheckError('BUILD_SHORT_VERSION or SQLITE_VERSION not found in %s' % repo)
    return version, sqlite


# ---------------------------------------------------------------------------
# Layouts

class Layout(object):
    """Where the engine and its files are."""

    def __init__(self, kind, engine, externals, drivers, tools=None, root=None, source=''):
        self.kind = kind            # development or installed
        self.engine = engine        # executable
        self.externals = externals
        self.drivers = drivers
        self.tools = tools          # tools folder (installed): Toolset, Extensions ...
        self.root = root            # the layout folder
        self.source = source        # what was tested, for the summary


def extract_package(path, dest):
    """Extract a zip or tar.xz package into dest with modes and symbolic
    links (zipfile.extractall keeps neither) and return its one top-level
    entry. Nothing may land outside dest, by its name or through a
    symbolic link extracted before it (package.inside_tree)."""
    if path.endswith('.zip'):
        root, links = os.path.realpath(dest), []
        with zipfile.ZipFile(path) as z:
            for info in z.infolist():
                name = info.filename
                parts = [x for x in name.rstrip('/').split('/') if x]
                if not parts or name.startswith('/') or '..' in parts or re.match(r'^[A-Za-z]:', name):
                    raise CheckError('%s: unsafe member name %r' % (path, name))
                target = os.path.join(dest, *parts)
                # as in package.extract_bin_tar: a link extracted before
                # this entry must not carry it out of dest
                if os.path.islink(target) or not package.inside_tree(root, os.path.dirname(target)):
                    raise CheckError('%s: %s would be written through a symbolic link' % (path, name))
                mode = (info.external_attr >> 16) if info.create_system == 3 else 0
                if name.endswith('/'):
                    os.makedirs(target, exist_ok=True)
                    continue
                os.makedirs(os.path.dirname(target), exist_ok=True)
                if stat.S_ISLNK(mode) and os.name != 'nt':
                    link = z.read(info).decode('utf-8')
                    if package.link_escapes('/'.join(parts), link):
                        raise CheckError('%s: link %s -> %s leads out of the package' % (path, name, link))
                    os.symlink(link, target)
                    links.append((name, link, target))
                    continue
                with z.open(info) as src, open(target, 'wb') as out:
                    shutil.copyfileobj(src, out, 1 << 20)
                if mode and os.name != 'nt':
                    os.chmod(target, stat.S_IMODE(mode))
        tops = sorted({i.filename.split('/')[0] for i in zipfile.ZipFile(path).infolist()})
        if len(tops) != 1:
            raise CheckError('%s: expected one top-level folder, found %s' % (path, ', '.join(tops)))
        # once all are on disk: a later link can move an earlier one's
        # target out; against the package's top-level folder, which is
        # what gets tested, not dest (as in package.extract_bin_tar)
        top = os.path.realpath(os.path.join(dest, tops[0]))
        for name, link, target in links:
            if not package.inside_tree(top, target):
                raise CheckError('%s: link %s -> %s leads out of the package' % (path, name, link))
        return tops[0]
    top = package.extract_bin_tar(path, dest, lambda m: None)
    return os.path.basename(top)


def find_layout(args, p, temp_dirs):
    """The Layout of --bin, --install or --package."""
    given = [x for x in (args.bin_dir, args.install, args.package) if x]
    if len(given) != 1:
        raise CheckError('pass one of --bin, --install and --package')
    fam = p.family
    if args.bin_dir:
        b = os.path.abspath(args.bin_dir)
        engine = args.engine or os.path.join(b, *DEV[fam][0].split('/'))
        return Layout('development', engine, b, b, root=b, source=args.bin_dir)
    if args.package:
        base = os.environ.get('RUNNER_TEMP') or tempfile.gettempdir()
        dest = tempfile.mkdtemp(prefix='oxt-check-', dir=base)
        temp_dirs.append(dest)
        log('Extracting %s to %s ...' % (args.package, dest))
        top = extract_package(os.path.abspath(args.package), dest)
        # the portable zip and the tar.xz hold LegacyOXT-<version>/; the
        # macOS zip holds the .app itself
        root = dest if top == p.engine else os.path.join(dest, top)
        source = os.path.basename(args.package)
    else:
        root = os.path.abspath(args.install)
        source = args.install
    trap = package.repository_mode_trap(root, p)
    if trap:
        raise CheckError('%s has a folder named %s in its path, which makes the engine run the IDE of a source '
                         'checkout (repository mode); test an installed layout from a neutral path' % (root, trap))
    tools = os.path.join(root, *p.tools.rstrip('/').split('/')) if p.tools else root
    engine = args.engine or os.path.join(root, *p.engine_executable.split('/'))
    return Layout('installed', engine, os.path.join(tools, 'Externals'),
                  os.path.join(tools, 'Externals', 'Database Drivers'), tools=tools, root=root, source=source)


def run_engine(engine, script, env, cwd, timeout):
    """(exit code or None when it timed out, stdout lines, stderr lines)."""
    full = dict(os.environ)
    full.update({k: v for k, v in env.items() if v is not None})
    for k, v in env.items():
        if v is None:
            full.pop(k, None)
    try:
        proc = subprocess.run([engine, '-ui', script], cwd=cwd, env=full, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        out = (e.stdout or b'').decode('utf-8', 'replace').splitlines()
        err = (e.stderr or b'').decode('utf-8', 'replace').splitlines()
        return None, out, err
    except OSError as e:
        raise CheckError('cannot run %s: %s' % (engine, e))
    return (proc.returncode, proc.stdout.decode('utf-8', 'replace').splitlines(),
            [x for x in proc.stderr.decode('utf-8', 'replace').splitlines() if x.strip()])


def _append(var, text):
    path = os.environ.get(var)
    if path:
        with open(path, 'a', encoding='utf-8', newline='\n') as f:
            f.write(text)


# ---------------------------------------------------------------------------
# smoke

def xtalk_probe_lines(tools, fam):
    """The probe file lines (see smoke-test.livecodescript
    checkXtalkExtensions) for the xTalk Suite extensions of the manifest,
    in load order, or [] when the layout has none of them."""
    ext_dir = os.path.join(tools, 'Extensions')
    if not os.path.isdir(ext_dir) or not os.path.isfile(XTALK_MANIFEST):
        return [], 0
    with open(XTALK_MANIFEST, encoding='utf-8') as f:
        manifest = json.load(f)
    entries = []
    for m in manifest.get('members', []):
        for e in m.get('extensions', []):
            entries.append(dict(kind=e['kind'], folder=e['folder'], id=e['id'],
                                name=e['stack'] if e['kind'] == 'lcs' else e['id'],
                                probe=e['probe'], expect=e['expect'], requires=list(e.get('requires', [])),
                                present=os.path.isdir(os.path.join(ext_dir, e['folder']))))
    present = [e for e in entries if e['present']]
    if not present:
        return [], 0
    deps = check_native_deps.check_layout(tools, (fam,))
    ordered = [e for e in entries if e['kind'] == 'lcb']
    pending = [e for e in entries if e['kind'] != 'lcb']
    while pending:
        placed = {e['id'].lower() for e in ordered}
        ready = [e for e in pending if all(r.lower() in placed for r in e['requires'])]
        if not ready:
            raise CheckError('tools/oxt/xtalk-extensions.json: circular or unknown requires')
        ordered.extend(ready)
        pending = [e for e in pending if e not in ready]
    lines = []
    for e in ordered:
        if not e['present']:
            lines.append('missing\t%s' % e['folder'])
        elif e['kind'] == 'lcb':
            problems = ['%s/%s: %s' % (r['folder'], r['path'].rsplit('/', 1)[-1], check_native_deps.describe(r))
                        for r in deps if r['extension'] == e['folder'] and check_native_deps.failed(r)]
            lines.append('lcb\t%s\t%s\t%s\t%s\t%s' % (e['folder'], e['id'], e['probe'], e['expect'],
                                                    '; '.join(problems)))
        else:
            lines.append('lcs\t%s\t%s\t%s\t%s\t%s' % (e['folder'], e['name'], e['probe'], e['expect'],
                                                    ','.join(e['requires'])))
    return lines, len(present)


def cmd_smoke(args, p):
    temp_dirs = []
    passed = failed = None
    lay = None
    try:
        expect_version, expect_sqlite = read_expectations(REPO)
        lay = find_layout(args, p, temp_dirs)
        if not os.path.isfile(lay.engine):
            raise CheckError('engine not found: %s' % lay.engine)
        for d in (lay.externals, lay.drivers):
            if not os.path.isdir(d):
                raise CheckError('folder not found: %s' % d)
        processor = engine_processor(lay.engine)
        codes = code_folder_patterns(p.family, processor)
        log('Layout          : %s' % lay.kind)
        log('Engine          : %s' % lay.engine)
        log('Externals       : %s' % lay.externals)
        log('Database drivers: %s' % lay.drivers)
        log('Smoke test      : %s' % SMOKE_SCRIPT)
        log('Expected version: %s' % expect_version)
        log('Expected SQLite : %s' % expect_sqlite)
        log('Processor       : %s; externals %s; code folders %s' % (processor, DEV[p.family][1], codes))
        env = dict(OXT_EXPECT_VERSION=expect_version, OXT_EXPECT_SQLITE=expect_sqlite,
                   OXT_EXPECT_PROCESSOR=processor, OXT_EXTERNAL_SUFFIX=DEV[p.family][1],
                   OXT_CODE_FOLDERS=codes, OXT_EXTERNALS_DIR=lay.externals, OXT_DRIVERS_DIR=lay.drivers,
                   OXT_XTALK_PROBES=None, OXT_EXTENSIONS_DIR=None)
        xtalk_count = 0
        if lay.kind == 'installed':
            lines, xtalk_count = xtalk_probe_lines(lay.tools, p.family)
            if lines:
                fd, probes = tempfile.mkstemp(prefix='oxt-xtalk-probes-', suffix='.txt')
                with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as f:
                    f.write('\n'.join(lines) + '\n')
                temp_dirs.append(probes)
                env.update(OXT_XTALK_PROBES=probes, OXT_EXTENSIONS_DIR=os.path.join(lay.tools, 'Extensions'))
                log('xTalk extensions: %d in %s' % (xtalk_count, env['OXT_EXTENSIONS_DIR']))
            else:
                log('xTalk extensions: none in this layout (packaged with --no-xtalk-extensions); not tested')
        log('')
        code, out, err = run_engine(lay.engine, SMOKE_SCRIPT, env, os.path.dirname(lay.engine), args.timeout)
        for line in out:
            log(line)
        if err:
            log('')
            log('Engine stderr:')
            for line in err:
                log('  ' + line)
        if args.log:
            os.makedirs(os.path.dirname(os.path.abspath(args.log)), exist_ok=True)
            with open(args.log, 'w', encoding='utf-8', newline='\n') as f:
                f.write('\n'.join(out + ['', 'stderr:'] + err) + '\n')
        if code is None:
            raise CheckError('the engine did not finish within %d seconds' % args.timeout)
        summary = [m for m in (re.match(r'^SUMMARY passed=(\d+) failed=(\d+)', x) for x in out) if m]
        if not summary:
            raise CheckError('the smoke test did not finish (exit code %s, no SUMMARY line)%s'
                             % (code, _missing_library_hint(err)))
        passed, failed = int(summary[-1].group(1)), int(summary[-1].group(2))
        if code != failed:
            raise CheckError('exit code %s does not match the %d failed check(s) reported' % (code, failed))
        _append('GITHUB_STEP_SUMMARY', '### Smoke test (%s layout, %s)\n\nHeadless run of '
                '`tools/ci/smoke-test.livecodescript` with `%s` from `%s`: %s.%s\n\n'
                % (lay.kind, p.name, os.path.basename(lay.engine), lay.source,
                   'all %d checks passed' % passed if not failed else '%d of %d checks failed' % (failed, passed + failed),
                   ' It loaded and called the %d bundled xTalk Suite extensions.' % xtalk_count if xtalk_count else ''))
    except CheckError as e:
        log('')
        log('::error title=Smoke test::%s' % e if os.environ.get('GITHUB_ACTIONS') == 'true' else 'error: %s' % e)
        _append('GITHUB_STEP_SUMMARY', '### Smoke test\n\nDid not complete: %s\n\n' % e)
        return 1
    finally:
        if passed is not None:
            _append('GITHUB_OUTPUT', 'passed=%d\nfailed=%d\n' % (passed, failed))
        for d in temp_dirs:
            if os.path.isdir(d):
                shutil.rmtree(d, ignore_errors=True)
            elif os.path.exists(d):
                os.remove(d)
    log('')
    log('Smoke test passed (%d checks).' % passed if not failed else
        'Smoke test failed: %d of %d checks failed.' % (failed, passed + failed))
    return failed


def _missing_library_hint(stderr):
    """The loader's complaint, when the engine could not start (a missing
    shared library makes a Linux engine exit before any script runs)."""
    for line in stderr:
        if 'error while loading shared libraries' in line or 'Library not loaded' in line:
            return '; ' + line.strip()
    return ''


# ---------------------------------------------------------------------------
# compile

def assemble_repo_layout(repo, bin_dir, dest):
    """Toolset, Plugins and Extensions of the installed layout, from the
    repository (layout.py) and the build's packaged extensions, with LF
    line endings as package.py writes them. Returns the file count."""
    pairs, problems = layout.plan_assemble(repo)
    if problems:
        raise CheckError('layout.py: ' + '; '.join(problems[:5]))
    count = 0
    for target, repo_path in pairs:
        if target.split('/')[0] not in ('Toolset', 'Plugins', 'Extensions'):
            continue
        src = layout.native(repo, repo_path)
        dst = layout.native(dest, target)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        if layout.is_text(target):
            with open(src, 'rb') as f:
                data = layout._normalise(f.read())
            with open(dst, 'wb') as f:
                f.write(data)
        else:
            shutil.copyfile(src, dst)
        count += 1
    for ext in package.PACKAGED_EXTENSIONS:
        base = os.path.join(bin_dir, 'packaged_extensions', ext)
        if not os.path.isdir(base):
            raise CheckError('the build has no packaged_extensions/%s' % ext)
        for rel in package.walk_tree(base):
            dst = layout.native(dest, 'Extensions/%s/%s' % (ext, rel))
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copyfile(layout.native(base, rel), dst)
            count += 1
    return count


def read_lines(path):
    if not path or not os.path.isfile(path):
        return []
    with open(path, encoding='utf-8-sig', errors='replace') as f:
        return f.read().splitlines()


def platform_baseline(baseline, p):
    """ide-compile-baseline-<family>.txt next to the baseline: the errors
    that occur only on that platform."""
    stem, ext = os.path.splitext(baseline)
    return '%s-%s%s' % (stem, p.family, ext)


def cmd_compile(args, p):
    temp_dirs = []
    gha = os.environ.get('GITHUB_ACTIONS') == 'true'
    try:
        if args.repo_layout:
            if not args.bin_dir:
                raise CheckError('--repo-layout needs --bin (the build whose development engine runs the check)')
            bin_dir = os.path.abspath(args.bin_dir)
            engine = args.engine or os.path.join(bin_dir, *DEV[p.family][0].split('/'))
            base = os.environ.get('RUNNER_TEMP') or tempfile.gettempdir()
            root = tempfile.mkdtemp(prefix='oxt-ide-layout-', dir=base)
            temp_dirs.append(root)
            n = assemble_repo_layout(REPO, bin_dir, root)
            log('Assembled %d files of Toolset, Plugins and Extensions from %s and %s' % (n, REPO, bin_dir))
            source = 'the repository checkout (layout.py assemble) and the packaged extensions of %s' % args.bin_dir
        elif args.root:
            root = os.path.abspath(args.root)
            engine = args.engine
            if not engine:
                raise CheckError('--root needs --engine')
            source = args.root
        else:
            lay = find_layout(args, p, temp_dirs)
            if lay.kind != 'installed':
                raise CheckError('compile checks a layout: pass --repo-layout with --bin, or --root, --install '
                                 'or --package')
            root, engine, source = lay.tools, lay.engine, lay.source
        if not os.path.isdir(os.path.join(root, 'Toolset')):
            raise CheckError('not a layout (no Toolset folder): %s' % root)
        if not os.path.isfile(engine):
            raise CheckError('engine not found: %s' % engine)
        baseline = os.path.abspath(args.baseline or BASELINE)
        log('Layout   : %s' % root)
        log('Engine   : %s' % engine)
        log('Checker  : %s' % COMPILE_SCRIPT)
        log('Baseline : %s%s' % (baseline, ' (will be rewritten)' if args.update_baseline else ''))
        log('')

        base = os.environ.get('RUNNER_TEMP') or tempfile.gettempdir()
        work = tempfile.mkdtemp(prefix='oxt-compile-', dir=base)
        temp_dirs.append(work)
        records = os.path.join(work, 'errors.txt')
        exclude_file = os.path.join(work, 'exclude.txt')
        crashed = []
        all_out, all_err = [], []
        t0 = time.time()
        # A file that crashes the engine would end the whole check. It is
        # recorded as an error of its own (the same record on every run, so
        # a platform baseline can list a known crash) and the check runs
        # again without it.
        while True:
            env = dict(OXT_CHECK_ROOT=root, OXT_CHECK_OUT=records, OXT_CHECK_EXCLUDE_FILE=None)
            if crashed:
                with open(exclude_file, 'w', encoding='utf-8', newline='\n') as f:
                    f.write('\n'.join(crashed) + '\n')
                env['OXT_CHECK_EXCLUDE_FILE'] = exclude_file
            if os.path.exists(records):
                os.remove(records)
            code, out, err = run_engine(engine, COMPILE_SCRIPT, env, work, args.timeout)
            out = [x for x in out if x]
            all_out += out
            all_err += err
            last = next((x[5:] for x in reversed(out) if x.startswith('FILE ')), None)
            summary_line = next((x for x in reversed(out) if x.startswith('SUMMARY ')), None)
            if code is None or summary_line is not None or last is None or last in crashed or \
                    len(crashed) >= MAX_CRASHES or _missing_library_hint(err):
                break
            log('The engine stopped (exit status %s) while checking %s; checking the rest without it'
                % (code, last))
            crashed.append(last)
        elapsed = time.time() - t0
        for line in all_out:
            if not line.startswith('FILE '):
                log(line)
        if all_err:
            log('')
            log('Engine stderr:')
            for line in all_err:
                log('  ' + line)
        out, err = all_out, all_err
        counts = dict(re.findall(r'(\w+)=([0-9.]+)', summary_line or ''))
        completed = code == 0 and summary_line is not None and os.path.isfile(records)
        problem = None
        if code is None:
            problem = 'The engine did not finish within %d seconds (last file started: %s)' % (
                args.timeout, last or '(none)')
        elif not completed:
            problem = 'The compile check did not complete (exit code %s%s%s)%s' % (
                code, '' if summary_line else ', no SUMMARY line',
                ', last file started: %s' % last if last else '', _missing_library_hint(err))
        current = [x.rstrip() for x in read_lines(records) if x.strip()] if completed else []
        current += [CRASH_RECORD % f for f in crashed] if completed else []

        # Errors that occur only on this platform (a known engine crash)
        # are listed in ide-compile-baseline-<family>.txt next to the shared
        # baseline, and count as known here
        supplement_path = platform_baseline(baseline, p)
        supplement = [x.rstrip() for x in read_lines(supplement_path) if x.strip() and not x.startswith('#')]
        if supplement:
            log('Platform baseline: %s (%d entries)' % (supplement_path, len(supplement)))
        new_errors, fixed, known = [], [], 0
        if completed and args.update_baseline:
            left = collections.Counter(supplement)
            shared = []
            for c in current:
                if left[c] > 0:
                    left[c] -= 1
                else:
                    shared.append(c)
            current = shared
            info = next((re.match(r'^INFO version=(\S+) build=(\S+)', x) for x in out
                         if x.startswith('INFO version=')), None)
            engine_version = '%s (build %s)' % (info.group(1), info.group(2)) if info else ''
            header = [
                '# Known compile errors in the IDE scripts, one per line:',
                '#   <file> | <object> | line <n> | <message>',
                '# tools/ci/ide-compile-check.ps1 fails only on errors that are not listed',
                '# here and reports listed errors that no longer occur. Regenerate with',
                '#   tools/ci/ide-compile-check.ps1 -Root <installed layout> -UpdateBaseline',
                '# Generated from %s' % (args.baseline_source or source),
                '# with engine %s: %s files, %s scripts, %d error(s).'
                % (engine_version, counts.get('files'), counts.get('scripts'), len(current)),
            ]
            with open(baseline, 'w', encoding='utf-8', newline='\n') as f:
                f.write('\n'.join(header + current) + '\n')
            log('')
            log('Wrote %d error(s) to %s' % (len(current), baseline))
        elif completed:
            if not os.path.isfile(baseline):
                raise CheckError('baseline not found: %s' % baseline)
            expected = [x.rstrip() for x in read_lines(baseline) if x.strip() and not x.startswith('#')]
            expected += supplement
            remaining = collections.Counter(expected)
            for c in current:
                if remaining[c] > 0:
                    remaining[c] -= 1
                    known += 1
                else:
                    new_errors.append(c)
            for e in expected:
                if remaining[e] > 0:
                    remaining[e] -= 1
                    fixed.append(e)
            log('')
            log('Compared with the baseline: %d known, %d new, %d no longer occurring'
                % (known, len(new_errors), len(fixed)))
            if new_errors:
                log('')
                log('New compile errors (not in the baseline):')
                for i, e in enumerate(new_errors):
                    log('  ' + e)
                    if gha and i < 20:
                        log('::error title=IDE compile check::%s' % e)
            if fixed:
                log('')
                log('Baseline entries that no longer occur (remove them from the baseline):')
                for i, e in enumerate(fixed):
                    log('  ' + e)
                    if gha and i < 10:
                        log('::warning title=IDE compile check::No longer occurs: %s' % e)

        passed = completed and (args.update_baseline or not new_errors)
        if problem:
            result = problem
        elif args.update_baseline:
            result = 'baseline rewritten with %d error(s)' % len(current)
        elif passed:
            result = 'passed: %d error(s), all in the baseline' % len(current)
        else:
            result = 'FAILED: %d new error(s)' % len(new_errors)

        if args.log:
            os.makedirs(os.path.dirname(os.path.abspath(args.log)), exist_ok=True)
            with open(args.log, 'w', encoding='utf-8', newline='\n') as f:
                f.write('\n'.join(['Layout: %s' % root, 'Engine: %s' % engine, 'Baseline: %s' % baseline, '']
                                  + out + ['', 'stderr:'] + err + ['', 'Result: %s' % result, '', 'New errors:']
                                  + new_errors + ['', 'Baseline entries that no longer occur:'] + fixed) + '\n')
        if completed:
            _append('GITHUB_OUTPUT', 'scripts=%s\nerrors=%d\nnew-errors=%d\nfixed=%d\n'
                    % (counts.get('scripts', ''), len(current), len(new_errors), len(fixed)))
        md = ['### IDE compile check (%s)' % p.name, '']
        if completed:
            md.append('%s files, %s objects, %s scripts compiled in %.0f s. %d error(s): %d in the baseline, '
                      '%d new; %d baseline entr%s no longer occur%s.'
                      % (counts.get('files'), counts.get('objects'), counts.get('scripts'), elapsed, len(current),
                         known, len(new_errors), len(fixed), 'y' if len(fixed) == 1 else 'ies',
                         's' if len(fixed) == 1 else ''))
        md += ['', 'Result: **%s**' % result, '']
        if new_errors:
            md += ['New errors:', '', '```text'] + new_errors[:50] + ['```', '']
        if fixed:
            md += ['No longer occurring (update `tools/ci/ide-compile-baseline.txt`):', '', '```text'] + \
                fixed[:50] + ['```', '']
        _append('GITHUB_STEP_SUMMARY', '\n'.join(md) + '\n')
        log('')
        if problem and gha:
            log('::error title=IDE compile check::%s' % problem)
        log('IDE compile check: %s (%.1f s).' % (result, elapsed))
        return 0 if passed else 1
    except CheckError as e:
        log('::error title=IDE compile check::%s' % e if gha else 'error: %s' % e)
        return 1
    finally:
        for d in temp_dirs:
            shutil.rmtree(d, ignore_errors=True)


def main(argv=None):
    ap = argparse.ArgumentParser(description='Run the headless LiveCode checks of tools/ci with a development '
                                             'engine (smoke test, IDE compile check).')
    sub = ap.add_subparsers(dest='command')
    sub.required = True
    for name in ('smoke', 'compile'):
        s = sub.add_parser(name)
        s.add_argument('--bin', dest='bin_dir', metavar='DIR', help='build output (development layout)')
        s.add_argument('--install', metavar='DIR', help='staged or installed LegacyOXT-<version> folder')
        s.add_argument('--package', metavar='FILE', help='package to extract and test')
        s.add_argument('--platform', choices=list(package.PLATFORMS),
                       help='layout names (default: this machine\'s, %s)' % default_platform())
        s.add_argument('--engine', metavar='FILE', help='the engine to run instead of the layout\'s')
        s.add_argument('--log', metavar='FILE', help='write the engine\'s output here')
        if name == 'smoke':
            s.add_argument('--timeout', type=int, default=300, help='seconds (default: %(default)s)')
        else:
            s.add_argument('--repo-layout', action='store_true',
                           help='check the repository\'s IDE (with the packaged extensions of --bin)')
            s.add_argument('--root', metavar='DIR', help='any folder with a Toolset (needs --engine)')
            s.add_argument('--baseline', metavar='FILE', help='default: tools/ci/ide-compile-baseline.txt')
            s.add_argument('--update-baseline', action='store_true', help='rewrite the baseline')
            s.add_argument('--baseline-source', metavar='TEXT', help='what was checked, for the baseline header')
            s.add_argument('--timeout', type=int, default=900, help='seconds (default: %(default)s)')
    args = ap.parse_args(argv)
    p = package.PLATFORMS[args.platform or default_platform()]
    if args.command == 'smoke':
        return cmd_smoke(args, p)
    return cmd_compile(args, p)


if __name__ == '__main__':
    sys.exit(main())
