"""Own a Windows command tree, including descendants whose parent has exited.

The launcher waits for stdin before spawning the requested command, so the
entire tree belongs to the job before any user command can run.
https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import subprocess
import sys


class _BasicLimits(ctypes.Structure):
    _fields_ = [("process_time", ctypes.c_int64), ("job_time", ctypes.c_int64),
                ("flags", wintypes.DWORD), ("min_working_set", ctypes.c_size_t),
                ("max_working_set", ctypes.c_size_t), ("active_process_limit", wintypes.DWORD),
                ("affinity", ctypes.c_size_t), ("priority", wintypes.DWORD),
                ("scheduling_class", wintypes.DWORD)]


class _ExtendedLimits(ctypes.Structure):
    _fields_ = [("basic", _BasicLimits), ("io_counters", ctypes.c_uint64 * 6),
                ("process_memory", ctypes.c_size_t), ("job_memory", ctypes.c_size_t),
                ("peak_process_memory", ctypes.c_size_t), ("peak_job_memory", ctypes.c_size_t)]


class WindowsJob:
    def __init__(self):
        self.api = ctypes.WinDLL("kernel32", use_last_error=True)
        self.api.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        self.api.CreateJobObjectW.restype = wintypes.HANDLE
        self.api.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        self.api.SetInformationJobObject.restype = wintypes.BOOL
        self.api.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self.api.AssignProcessToJobObject.restype = wintypes.BOOL
        self.api.CloseHandle.argtypes = [wintypes.HANDLE]
        self.api.CloseHandle.restype = wintypes.BOOL
        self.handle = self.api.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = _ExtendedLimits()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.api.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            error = ctypes.WinError(ctypes.get_last_error())
            self.close()
            raise error

    def spawn(self, argv, **options):
        # argv stays an argument vector; no shell parsing or string-built command.
        launcher = (
            "import subprocess,sys; "
            "ready=sys.stdin.buffer.read(1); "
            "sys.exit(subprocess.call(sys.argv[1:],stdin=subprocess.DEVNULL,"
            "creationflags=subprocess.CREATE_NO_WINDOW) if ready==b'1' else 1)"
        )
        process = subprocess.Popen([sys.executable, "-c", launcher, *argv],
                                   stdin=subprocess.PIPE, **options)
        try:
            if not self.api.AssignProcessToJobObject(self.handle, int(process._handle)):
                raise ctypes.WinError(ctypes.get_last_error())
            process.stdin.write(b"1")
            process.stdin.close()
            return process
        except BaseException:
            process.kill()
            process.wait(timeout=10)
            for pipe in (process.stdin, process.stdout, process.stderr):
                if pipe:
                    pipe.close()
            raise

    def close(self):
        if self.handle:
            self.api.CloseHandle(self.handle)
            self.handle = None
