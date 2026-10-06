#!/usr/bin/env python3
"""Test the install scripts of an extracted Linux LegacyOXT package.

  python3 tools/ci/test_linux_install.py --root <LegacyOXT-<version> folder>
      [--require-desktop-tools]

Runs install.sh and uninstall.sh as a user does, in scratch home folders
(HOME and XDG_DATA_HOME are set for each run; nothing outside the scratch
folder is touched):

  default   install.sh with no argument: the program folder in
            ~/.runrev/components/livecodecommunity-<version>.<arch>, where
            LiveCode's installer put a user's copy, with the marker file
            install.sh writes; LiveCode's desktop entry (from
            Installer/application.desktop) in ~/.local/share/applications
            with its Exec line pointing at the installed engine, which
            desktop-file-validate accepts (its warnings, about LiveCode's
            own keys, are reported, not failures); the icon in
            ~/.local/share/icons/hicolor/48x48/apps. The engine named by
            the Exec line runs (-ui, a script that writes the version) and
            reports the package's version. The installed uninstall.sh then
            removes all of it.
  folder    install.sh with a folder whose name has spaces and an
            XDG_DATA_HOME of its own; then uninstall.sh.
  refused   install.sh refuses a folder name with a "$", and a folder that
            exists but was not installed by install.sh (which stays as it
            was); uninstall.sh in the extracted package (not an installed
            copy) refuses and removes nothing.

--require-desktop-tools fails when desktop-file-validate is not installed
(the CI installs it); without it, that check is skipped.

Exit status 0 when every check passed, 1 otherwise. Only the Python 3
standard library is used.
"""

import argparse
import glob
import os
import re
import shutil
import subprocess
import sys
import tempfile


class Checks(object):
    def __init__(self):
        self.failed = []
        self.count = 0

    def check(self, name, ok, detail=''):
        self.count += 1
        print('  [%s] %s%s' % ('ok  ' if ok else 'FAIL', name, ' (%s)' % detail if detail else ''))
        if not ok:
            self.failed.append(name + (': ' + detail if detail else ''))
        return ok


def run(cmd, env, cwd=None, timeout=600):
    r = subprocess.run(cmd, env=env, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout)
    return r.returncode, r.stdout.decode('utf-8', 'replace')


def scratch_env(home, data=None):
    env = {k: v for k, v in os.environ.items()
           if k not in ('XDG_DATA_HOME', 'XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'DISPLAY', 'WAYLAND_DISPLAY')}
    env['HOME'] = home
    if data:
        env['XDG_DATA_HOME'] = data
    return env


def exec_path(entry_text):
    """The program of a desktop entry's Exec line ("<path>" %U)."""
    m = re.search(r'^Exec=(.*)$', entry_text, re.M)
    if not m:
        return None
    value = m.group(1).strip()
    q = re.match(r'^"((?:[^"\\]|\\.)*)"', value)
    if q:
        return re.sub(r'\\(.)', r'\1', q.group(1))
    return value.split()[0]


def engine_version(engine, work, env):
    """What the engine says "the version" is, run headless."""
    script = os.path.join(work, 'version.livecodescript')
    with open(script, 'w', encoding='utf-8') as f:
        f.write('script "version"\non startup\n   write the version & return to stdout\n   quit 0\nend startup\n')
    code, out = run([engine, '-ui', script], env, cwd=work, timeout=120)
    return code, out.strip()


def test_install(c, root, work, label, dest_arg, data, validate, version, names):
    home = os.path.join(work, label, 'home')
    os.makedirs(home)
    env = scratch_env(home, data)
    data_dir = data or os.path.join(home, '.local', 'share')
    dest = dest_arg or os.path.join(home, '.runrev', 'components', names['install'])
    print('%s: install.sh %s' % (label, '"%s"' % dest_arg if dest_arg else '(default folder)'))
    code, out = run(['sh', os.path.join(root, 'install.sh')] + ([dest_arg] if dest_arg else []), env, cwd=work)
    print('\n'.join('    ' + l for l in out.splitlines()))
    if not c.check('%s: install.sh exits 0' % label, code == 0, 'exit %d' % code):
        return
    engine = os.path.join(dest, names['engine'])
    c.check('%s: the engine is installed and executable' % label, os.access(engine, os.X_OK), engine)
    c.check('%s: install.sh wrote its marker' % label, os.path.isfile(os.path.join(dest, '.legacyoxt-install')))
    c.check('%s: Toolset is installed' % label, os.path.isfile(os.path.join(dest, 'Toolset', 'home.livecodescript')))
    entry = os.path.join(data_dir, 'applications', names['desktop'] + '.desktop')
    icon = os.path.join(data_dir, 'icons', 'hicolor', '48x48', 'apps', names['desktop'] + '.png')
    c.check('%s: the icon is installed' % label, os.path.isfile(icon), icon)
    if c.check('%s: the desktop entry is installed' % label, os.path.isfile(entry), entry):
        with open(entry, encoding='utf-8') as f:
            text = f.read()
        c.check('%s: no package.txt variable left in it' % label, '[[' not in text)
        prog = exec_path(text)
        c.check('%s: its Exec line starts the installed engine' % label, prog == engine, '%s' % prog)
        c.check('%s: its Icon is %s' % (label, names['desktop']),
                re.search(r'^Icon=%s$' % re.escape(names['desktop']), text, re.M) is not None)
        if validate:
            code, out = run([validate, entry], env)
            errors = [l for l in out.splitlines() if 'error:' in l]
            warnings = [l for l in out.splitlines() if 'error:' not in l and l.strip()]
            for w in warnings:
                print('    desktop-file-validate (LiveCode\'s entry, not a failure): %s' % w.strip())
            c.check('%s: desktop-file-validate finds no error' % label, code == 0 and not errors,
                    '; '.join(errors) or 'exit %d' % code)
        if prog and os.access(prog, os.X_OK):
            code, got = engine_version(prog, os.path.join(work, label), env)
            c.check('%s: the engine of the Exec line runs and reports %s' % (label, version),
                    code == 0 and got == version, 'exit %s, %r' % (code, got[-200:]))
    print('%s: uninstall.sh of the installed copy' % label)
    code, out = run(['sh', os.path.join(dest, 'uninstall.sh')], env, cwd=work)
    print('\n'.join('    ' + l for l in out.splitlines()))
    c.check('%s: uninstall.sh exits 0' % label, code == 0, 'exit %d' % code)
    c.check('%s: the installed folder is gone' % label, not os.path.exists(dest), dest)
    c.check('%s: the desktop entry is gone' % label, not os.path.exists(entry))
    c.check('%s: the icon is gone' % label, not os.path.exists(icon))


def test_refusals(c, root, work, names):
    home = os.path.join(work, 'refused', 'home')
    os.makedirs(home)
    env = scratch_env(home)
    print('refused: install.sh with awkward or occupied folders')
    bad = os.path.join(work, 'refused', 'price$5')
    code, out = run(['sh', os.path.join(root, 'install.sh'), bad], env, cwd=work)
    c.check('refused: a folder name with "$"', code != 0 and not os.path.exists(bad), out.strip().splitlines()[-1:] and
            out.strip().splitlines()[-1])
    taken = os.path.join(work, 'refused', 'taken')
    os.makedirs(taken)
    keep = os.path.join(taken, 'mine.txt')
    with open(keep, 'w') as f:
        f.write('mine\n')
    code, out = run(['sh', os.path.join(root, 'install.sh'), taken], env, cwd=work)
    c.check('refused: a folder that install.sh did not make', code != 0 and os.path.isfile(keep)
            and not os.path.exists(os.path.join(taken, names['engine'])), 'exit %d' % code)
    before = sorted(os.listdir(root))
    code, out = run(['sh', os.path.join(root, 'uninstall.sh')], env, cwd=work)
    c.check('refused: uninstall.sh in the extracted package', code != 0 and sorted(os.listdir(root)) == before,
            'exit %d' % code)


def main(argv=None):
    ap = argparse.ArgumentParser(description='Test the install scripts of an extracted Linux LegacyOXT package.')
    ap.add_argument('--root', required=True, help='the extracted package folder (LegacyOXT-<version>)')
    ap.add_argument('--require-desktop-tools', action='store_true',
                    help='fail when desktop-file-validate is not installed')
    args = ap.parse_args(argv)
    root = os.path.abspath(args.root)
    c = Checks()

    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'oxt'))
    import package
    version = package.LIVECODE_VERSION
    engines = [os.path.basename(p) for p in glob.glob(os.path.join(root, package.LIVECODE_NAME + '.*'))]
    if not c.check('the package has one engine named "%s.<arch>"' % package.LIVECODE_NAME, len(engines) == 1,
                   ', '.join(engines) or 'none'):
        return 1
    arch = engines[0].rsplit('.', 1)[1]
    values = package.linux_script_values(arch)
    names = {'engine': values['ENGINE'], 'install': values['INSTALL_NAME'], 'desktop': values['DESKTOP_NAME']}
    for f in ('install.sh', 'uninstall.sh'):
        c.check('%s is executable' % f, os.access(os.path.join(root, f), os.X_OK))
    validate = shutil.which('desktop-file-validate')
    if not validate:
        c.check('desktop-file-validate is installed', not args.require_desktop_tools,
                'not found; the entry is not validated')

    work = tempfile.mkdtemp(prefix='legacyoxt-install-test-')
    try:
        test_install(c, root, work, 'default', None, None, validate, version, names)
        test_install(c, root, work, 'folder', os.path.join(work, 'folder', 'My Apps', 'LiveCode'),
                     os.path.join(work, 'folder', 'xdg data'), validate, version, names)
        test_refusals(c, root, work, names)
    finally:
        shutil.rmtree(work, ignore_errors=True)

    print()
    if c.failed:
        print('Install test failed: %d of %d checks:' % (len(c.failed), c.count))
        for f in c.failed:
            print('  - ' + f)
        if os.environ.get('GITHUB_ACTIONS') == 'true':
            for f in c.failed:
                print('::error title=Linux install test::' + f)
        return 1
    print('Install test passed (%d checks).' % c.count)
    return 0


if __name__ == '__main__':
    sys.exit(main())
