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

"""Test the launcher and the per-user install of an extracted Linux package
of OpenXTalk-Lite (Installer/linux, staged by tools/oxt/package.py).

  python tools/ci/test_linux_package.py --root <OpenXTalk-Lite-<version> folder>
      [--launcher] [--install] [--work DIR] [--require-desktop-tools]

With neither --launcher nor --install, both run. <root> must be the
package as users get it (extracted from the tar.xz), in a neutral path.

--launcher runs a copy of the launcher (openxtalk-lite) in a scratch folder
next to a stand-in engine, a shell script that prints its arguments and
LIVECODE_USE_CEF, and a library list that is the package's plus, per case,
a library no system has:

  1. every library there: the engine gets the arguments unchanged (also
     through a symbolic link to the launcher, as ~/.local/bin has), and
     LIVECODE_USE_CEF stays unset;
  2. a missing "gui" library: exit status 127 before the engine, and the
     message names the library, its Debian and Fedora packages and
     OXT_BEYOND_SKIP_LIBRARY_CHECK;
  3. the same with -ui: the engine runs (no screen, no GUI library needed);
  4. OXT_BEYOND_SKIP_LIBRARY_CHECK=1: the engine runs;
  5. a missing "browser" library: the engine runs with LIVECODE_USE_CEF=0
     and the launcher names the library;
  6. LIVECODE_USE_CEF set by the user: kept;
  7. no Externals/CEF/libcef.so (a package without CEF): LIVECODE_USE_CEF=0;
  8. an engine that is not executable: an error that names it, which a
     launcher without a terminal but with a display shows with zenity (a
     stand-in that records its arguments) as plain text, --no-markup: the
     "<version>" in it is not Pango markup;
  9. an engine that is the launcher itself (what extracting onto a folder
     that does not tell upper from lower case leaves): an error, not a
     launcher that starts itself again and again.
The package's own list must pass on this machine (case 1), so run it where
the list's packages are installed.

--install runs install.sh and uninstall.sh with HOME (and XDG_DATA_HOME) in
a scratch folder, in four set-ups: the defaults (~/.local/share), an
XDG_DATA_HOME, an XDG_DATA_HOME whose path has a space, $, ", ` and %
(which the desktop entry's Exec line must quote), and (with
update-mime-database) the defaults with another program's MIME type
already compiled there, a database that uninstall.sh updates instead of
removing. Each time it checks that

  - install.sh installs the program folder with its manifest, the desktop
    entry (Exec is the launcher's absolute path, quoted per the Desktop
    Entry Specification; desktop-file-validate passes when it is there),
    the eight icons, the MIME types (and, with update-mime-database and
    update-desktop-database, the compiled types and the mimeinfo.cache
    entry), and ~/.local/bin/openxtalk-lite -> the launcher;
  - the installed engine runs a script without a user interface through
    that link and reports the installed folder as its own;
  - install.sh again, and the installed copy's install.sh, leave the same
    files;
  - uninstall.sh leaves exactly the files and folders that were there
    before install.sh, and files that install.sh did not make (a
    ~/.local/bin/openxtalk-lite of the user's) stay untouched;
  - install.sh refuses a program folder it did not install.

It also checks that a ~/.local/bin/openxtalk-lite that the user put in place of
install.sh's link (a wrapper script, or a link to something else) survives
install.sh again and uninstall.sh; and that an install that fails after
the program folder is in place (at a dangling ~/.local/bin link; an update
that cannot replace the desktop entry) keeps a manifest of what it and the
previous install made, so that uninstall.sh and a new install.sh still
work and uninstall.sh leaves exactly what was there before.

--require-desktop-tools makes a missing desktop-file-validate,
update-mime-database or update-desktop-database a failure (CI installs
desktop-file-utils and has shared-mime-info); without it they are skipped
with a note. --work is the scratch folder (default: a new one in
RUNNER_TEMP or the system's temporary folder), removed afterwards.

Exit status 0 when every check passes, 1 otherwise, 2 on a usage error.
Only the Python 3 standard library is used (3.8 or later).
"""

import argparse
import os
import shutil
import stat
import subprocess
import sys
import tempfile

ICON_SIZES = (16, 24, 32, 48, 64, 128, 256, 512)
MISSING_GUI = 'liboxt-test-missing-gui.so.7'
MISSING_BROWSER = 'liboxt-test-missing-browser.so.8'

FAKE_ENGINE = '''#!/bin/sh
printf 'ENGINE RAN:'
for a in "$@"; do printf ' [%s]' "$a"; done
printf '\\n'
printf 'LIVECODE_USE_CEF=%s\\n' "${LIVECODE_USE_CEF-unset}"
exit 0
'''

# A zenity that writes each argument to $OXT_TEST_ZENITY_LOG, followed by
# a line ZENITY_ARG_END (a message may have line breaks)
FAKE_ZENITY = '''#!/bin/sh
for a in "$@"; do printf '%s\\nZENITY_ARG_END\\n' "$a"; done > "$OXT_TEST_ZENITY_LOG"
exit 0
'''

# Another program's MIME type, of the application/ media type only: the
# text/ folder of OpenXTalk-Lite's text/x-oxtscript is then new
OTHER_MIME = '''<?xml version="1.0" encoding="UTF-8"?>
<mime-info xmlns="http://www.freedesktop.org/standards/shared-mime-info">
  <mime-type type="application/x-oxt-test-other">
    <comment>Another program's file type</comment>
    <glob pattern="*.oxt-test-other"/>
  </mime-type>
</mime-info>
'''

PROBE = '''script "oxt_install_probe"
on startup
   write "ENGINE " & specialFolderPath("engine") & return to stdout
   write "VERSION " & the version & return to stdout
   quit 0
end startup
'''


class Checks(object):
    def __init__(self):
        self.failed = 0
        self.passed = 0
        self.gha = os.environ.get('GITHUB_ACTIONS') == 'true'

    def check(self, name, ok, detail=''):
        """detail is shown for a failed check only (it is usually the
        output that shows why)."""
        if ok:
            self.passed += 1
            print('PASS %s' % name)
        else:
            self.failed += 1
            print('FAIL %s%s' % (name, ': ' + detail if detail else ''))
            if self.gha:
                print('::error title=Linux package test::%s%s' % (name, ': ' + detail.replace('\n', ' | ')
                                                                   if detail else ''))
        sys.stdout.flush()
        return ok


def run(cmd, env=None, cwd=None, timeout=600):
    """(exit status, stdout and stderr together)"""
    proc = subprocess.run(cmd, env=env, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          timeout=timeout)
    return proc.returncode, proc.stdout.decode('utf-8', 'replace')


def base_env(**extra):
    """The environment without the variables the launcher and install.sh
    read, and without a display: with one, the launcher would show its
    missing-library message in a dialog that waits for a click."""
    env = {k: v for k, v in os.environ.items()
           if k not in ('LIVECODE_USE_CEF', 'OXT_BEYOND_SKIP_LIBRARY_CHECK', 'XDG_DATA_HOME', 'DISPLAY',
                        'WAYLAND_DISPLAY')}
    env.update(extra)
    return env


def snapshot(top):
    """Every path below top, folders marked with a trailing "/" and links
    with their target, as a set."""
    out = set()
    for dirpath, dirnames, filenames in os.walk(top):
        rel = os.path.relpath(dirpath, top)
        for d in list(dirnames):
            p = os.path.join(dirpath, d)
            if os.path.islink(p):
                out.add('%s -> %s' % (os.path.normpath(os.path.join(rel, d)), os.readlink(p)))
                dirnames.remove(d)
            else:
                out.add(os.path.normpath(os.path.join(rel, d)) + '/')
        for f in filenames:
            p = os.path.join(dirpath, f)
            name = os.path.normpath(os.path.join(rel, f))
            out.add('%s -> %s' % (name, os.readlink(p)) if os.path.islink(p) else name)
    return out


def describe_diff(before, after):
    added = sorted(after - before)
    removed = sorted(before - after)
    parts = []
    if added:
        parts.append('left behind: ' + ', '.join(added[:10]) + (' ...' if len(added) > 10 else ''))
    if removed:
        parts.append('removed: ' + ', '.join(removed[:10]) + (' ...' if len(removed) > 10 else ''))
    return '; '.join(parts)


# ---------------------------------------------------------------------------
# Launcher

def test_launcher(c, root, work):
    base = os.path.join(work, 'launcher')
    app = os.path.join(base, 'app')
    os.makedirs(os.path.join(app, 'linux'))
    os.makedirs(os.path.join(app, 'Externals', 'CEF'))
    os.makedirs(os.path.join(base, 'bin'))
    launcher = os.path.join(app, 'openxtalk-lite')
    shutil.copy2(os.path.join(root, 'openxtalk-lite'), launcher)
    engine = os.path.join(app, 'OpenXTalk-Lite')
    with open(engine, 'w', encoding='utf-8', newline='\n') as f:
        f.write(FAKE_ENGINE)
    os.chmod(engine, 0o755)
    with open(os.path.join(root, 'linux', 'libraries.txt'), encoding='utf-8') as f:
        real_list = f.read()
    cef = os.path.join(app, 'Externals', 'CEF', 'libcef.so')
    open(cef, 'wb').close()
    link = os.path.join(base, 'bin', 'openxtalk-lite')
    os.symlink(launcher, link)

    def with_list(extra):
        with open(os.path.join(app, 'linux', 'libraries.txt'), 'w', encoding='utf-8', newline='\n') as f:
            f.write(real_list + extra)

    ran = 'ENGINE RAN: [a b.oxtstack] [--x] [$HOME]'
    with_list('')
    code, out = run([launcher, 'a b.oxtstack', '--x', '$HOME'], env=base_env())
    c.check('launcher: all libraries present, the engine runs with the arguments unchanged',
            code == 0 and ran in out and 'LIVECODE_USE_CEF=unset' in out, 'exit %d, output:\n%s' % (code, out))
    code, out = run([link, 'a b.oxtstack', '--x', '$HOME'], env=base_env(), cwd=work)
    c.check('launcher: started through a symbolic link, it finds the engine next to itself',
            code == 0 and ran in out, 'exit %d, output:\n%s' % (code, out))

    with_list('%s  gui  oxt-test-deb-package  oxt-test-rpm-package\n' % MISSING_GUI)
    code, out = run([link, 'x.oxtstack'], env=base_env())
    c.check('launcher: a missing gui library stops it before the engine, with exit status 127',
            code == 127 and 'ENGINE RAN' not in out, 'exit %d, output:\n%s' % (code, out))
    c.check('launcher: the message names the library, its packages and the way to start anyway',
            all(s in out for s in (MISSING_GUI, 'oxt-test-deb-package', 'OXT_BEYOND_SKIP_LIBRARY_CHECK')) and
            ('oxt-test-rpm-package' in out or 'apt install' in out),
            'output:\n%s' % out)
    code, out = run([link, '-ui', 'x.livecodescript'], env=base_env())
    c.check('launcher: with -ui a missing gui library does not matter',
            code == 0 and 'ENGINE RAN: [-ui] [x.livecodescript]' in out, 'exit %d, output:\n%s' % (code, out))
    code, out = run([link, 'x.oxtstack'], env=base_env(OXT_BEYOND_SKIP_LIBRARY_CHECK='1'))
    c.check('launcher: OXT_BEYOND_SKIP_LIBRARY_CHECK=1 starts the engine anyway',
            code == 0 and 'ENGINE RAN: [x.oxtstack]' in out, 'exit %d, output:\n%s' % (code, out))

    with_list('%s  browser  oxt-test-browser-deb  oxt-test-browser-rpm\n' % MISSING_BROWSER)
    code, out = run([link], env=base_env())
    c.check('launcher: a missing browser library turns the browser off (LIVECODE_USE_CEF=0) and says why',
            code == 0 and 'ENGINE RAN:' in out and 'LIVECODE_USE_CEF=0' in out and MISSING_BROWSER in out,
            'exit %d, output:\n%s' % (code, out))
    code, out = run([link], env=base_env(LIVECODE_USE_CEF='1'))
    c.check('launcher: a LIVECODE_USE_CEF the user set is kept',
            code == 0 and 'LIVECODE_USE_CEF=1' in out, 'exit %d, output:\n%s' % (code, out))

    with_list('')
    os.remove(cef)
    code, out = run([link], env=base_env())
    c.check('launcher: without Externals/CEF/libcef.so the browser is off (LIVECODE_USE_CEF=0)',
            code == 0 and 'LIVECODE_USE_CEF=0' in out, 'exit %d, output:\n%s' % (code, out))

    os.chmod(engine, 0o644)
    code, out = run([link], env=base_env())
    c.check('launcher: an engine that is not executable is an error that names it',
            code != 0 and 'OpenXTalk-Lite' in out and 'ENGINE RAN' not in out, 'exit %d, output:\n%s' % (code, out))

    # Started from a menu (no terminal, a display), the launcher shows the
    # message with zenity. zenity reads --text as Pango markup unless told
    # otherwise, and the "<version>" of this message is not valid markup:
    # the dialog would be empty.
    fakebin = os.path.join(base, 'fakebin')
    os.makedirs(fakebin)
    with open(os.path.join(fakebin, 'zenity'), 'w', encoding='utf-8', newline='\n') as f:
        f.write(FAKE_ZENITY)
    os.chmod(os.path.join(fakebin, 'zenity'), 0o755)
    zenity_log = os.path.join(base, 'zenity.log')
    code, out = run([link], env=base_env(DISPLAY=':99', OXT_TEST_ZENITY_LOG=zenity_log,
                                         PATH=fakebin + os.pathsep + os.environ.get('PATH', '')))
    args = []
    if os.path.isfile(zenity_log):
        with open(zenity_log, encoding='utf-8') as f:
            args = f.read().split('\nZENITY_ARG_END\n')[:-1]
    text = next((a[len('--text='):] for a in args if a.startswith('--text=')), None)
    c.check('launcher: without a terminal, zenity shows the message as plain text (--no-markup), unchanged',
            code != 0 and '--no-markup' in args and text is not None and '<version>' in text and text in out,
            'exit %d, zenity arguments %r, output:\n%s' % (code, args, out))

    # On FAT, exFAT or a Windows drive, tar writes the launcher openxtalk-lite
    # over the engine OpenXTalk-Lite, extracted just before it: the engine's
    # path is then the launcher, which would exec itself for ever
    os.remove(engine)
    os.link(launcher, engine)
    try:
        code, out = run([link], env=base_env(), timeout=30)
    except subprocess.TimeoutExpired:
        code, out = -1, 'still running after 30 s: the launcher starts itself again and again'
    c.check('launcher: an engine that is the launcher itself (a case-insensitive folder) is an error, not a loop',
            code == 1 and 'upper from lower case' in out, 'exit %d, output:\n%s' % (code, out))


# ---------------------------------------------------------------------------
# Install

def desktop_exec_path(line):
    """The program of an Exec value written as install.sh writes it: one
    double-quoted argument, then %f (Desktop Entry Specification: string
    escapes first, then the quoting rules)."""
    value = line[len('Exec='):]
    # string-level escapes
    out, i = [], 0
    while i < len(value):
        ch = value[i]
        if ch == '\\' and i + 1 < len(value):
            nxt = value[i + 1]
            out.append({'s': ' ', 'n': '\n', 't': '\t', 'r': '\r', '\\': '\\'}.get(nxt, '\\' + nxt))
            i += 2
        else:
            out.append(ch)
            i += 1
    value = ''.join(out)
    if not value.startswith('"'):
        return None, 'the program is not quoted'
    arg, i = [], 1
    while i < len(value):
        ch = value[i]
        if ch == '\\' and i + 1 < len(value) and value[i + 1] in '"`$\\':
            arg.append(value[i + 1])
            i += 2
        elif ch == '"':
            break
        else:
            arg.append(ch)
            i += 1
    else:
        return None, 'no closing quote'
    rest = value[i + 1:]
    if rest != ' %f':
        return None, 'after the program: %r, not " %%f"' % rest
    return ''.join(arg).replace('%%', '%'), None


def tool(name, required, c):
    path = shutil.which(name)
    if not path and required:
        c.check('%s is installed' % name, False, 'needed with --require-desktop-tools')
    return path


def other_mime_database(data):
    """A compiled MIME database of another program's type (OTHER_MIME) in
    data/mime: not install.sh's, since it has a mime.cache already.
    (ok, output of update-mime-database)"""
    mime = os.path.join(data, 'mime')
    os.makedirs(os.path.join(mime, 'packages'))
    with open(os.path.join(mime, 'packages', 'oxt-test-other.xml'), 'w', encoding='utf-8', newline='\n') as f:
        f.write(OTHER_MIME)
    code, out = run(['update-mime-database', mime])
    return (code == 0 and os.path.isfile(os.path.join(mime, 'mime.cache')) and
            os.path.isfile(os.path.join(mime, 'application', 'x-oxt-test-other.xml')) and
            not os.path.exists(os.path.join(mime, 'text'))), 'exit %d, output:\n%s' % (code, out)


def test_install_setup(c, root, work, label, xdg, required, prepare=None):
    """prepare(data folder) -> (ok, detail) sets up what is there before
    install.sh."""
    home = os.path.join(work, 'home-' + label)
    os.makedirs(home)
    # something of the user's that install.sh must leave alone
    os.makedirs(os.path.join(home, 'Documents'))
    with open(os.path.join(home, 'Documents', 'mine.oxtstack'), 'wb') as f:
        f.write(b'REVO7000')
    env = base_env(HOME=home)
    if xdg is not None:
        env['XDG_DATA_HOME'] = xdg
        os.makedirs(xdg, exist_ok=True)
    data = xdg if xdg is not None else os.path.join(home, '.local', 'share')
    app = os.path.join(data, 'openxtalk-lite')
    watch = [home] + ([xdg] if xdg is not None else [])
    name = 'install (%s)' % label
    if prepare is not None:
        ok, detail = prepare(data)
        if not c.check('%s: set-up' % name, ok, detail):
            return
    before = {w: snapshot(w) for w in watch}

    code, out = run([os.path.join(root, 'install.sh')], env=env)
    if not c.check('%s: install.sh succeeds' % name, code == 0, 'exit %d, output:\n%s' % (code, out)):
        return
    manifest = os.path.join(app, '.install-manifest')
    c.check('%s: the program folder with its manifest' % name,
            os.path.isfile(os.path.join(app, 'OpenXTalk-Lite')) and os.access(os.path.join(app, 'OpenXTalk-Lite'), os.X_OK)
            and os.path.isfile(manifest), app)
    same = run(['diff', '-rq', '--no-dereference', root, app])
    c.check('%s: the program folder is a copy of the package (plus the manifest)' % name,
            [x for x in same[1].splitlines() if not x.startswith('Only in %s: .install-manifest' % app)] == [],
            same[1][-2000:])
    modes_ok = all(stat.S_IMODE(os.stat(os.path.join(app, f)).st_mode) == 0o755
                   for f in ('OpenXTalk-Lite', 'openxtalk-lite', 'install.sh', 'uninstall.sh'))
    c.check('%s: the engine and the scripts keep mode 0755' % name, modes_ok)
    desktop = os.path.join(data, 'applications', 'openxtalk-lite.desktop')
    launcher = os.path.join(app, 'openxtalk-lite')
    exec_line = None
    if c.check('%s: the desktop entry' % name, os.path.isfile(desktop), desktop):
        with open(desktop, encoding='utf-8') as f:
            lines = f.read().splitlines()
        exec_line = next((x for x in lines if x.startswith('Exec=')), '')
        path, why = desktop_exec_path(exec_line)
        c.check('%s: its Exec line is the launcher, quoted, with %%f' % name, path == launcher,
                '%s -> %r%s' % (exec_line, path, ' (%s)' % why if why else ''))
        tryexec = next((x for x in lines if x.startswith('TryExec=')), '')
        c.check('%s: its TryExec is the launcher' % name,
                tryexec[len('TryExec='):].replace('\\\\', '\\') == launcher, tryexec)
        c.check('%s: it opens .oxtstack and .oxtscript' % name,
                'MimeType=application/x-oxtstack;text/x-oxtscript;' in lines)
        validate = tool('desktop-file-validate', required, c)
        if validate:
            code, vout = run([validate, desktop])
            c.check('%s: desktop-file-validate passes (no errors or warnings)' % name,
                    code == 0 and 'error:' not in vout and 'warning:' not in vout, vout.strip() or 'no output')
    icons = [os.path.join(data, 'icons', 'hicolor', '%dx%d' % (n, n), 'apps', 'openxtalk-lite.png') for n in ICON_SIZES]
    c.check('%s: the icons 16 to 512 px' % name, all(os.path.isfile(i) for i in icons),
            ', '.join(i for i in icons if not os.path.isfile(i)))
    c.check('%s: the MIME types' % name, os.path.isfile(os.path.join(data, 'mime', 'packages', 'openxtalk-lite.xml')))
    if tool('update-mime-database', required, c):
        c.check('%s: update-mime-database compiled the two types' % name,
                os.path.isfile(os.path.join(data, 'mime', 'application', 'x-oxtstack.xml')) and
                os.path.isfile(os.path.join(data, 'mime', 'text', 'x-oxtscript.xml')))
    if tool('update-desktop-database', required, c):
        cache = os.path.join(data, 'applications', 'mimeinfo.cache')
        text = open(cache, encoding='utf-8').read() if os.path.isfile(cache) else ''
        c.check('%s: mimeinfo.cache opens .oxtstack with OpenXTalk-Lite' % name,
                'application/x-oxtstack=openxtalk-lite.desktop;' in text, cache)
    link = os.path.join(home, '.local', 'bin', 'openxtalk-lite')
    c.check('%s: ~/.local/bin/openxtalk-lite links to the launcher' % name,
            os.path.islink(link) and os.readlink(link) == launcher, link)

    # The installed engine, through the link, without a user interface
    probe = os.path.join(work, 'probe-%s.livecodescript' % label)
    with open(probe, 'w', encoding='utf-8', newline='\n') as f:
        f.write(PROBE)
    code, out = run([link, '-ui', probe], env=env, cwd=home, timeout=120)
    reported = next((x[len('ENGINE '):] for x in out.splitlines() if x.startswith('ENGINE ')), None)
    c.check('%s: the installed engine runs through ~/.local/bin/openxtalk-lite and reports its folder' % name,
            code == 0 and reported is not None and os.path.realpath(reported) == os.path.realpath(app),
            'exit %d, engine folder %r, output:\n%s' % (code, reported, out))

    installed = {w: snapshot(w) for w in watch}
    code, out = run([os.path.join(root, 'install.sh')], env=env)
    again = {w: snapshot(w) for w in watch}
    c.check('%s: install.sh again replaces the install and leaves the same files' % name,
            code == 0 and again == installed,
            'exit %d; %s\n%s' % (code, '; '.join(describe_diff(installed[w], again[w]) for w in watch), out))
    code, out = run([os.path.join(app, 'install.sh')], env=env)
    again = {w: snapshot(w) for w in watch}
    c.check('%s: the installed copy\'s install.sh registers it in place, same files' % name,
            code == 0 and again == installed and 'already installed there' in out,
            'exit %d; %s\n%s' % (code, '; '.join(describe_diff(installed[w], again[w]) for w in watch), out))

    code, out = run([os.path.join(app, 'uninstall.sh')], env=env)
    after = {w: snapshot(w) for w in watch}
    c.check('%s: uninstall.sh succeeds' % name, code == 0, 'exit %d, output:\n%s' % (code, out))
    for w in watch:
        c.check('%s: after uninstall.sh, %s holds exactly what it held before install.sh' % (name, w),
                after[w] == before[w], describe_diff(before[w], after[w]))


def test_install_guards(c, root, work, required):
    """Files install.sh did not make stay, and a program folder it did not
    install is refused."""
    home = os.path.join(work, 'home-guards')
    bindir = os.path.join(home, '.local', 'bin')
    os.makedirs(bindir)
    mine = os.path.join(bindir, 'openxtalk-lite')
    with open(mine, 'w') as f:
        f.write('#!/bin/sh\necho mine\n')
    env = base_env(HOME=home)
    before = snapshot(home)
    code, out = run([os.path.join(root, 'install.sh')], env=env)
    c.check('guards: install.sh leaves a ~/.local/bin/openxtalk-lite of the user\'s alone, with a warning',
            code == 0 and 'left as it is' in out and not os.path.islink(mine), 'exit %d, output:\n%s' % (code, out))
    app = os.path.join(home, '.local', 'share', 'openxtalk-lite')
    code, out = run([os.path.join(app, 'uninstall.sh')], env=env)
    c.check('guards: uninstall.sh keeps it too, and removes the rest',
            code == 0 and snapshot(home) == before, 'exit %d; %s\n%s' % (code, describe_diff(before, snapshot(home)),
                                                                       out))
    os.makedirs(os.path.join(app, 'something'))
    before = snapshot(home)
    code, out = run([os.path.join(root, 'install.sh')], env=env)
    c.check('guards: install.sh refuses a program folder it did not install, and changes nothing',
            code != 0 and 'was not installed by install.sh' in out and snapshot(home) == before,
            'exit %d, output:\n%s' % (code, out))
    code, out = run([os.path.join(root, 'uninstall.sh')], env=env)
    c.check('guards: uninstall.sh refuses a folder without a manifest, and changes nothing',
            code != 0 and snapshot(home) == before, 'exit %d, output:\n%s' % (code, out))


def test_install_own_command(c, root, work):
    """A ~/.local/bin/openxtalk-lite that the user put in place of install.sh's
    link after an install (a wrapper script that sets GDK_SCALE for a HiDPI
    screen, or a link to something else) is theirs, although the manifest
    names the path: install.sh again and uninstall.sh leave it as it is."""
    for kind in ('wrapper', 'link'):
        home = os.path.join(work, 'home-own-' + kind)
        os.makedirs(home)
        env = base_env(HOME=home)
        before = snapshot(home)
        app = os.path.join(home, '.local', 'share', 'openxtalk-lite')
        command = os.path.join(home, '.local', 'bin', 'openxtalk-lite')
        name = 'own command (%s)' % kind
        code, out = run([os.path.join(root, 'install.sh')], env=env)
        if not c.check('%s: install.sh succeeds and links ~/.local/bin/openxtalk-lite' % name,
                       code == 0 and os.path.islink(command), 'exit %d, output:\n%s' % (code, out)):
            continue
        os.remove(command)
        if kind == 'wrapper':
            wrapper = '#!/bin/sh\nGDK_SCALE=2 exec "%s" "$@"\n' % os.path.join(app, 'openxtalk-lite')
            with open(command, 'w', encoding='utf-8', newline='\n') as f:
                f.write(wrapper)
            os.chmod(command, 0o755)
            mine = {'.local/', '.local/bin/', '.local/bin/openxtalk-lite'}
        else:
            os.symlink('/usr/bin/true', command)
            mine = {'.local/', '.local/bin/', '.local/bin/openxtalk-lite -> /usr/bin/true'}

        def unchanged():
            if kind == 'wrapper':
                if os.path.islink(command) or not os.path.isfile(command):
                    return False
                with open(command, encoding='utf-8') as f:
                    return f.read() == wrapper
            return os.path.islink(command) and os.readlink(command) == '/usr/bin/true'

        code, out = run([os.path.join(root, 'install.sh')], env=env)
        c.check('%s: install.sh again leaves it as it is, with a warning' % name,
                code == 0 and unchanged() and 'left as it is' in out, 'exit %d, output:\n%s' % (code, out))
        code, out = run([os.path.join(app, 'uninstall.sh')], env=env)
        after = snapshot(home)
        # its folders stay too, since they are not empty
        c.check('%s: uninstall.sh keeps it too, and removes the rest' % name,
                code == 0 and unchanged() and after == before | mine,
                'exit %d; %s\n%s' % (code, describe_diff(before | mine, after), out))


def test_install_failure(c, root, work):
    """An install that fails after the program folder is in place must not
    leave it without a manifest (install.sh would then refuse the folder,
    uninstall.sh refuse to run, and the desktop entry, icons and MIME file
    it had already installed would never be updated or removed): first a
    ~/.local/bin that is a dangling link, then an update that cannot
    replace the desktop entry."""
    home = os.path.join(work, 'home-failure')
    local = os.path.join(home, '.local')
    os.makedirs(local)
    binfolder = os.path.join(home, 'bin-folder')
    os.symlink(binfolder, os.path.join(local, 'bin'))    # dangling until bin-folder exists
    env = base_env(HOME=home)
    app = os.path.join(local, 'share', 'openxtalk-lite')
    manifest = os.path.join(app, '.install-manifest')
    install = os.path.join(root, 'install.sh')
    before = snapshot(home)

    code, out = run([install], env=env)
    c.check('failure: install.sh stops at a dangling ~/.local/bin, and says why',
            code != 0 and 'is not a folder' in out, 'exit %d, output:\n%s' % (code, out))
    c.check('failure: the program folder it put in place has a manifest', os.path.isfile(manifest), manifest)
    code, out = run([os.path.join(root, 'uninstall.sh')], env=env)
    after = snapshot(home)
    c.check('failure: uninstall.sh then removes everything the failed install.sh made',
            code == 0 and after == before, 'exit %d; %s\n%s' % (code, describe_diff(before, after), out))

    code, out = run([install], env=env)
    c.check('failure: install.sh fails the same way again', code != 0, 'exit %d, output:\n%s' % (code, out))
    os.makedirs(binfolder)
    fixed = before | {'bin-folder/'}
    code, out = run([install], env=env)
    c.check('failure: with the link fixed, install.sh takes over what the failed one made, without warnings',
            code == 0 and 'warning' not in out and os.path.islink(os.path.join(binfolder, 'openxtalk-lite')),
            'exit %d, output:\n%s' % (code, out))

    # An update that fails: the desktop entry can be neither removed nor
    # written
    apps = os.path.join(local, 'share', 'applications')
    desktop = os.path.join(apps, 'openxtalk-lite.desktop')
    os.chmod(desktop, 0o444)
    os.chmod(apps, 0o555)
    try:
        code, out = run([install], env=env)
    finally:
        os.chmod(apps, 0o755)
        os.chmod(desktop, 0o644)
    c.check('failure: an update that cannot replace the desktop entry fails, and keeps the manifest',
            code != 0 and 'cannot write' in out and os.path.isfile(manifest), 'exit %d, output:\n%s' % (code, out))
    code, out = run([os.path.join(app, 'uninstall.sh')], env=env)
    after = snapshot(home)
    c.check('failure: uninstall.sh after it leaves exactly what was there before the first install',
            code == 0 and after == fixed, 'exit %d; %s\n%s' % (code, describe_diff(fixed, after), out))


def main(argv=None):
    ap = argparse.ArgumentParser(description='Test the launcher and install scripts of a Linux OpenXTalk-Lite package.')
    ap.add_argument('--root', required=True, help='the extracted package folder (OpenXTalk-Lite-<version>)')
    ap.add_argument('--launcher', action='store_true', help='test the launcher')
    ap.add_argument('--install', action='store_true', help='test install.sh and uninstall.sh')
    ap.add_argument('--work', help='scratch folder (default: a new temporary one)')
    ap.add_argument('--require-desktop-tools', action='store_true',
                    help='fail when desktop-file-validate, update-mime-database or update-desktop-database is missing')
    args = ap.parse_args(argv)
    root = os.path.realpath(args.root)
    for f in ('OpenXTalk-Lite', 'openxtalk-lite', 'install.sh', 'uninstall.sh', 'linux/libraries.txt'):
        if not os.path.exists(os.path.join(root, f)):
            sys.stderr.write('error: %s has no %s: not an extracted Linux package\n' % (root, f))
            return 2
    if os.getuid() == 0:
        sys.stderr.write('error: run this as a normal user: install.sh refuses root\n')
        return 2
    both = not (args.launcher or args.install)
    base = args.work or os.environ.get('RUNNER_TEMP') or tempfile.gettempdir()
    os.makedirs(base, exist_ok=True)
    work = tempfile.mkdtemp(prefix='oxt-linux-test-', dir=base)
    c = Checks()
    try:
        if both or args.launcher:
            test_launcher(c, root, work)
        if both or args.install:
            test_install_setup(c, root, work, 'defaults', None, args.require_desktop_tools)
            test_install_setup(c, root, work, 'xdg', os.path.join(work, 'xdg-data'), args.require_desktop_tools)
            test_install_setup(c, root, work, 'odd-path',
                               os.path.join(work, 'data dir $HOME "q" `x` 100%'), args.require_desktop_tools)
            if tool('update-mime-database', args.require_desktop_tools, c):
                test_install_setup(c, root, work, 'other-mime-types', None, args.require_desktop_tools,
                                   prepare=other_mime_database)
            else:
                print('SKIP install (other-mime-types): no update-mime-database')
            test_install_guards(c, root, work, args.require_desktop_tools)
            test_install_own_command(c, root, work)
            test_install_failure(c, root, work)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    print('')
    print('SUMMARY passed=%d failed=%d' % (c.passed, c.failed))
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a', encoding='utf-8', newline='\n') as f:
            f.write('### Linux launcher and install scripts\n\n%s\n\n'
                    % ('All %d checks passed.' % c.passed if not c.failed
                       else '**%d of %d checks failed.**' % (c.failed, c.passed + c.failed)))
    return 1 if c.failed else 0


if __name__ == '__main__':
    sys.exit(main())
