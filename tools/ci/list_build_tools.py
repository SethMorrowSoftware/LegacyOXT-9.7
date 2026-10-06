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

"""List the build's own tools in a Linux or macOS build output, for the CI
tarballs to leave out.

  python3 tools/ci/list_build_tools.py DIR

DIR is a build output folder (linux-<arch>-bin, _build/mac/Release). The
Linux and macOS builds leave the tools they make to build themselves in
it (tools/oxt/package.py BUILD_PROGRAMS: gentle-target and
reflex-target, which are GENTLE 97, whose licence forbids redistributing
it (THIRD-PARTY-NOTICES.md "GENTLE"); perfect-target, the lc-compile
bootstrap stages, zic and lcidlc), with their .dbg files or .dSYM
bundles. The CI artifacts of the build (OpenXTalk-Lite-<platform>-bin and
-symbols) are downloadable, so they must not carry GENTLE either; nothing
that uses those artifacts needs any of these tools (package.py never
installs them, and package_dist.py leaves them out of the binaries and
symbols archives in the same way).

Prints every entry at the top of DIR that package_dist.build_tool()
counts, the same rule as the release archives, one per line and sorted,
as <base name of DIR>/<entry>: the paths that "tar -C <parent of DIR>
... <base name of DIR>" stores, for tar's --exclude-from (-X) and for
filtering a "find <base name of DIR>" list with grep -vxF -f. Prints
nothing when there are none. Exit status 2 when DIR is not a folder.

Only the Python 3 standard library is used (3.8 or newer: the Ubuntu
20.04 build container has 3.8).
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), 'oxt'))
import package_dist  # noqa: E402


def build_tools(folder):
    base = os.path.basename(os.path.normpath(folder))
    return ['%s/%s' % (base, name) for name in sorted(os.listdir(folder)) if package_dist.build_tool(name)]


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1 or argv[0].startswith('-'):
        sys.stderr.write('usage: list_build_tools.py DIR\n')
        return 2
    if not os.path.isdir(argv[0]):
        sys.stderr.write('error: %s is not a folder\n' % argv[0])
        return 2
    for path in build_tools(argv[0]):
        print(path)
    return 0


if __name__ == '__main__':
    sys.exit(main())
