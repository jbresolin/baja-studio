"""On Windows, ffmpeg children die with the node, even when the node is killed.

If the node's Python process crashed or was killed (Task Manager, a service
timeout), its ffmpeg children would keep running: still streaming, and
still holding the cameras, so the restarted node could not open them
("camera busy"). Windows ends every process of a *job object* when the last
handle to the job closes, which happens when the process holding it ends
however it ends. So the node keeps one job, created on first use, with
"kill on job close" set, and puts each ffmpeg in it right after starting it.

Elsewhere (Linux CI, a dev machine) this does nothing: a stopped pipeline
still stops its ffmpeg, and nothing runs unattended there.
"""

import logging
import subprocess
import sys

logger = logging.getLogger(__name__)

if sys.platform == "win32":
    import ctypes
    import threading
    from ctypes import wintypes

    _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
    _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
    _PROCESS_SET_QUOTA = 0x0100
    _PROCESS_TERMINATE = 0x0001
    _PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

    class _BasicLimits(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _IoCounters(ctypes.Structure):
        _fields_ = [
            (name, ctypes.c_uint64)
            for name in (
                "ReadOperationCount",
                "WriteOperationCount",
                "OtherOperationCount",
                "ReadTransferCount",
                "WriteTransferCount",
                "OtherTransferCount",
            )
        ]

    class _ExtendedLimits(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _BasicLimits),
            ("IoInfo", _IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    _kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    _kernel32.SetInformationJobObject.restype = wintypes.BOOL
    _kernel32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
    ]  # fmt: skip
    _kernel32.OpenProcess.restype = wintypes.HANDLE
    _kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    _kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    _kernel32.IsProcessInJob.restype = wintypes.BOOL
    _kernel32.IsProcessInJob.argtypes = [
        wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL),
    ]  # fmt: skip
    _kernel32.CloseHandle.restype = wintypes.BOOL
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

    _job_lock = threading.Lock()
    # Never closed: it must live exactly as long as this process.
    _job: int | None = None

    def _the_job() -> int:
        global _job
        with _job_lock:
            if _job is None:
                job = _kernel32.CreateJobObjectW(None, None)
                if not job:
                    raise ctypes.WinError(ctypes.get_last_error())
                limits = _ExtendedLimits()
                limits.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
                if not _kernel32.SetInformationJobObject(
                    job,
                    _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
                    ctypes.byref(limits),
                    ctypes.sizeof(limits),
                ):
                    raise ctypes.WinError(ctypes.get_last_error())
                _job = int(job)
            return _job

    def tie_to_this_process(process: "subprocess.Popen[bytes]") -> bool:
        """Put `process` in the node's kill-on-close job; False (logged) if Windows refused."""
        try:
            job = _the_job()
            handle = _kernel32.OpenProcess(
                _PROCESS_SET_QUOTA | _PROCESS_TERMINATE, False, process.pid
            )
            if not handle:
                raise ctypes.WinError(ctypes.get_last_error())
            try:
                if not _kernel32.AssignProcessToJobObject(job, handle):
                    raise ctypes.WinError(ctypes.get_last_error())
            finally:
                _kernel32.CloseHandle(handle)
        except OSError as error:
            logger.warning(
                "ffmpeg (pid %s) will not stop if the node crashes: %s", process.pid, error
            )
            return False
        return True

    def is_tied_to_this_process(pid: int) -> bool:
        """True if process `pid` is in the node's job (for the tests)."""
        handle = _kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            result = wintypes.BOOL(False)
            if not _kernel32.IsProcessInJob(handle, _the_job(), ctypes.byref(result)):
                raise ctypes.WinError(ctypes.get_last_error())
            return bool(result.value)
        finally:
            _kernel32.CloseHandle(handle)

else:

    def tie_to_this_process(process: "subprocess.Popen[bytes]") -> bool:
        """Nothing to do outside Windows (see the module docstring); False."""
        return False

    def is_tied_to_this_process(pid: int) -> bool:
        """Never, outside Windows."""
        return False
