#!/usr/bin/env python3
"""Check that every difference from LiveCode's source is documented.

  python3 tools/ci/pristine_guard.py [--summary]

The tag livecode-9.7.0-dp-1 is LiveCode Community's last develop build
(livecode/livecode 4606a10ea, 2021-07-26, BUILD_SHORT_VERSION 9.7.0-dp-1)
with its ide and thirdparty submodules vendored at the commits it pins. Its
tree must still be EXPECTED_TREE. Every file of that tree that HEAD changes
or deletes, and every file HEAD adds outside the folders of
.github/pristine-allowlist.txt, must be named (in backticks) in
CHANGES-FROM-LIVECODE.md, which says why. A name there that no longer
differs is reported as a warning.

With --summary the documented differences go into the GitHub job summary
with their line counts. Exit status 0 when everything is documented, 1
otherwise. Only the Python 3 standard library is used.
"""

import fnmatch
import os
import re
import subprocess
import sys

BASE_TAG = 'livecode-9.7.0-dp-1'
# The tree of LiveCode develop 4606a10ea with livecode-ide ccc733a15 in ide/
# and livecode-thirdparty e5e050573 in thirdparty/ (their trees 8a1564b1 and
# 6ea49f1c, as GitHub reports them)
EXPECTED_TREE = '1f8d052b254b4c32f41f50d26a43a2fe486b4b2d'
DOC = 'CHANGES-FROM-LIVECODE.md'
ALLOWLIST = '.github/pristine-allowlist.txt'


def git(*args):
    return subprocess.run(('git',) + args, check=True, stdout=subprocess.PIPE).stdout.decode('utf-8', 'replace')


def patterns():
    out = []
    with open(ALLOWLIST, encoding='utf-8') as f:
        for line in f:
            line = line.split('#', 1)[0].strip()
            if line:
                out.append(line)
    return out


def allowed(path, pats):
    for pat in pats:
        if pat.endswith('/**') and (path + '/').startswith(pat[:-2]):
            return True
        if fnmatch.fnmatchcase(path, pat):
            return True
    return False


def documented_names():
    try:
        with open(DOC, encoding='utf-8') as f:
            text = f.read()
    except OSError:
        return set()
    return set(re.findall(r'`([^`\s]+)`', text))


def main(argv):
    summary = '--summary' in argv
    problems, warnings = [], []
    tree = git('rev-parse', BASE_TAG + '^{tree}').strip()
    if tree != EXPECTED_TREE:
        problems.append('%s has the tree %s, not %s: the pristine base was changed' % (BASE_TAG, tree, EXPECTED_TREE))
    pats = patterns()
    named = documented_names()
    rows = []
    for line in git('diff', '--no-renames', '--name-status', BASE_TAG, 'HEAD').splitlines():
        status, path = line.split('\t', 1)
        status = status[0]
        if status == 'A' and allowed(path, pats):
            continue
        if path not in named:
            what = {'A': 'added outside the allowlist', 'M': 'changed', 'D': 'deleted'}.get(status, status)
            problems.append('%s is %s but not named in %s' % (path, what, DOC))
        rows.append((status, path))
    differing = {p for _, p in rows}
    for name in sorted(named):
        if '/' in name and name not in differing and not allowed(name, pats) \
                and git('ls-tree', BASE_TAG, '--', name).strip():
            warnings.append('%s names %s, which does not differ from %s' % (DOC, name, BASE_TAG))
    gha = os.environ.get('GITHUB_ACTIONS') == 'true'
    for w in warnings:
        print('warning: ' + w)
        if gha:
            print('::warning title=Pristine guard::' + w)
    for p in problems:
        print('error: ' + p)
        if gha:
            print('::error title=Pristine guard::' + p)
    result = 'passed' if not problems else 'FAILED (%d problems)' % len(problems)
    print('Pristine guard %s: %d documented differences from %s (tree %s).'
          % (result, len(rows), BASE_TAG, tree[:12]))
    if summary and os.environ.get('GITHUB_STEP_SUMMARY'):
        numstat = {}
        for line in git('diff', '--no-renames', '--numstat', BASE_TAG, 'HEAD').splitlines():
            add, rem, path = line.split('\t', 2)
            numstat[path] = (add, rem)
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a', encoding='utf-8') as f:
            f.write('## Pristine guard: %s\n\n' % result)
            f.write('Differences from `%s` (LiveCode develop 4606a10ea, 9.7.0-dp-1), each named in `%s`:\n\n'
                    % (BASE_TAG, DOC))
            f.write('| file | status | + | - |\n|---|---|---|---|\n')
            for status, path in rows:
                add, rem = numstat.get(path, ('', ''))
                f.write('| `%s` | %s | %s | %s |\n' % (path, status, add, rem))
            for p in problems:
                f.write('\n- **error:** %s' % p)
            f.write('\n')
    return 1 if problems else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
