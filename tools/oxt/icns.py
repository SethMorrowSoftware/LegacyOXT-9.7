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

"""Write and check Apple icon files (.icns) whose images are PNG files,
with the Python standard library only.

  python tools/oxt/icns.py write OUT.icns PNG...
  python tools/oxt/icns.py check FILE.icns

WHY not iconutil: it exists only on macOS, and package.py stages the
macOS layout on Linux and WSL too; an .icns file is a simple container,
and PNG payloads are what iconutil itself stores for every size from
32 pixels up.

"write" takes the PNG files of one icon (8-bit RGBA or RGB, not
interlaced, square) and uses those whose pixel size an entry type needs
(ENTRY_TYPES): 32, 64, 128, 256, 512 and 1024 pixels, at 1x and 2x
(Retina); each image is stored unchanged. The file starts with a table
of contents ('TOC '), as iconutil writes it. The 16x16 and 32x32 1x
entries of iconutil (ic04, ic05) hold ARGB pixel data, not PNG; there is
no PNG entry type for them that every supported macOS reads, so they are
left out and macOS scales the 2x images (ic11, ic12) down, as it does
for Pillow's icons. The same PNG files give the same bytes.

"check" reads an .icns file back: the header and every entry, and for
each PNG entry its signature, the CRC of every chunk, an IHDR whose size
is the entry type's, and image data that inflates to exactly the size
the header announces. Exit status 0 when all is well, 1 otherwise.
"""

import struct
import sys
import zlib

PNG_SIGNATURE = b'\x89PNG\r\n\x1a\n'

# (type, pixel size, what it is): the PNG entry types, in file order
ENTRY_TYPES = (
    (b'ic11', 32, '16x16@2x'),
    (b'ic12', 64, '32x32@2x'),
    (b'ic07', 128, '128x128'),
    (b'ic13', 256, '128x128@2x'),
    (b'ic08', 256, '256x256'),
    (b'ic14', 512, '256x256@2x'),
    (b'ic09', 512, '512x512'),
    (b'ic10', 1024, '512x512@2x'),
)
PIXELS = dict((t, n) for t, n, _ in ENTRY_TYPES)
# bytes per pixel of the colour types this module reads (8 bits per
# channel): greyscale, RGB, greyscale with alpha, RGBA
CHANNELS = {0: 1, 2: 3, 4: 2, 6: 4}


class IcnsError(Exception):
    pass


def png_info(data, name='PNG'):
    """(width, height) of a PNG file after checking it: the signature, the
    CRC of every chunk, IHDR first and IEND last, 8 bits per channel, no
    interlacing, and IDAT data that inflates to exactly the rows the
    header announces (one filter byte per row)."""
    if data[:8] != PNG_SIGNATURE:
        raise IcnsError('%s: not a PNG file' % name)
    pos, chunks, idat = 8, [], []
    while pos < len(data):
        if pos + 12 > len(data):
            raise IcnsError('%s: truncated chunk at byte %d' % (name, pos))
        length, kind = struct.unpack_from('>I4s', data, pos)
        body = data[pos + 8:pos + 8 + length]
        crc = data[pos + 8 + length:pos + 12 + length]
        if len(body) != length or len(crc) != 4:
            raise IcnsError('%s: truncated %s chunk' % (name, kind.decode('latin-1')))
        if struct.unpack('>I', crc)[0] != zlib.crc32(kind + body) & 0xffffffff:
            raise IcnsError('%s: bad CRC in the %s chunk' % (name, kind.decode('latin-1')))
        chunks.append(kind)
        if kind == b'IDAT':
            idat.append(body)
        pos += 12 + length
        if kind == b'IEND':
            break
    if not chunks or chunks[0] != b'IHDR' or chunks[-1] != b'IEND' or pos != len(data):
        raise IcnsError('%s: chunks do not start with IHDR and end with IEND' % name)
    width, height, depth, colour, _, _, interlace = struct.unpack_from('>IIBBBBB', data, 16)
    if depth != 8 or colour not in CHANNELS or interlace:
        raise IcnsError('%s: %d-bit colour type %d%s; expected 8-bit RGBA or RGB, not interlaced'
                        % (name, depth, colour, ', interlaced' if interlace else ''))
    try:
        raw = zlib.decompress(b''.join(idat))
    except zlib.error as e:
        raise IcnsError('%s: image data does not inflate: %s' % (name, e))
    if len(raw) != height * (1 + width * CHANNELS[colour]):
        raise IcnsError('%s: image data is %d bytes; a %dx%d image needs %d'
                        % (name, len(raw), width, height, height * (1 + width * CHANNELS[colour])))
    return width, height


def build(pngs):
    """The bytes of an .icns file from {pixel size: PNG bytes}; every
    size of ENTRY_TYPES must be there."""
    missing = sorted(set(n for _, n, _ in ENTRY_TYPES) - set(pngs))
    if missing:
        raise IcnsError('no PNG image of %s pixels' % ', '.join(str(n) for n in missing))
    entries = [(t, pngs[n]) for t, n, _ in ENTRY_TYPES]
    toc = b''.join(t + struct.pack('>I', 8 + len(d)) for t, d in entries)
    body = b'TOC ' + struct.pack('>I', 8 + len(toc)) + toc
    body += b''.join(t + struct.pack('>I', 8 + len(d)) + d for t, d in entries)
    return b'icns' + struct.pack('>I', 8 + len(body)) + body


def build_from_files(paths):
    """build() from PNG files, each used for its own pixel size; files of
    a size no entry needs are ignored, and two files of one size are an
    error."""
    pngs = {}
    for path in paths:
        with open(path, 'rb') as f:
            data = f.read()
        w, h = png_info(data, path)
        if w != h:
            raise IcnsError('%s is %dx%d, not square' % (path, w, h))
        if w in pngs:
            raise IcnsError('two PNG images of %d pixels (%s)' % (w, path))
        pngs[w] = data
    return build(dict((n, d) for n, d in pngs.items() if n in PIXELS.values()))


def read(data):
    """[(type, bytes)] of the entries of an .icns file."""
    if data[:4] != b'icns' or len(data) < 8 or struct.unpack_from('>I', data, 4)[0] != len(data):
        raise IcnsError('not an .icns file, or its length is not the length in its header')
    pos, out = 8, []
    while pos < len(data):
        if pos + 8 > len(data):
            raise IcnsError('truncated entry header at byte %d' % pos)
        kind, length = struct.unpack_from('>4sI', data, pos)
        if length < 8 or pos + length > len(data):
            raise IcnsError('entry %r at byte %d has length %d' % (kind, pos, length))
        out.append((kind, data[pos + 8:pos + length]))
        pos += length
    return out


def check(data):
    """Problems of an .icns file written by build() (an empty list when
    there are none), and a description of each entry."""
    problems, lines = [], []
    try:
        entries = read(data)
    except IcnsError as e:
        return [str(e)], lines
    kinds = [k for k, _ in entries]
    for kind, body in entries:
        name = kind.decode('latin-1')
        if kind == b'TOC ':
            toc = [(body[i:i + 4], struct.unpack_from('>I', body, i + 4)[0]) for i in range(0, len(body), 8)]
            real = [(k, 8 + len(b)) for k, b in entries if k != b'TOC ']
            if toc != real:
                problems.append('the table of contents does not list the entries as they are')
            lines.append('TOC   %d entries' % len(toc))
            continue
        if kind not in PIXELS:
            problems.append('%s: not a PNG entry type this module writes' % name)
            continue
        try:
            w, h = png_info(body, name)
        except IcnsError as e:
            problems.append(str(e))
            continue
        if (w, h) != (PIXELS[kind], PIXELS[kind]):
            problems.append('%s holds a %dx%d image; the type needs %dx%d' % (name, w, h, PIXELS[kind],
                                                                           PIXELS[kind]))
        lines.append('%s  %4dx%-4d PNG %8d bytes  %s' % (name, w, h, len(body),
                                                        dict((t, s) for t, _, s in ENTRY_TYPES)[kind]))
    for t, _, _ in ENTRY_TYPES:
        if t not in kinds:
            problems.append('no %s entry' % t.decode('latin-1'))
    return problems, lines


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    try:
        if len(args) >= 3 and args[0] == 'write':
            data = build_from_files(args[2:])
            with open(args[1], 'wb') as f:
                f.write(data)
            print('%s: %d bytes' % (args[1], len(data)))
            return 0
        if len(args) == 2 and args[0] == 'check':
            with open(args[1], 'rb') as f:
                problems, lines = check(f.read())
            for line in lines:
                print(line)
            for p in problems:
                print('error: %s' % p)
            return 1 if problems else 0
    except (OSError, IcnsError) as e:
        sys.stderr.write('error: %s\n' % e)
        return 1
    sys.stderr.write('usage: icns.py write OUT.icns PNG... | icns.py check FILE.icns\n')
    return 2


if __name__ == '__main__':
    sys.exit(main())
