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

"""Run a 64-bit Windows program under a minimal debugger and print a
symbolized stack trace when it crashes.

  python tools/ci/win_crashtrace.py [--symbols DIR]... [--timeout SECONDS]
      -- PROGRAM [ARGUMENTS...]

The program gets this process's standard handles, so its output goes where
ours goes. A first-chance access violation, stack overflow, heap corruption,
illegal instruction, integer division by zero or stack buffer overrun, and
any second-chance exception, is reported on stderr with the faulting address
and a stack walk of the thread (StackWalk64), each frame named with
dbghelp.dll from the .pdb files in the --symbols folders and the folders of
the loaded modules (no symbol server). Only the first such exception is
traced; the program then runs on, so that its own handling (if any) decides
how it ends. C++ exceptions and breakpoints are passed through silently.

Exit status: the program's, 3 when it crashed (after the trace), 4 when it
was stopped after --timeout seconds, 2 when it could not be started. This
needs only the Python standard library and Windows' own dbghelp.dll, so it
runs on any GitHub Windows runner and on a developer's PC without the
Debugging Tools for Windows.
"""

import argparse
import ctypes
import os
import subprocess
import sys
import time
from ctypes import wintypes as wt

DEBUG_ONLY_THIS_PROCESS = 0x00000002
STARTF_USESTDHANDLES = 0x00000100
HANDLE_FLAG_INHERIT = 0x00000001
DBG_CONTINUE = 0x00010002
DBG_EXCEPTION_NOT_HANDLED = 0x80010001
INFINITE = 0xFFFFFFFF

EXCEPTION_DEBUG_EVENT = 1
CREATE_THREAD_DEBUG_EVENT = 2
CREATE_PROCESS_DEBUG_EVENT = 3
EXIT_THREAD_DEBUG_EVENT = 4
EXIT_PROCESS_DEBUG_EVENT = 5
LOAD_DLL_DEBUG_EVENT = 6

EXCEPTION_ACCESS_VIOLATION = 0xC0000005
EXCEPTION_BREAKPOINT = 0x80000003
STATUS_WX86_BREAKPOINT = 0x4000001F
# Exceptions worth a trace at first chance: the program is unlikely to
# recover from them, and its own crash handling might hide the second chance.
FATAL = {
    0xC0000005: 'access violation',
    0xC000001D: 'illegal instruction',
    0xC0000094: 'integer division by zero',
    0xC0000096: 'privileged instruction',
    0xC00000FD: 'stack overflow',
    0xC0000374: 'heap corruption',
    0xC0000409: 'stack buffer overrun (fast fail)',
}

IMAGE_FILE_MACHINE_AMD64 = 0x8664
CONTEXT_FULL_AMD64 = 0x0010000B
CONTEXT_SIZE = 1232          # sizeof(CONTEXT) on x64; it must be 16-byte aligned
CTX_FLAGS_OFFSET = 0x30
CTX_RSP_OFFSET = 0x98
CTX_RBP_OFFSET = 0xA0
CTX_RIP_OFFSET = 0xF8
ADDR_MODE_FLAT = 3

SYMOPT_UNDNAME = 0x00000002
SYMOPT_LOAD_LINES = 0x00000010
SYMOPT_FAIL_CRITICAL_ERRORS = 0x00000200
SYMOPT_NO_PROMPTS = 0x00080000

SYMBOL_INFOW_SIZE = 88       # sizeof(SYMBOL_INFOW) with its one-character Name
SYMBOL_INFOW_NAMELEN = 76
SYMBOL_INFOW_MAXNAMELEN = 80
SYMBOL_INFOW_NAME = 84
MAX_SYMBOL_NAME = 1024
MAX_FRAMES = 64


class STARTUPINFOW(ctypes.Structure):
    _fields_ = [('cb', wt.DWORD), ('lpReserved', wt.LPWSTR), ('lpDesktop', wt.LPWSTR),
                ('lpTitle', wt.LPWSTR), ('dwX', wt.DWORD), ('dwY', wt.DWORD),
                ('dwXSize', wt.DWORD), ('dwYSize', wt.DWORD), ('dwXCountChars', wt.DWORD),
                ('dwYCountChars', wt.DWORD), ('dwFillAttribute', wt.DWORD), ('dwFlags', wt.DWORD),
                ('wShowWindow', wt.WORD), ('cbReserved2', wt.WORD), ('lpReserved2', ctypes.c_void_p),
                ('hStdInput', wt.HANDLE), ('hStdOutput', wt.HANDLE), ('hStdError', wt.HANDLE)]


class PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [('hProcess', wt.HANDLE), ('hThread', wt.HANDLE),
                ('dwProcessId', wt.DWORD), ('dwThreadId', wt.DWORD)]


class EXCEPTION_RECORD(ctypes.Structure):
    _fields_ = [('ExceptionCode', wt.DWORD), ('ExceptionFlags', wt.DWORD),
                ('ExceptionRecord', ctypes.c_void_p), ('ExceptionAddress', ctypes.c_void_p),
                ('NumberParameters', wt.DWORD), ('ExceptionInformation', ctypes.c_uint64 * 15)]


class EXCEPTION_DEBUG_INFO(ctypes.Structure):
    _fields_ = [('ExceptionRecord', EXCEPTION_RECORD), ('dwFirstChance', wt.DWORD)]


class CREATE_THREAD_DEBUG_INFO(ctypes.Structure):
    _fields_ = [('hThread', wt.HANDLE), ('lpThreadLocalBase', ctypes.c_void_p),
                ('lpStartAddress', ctypes.c_void_p)]


class CREATE_PROCESS_DEBUG_INFO(ctypes.Structure):
    _fields_ = [('hFile', wt.HANDLE), ('hProcess', wt.HANDLE), ('hThread', wt.HANDLE),
                ('lpBaseOfImage', ctypes.c_void_p), ('dwDebugInfoFileOffset', wt.DWORD),
                ('nDebugInfoSize', wt.DWORD), ('lpThreadLocalBase', ctypes.c_void_p),
                ('lpStartAddress', ctypes.c_void_p), ('lpImageName', ctypes.c_void_p),
                ('fUnicode', wt.WORD)]


class EXIT_PROCESS_DEBUG_INFO(ctypes.Structure):
    _fields_ = [('dwExitCode', wt.DWORD)]


class LOAD_DLL_DEBUG_INFO(ctypes.Structure):
    _fields_ = [('hFile', wt.HANDLE), ('lpBaseOfDll', ctypes.c_void_p),
                ('dwDebugInfoFileOffset', wt.DWORD), ('nDebugInfoSize', wt.DWORD),
                ('lpImageName', ctypes.c_void_p), ('fUnicode', wt.WORD)]


class DEBUG_EVENT_UNION(ctypes.Union):
    _fields_ = [('Exception', EXCEPTION_DEBUG_INFO), ('CreateThread', CREATE_THREAD_DEBUG_INFO),
                ('CreateProcessInfo', CREATE_PROCESS_DEBUG_INFO),
                ('ExitProcess', EXIT_PROCESS_DEBUG_INFO), ('LoadDll', LOAD_DLL_DEBUG_INFO),
                ('_size', ctypes.c_byte * 160)]


class DEBUG_EVENT(ctypes.Structure):
    _fields_ = [('dwDebugEventCode', wt.DWORD), ('dwProcessId', wt.DWORD),
                ('dwThreadId', wt.DWORD), ('u', DEBUG_EVENT_UNION)]


class ADDRESS64(ctypes.Structure):
    _fields_ = [('Offset', ctypes.c_uint64), ('Segment', wt.WORD), ('Mode', ctypes.c_int)]


class STACKFRAME64(ctypes.Structure):
    # KdHelp (KDHELP64) is only written by dbghelp; room enough for any version
    _fields_ = [('AddrPC', ADDRESS64), ('AddrReturn', ADDRESS64), ('AddrFrame', ADDRESS64),
                ('AddrStack', ADDRESS64), ('AddrBStore', ADDRESS64),
                ('FuncTableEntry', ctypes.c_void_p), ('Params', ctypes.c_uint64 * 4),
                ('Far', wt.BOOL), ('Virtual', wt.BOOL), ('Reserved', ctypes.c_uint64 * 3),
                ('KdHelp', ctypes.c_byte * 512)]


class IMAGEHLP_LINEW64(ctypes.Structure):
    _fields_ = [('SizeOfStruct', wt.DWORD), ('Key', ctypes.c_void_p), ('LineNumber', wt.DWORD),
                ('FileName', ctypes.c_wchar_p), ('Address', ctypes.c_uint64)]


def load_apis():
    k32 = ctypes.WinDLL('kernel32', use_last_error=True)
    dbghelp = ctypes.WinDLL('dbghelp', use_last_error=True)
    k32.CreateProcessW.argtypes = [wt.LPCWSTR, wt.LPWSTR, ctypes.c_void_p, ctypes.c_void_p, wt.BOOL,
                                   wt.DWORD, ctypes.c_void_p, wt.LPCWSTR,
                                   ctypes.POINTER(STARTUPINFOW), ctypes.POINTER(PROCESS_INFORMATION)]
    k32.WaitForDebugEvent.argtypes = [ctypes.POINTER(DEBUG_EVENT), wt.DWORD]
    k32.ContinueDebugEvent.argtypes = [wt.DWORD, wt.DWORD, wt.DWORD]
    k32.GetThreadContext.argtypes = [wt.HANDLE, ctypes.c_void_p]
    k32.TerminateProcess.argtypes = [wt.HANDLE, wt.UINT]
    k32.CloseHandle.argtypes = [wt.HANDLE]
    k32.GetStdHandle.argtypes = [wt.DWORD]
    k32.GetStdHandle.restype = wt.HANDLE
    k32.SetHandleInformation.argtypes = [wt.HANDLE, wt.DWORD, wt.DWORD]
    k32.GetFinalPathNameByHandleW.argtypes = [wt.HANDLE, wt.LPWSTR, wt.DWORD, wt.DWORD]
    dbghelp.SymSetOptions.argtypes = [wt.DWORD]
    dbghelp.SymInitializeW.argtypes = [wt.HANDLE, wt.LPCWSTR, wt.BOOL]
    dbghelp.SymLoadModuleExW.argtypes = [wt.HANDLE, wt.HANDLE, wt.LPCWSTR, wt.LPCWSTR, ctypes.c_uint64,
                                         wt.DWORD, ctypes.c_void_p, wt.DWORD]
    dbghelp.SymLoadModuleExW.restype = ctypes.c_uint64
    dbghelp.SymFromAddrW.argtypes = [wt.HANDLE, ctypes.c_uint64, ctypes.POINTER(ctypes.c_uint64),
                                     ctypes.c_void_p]
    dbghelp.SymGetLineFromAddrW64.argtypes = [wt.HANDLE, ctypes.c_uint64, ctypes.POINTER(wt.DWORD),
                                              ctypes.POINTER(IMAGEHLP_LINEW64)]
    dbghelp.SymGetModuleBase64.argtypes = [wt.HANDLE, ctypes.c_uint64]
    dbghelp.SymGetModuleBase64.restype = ctypes.c_uint64
    dbghelp.SymFunctionTableAccess64.argtypes = [wt.HANDLE, ctypes.c_uint64]
    dbghelp.SymFunctionTableAccess64.restype = ctypes.c_void_p
    dbghelp.StackWalk64.argtypes = [wt.DWORD, wt.HANDLE, wt.HANDLE, ctypes.POINTER(STACKFRAME64),
                                    ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                                    ctypes.c_void_p, ctypes.c_void_p]
    return k32, dbghelp


def report(text):
    sys.stderr.write('win_crashtrace: %s\n' % text)
    sys.stderr.flush()


class Tracer:
    def __init__(self, k32, dbghelp, symbol_dirs):
        self.k32 = k32
        self.dbghelp = dbghelp
        self.symbol_dirs = symbol_dirs
        self.hprocess = None
        self.threads = {}
        self.modules = {}
        self.symbols = False

    def final_path(self, handle):
        if not handle:
            return None
        buf = ctypes.create_unicode_buffer(1024)
        n = self.k32.GetFinalPathNameByHandleW(handle, buf, 1024, 0)
        if n == 0 or n >= 1024:
            return None
        path = buf.value
        if path.startswith('\\\\?\\UNC\\'):
            return '\\\\' + path[8:]
        if path.startswith('\\\\?\\'):
            return path[4:]
        return path

    def start_symbols(self, hprocess, exe):
        self.hprocess = hprocess
        dirs = list(self.symbol_dirs) + [os.path.dirname(exe)]
        # No SYMOPT_DEFERRED_LOADS: a deferred load would happen after the
        # module's file handle is closed, and fail (ERROR_MOD_NOT_FOUND).
        self.dbghelp.SymSetOptions(SYMOPT_UNDNAME | SYMOPT_LOAD_LINES | SYMOPT_FAIL_CRITICAL_ERRORS
                                   | SYMOPT_NO_PROMPTS)
        self.symbols = bool(self.dbghelp.SymInitializeW(hprocess, ';'.join(dirs), False))
        if not self.symbols:
            report('SymInitialize failed (%d); frames will not be named' % ctypes.get_last_error())

    def add_module(self, hfile, base):
        path = self.final_path(hfile)
        self.modules[base] = path or '?'
        if self.symbols and path:
            self.dbghelp.SymLoadModuleExW(self.hprocess, None, path, None, base, 0, None, 0)

    def describe(self, addr):
        owner = None
        for base, path in self.modules.items():
            if base <= addr and (owner is None or base > owner[0]):
                owner = (base, path)
        module = os.path.basename(owner[1]) if owner else '?'
        text = '0x%016x' % addr
        named = False
        if self.symbols:
            buf = ctypes.create_string_buffer(SYMBOL_INFOW_SIZE + 2 * MAX_SYMBOL_NAME)
            ctypes.c_uint32.from_buffer(buf, 0).value = SYMBOL_INFOW_SIZE
            ctypes.c_uint32.from_buffer(buf, SYMBOL_INFOW_MAXNAMELEN).value = MAX_SYMBOL_NAME
            disp = ctypes.c_uint64(0)
            if self.dbghelp.SymFromAddrW(self.hprocess, addr, ctypes.byref(disp), buf):
                length = ctypes.c_uint32.from_buffer(buf, SYMBOL_INFOW_NAMELEN).value
                name = ctypes.wstring_at(ctypes.addressof(buf) + SYMBOL_INFOW_NAME, length)
                text += '  %s!%s+0x%x' % (module, name, disp.value)
                named = True
            line = IMAGEHLP_LINEW64()
            line.SizeOfStruct = ctypes.sizeof(IMAGEHLP_LINEW64)
            ldisp = wt.DWORD(0)
            if self.dbghelp.SymGetLineFromAddrW64(self.hprocess, addr, ctypes.byref(ldisp), ctypes.byref(line)):
                text += '  [%s:%d]' % (line.FileName, line.LineNumber)
        if not named and owner:
            text += '  %s+0x%x' % (module, addr - owner[0])
        return text

    def trace(self, tid, record, first_chance):
        code = record.ExceptionCode
        report('%s-chance exception 0x%08X (%s) at %s, thread %d' % (
            'first' if first_chance else 'second', code, FATAL.get(code, 'exception'),
            self.describe(record.ExceptionAddress or 0), tid))
        if code == EXCEPTION_ACCESS_VIOLATION and record.NumberParameters >= 2:
            kind = {0: 'read', 1: 'write', 8: 'execute'}.get(record.ExceptionInformation[0], 'access')
            report('%s of address 0x%016x' % (kind, record.ExceptionInformation[1]))
        hthread = self.threads.get(tid)
        if not hthread:
            return
        raw = ctypes.create_string_buffer(CONTEXT_SIZE + 16)
        context = (ctypes.addressof(raw) + 15) & ~15
        ctypes.c_uint32.from_address(context + CTX_FLAGS_OFFSET).value = CONTEXT_FULL_AMD64
        if not self.k32.GetThreadContext(hthread, context):
            report('GetThreadContext failed (%d)' % ctypes.get_last_error())
            return
        frame = STACKFRAME64()
        for field, offset in (('AddrPC', CTX_RIP_OFFSET), ('AddrFrame', CTX_RBP_OFFSET),
                              ('AddrStack', CTX_RSP_OFFSET)):
            addr = getattr(frame, field)
            addr.Offset = ctypes.c_uint64.from_address(context + offset).value
            addr.Mode = ADDR_MODE_FLAT
        table_access = ctypes.cast(self.dbghelp.SymFunctionTableAccess64, ctypes.c_void_p)
        module_base = ctypes.cast(self.dbghelp.SymGetModuleBase64, ctypes.c_void_p)
        for i in range(MAX_FRAMES):
            if not self.dbghelp.StackWalk64(IMAGE_FILE_MACHINE_AMD64, self.hprocess, hthread,
                                            ctypes.byref(frame), context, None, table_access,
                                            module_base, None):
                break
            if frame.AddrPC.Offset == 0:
                break
            sys.stderr.write('  #%-2d %s\n' % (i, self.describe(frame.AddrPC.Offset)))
        sys.stderr.flush()


def run(command, symbol_dirs, timeout):
    """Run command (a list) under the debugger; returns the exit status
    described in the module docstring."""
    k32, dbghelp = load_apis()
    exe = os.path.abspath(command[0])
    si = STARTUPINFOW()
    si.cb = ctypes.sizeof(STARTUPINFOW)
    si.dwFlags = STARTF_USESTDHANDLES
    for field, which in (('hStdInput', -10), ('hStdOutput', -11), ('hStdError', -12)):
        handle = k32.GetStdHandle(which & 0xFFFFFFFF)
        if handle and handle != wt.HANDLE(-1).value:
            k32.SetHandleInformation(handle, HANDLE_FLAG_INHERIT, HANDLE_FLAG_INHERIT)
        setattr(si, field, handle)
    pi = PROCESS_INFORMATION()
    cmdline = ctypes.create_unicode_buffer(subprocess.list2cmdline([exe] + list(command[1:])))
    if not k32.CreateProcessW(exe, cmdline, None, None, True, DEBUG_ONLY_THIS_PROCESS, None,
                              os.getcwd(), ctypes.byref(si), ctypes.byref(pi)):
        report('cannot start %s (%d)' % (exe, ctypes.get_last_error()))
        return 2

    tracer = Tracer(k32, dbghelp, symbol_dirs)
    deadline = time.monotonic() + timeout if timeout else None
    crashed = timed_out = False
    exit_code = 0
    while True:
        event = DEBUG_EVENT()
        wait = INFINITE
        if deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0 and not timed_out:
                report('stopped after %g s' % timeout)
                k32.TerminateProcess(pi.hProcess, 4)
                timed_out = True
            wait = 5000 if timed_out else int(max(remaining, 0) * 1000)
        if not k32.WaitForDebugEvent(ctypes.byref(event), wait):
            continue
        status = DBG_CONTINUE
        code = event.dwDebugEventCode
        if code == CREATE_PROCESS_DEBUG_EVENT:
            info = event.u.CreateProcessInfo
            tracer.threads[event.dwThreadId] = info.hThread
            tracer.start_symbols(pi.hProcess, exe)
            tracer.add_module(info.hFile, info.lpBaseOfImage or 0)
            if info.hFile:
                k32.CloseHandle(info.hFile)
        elif code == CREATE_THREAD_DEBUG_EVENT:
            tracer.threads[event.dwThreadId] = event.u.CreateThread.hThread
        elif code == EXIT_THREAD_DEBUG_EVENT:
            tracer.threads.pop(event.dwThreadId, None)
        elif code == LOAD_DLL_DEBUG_EVENT:
            info = event.u.LoadDll
            tracer.add_module(info.hFile, info.lpBaseOfDll or 0)
            if info.hFile:
                k32.CloseHandle(info.hFile)
        elif code == EXCEPTION_DEBUG_EVENT:
            record = event.u.Exception.ExceptionRecord
            first = bool(event.u.Exception.dwFirstChance)
            ex = record.ExceptionCode
            if ex not in (EXCEPTION_BREAKPOINT, STATUS_WX86_BREAKPOINT):
                status = DBG_EXCEPTION_NOT_HANDLED
                if (ex in FATAL or not first) and not crashed:
                    tracer.trace(event.dwThreadId, record, first)
                    crashed = True
                if not first:
                    k32.TerminateProcess(pi.hProcess, 3)
        elif code == EXIT_PROCESS_DEBUG_EVENT:
            exit_code = event.u.ExitProcess.dwExitCode
            k32.ContinueDebugEvent(event.dwProcessId, event.dwThreadId, DBG_CONTINUE)
            break
        k32.ContinueDebugEvent(event.dwProcessId, event.dwThreadId, status)
    k32.CloseHandle(pi.hThread)
    k32.CloseHandle(pi.hProcess)
    if timed_out:
        return 4
    if crashed:
        return 3
    return exit_code if exit_code < 0x80000000 else 3


def main(argv=None):
    ap = argparse.ArgumentParser(description='Run a Windows program and print a symbolized stack trace '
                                             'if it crashes.')
    ap.add_argument('--symbols', action='append', default=[], metavar='DIR',
                    help='folder with .pdb files (repeatable)')
    ap.add_argument('--timeout', type=float, default=0, metavar='SECONDS',
                    help='stop the program after this many seconds (default: never)')
    ap.add_argument('command', nargs=argparse.REMAINDER, help='-- PROGRAM [ARGUMENTS...]')
    args = ap.parse_args(argv)
    command = args.command[1:] if args.command[:1] == ['--'] else args.command
    if not command:
        ap.error('no program given')
    if os.name != 'nt':
        ap.error('this tool runs on Windows only')
    return run(command, [os.path.abspath(d) for d in args.symbols], args.timeout)


if __name__ == '__main__':
    sys.exit(main())
