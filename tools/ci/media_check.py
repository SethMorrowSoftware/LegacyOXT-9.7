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

"""Check that the browser widget, revBrowser and the player work in a
standalone of an installed LiveCode Community, with a user interface.

  python tools/ci/media_check.py (--install DIR | --package FILE)
      [--platform P] [--what widget,revbrowser,player] [--log FILE]
      [--snapshots DIR] [--timeout SECONDS]

The layout is one of run_livecode_check.py's (--install, or --package to
extract first). Its own platform's standalone runtime (standalone_check.py
RUNTIMES) is copied to a folder of its own, laid out as a standalone the
IDE builds: on Windows and Linux the engine with Externals (CEF included)
and the CEF helper processes next to it, on macOS the Standalone*.app.
That engine then runs tools/ci/media-check.livecodescript, which

  - loads the browser widget from the layout's Extensions, opens a page
    this script serves on 127.0.0.1, and checks that the page loads, that
    its JavaScript calls a handler of the script (javascriptHandlers) and
    that the widget's htmlText is the page after its JavaScript ran;
  - does the same with revBrowser (revBrowserOpenCef; on macOS
    revBrowserOpen), loaded from the runtime's Externals by a stack's
    externals property, as in a standalone;
  - plays a WAV file this script writes and the files of tools/ci/media:
    oxt-check.mp4 (H.264 and AAC), oxt-check-silent.mp4 (H.264 only),
    oxt-check.avi (Motion JPEG and PCM) and oxt-check-silent.avi (Motion
    JPEG only), 3 seconds each: duration and
    timeScale, currentTime moving on while it plays, pausing, and
    playStopped at the end. The files without sound tell a player that
    cannot open a format from one that cannot play sound (a machine with
    no sound device). What a platform is known not to play (KNOWN) is
    reported without failing the check;
  - on Linux, clicks the controller the engine draws below mplayer's
    video: its play button plays and pauses, a click in its well seeks,
    and the snapshots of what it draws show which. (LiveCode's Linux
    player draws no controller and runs none of mplayer's commands:
    KNOWN and KNOWN_NO_CONTROLLER report those failures without failing
    the check.) With --snapshots those
    snapshots, and the screen with the video, are written to DIR (and,
    under GitHub Actions, into the log as base64).

Each of the three runs in an engine of its own, as an application would
use one of them, so that one cannot hide a failure of another (the
browser widget and revBrowser load the same CEF).

On Linux the engine runs under xvfb-run when there is no DISPLAY, and the
player needs mplayer (/usr/bin/mplayer, as the engine runs it). On a
machine without a sound card (no /dev/snd) mplayer is given MPLAYER_HOME
with "ao=null", so that it plays in real time instead of skipping to the
end of a file it cannot play the sound of.

Exit status 0 when every check passed, 1 otherwise. Under GitHub Actions
failures are annotations and the results go into the job summary. Only
the Python 3 standard library is used (3.8 or later).
"""

import argparse
import base64
import functools
import http.server
import math
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import wave

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), 'oxt'))
sys.path.insert(0, HERE)
import package  # noqa: E402
import run_livecode_check as rlc  # noqa: E402
import standalone_check  # noqa: E402

SCRIPT = os.path.join(HERE, 'media-check.livecodescript')
MEDIA = os.path.join(HERE, 'media')
VIDEOS = ('oxt-check.mp4', 'oxt-check-silent.mp4', 'oxt-check.avi', 'oxt-check-silent.avi')

# Files a platform's player is known not to play, with the reason: their
# failures are reported (KNOWN) but do not fail the check, and a file that
# plays after all is reported so that it can come off the list.
KNOWN = {
    # DirectShow (engine/src/w32-ds-player.cpp) has no reader for MP4: the
    # player says "could not create movie reference" unless a DirectShow
    # filter for it (LAV Filters, a codec pack) is installed
    'windows': {
        'oxt-check.mp4': 'Windows: DirectShow cannot open MP4 without a third-party filter',
        'oxt-check-silent.mp4': 'Windows: DirectShow cannot open MP4 without a third-party filter',
    },
    # LiveCode 9.7's Linux player (engine/src/
    # lnxmplayer.cpp), which writes mplayer's slave commands without the
    # newline that ends each one, so mplayer runs none of them and plays
    # every file to its end whatever the script does
    'linux': dict((name, "Linux: this tree's player sends mplayer its commands without the newline "
                         "that ends them, so mplayer runs none: duration and currentTime stay 0 and "
                         "pause does nothing (fixed in OXT-Beyond dd9dd938c)")
                  for name in ('oxt-check.wav',) + VIDEOS),
}
# Platforms whose player draws no controller, with the reason: the
# failures of the controller checks ("player controller (<file>): ...")
# are reported (KNOWN) but do not fail the check
KNOWN_NO_CONTROLLER = {
    'linux': "Linux: this tree's player has no controller, showController does nothing "
             "(OXT-Beyond e9c28e90e added one)",
}
# Without a sound device (a CI runner) Windows cannot play a file that is
# only sound
NO_SOUND_DEVICE = {'oxt-check.wav': 'Windows without a sound device (as on the CI runners) does not open it'}
MPLAYER = '/usr/bin/mplayer'

# The page the browsers open: media-check.livecodescript looks for its
# title, for "script ran: 3" in its HTML and for the oxtReport call
PAGE = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>OXT media check</title></head>
<body><h1 id="h">waiting for the script</h1>
<script>
document.getElementById('h').textContent = 'script ran: ' + (1 + 2);
setTimeout(function () {
  if (window.liveCode && liveCode.oxtReport) liveCode.oxtReport('loaded', navigator.userAgent);
}, 300);
</script>
</body></html>
"""


def gha():
    return bool(os.environ.get('GITHUB_ACTIONS'))


def write_tone(path, seconds=3.0, rate=44100):
    """A 440 Hz sine, 16-bit mono PCM."""
    frames = bytearray()
    for i in range(int(seconds * rate)):
        frames += struct.pack('<h', int(12000 * math.sin(2 * math.pi * 440 * i / rate)))
    with wave.open(path, 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(bytes(frames))


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


def serve(folder):
    """An HTTP server for folder on 127.0.0.1, in a thread; (server, port)."""
    handler = functools.partial(_QuietHandler, directory=folder)
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, server.server_address[1]


def stage_runtime(tools, p, work):
    """Copy the layout's own standalone runtime into work as a standalone
    has it; (engine executable, revbrowser external)."""
    runtime = os.path.join(tools, *standalone_check.RUNTIMES[standalone_check.own_target(p)].split('/'))
    if not os.path.exists(runtime):
        raise rlc.CheckError('no standalone runtime at %s' % runtime)
    folder = os.path.dirname(runtime)
    if p.family == 'mac':
        app = os.path.join(work, 'OXTMediaCheck.app')
        shutil.copytree(runtime, app, symlinks=True)
        return (os.path.join(app, *standalone_check.MAC_EXE.split('/')),
                os.path.join(folder, 'Externals', 'revbrowser.bundle'))
    app = os.path.join(work, 'OXTMediaCheck')
    shutil.copytree(folder, app, symlinks=True)
    name = os.path.basename(runtime)
    if p.family == 'windows':
        os.rename(os.path.join(app, name), os.path.join(app, 'OXTMediaCheck.exe'))
        return os.path.join(app, 'OXTMediaCheck.exe'), os.path.join(app, 'Externals', 'revbrowser.dll')
    return os.path.join(app, name), os.path.join(app, 'Externals', 'revbrowser.so')


def windows_sound_devices():
    """The names of the sound devices Windows has (Win32_SoundDevice)."""
    try:
        out = subprocess.run(['powershell', '-NoProfile', '-Command',
                              '(Get-CimInstance Win32_SoundDevice | ForEach-Object { $_.Name }) -join "; "'],
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=60).stdout
        return out.decode('utf-8', 'replace').strip() or 'none'
    except (OSError, subprocess.TimeoutExpired) as e:
        return 'unknown (%s)' % e


def main(argv=None):
    ap = argparse.ArgumentParser(description='Check the browser widget, revBrowser and the player in a standalone '
                                             'of an installed LiveCode Community.')
    ap.add_argument('--install', metavar='DIR', help='installed layout')
    ap.add_argument('--package', metavar='FILE', help='a package to extract and check like --install')
    ap.add_argument('--platform', choices=list(package.PLATFORMS),
                    help='the layout (default: this machine\'s, %s)' % rlc.default_platform())
    ap.add_argument('--what', default='widget,revbrowser,player',
                    help='comma-separated: widget, revbrowser, player (default: %(default)s)')
    ap.add_argument('--must-play', metavar='FILES', default='',
                    help='comma-separated media files that must play although KNOWN lists them (for example the '
                         'MP4 files on Windows with LAV Filters installed)')
    ap.add_argument('--log', metavar='FILE', help='write the results here too')
    ap.add_argument('--snapshots', metavar='DIR', help='write the snapshots of the Linux player\'s controller here')
    ap.add_argument('--timeout', type=int, default=240, help='seconds for the engine run (default: %(default)s)')
    args = ap.parse_args(argv)
    if bool(args.install) == bool(args.package):
        ap.error('pass --install or --package')
    p = package.PLATFORMS[args.platform or rlc.default_platform()]
    if standalone_check.own_target(p) not in standalone_check.RUNTIMES:
        ap.error('%s has no standalone runtime of its own' % p.name)
    what = [w.strip() for w in args.what.split(',') if w.strip()]
    if p.name.startswith('linux-') and p.name != 'linux-x86_64':
        # CEF is in the x86_64 Linux package only
        what = [w for w in what if w == 'player']

    lines, problems, temp_dirs, server = [], [], [], None
    try:
        base = os.environ.get('RUNNER_TEMP') or tempfile.gettempdir()
        work = tempfile.mkdtemp(prefix='oxt-media-', dir=base)
        temp_dirs.append(work)
        args.bin_dir, args.engine = None, None
        lay = rlc.find_layout(args, p, temp_dirs)
        rlc.log('Layout  : %s' % lay.root)
        engine, revbrowser = stage_runtime(lay.tools, p, work)
        rlc.log('Engine  : %s' % engine)

        site = os.path.join(work, 'site')
        os.makedirs(site)
        with open(os.path.join(site, 'index.html'), 'w', encoding='utf-8', newline='\n') as f:
            f.write(PAGE)
        server, port = serve(site)
        media = os.path.join(work, 'media')
        os.makedirs(media)
        write_tone(os.path.join(media, 'oxt-check.wav'))
        for name in VIDEOS:
            shutil.copy(os.path.join(MEDIA, name), media)
        files = [os.path.join(media, n).replace('\\', '/') for n in ('oxt-check.wav',) + VIDEOS]

        env = dict(os.environ)
        env.update({
            'OXT_MEDIA_URL': 'http://127.0.0.1:%d/index.html' % port,
            'OXT_MEDIA_FILES': ','.join(files),
            'OXT_MEDIA_EXTENSIONS': os.path.join(lay.tools, 'Extensions').replace('\\', '/'),
            'OXT_MEDIA_REVBROWSER': revbrowser.replace('\\', '/'),
        })
        if args.snapshots:
            os.makedirs(args.snapshots, exist_ok=True)
            env['OXT_MEDIA_SNAPSHOTS'] = os.path.abspath(args.snapshots).replace('\\', '/')
        cmd = [engine, SCRIPT]
        if p.family == 'linux':
            if 'player' in what:
                if not os.path.exists(MPLAYER):
                    problems.append('the player needs %s, which is not installed' % MPLAYER)
                if not os.path.exists('/dev/snd'):
                    home = os.path.join(work, 'mplayer')
                    os.makedirs(home)
                    with open(os.path.join(home, 'config'), 'w') as f:
                        f.write('ao=null\n')
                    env['MPLAYER_HOME'] = home
                    rlc.log('No sound card: mplayer plays without sound (ao=null)')
            if not env.get('DISPLAY'):
                xvfb = shutil.which('xvfb-run')
                if not xvfb:
                    raise rlc.CheckError('no DISPLAY and no xvfb-run')
                cmd = [xvfb, '-a', '-s', '-screen 0 1280x1024x24'] + cmd
        known = dict(KNOWN.get(p.family, {}))
        for name in args.must_play.split(','):
            known.pop(name.strip(), None)
        if p.family == 'windows' and 'player' in what:
            devices = windows_sound_devices()
            lines.append('INFO sound devices: %s' % devices)
            if devices == 'none':
                known.update(NO_SOUND_DEVICE)
        rlc.log('Running : %s' % ' '.join(cmd))
        for part in what:
            result_log = os.path.join(work, 'media-check-%s.log' % part)
            env['OXT_MEDIA_LOG'] = result_log
            env['OXT_MEDIA_WHAT'] = part
            try:
                proc = subprocess.run(cmd, cwd=work, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                      timeout=args.timeout)
                code, output = proc.returncode, proc.stdout
            except subprocess.TimeoutExpired as e:
                code, output = None, e.stdout or b''
            results = []
            if os.path.exists(result_log):
                with open(result_log, encoding='utf-8', errors='replace') as f:
                    results = [x.rstrip('\r\n') for x in f if x.strip()]
            finished = any(x.startswith('DONE ') for x in results)
            results = [x for x in results if not x.startswith('DONE ') and not (x.startswith('INFO platform=')
                                                                              and part != what[0])]
            for name, reason in sorted(known.items()):
                mine = [x for x in results if x.split(' ', 1)[-1].startswith('player %s:' % name)]
                if mine and not any(x.startswith('FAIL ') for x in mine):
                    lines.append('INFO player %s plays, though it is listed as known not to (%s)' % (name, reason))
                results = ['KNOWN' + x[4:] + ' [%s]' % reason if x in mine and x.startswith('FAIL ') else x
                           for x in results]
            if p.family in KNOWN_NO_CONTROLLER:
                reason = KNOWN_NO_CONTROLLER[p.family]
                mine = [x for x in results if x.split(' ', 1)[-1].startswith('player controller (')]
                if mine and not any(x.startswith('FAIL ') for x in mine):
                    lines.append('INFO the player controller works, though it is listed as known not to (%s)'
                                 % reason)
                results = ['KNOWN' + x[4:] + ' [%s]' % reason if x in mine and x.startswith('FAIL ') else x
                           for x in results]
            lines += results
            problems += [x[5:] for x in results if x.startswith('FAIL ')]
            if not finished:
                problems.append('%s: the engine did not finish (%s)'
                                % (part, 'timed out after %d seconds' % args.timeout if code is None
                                   else 'exit status %s' % code))
                tail = output.decode('utf-8', 'replace').splitlines()[-30:]
                if tail:
                    rlc.log('Engine output (%s, last lines):' % part)
                    for x in tail:
                        rlc.log('  ' + x)
    except (rlc.CheckError, OSError) as e:
        problems.append(str(e))
    finally:
        if server:
            server.shutdown()
        for d in temp_dirs:
            shutil.rmtree(d, ignore_errors=True)

    if args.snapshots and gha() and os.path.isdir(args.snapshots):
        names = sorted(os.listdir(args.snapshots))
        if names:
            rlc.log('::group::Snapshots (base64)')
            for name in names:
                with open(os.path.join(args.snapshots, name), 'rb') as f:
                    rlc.log('%s %s' % (name, base64.b64encode(f.read()).decode('ascii')))
            rlc.log('::endgroup::')
    rlc.log('')
    for x in lines:
        rlc.log(x)
    for msg in problems:
        rlc.log('::error title=Browser and player check::%s' % msg if gha() else 'error: %s' % msg)
    result = 'passed' if not problems else 'FAILED (%d problems)' % len(problems)
    rlc.log('Browser and player check %s.' % result)
    rlc._append('GITHUB_STEP_SUMMARY', '### Browser and player check (%s)\n\n%s\n\nResult: **%s**\n\n'
                % (p.name, '\n'.join('- ' + x for x in lines + ['problem: ' + m for m in problems
                                                                  if 'FAIL ' + m not in lines]), result))
    if args.log:
        with open(args.log, 'w', encoding='utf-8', newline='\n') as f:
            f.write('\n'.join(lines + problems + [result]) + '\n')
    return 1 if problems else 0


if __name__ == '__main__':
    sys.exit(main())
