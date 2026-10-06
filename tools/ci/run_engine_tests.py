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

"""Run LiveCode Community's engine test suites (tests/) against a build,
headless, on Windows, Linux or macOS, and compare the failures with a
baseline of known ones.

  python tools/ci/run_engine_tests.py --bin DIR [--repo DIR]
      [--suite lcs|lcb|compiler|parser]... [--filter REGEX]
      [--baseline FILE] [--arch ARCH] [--update-baseline] [--timeout SECONDS]
      [--symbols DIR]... [--log FILE]

--bin is a build output folder (win-x86_64-bin, linux-<arch>-bin, or on
macOS the Release folder of _build/mac): the standalone engine
(standalone-community, on macOS Standalone-Community.app), lc-compile,
lc-run and modules/lci. The tests come from the source checkout --repo (default: this
one).

The suites, as tests/Makefile names them:

  lcs       (lcs-check) every "on Test..." handler of the LiveCode Script
            tests in tests/lcs, each run on its own by the standalone engine
            with -ui through tests/_testrunner.livecodescript (invoke), as
            the test runner's own "run" does, but started from here (with
            --jobs N, the tests of N files at a time, those of the files in
            SERIAL_FILES one file after the other): the
            output is read from the engine's standard output directly (on
            Windows the runner's shell() through cmd.exe gets nothing from
            the engine, a GUI program), and each test has a time limit
            (--timeout). The LiveCode Builder modules the tests load are
            compiled first, in dependency order, to _tests/_build in the
            checkout, as the Makefile's lcm_compile does.
  lcb       (lcb-check) the LiveCode Builder tests of tests/lcb (the
            virtual machine, the standard library and compiled code): each
            "Test..." handler of each module, run on its own by lc-run with
            the test library tests/_testlib.lcb, as tests/_testrunner.lcb
            does (which does not run on Windows).
  compiler  (compiler-check) tests/_compilertestrunner.livecodescript run:
            the LiveCode Builder compiler tests of tests/lcb/compiler.
  parser    (lcs-parser-check) tests/_parsertestrunner.livecodescript run:
            the LiveCode Script parser tests.

A test's result is its worst TAP result, as the test runner counts them:
FAIL (a "not ok", or the engine ending with a non-zero status, crashing or
running out of time), XPASS, XFAIL, PASS, SKIP; a LiveCode Script test
that skipped all but the test runner's check that its script compiles is
SKIP. Each failed test is named
"<suite>: <test>", for example "lcs: core/engine/put:
TestPutBeforeIntoAfterInvalidContainer", and looked up in the baseline:
tools/ci/engine-tests-baseline.txt (failures on every platform), the
platform family's engine-tests-baseline-<windows|linux|mac>.txt next to
it, and engine-tests-baseline-<family>-<arch>.txt for the failures on one
processor architecture, x86_64, arm64 or x86 (--arch, by default this
computer's). A failure in none of them is new, and fails the check (exit
status 1); a baseline entry that passed is reported, so that it can be
removed, but is not an error (some tests depend on timing); an entry
marked "? " fails only sometimes, and its passing is not reported. A
LiveCode Script test that fails and is not in the baseline is run again
(--retries, default once); when it passes then, it is reported as flaky
and does not fail the check. --update-baseline rewrites the family's file
with the failures that are in neither the shared one nor the
architecture's.

Some tests act on the desktop of the computer they run on: they launch
Notepad or TextEdit and then end every running copy of it, write a file to
the Desktop, or replace what is on the system clipboard (INTRUSIVE). They
run in CI (when the environment variable CI is "true", as in GitHub
Actions) or with --desktop-tests, and are reported as skipped elsewhere, so
that running the suites on a developer's computer leaves their work alone.

When the engine (or lc-run) crashes, the test is run once more under a
debugger to log
a stack trace: on Windows with tools/ci/win_crashtrace.py and the .pdb
files of --symbols (default: --bin), on Linux with gdb and on macOS with
lldb when they are installed (the .dbg files or .dSYM bundles next to the
binaries name the frames).

The log (--log, default _tests/engine-tests.log in the checkout) has every
failed test's output. In GitHub Actions a summary is added to the job
summary. Exit status: 0 when no test failed that is not in the baseline,
1 when one did, 2 when the suites could not be run.
"""

import argparse
import concurrent.futures
import os
import platform
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
BASELINE = os.path.join(HERE, 'engine-tests-baseline.txt')
SUITES = ('lcs', 'lcb', 'compiler', 'parser')
RESULT_ORDER = ('fail', 'xpass', 'xfail', 'pass', 'skip')
ANSI = re.compile(r'\x1b\[[0-9;]*[A-Za-z]|\x1b\(B')
RUNNER_LINE = re.compile(r'^(PASS|FAIL|XFAIL|XPASS|SKIP):\s+(.*?)\s+\[\d+/\d+\]\s*$')
TEST_HANDLER = re.compile(r'^\s*on\s+([^\s,;()"\[\]]+)', re.IGNORECASE)

# LiveCode Script test files whose tests must not run beside other tests
# with --jobs: they use fixed ports, the system clipboard, programs that they
# start and end, or files and registry keys of fixed names. (prefixes of
# the file's name in tests/lcs)
SERIAL_FILES = (
    'core/network/',        # ports 8001 and 8008 to 8010
    'liburl/',              # outside hosts, and their time limits
    'core/engine/clipboard',
    'core/chunks/cut',      # the clipboard
    'core/files/files',     # Notepad or TextEdit, Test.txt on the Desktop, the registry
)

# Tests that act on the desktop of the computer they run on (see the
# docstring), as patterns matched against "<suite>: <test>".
INTRUSIVE = (
    # launch "notepad.exe" (TextEdit on macOS), then taskkill /IM notepad.exe:
    # ends every Notepad, the user's too; TestLaunchWith writes Test.txt on
    # the Desktop
    r'^lcs: core/files/files: TestLaunch(With)?$',
    # set the clipboardData and the fullClipboardData
    r'^lcs: core/engine/clipboard: ',
    r'^lcs: core/chunks/cut: TestCutVar$',
)


class SuiteError(Exception):
    pass


def log(msg=''):
    print(msg)
    sys.stdout.flush()


def host_family():
    if sys.platform.startswith('win'):
        return 'windows'
    if sys.platform == 'darwin':
        return 'mac'
    return 'linux'


def host_arch():
    """This computer's processor architecture, as the builds name it."""
    machine = platform.machine().lower()
    if machine in ('amd64', 'x86_64', 'x64'):
        return 'x86_64'
    if machine in ('arm64', 'aarch64'):
        return 'arm64'
    if machine in ('i386', 'i486', 'i586', 'i686', 'x86'):
        return 'x86'
    return machine


def tools_in(bin_dir, family):
    exe = '.exe' if family == 'windows' else ''
    if family == 'mac':
        engine = os.path.join(bin_dir, 'Standalone-Community.app', 'Contents', 'MacOS', 'Standalone-Community')
    else:
        engine = os.path.join(bin_dir, 'standalone-community' + exe)
    tools = {'engine': engine,
             'lc-compile': os.path.join(bin_dir, 'lc-compile' + exe),
             'lc-run': os.path.join(bin_dir, 'lc-run' + exe),
             'modules': os.path.join(bin_dir, 'modules', 'lci')}
    missing = [p for p in tools.values() if not os.path.exists(p)]
    if missing:
        raise SuiteError('not in the build output: %s' % ', '.join(missing))
    return tools


def lc_path(path):
    """A path as LiveCode scripts write it (forward slashes)."""
    return os.path.abspath(path).replace('\\', '/')


def linux_jvm_dir():
    """The folder of a JDK's libjvm.so on Linux (from JAVA_HOME, else from
    javac on the PATH), or None."""
    home = os.environ.get('JAVA_HOME')
    if not home:
        javac = shutil.which('javac')
        if javac is None:
            return None
        # <home>/bin/javac
        home = os.path.dirname(os.path.dirname(os.path.realpath(javac)))
    # lib/server since Java 9, jre/lib/<arch>/server in Java 8
    for sub in [os.path.join('lib', 'server')] + \
            [os.path.join('jre', 'lib', a, 'server') for a in ('amd64', 'aarch64', 'arm', 'i386')]:
        folder = os.path.join(home, sub)
        if os.path.isfile(os.path.join(folder, 'libjvm.so')):
            return folder
    return None


def child_env():
    env = dict(os.environ)
    # Headless: -ui needs no display, and a test must not reach one.
    for var in ('DISPLAY', 'WAYLAND_DISPLAY'):
        env.pop(var, None)
    # On Linux the engine loads Java as "libjvm.so", which it finds only on
    # the library path (libfoundation/src/foundation-java-private.cpp), as a
    # user of the engine's Java support sets it up: without it the Java
    # tests fail with "Could not initialise Java Runtime Environment"
    if sys.platform.startswith('linux'):
        jvm = linux_jvm_dir()
        if jvm is not None:
            env['LD_LIBRARY_PATH'] = os.pathsep.join(
                [jvm] + [x for x in env.get('LD_LIBRARY_PATH', '').split(os.pathsep) if x])
    return env


def kill_tree(proc):
    try:
        if os.name == 'nt':
            subprocess.run(['taskkill', '/F', '/T', '/PID', str(proc.pid)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            os.killpg(proc.pid, signal.SIGKILL)
    except (OSError, ProcessLookupError):
        pass
    try:
        proc.kill()
    except OSError:
        pass


def run_process(cmd, cwd, env, timeout):
    """(exit status or None after a timeout, stdout text, stderr text).

    The output goes to temporary files, not pipes: a program the test starts
    (launch, open process) inherits the engine's handles and can outlive it,
    and a pipe would then never reach its end."""
    kwargs = {}
    if os.name == 'nt':
        kwargs['creationflags'] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs['start_new_session'] = True
    with tempfile.TemporaryFile() as out_file, tempfile.TemporaryFile() as err_file:
        try:
            proc = subprocess.Popen(cmd, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                    stdout=out_file, stderr=err_file, **kwargs)
        except OSError as e:
            raise SuiteError('cannot run %s: %s' % (cmd[0], e))
        try:
            status = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            kill_tree(proc)
            proc.wait()
            status = None
        out_file.seek(0)
        err_file.seek(0)
        return (status, out_file.read().decode('utf-8', 'replace'),
                err_file.read().decode('utf-8', 'replace'))


def describe_status(status):
    if status is None:
        return 'ran out of time'
    if status < 0:
        try:
            name = signal.Signals(-status).name
        except ValueError:
            name = 'signal %d' % -status
        return 'killed by %s' % name
    if os.name == 'nt' and status >= 0xC0000000:
        return 'crashed with exception 0x%08X' % status
    return 'exited with status %d' % status


def crashed(status):
    if status is None:
        return False
    if status < 0:
        return -status in (signal.SIGSEGV, signal.SIGABRT, signal.SIGBUS, signal.SIGILL, signal.SIGFPE)
    return os.name == 'nt' and status >= 0xC0000000


def analyse_tap(text):
    """Count the TAP results of text as _testerlib.livecodescript's
    TesterTapAnalyse does; returns (counts, failure lines)."""
    counts = dict((k, 0) for k in RESULT_ORDER)
    failures = []
    for line in text.splitlines():
        words = line.split()
        if not words:
            continue
        if words[0].lower() == 'ok' or words[0].lower().startswith('ok-'):
            okay = True
        elif words[0].lower() == 'not' and len(words) > 1 and words[1].lower().startswith('ok'):
            okay = False
        else:
            continue
        tail = line[line.index('#') + 1:] if '#' in line else line
        directive = (tail.split() or [''])[0].upper()
        todo, skip = directive == 'TODO', directive == 'SKIP'
        if okay:
            counts['xpass' if todo else 'skip' if skip else 'pass'] += 1
        elif todo:
            counts['xfail'] += 1
        else:
            counts['fail'] += 1
            failures.append(line.strip())
    return counts, failures


def worst(counts):
    for key in RESULT_ORDER:
        if counts.get(key):
            return key
    return 'pass'


def outcome_of(counts, text):
    """The result of a test that printed text: its worst TAP result, but
    SKIP for a LiveCode Script test whose only pass is the test runner's
    own check that the script compiles (a TestSkipIf in the TestSetup,
    for example, skips all of the test after that)."""
    outcome = worst(counts)
    if outcome == 'pass' and counts['skip'] and \
            counts['pass'] == len(re.findall(r'(?m)^ok - script compiles\s*$', text)):
        return 'skip'
    return outcome


class Result:
    def __init__(self, suite, name, outcome, counts=None, output='', details=None):
        self.suite = suite
        self.name = name
        self.outcome = outcome      # one of RESULT_ORDER
        self.counts = counts or {}
        self.output = output
        self.details = details or []

    @property
    def ident(self):
        return '%s: %s' % (self.suite, self.name)


# ---------------------------------------------------------------------------
# Preparation: the LiveCode Builder modules the tests load

def compile_test_modules(tools, repo, log_lines):
    """Compile every .lcb under tests/ to _tests/_build/<path>.lcm, in
    dependency order, as tests/Makefile's lcm_compile does. Returns the
    sources that did not compile."""
    tests = os.path.join(repo, 'tests')
    build = os.path.join(repo, '_tests', '_build')
    os.makedirs(build, exist_ok=True)
    sources = []
    for root, dirs, files in os.walk(tests):
        dirs.sort()
        for f in sorted(files):
            if f.endswith('.lcb'):
                sources.append(os.path.relpath(os.path.join(root, f), tests).replace('\\', '/'))
    base = [tools['lc-compile'], '--modulepath', build, '--modulepath', tools['modules']]
    status, out, err = run_process(base + ['--deps', 'order', '--'] + sources, tests, child_env(), 300)
    order = [x.strip().replace('\\', '/') for x in out.splitlines() if x.strip()]
    if status != 0 or not order:
        log_lines.append('lc-compile --deps order failed (%s): %s' % (describe_status(status), err.strip()))
        order = sources
    # --deps order names the files as given; keep any it left out at the end
    order += [s for s in sources if s not in order]
    failed = []
    for src in order:
        if src not in sources:
            continue
        out_file = os.path.join(build, os.path.splitext(src)[0] + '.lcm')
        os.makedirs(os.path.dirname(out_file), exist_ok=True)
        status, out, err = run_process(base + ['--output', out_file, '--', src], tests, child_env(), 300)
        if status != 0:
            failed.append(src)
            log_lines.append('lc-compile %s: %s' % (src, describe_status(status)))
            log_lines.extend('    ' + x for x in (out + err).splitlines() if x.strip())
    return failed


# ---------------------------------------------------------------------------
# The suites

def lcs_tests(root, pattern):
    """(test file, handler) pairs in the order the test runner finds them:
    .livecodescript files whose names do not start with "_" or ".", in
    folders not starting with ".", and in each the "on Test..." handlers
    except TestSetup and TestTearDown (TesterParseTestCommandNames)."""
    tests = []
    for folder, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if not d.startswith('.'))
        for f in sorted(files):
            if not f.endswith('.livecodescript') or f.startswith(('_', '.')):
                continue
            path = os.path.join(folder, f)
            pretty = os.path.relpath(path, root).replace('\\', '/')[:-len('.livecodescript')]
            seen = set()
            with open(path, encoding='utf-8-sig', errors='replace') as fh:
                for line in fh:
                    m = TEST_HANDLER.match(line)
                    if not m or not m.group(1).lower().startswith('test'):
                        continue
                    name = m.group(1)
                    if name.lower() in ('testsetup', 'testteardown'):
                        continue
                    if name.lower() in seen:
                        raise SuiteError('duplicate test name %s in %s' % (name, path))
                    seen.add(name.lower())
                    ident = '%s: %s' % (pretty, name)
                    if pattern and not re.search(pattern, 'lcs: ' + ident):
                        continue
                    tests.append((path, name, ident))
    return tests


def crash_trace(cmd, cwd, family, symbol_dirs, timeout):
    """Run cmd again under a debugger and return its report lines."""
    if family == 'windows':
        tracer = [sys.executable, os.path.join(HERE, 'win_crashtrace.py'), '--timeout', str(timeout)]
        for d in symbol_dirs:
            tracer += ['--symbols', d]
        full = tracer + ['--'] + cmd
    elif family == 'linux' and shutil.which('gdb'):
        full = ['gdb', '-batch', '-q', '-nx', '-ex', 'set pagination off', '-ex', 'run',
                '-ex', 'bt 40', '--args'] + cmd
    elif family == 'mac' and shutil.which('lldb'):
        full = ['lldb', '--batch', '-o', 'run', '-k', 'bt 40', '-k', 'quit 1', '--'] + cmd
    else:
        return ['(no debugger to trace the crash with)']
    status, out, err = run_process(full, cwd, child_env(), timeout + 60)
    text = err if family == 'windows' else out + err
    keep = []
    for line in text.splitlines():
        s = line.rstrip()
        if family == 'windows' and not (s.startswith('win_crashtrace:') or s.startswith('  #')):
            continue
        if family != 'windows' and not re.match(r'\s*(#\d+|\*?\s*frame #\d+|thread #|Program received|'
                                                 r'Process \d+ stopped|stop reason)', s):
            continue
        keep.append(s)
    return keep or ['(the crash did not happen again under the debugger: %s)' % describe_status(status)]


def run_test_process(cmd, tests_dir, env, timeout):
    """Run one test command; returns (status, TAP text, details, seconds)."""
    started = time.monotonic()
    status, out, err = run_process(cmd, tests_dir, env, timeout)
    took = time.monotonic() - started
    text = out
    details = []
    if status != 0:
        text += '\nnot ok # Subprocess %s\n' % describe_status(status)
        details.append('the engine %s after %.0f s' % (describe_status(status), took))
        if err.strip():
            details.append('stderr:')
            details.extend('    ' + x for x in err.splitlines() if x.strip())
    return status, text, details, took


def run_test(suite, ident, cmd, cwd, env, args, known, family, symbol_dirs, out=None):
    """Run one test (a process that prints TAP) and return its Result. A
    failure that is not known, unless the test ran out of time, is run
    again (--retries): one that passes then is reported as flaky, with the
    first run's output, and does not fail the check. A crash that stays is
    run under a debugger. The result line goes to out (a list) when given,
    else to the console."""
    status, text, details, took = run_test_process(cmd, cwd, env, args.timeout)
    counts, failures = analyse_tap(text)
    outcome = outcome_of(counts, text)
    flaky = None
    # (not one that ran out of time: that would most likely take as long again)
    if outcome == 'fail' and status is not None and '%s: %s' % (suite, ident) not in known:
        first = (text, details, failures)
        for _ in range(args.retries):
            status, text, details, took = run_test_process(cmd, cwd, env, args.timeout)
            counts, failures = analyse_tap(text)
            outcome = outcome_of(counts, text)
            if outcome != 'fail':
                flaky = first
                break
    if outcome == 'fail' and crashed(status):
        details.append('stack trace (run again under a debugger):')
        details.extend('    ' + x for x in crash_trace(cmd, cwd, family, symbol_dirs, args.timeout))
    result = Result(suite, ident, outcome, counts, text, details)
    result.failures = failures
    if flaky is not None:
        result.flaky = True
        result.details = ['failed on the first run, passed on the second; the first run:'] + \
            flaky[1] + [flaky[0].rstrip()]
        result.failures = flaky[2]
    tally = sum(counts.values())
    line = '%-5s %s%s [%d/%d]%s%s' % (outcome.upper(), '' if suite == 'lcs' else suite + ': ', ident,
                                      counts['pass'] + counts['xfail'] + counts['skip'], tally,
                                      '' if took < 30 else ' (%.0f s)' % took,
                                      ' (flaky: failed the first time)' if flaky is not None else '')
    if out is None:
        log(line)
    else:
        out.append(line)
    return result


def run_lcs(tools, repo, family, args, symbol_dirs, known):
    tests_dir = os.path.join(repo, 'tests')
    runner = lc_path(os.path.join(tests_dir, '_testrunner.livecodescript'))
    tests = lcs_tests(os.path.join(tests_dir, 'lcs'), args.filter)
    log('LiveCode Script tests: %d in %s' % (len(tests), lc_path(os.path.join(tests_dir, 'lcs'))))
    env = child_env()
    desktop_tests = args.desktop_tests or os.environ.get('CI', '').lower() == 'true'

    def run_file(file_tests, out):
        file_results = []
        for path, name, ident in file_tests:
            if not desktop_tests and any(re.search(x, 'lcs: ' + ident) for x in INTRUSIVE):
                result = Result('lcs', ident, 'skip', {'skip': 1},
                                'ok # SKIP acts on this computer\'s desktop (run with --desktop-tests)\n')
                result.failures = []
                file_results.append(result)
                line = 'SKIP  %s (acts on the desktop; --desktop-tests runs it)' % ident
                if out is None:
                    log(line)
                else:
                    out.append(line)
                continue
            cmd = [tools['engine'], '-ui', runner, 'invoke', lc_path(path), name]
            file_results.append(run_test('lcs', ident, cmd, tests_dir, env, args, known, family,
                                         symbol_dirs, out))
        return file_results

    if args.jobs <= 1:
        return run_file(tests, None)

    # With --jobs, the files run side by side, the tests of a file one after
    # the other; the files of SERIAL_FILES run one after the other in one of
    # the workers, so that none of them runs beside another. A file's lines
    # are printed when it is done, and the results keep the order of a run
    # without --jobs.
    files = []
    for test in tests:
        name = test[2].split(': ', 1)[0]
        if not files or files[-1][0] != name:
            files.append((name, []))
        files[-1][1].append(test)
    serial = [f for f in files if f[0].startswith(SERIAL_FILES)]

    def run_serial(out):
        return dict((name, run_file(file_tests, out)) for name, file_tests in serial)

    by_file = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
        running = {}
        if serial:
            out = []
            running[pool.submit(run_serial, out)] = (None, out)
        for name, file_tests in files:
            if not name.startswith(SERIAL_FILES):
                out = []
                running[pool.submit(run_file, file_tests, out)] = (name, out)
        for future in concurrent.futures.as_completed(running):
            name, out = running[future]
            if name is None:
                by_file.update(future.result())
            else:
                by_file[name] = future.result()
            for line in out:
                log(line)
    return [r for name, _ in files for r in by_file[name]]


def run_lcb(tools, repo, family, args, symbol_dirs, known):
    """The LiveCode Builder tests of tests/lcb, as tests/Makefile's
    lcb-check runs them, but each handler in its own lc-run from here:
    tests/_testrunner.lcb does the same but refuses to run on Windows."""
    tests_dir = os.path.join(repo, 'tests')
    build = os.path.join(repo, '_tests', '_build')
    testlib = os.path.join(build, '_testlib.lcm')
    root = os.path.join(tests_dir, 'lcb')
    env = child_env()
    modules = []
    for folder, dirs, files in os.walk(root):
        dirs.sort()
        for f in sorted(files):
            if f.endswith('.lcb'):
                modules.append(os.path.relpath(os.path.join(folder, f), tests_dir).replace('\\', '/')[:-4])
    log('LiveCode Builder tests: %d modules in %s' % (len(modules), lc_path(root)))
    results = []
    for module in modules:
        lcm = os.path.join(build, module + '.lcm')
        if not os.path.isfile(lcm) or not os.path.isfile(testlib):
            ident = '%s: (the module)' % module
            if not args.filter or re.search(args.filter, 'lcb: ' + ident):
                results.append(Result('lcb', ident, 'fail', {'fail': 1}, 'not ok # the module did not compile\n'))
                results[-1].failures = ['not ok # the module did not compile']
                log('FAIL  lcb: %s' % ident)
            continue
        status, out, err = run_process([tools['lc-run'], '-l', testlib, '--list-handlers', lcm],
                                       tests_dir, env, args.timeout)
        handlers = [h.strip() for h in out.splitlines() if h.strip().lower().startswith('test')]
        if status != 0:
            ident = '%s: (the module)' % module
            if not args.filter or re.search(args.filter, 'lcb: ' + ident):
                text = 'not ok # lc-run --list-handlers %s\n' % describe_status(status)
                results.append(Result('lcb', ident, 'fail', {'fail': 1}, text + err))
                results[-1].failures = [text.strip()]
                log('FAIL  lcb: %s' % ident)
            continue
        for handler in handlers:
            ident = '%s: %s' % (module, handler)
            if args.filter and not re.search(args.filter, 'lcb: ' + ident):
                continue
            cmd = [tools['lc-run'], '-l', testlib, '--handler', handler, lcm]
            results.append(run_test('lcb', ident, cmd, tests_dir, env, args, known, family, symbol_dirs))
    return results


def run_runner_suite(suite, tools, repo, args):
    tests_dir = os.path.join(repo, 'tests')
    script = {'compiler': '_compilertestrunner.livecodescript',
              'parser': '_parsertestrunner.livecodescript'}[suite]
    env = child_env()
    if suite == 'compiler':
        env['LC_COMPILE'] = os.path.abspath(tools['lc-compile'])
    env['TEST_SUITE_LOG_FILE'] = os.path.join(repo, '_tests', '_%s_test_suite.log' % suite)
    cmd = [tools['engine'], '-ui', lc_path(os.path.join(tests_dir, script)), 'run']
    status, out, err = run_process(cmd, tests_dir, env, max(args.timeout, 600))
    results = []
    current = None
    for line in ANSI.sub('', out).splitlines():
        m = RUNNER_LINE.match(line.strip())
        if not m:
            # The runner prints a failed test's TAP lines after its result
            if current is not None and line.strip():
                current.output += line.rstrip() + '\n'
                if line.lstrip().startswith('not ok'):
                    current.failures.append(line.strip())
            continue
        current = None
        name = m.group(2).strip()
        # The runner names a test "<file without extension>: <test>", the
        # file as given: make it relative to tests/
        prefix = lc_path(tests_dir) + '/'
        if name.startswith(prefix):
            name = name[len(prefix):]
        if name.startswith(': '):
            name = name[2:]
        if args.filter and not re.search(args.filter, '%s: %s' % (suite, name)):
            continue
        outcome = m.group(1).lower()
        result = Result(suite, name, outcome)
        result.failures = []
        results.append(result)
        if outcome in ('fail', 'xpass'):
            current = result
        log('%-5s %s: %s' % (outcome.upper(), suite, name))
    # The runner exits with 1 when a test failed; anything else, or no
    # results at all, is the runner itself failing.
    if status not in (0, 1) or not results:
        details = ['the %s test runner %s with %d result(s)' % (suite, describe_status(status), len(results))]
        details += ['    ' + x for x in (out + err).splitlines() if x.strip()][-40:]
        results.append(Result(suite, '(the test runner)', 'fail', details=details))
        log('FAIL  %s: (the test runner) %s' % (suite, describe_status(status)))
    return results


# ---------------------------------------------------------------------------
# Baselines and reports

def read_baseline(path):
    """{"<suite>: <test>": may it pass} of a baseline file: a line is a
    test, "? " before it marks one that fails only sometimes (its passing is
    not reported), text after " #" is a comment."""
    entries = {}
    if not os.path.isfile(path):
        return entries
    with open(path, encoding='utf-8') as f:
        for line in f:
            text = line.split(' #', 1)[0].strip()
            if not text or text.startswith('#'):
                continue
            sometimes = text.startswith('?')
            if sometimes:
                text = text[1:].strip()
            entries[text] = sometimes
    return entries


def family_baseline(baseline, family):
    stem, ext = os.path.splitext(baseline)
    return '%s-%s%s' % (stem, family, ext)


def write_family_baseline(path, family, failing, shared, previous):
    """Rewrite the family's baseline: the failures not in the shared one,
    and the entries marked "?" (may pass) that it had, with their comments."""
    lines = ['# Engine test suite (tools/ci/run_engine_tests.py): tests known to fail on %s' % family,
             '# that are not in engine-tests-baseline.txt (the failures on every platform).',
             '# One "<suite>: <test>" per line; "? " before it: fails only sometimes;',
             '# text after " #" is a comment.', '']
    keep = set(failing) | set(k for k, v in previous.items() if v[0])
    for ident in sorted(keep - set(shared)):
        sometimes, comment = previous.get(ident, (False, None))
        lines.append(('? ' if sometimes else '') + ident + ('  # ' + comment if comment else ''))
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        f.write('\n'.join(lines) + '\n')


def baseline_entries(path):
    """{"<suite>: <test>": (sometimes, comment)} of a baseline file."""
    entries = {}
    if os.path.isfile(path):
        with open(path, encoding='utf-8') as f:
            for line in f:
                if not line.strip() or line.lstrip().startswith('#'):
                    continue
                ident, _, comment = line.partition(' #')
                ident = ident.strip()
                sometimes = ident.startswith('?')
                if sometimes:
                    ident = ident[1:].strip()
                entries[ident] = (sometimes, comment.strip() or None)
    return entries


def append_summary(markdown):
    path = os.environ.get('GITHUB_STEP_SUMMARY')
    if path:
        with open(path, 'a', encoding='utf-8', newline='\n') as f:
            f.write(markdown + '\n')


def main(argv=None):
    ap = argparse.ArgumentParser(description='Run the engine test suites of tests/ against a build and compare '
                                             'the failures with a baseline.')
    ap.add_argument('--bin', dest='bin_dir', required=True, metavar='DIR', help='build output folder')
    ap.add_argument('--repo', default=REPO, metavar='DIR', help='source checkout (default: %(default)s)')
    ap.add_argument('--suite', action='append', choices=SUITES, help='suite to run (repeatable; default: all)')
    ap.add_argument('--filter', metavar='REGEX', help='run only the tests whose "<suite>: <test>" matches')
    ap.add_argument('--baseline', default=BASELINE, metavar='FILE', help='default: %(default)s')
    ap.add_argument('--update-baseline', action='store_true',
                    help='rewrite the platform family\'s baseline with the failures not in the shared one')
    ap.add_argument('--timeout', type=int, default=300, metavar='SECONDS',
                    help='time limit of one test (default: %(default)s)')
    ap.add_argument('--symbols', action='append', default=[], metavar='DIR',
                    help='Windows: folders with the .pdb files for crash traces (default: --bin)')
    ap.add_argument('--log', metavar='FILE', help='default: _tests/engine-tests.log in the checkout')
    ap.add_argument('--jobs', type=int, default=1, metavar='N',
                    help='run the LiveCode Script tests of N files at a time (default: %(default)s)')
    ap.add_argument('--retries', type=int, default=1, metavar='N',
                    help='run a failed test that is not in the baseline again, at most N times '
                         '(default: %(default)s; 0 never)')
    ap.add_argument('--desktop-tests', action='store_true',
                    help='also run the tests that act on this computer\'s desktop (always run in CI)')
    ap.add_argument('--arch', default=host_arch(), metavar='ARCH',
                    help='processor architecture of the build, which picks its baseline '
                         '(default: this computer\'s, %(default)s)')
    args = ap.parse_args(argv)

    family = host_family()
    repo = os.path.abspath(args.repo)
    suites = args.suite or list(SUITES)
    log_path = os.path.abspath(args.log or os.path.join(repo, '_tests', 'engine-tests.log'))
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    symbol_dirs = [os.path.abspath(d) for d in args.symbols] or [os.path.abspath(args.bin_dir)]
    log_lines = []
    started = time.monotonic()
    shared_path = os.path.abspath(args.baseline)
    family_path = family_baseline(shared_path, family)
    arch_path = family_baseline(shared_path, '%s-%s' % (family, args.arch))
    shared = read_baseline(shared_path)
    arch_known = read_baseline(arch_path)
    known = dict(shared)
    known.update(read_baseline(family_path))
    known.update(arch_known)
    try:
        tools = tools_in(os.path.abspath(args.bin_dir), family)
        log('Engine   : %s' % tools['engine'])
        log('Checkout : %s' % repo)
        log('Platform : %s %s' % (family, args.arch))
        results = []
        if 'lcs' in suites or 'lcb' in suites:
            failed = compile_test_modules(tools, repo, log_lines)
            log('Compiled the test modules to %s%s' % (
                lc_path(os.path.join(repo, '_tests', '_build')),
                '' if not failed else '; %d did not compile: %s' % (len(failed), ', '.join(failed))))
        if 'lcs' in suites:
            results += run_lcs(tools, repo, family, args, symbol_dirs, known)
        if 'lcb' in suites:
            results += run_lcb(tools, repo, family, args, symbol_dirs, known)
        for suite in ('compiler', 'parser'):
            if suite in suites:
                results += run_runner_suite(suite, tools, repo, args)
    except SuiteError as e:
        log('error: %s' % e)
        append_summary('### Engine tests\n\nThe suites could not be run: %s' % e)
        return 2

    failing = [r for r in results if r.outcome == 'fail']
    flaky = [r for r in results if getattr(r, 'flaky', False)]
    passed = set(r.ident for r in results if r.outcome in ('pass', 'xpass', 'xfail'))
    new = [r for r in failing if r.ident not in known]
    fixed = sorted(k for k, sometimes in known.items() if k in passed and not sometimes)
    totals = dict((k, sum(1 for r in results if r.outcome == k)) for k in RESULT_ORDER)

    with open(log_path, 'w', encoding='utf-8', newline='\n') as f:
        f.write('Engine: %s\nCheckout: %s\nBaselines: %s, %s, %s\n\n' % (tools['engine'], repo, shared_path,
                                                                         family_path, arch_path))
        for line in log_lines:
            f.write(line + '\n')
        for r in results:
            if r.outcome in ('fail', 'xpass') or getattr(r, 'flaky', False):
                f.write('\n### %s %s%s\n' % ('FLAKY' if getattr(r, 'flaky', False) else r.outcome.upper(),
                                              r.ident, '' if r.ident not in known else ' (in the baseline)'))
                for line in r.details:
                    f.write(line + '\n')
                if r.output:
                    f.write(r.output.rstrip() + '\n')
        f.write('\n%s\n' % ', '.join('%d %s' % (totals[k], k) for k in RESULT_ORDER))

    elapsed = time.monotonic() - started
    log('')
    log('%d tests in %.0f s: %s' % (len(results), elapsed, ', '.join('%d %s' % (totals[k], k)
                                                                      for k in RESULT_ORDER)))
    log('Failures: %d in the baseline, %d new' % (len(failing) - len(new), len(new)))
    for r in new:
        log('  NEW   %s' % r.ident)
        for line in getattr(r, 'failures', [])[:10] + r.details[:30]:
            log('        %s' % line)
    # what a "? " entry failed with: without it a test that fails every time
    # behind its "sometimes" mark goes unnoticed, and so does how it failed
    sometimes = [r for r in failing if known.get(r.ident)]
    if sometimes:
        log('Failed, in the baseline as failing only sometimes:')
        for r in sometimes:
            log('  ?     %s' % r.ident)
            for line in getattr(r, 'failures', [])[:5]:
                log('        %s' % line)
    if flaky:
        log('Flaky (failed, then passed when run again; not counted as failures):')
        for r in flaky:
            log('  FLAKY %s' % r.ident)
            for line in r.failures[:5]:
                log('        %s' % line)
    if fixed:
        log('Baseline entries that passed (remove them from the baseline):')
        for ident in fixed:
            log('  FIXED %s' % ident)
    log('Log: %s' % log_path)

    if args.update_baseline:
        # The failures of one architecture stay in its own file.
        write_family_baseline(family_path, family, [r.ident for r in failing],
                              list(shared) + list(arch_known), baseline_entries(family_path))
        log('Wrote %s' % family_path)

    md = ['### Engine tests (%s %s)' % (family, args.arch), '',
          '%d tests in %.0f s: %s.' % (len(results), elapsed, ', '.join('%d %s' % (totals[k], k)
                                                                        for k in RESULT_ORDER)),
          '%d failure(s) in the baseline, **%d new**.' % (len(failing) - len(new), len(new))]
    if new:
        md += ['', 'New failures:', '', '```text']
        for r in new:
            md.append(r.ident)
            md += ['    ' + x for x in (getattr(r, 'failures', [])[:5] + r.details[:12])]
        md.append('```')
    if flaky:
        md += ['', 'Flaky (failed, then passed when run again):', '', '```text']
        for r in flaky:
            md.append(r.ident)
            md += ['    ' + x for x in r.failures[:5]]
        md.append('```')
    if fixed:
        md += ['', 'Baseline entries that passed (remove them from the baseline):', '', '```text'] + fixed + ['```']
    append_summary('\n'.join(md))
    return 1 if new and not args.update_baseline else 0


if __name__ == '__main__':
    sys.exit(main())
