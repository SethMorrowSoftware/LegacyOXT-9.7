#!/usr/bin/env python3
"""Give the packaged OpenXTalk-Lite.exe the icon and version resources of
Tom Perry's own OpenXTalk-Lite.exe, as his release has them.

  python tools/oxt/win_resources.py apply EXE [DIR]
  python tools/oxt/win_resources.py check EXE [DIR]

Tom Perry re-branded the development engine of his OpenXTalk Lite 1.15
release after building it: its icons (RT_GROUP_ICON 111, the class icon of
every engine window, and 112, the document icon, with their RT_ICON images)
and its version information (RT_VERSION) are his, while the engine sources
(engine/rsrc/development.rc) still name LiveCode's. No source tree has his
resources, so they were taken byte for byte from his release
(openxtalk-lite-1.15-win-noinstaller.7z) into DIR, by default
Installer/openxtalk-lite/from-tom-release/win-exe-resources, one file per
resource named <type>-<id>-<language>.bin.

"apply" replaces every icon, icon group and version resource of EXE with
those files and then checks the result as "check" does. "check" fails when
EXE's icon, icon group and version resources are not exactly the files.
Other resources (the manifest) are left as they are. Windows only (the
Win32 resource update functions); only the Python standard library is used.
"""

import ctypes
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DIR = os.path.normpath(os.path.join(HERE, '..', '..', 'Installer', 'openxtalk-lite',
                                            'from-tom-release', 'win-exe-resources'))
TYPES = {'RT_ICON': 3, 'RT_GROUP_ICON': 14, 'RT_VERSION': 16}
LOAD_LIBRARY_AS_DATAFILE = 0x02
LOAD_LIBRARY_AS_IMAGE_RESOURCE = 0x20


def _kernel32():
    from ctypes import wintypes as wt
    k = ctypes.WinDLL('kernel32', use_last_error=True)
    k.LoadLibraryExW.argtypes = (wt.LPCWSTR, wt.HANDLE, wt.DWORD)
    k.LoadLibraryExW.restype = wt.HMODULE
    k.FreeLibrary.argtypes = (wt.HMODULE,)
    k.FindResourceExW.argtypes = (wt.HMODULE, ctypes.c_void_p, ctypes.c_void_p, wt.WORD)
    k.FindResourceExW.restype = ctypes.c_void_p
    k.SizeofResource.argtypes = (wt.HMODULE, ctypes.c_void_p)
    k.SizeofResource.restype = wt.DWORD
    k.LoadResource.argtypes = (wt.HMODULE, ctypes.c_void_p)
    k.LoadResource.restype = ctypes.c_void_p
    k.LockResource.argtypes = (ctypes.c_void_p,)
    k.LockResource.restype = ctypes.c_void_p
    k.BeginUpdateResourceW.argtypes = (wt.LPCWSTR, wt.BOOL)
    k.BeginUpdateResourceW.restype = wt.HANDLE
    k.UpdateResourceW.argtypes = (wt.HANDLE, ctypes.c_void_p, ctypes.c_void_p, wt.WORD, ctypes.c_void_p, wt.DWORD)
    k.UpdateResourceW.restype = wt.BOOL
    k.EndUpdateResourceW.argtypes = (wt.HANDLE, wt.BOOL)
    k.EndUpdateResourceW.restype = wt.BOOL
    return k


def _error(what):
    return OSError('%s failed: %s' % (what, ctypes.FormatError(ctypes.get_last_error())))


def wanted(folder):
    """{(type, id, language): bytes} of the resource files in folder."""
    out = {}
    for name in sorted(os.listdir(folder)):
        m = re.match(r'^(RT_[A-Z_]+)-(\d+)-(\d+)\.bin$', name)
        if not m or m.group(1) not in TYPES:
            continue
        with open(os.path.join(folder, name), 'rb') as f:
            out[(TYPES[m.group(1)], int(m.group(2)), int(m.group(3)))] = f.read()
    if not out:
        raise SystemExit('no <type>-<id>-<language>.bin resource files in %s' % folder)
    return out


def existing(k, exe):
    """{(type, id, language): bytes} of EXE's icon, icon group and version
    resources (all of them have numeric ids in an MSVC build)."""
    from ctypes import wintypes as wt
    names_proc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HMODULE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)
    langs_proc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HMODULE, ctypes.c_void_p, ctypes.c_void_p, wt.WORD,
                                    ctypes.c_void_p)
    k.EnumResourceNamesW.argtypes = (wt.HMODULE, ctypes.c_void_p, names_proc, ctypes.c_void_p)
    k.EnumResourceLanguagesW.argtypes = (wt.HMODULE, ctypes.c_void_p, ctypes.c_void_p, langs_proc, ctypes.c_void_p)
    h = k.LoadLibraryExW(os.path.abspath(exe), None, LOAD_LIBRARY_AS_DATAFILE | LOAD_LIBRARY_AS_IMAGE_RESOURCE)
    if not h:
        raise _error('LoadLibraryEx(%s)' % exe)
    found = {}
    try:
        for rtype in TYPES.values():
            ids = []

            def on_name(module, t, name, param):
                if name is not None and name > 0xFFFF:
                    raise SystemExit('%s has a resource of type %d with a string name' % (exe, rtype))
                ids.append(name)
                return True

            k.EnumResourceNamesW(h, rtype, names_proc(on_name), None)   # fails when there is none
            for rid in ids:
                langs = []

                def on_lang(module, t, name, lang, param):
                    langs.append(lang)
                    return True

                k.EnumResourceLanguagesW(h, rtype, rid, langs_proc(on_lang), None)
                for lang in langs:
                    res = k.FindResourceExW(h, rtype, rid, lang)
                    size = k.SizeofResource(h, res)
                    data = ctypes.string_at(k.LockResource(k.LoadResource(h, res)), size)
                    found[(rtype, rid, lang)] = data
    finally:
        k.FreeLibrary(h)
    return found


def check(k, exe, want):
    have = existing(k, exe)
    problems = []
    for key in sorted(set(want) | set(have)):
        if key not in have:
            problems.append('missing resource type %d id %d language %d' % key)
        elif key not in want:
            problems.append('extra resource type %d id %d language %d' % key)
        elif have[key] != want[key]:
            problems.append('resource type %d id %d language %d differs' % key)
    return problems


def apply(k, exe, want):
    have = existing(k, exe)
    h = k.BeginUpdateResourceW(os.path.abspath(exe), False)
    if not h:
        raise _error('BeginUpdateResource(%s)' % exe)
    ok = False
    try:
        for (rtype, rid, lang) in sorted(set(have) - set(want)):
            if not k.UpdateResourceW(h, rtype, rid, lang, None, 0):
                raise _error('deleting resource type %d id %d' % (rtype, rid))
        for (rtype, rid, lang), data in sorted(want.items()):
            buf = ctypes.create_string_buffer(data, len(data))
            if not k.UpdateResourceW(h, rtype, rid, lang, buf, len(data)):
                raise _error('writing resource type %d id %d' % (rtype, rid))
        ok = True
    finally:
        if not k.EndUpdateResourceW(h, not ok) and ok:
            raise _error('EndUpdateResource(%s)' % exe)
    return len(have), len(want)


def main(argv):
    if len(argv) not in (3, 4) or argv[1] not in ('apply', 'check'):
        sys.stderr.write(__doc__)
        return 2
    if os.name != 'nt':
        sys.stderr.write('win_resources.py runs on Windows only\n')
        return 2
    exe = argv[2]
    folder = argv[3] if len(argv) == 4 else DEFAULT_DIR
    k = _kernel32()
    want = wanted(folder)
    if argv[1] == 'apply':
        removed, written = apply(k, exe, want)
        print('%s: replaced %d icon, icon group and version resources with Tom Perry\'s %d (%s)'
              % (exe, removed, written, folder))
    problems = check(k, exe, want)
    for p in problems:
        print('%s: %s' % (exe, p))
    if problems:
        return 1
    print('%s: its icon, icon group and version resources are Tom Perry\'s (%d)' % (exe, len(want)))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
