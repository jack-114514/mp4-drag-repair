"""Windows limits for the FFmpeg and FFprobe worker processes."""
import ctypes as ct
from ctypes import wintypes as wt
import os
import threading
from typing import NamedTuple


class ResourceLimits(NamedTuple):
    cpu: int = 0
    memory: int = 0
    gpu: int = 0

    def describe(self):
        return ' · '.join(f'{name} {value}%' for name, value in zip(('CPU', '内存', 'GPU 目标'), self) if value)


class ResourceLimitError(RuntimeError):
    def __init__(self, message, suggestion='打开“资源限制”，调整相应上限后重试。'):
        super().__init__(message)
        self.suggestion = suggestion


class MemoryStatus(ct.Structure):
    _fields_ = [('length', wt.DWORD), ('load', wt.DWORD)] + [
        (name, ct.c_uint64) for name in ('total_physical', 'available_physical', 'total_page_file',
                                        'available_page_file', 'total_virtual', 'available_virtual',
                                        'available_extended_virtual')]


class BasicJobLimits(ct.Structure):
    _fields_ = [('process_time', ct.c_int64), ('job_time', ct.c_int64), ('flags', wt.DWORD),
                ('min_working_set', ct.c_size_t), ('max_working_set', ct.c_size_t),
                ('active_processes', wt.DWORD), ('affinity', ct.c_size_t),
                ('priority', wt.DWORD), ('scheduling_class', wt.DWORD)]


class ExtendedJobLimits(ct.Structure):
    _fields_ = [('basic', BasicJobLimits), ('io_counters', ct.c_uint64 * 6),
                ('process_memory', ct.c_size_t), ('job_memory', ct.c_size_t),
                ('peak_process_memory', ct.c_size_t), ('peak_job_memory', ct.c_size_t)]


class CpuRate(ct.Structure):
    _fields_ = [('flags', wt.DWORD), ('rate', wt.DWORD)]


JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
JOB_OBJECT_CPU_RATE_CONTROL_INFORMATION = 15
JOB_OBJECT_LIMIT_JOB_MEMORY = 0x200
JOB_OBJECT_CPU_RATE_ENABLE_HARD_CAP = 0x1 | 0x4
PROCESS_TERMINATE = 0x0001
PROCESS_SET_QUOTA = 0x0100
PROCESS_SUSPEND_RESUME = 0x0800


def win_api(library, name, result, *arguments):
    method = getattr(library, name)
    method.restype = result
    method.argtypes = arguments
    return method


class ProcessLimits:
    def __init__(self, process, limits, uses_gpu=False):
        self.process = process
        self.job = None
        self.handle = None
        self.memory_bytes = 0
        self.timer = None
        self.stopping = threading.Event()
        self.error = None
        gpu = limits.gpu if uses_gpu and 0 < limits.gpu < 100 else 0
        cpu = limits.cpu if 0 < limits.cpu < 100 else 0
        if not (cpu or limits.memory or gpu):
            return
        if os.name != 'nt':
            raise ResourceLimitError('当前系统不支持视频处理资源限制。')
        kernel = ct.WinDLL('kernel32', use_last_error=True)
        self.close_handle = win_api(kernel, 'CloseHandle', wt.BOOL, wt.HANDLE)
        open_process = win_api(kernel, 'OpenProcess', wt.HANDLE, wt.DWORD, wt.BOOL, wt.DWORD)
        access = (PROCESS_TERMINATE | PROCESS_SET_QUOTA if cpu or limits.memory else 0)
        self.handle = open_process(access | (PROCESS_SUSPEND_RESUME if gpu else 0), False, process.pid)
        if not self.handle:
            if process.poll() is None:
                raise ResourceLimitError(f'无法限制视频处理进程（Windows 错误 {ct.get_last_error()}）。')
            return
        try:
            if cpu or limits.memory:
                self.apply_job(kernel, cpu, limits.memory)
            if gpu:
                native = ct.WinDLL('ntdll')
                suspend = win_api(native, 'NtSuspendProcess', wt.LONG, wt.HANDLE)
                resume = win_api(native, 'NtResumeProcess', wt.LONG, wt.HANDLE)
                self.timer = threading.Thread(target=self.pace_gpu, args=(gpu, suspend, resume), daemon=True)
                self.timer.start()
        except (AttributeError, OSError, ResourceLimitError) as exc:
            self.close()
            if isinstance(exc, ResourceLimitError):
                raise
            raise ResourceLimitError(f'无法启用资源限制（{exc}）。') from exc

    def apply_job(self, kernel, cpu, memory):
        create_job = win_api(kernel, 'CreateJobObjectW', wt.HANDLE, ct.c_void_p, wt.LPCWSTR)
        set_job = win_api(kernel, 'SetInformationJobObject', wt.BOOL, wt.HANDLE, ct.c_int, ct.c_void_p, wt.DWORD)
        assign = win_api(kernel, 'AssignProcessToJobObject', wt.BOOL, wt.HANDLE, wt.HANDLE)
        self.job = create_job(None, None)
        if not self.job:
            raise ResourceLimitError(f'无法创建 Windows 资源限制（错误 {ct.get_last_error()}）。')
        if cpu:
            value = CpuRate(JOB_OBJECT_CPU_RATE_ENABLE_HARD_CAP, cpu * 100)
            if not set_job(self.job, JOB_OBJECT_CPU_RATE_CONTROL_INFORMATION, ct.byref(value), ct.sizeof(value)):
                raise ResourceLimitError(f'Windows 未能应用 CPU 上限（错误 {ct.get_last_error()}）。')
        if memory:
            get_memory = win_api(kernel, 'GlobalMemoryStatusEx', wt.BOOL, ct.POINTER(MemoryStatus))
            status = MemoryStatus(ct.sizeof(MemoryStatus))
            if not get_memory(ct.byref(status)):
                raise ResourceLimitError(f'无法读取电脑的总内存（错误 {ct.get_last_error()}）。')
            self.memory_bytes = status.total_physical * memory // 100
            value = ExtendedJobLimits()
            value.basic.flags = JOB_OBJECT_LIMIT_JOB_MEMORY
            value.job_memory = self.memory_bytes
            if not set_job(self.job, JOB_OBJECT_EXTENDED_LIMIT_INFORMATION, ct.byref(value), ct.sizeof(value)):
                raise ResourceLimitError(f'Windows 未能应用内存上限（错误 {ct.get_last_error()}）。')
        if not assign(self.job, self.handle) and self.process.poll() is None:
            raise ResourceLimitError(f'无法应用处理进程的 CPU 或内存上限（Windows 错误 {ct.get_last_error()}）。')

    def pace_gpu(self, percent, suspend, resume):
        # Limit submission time. Work already queued on a GPU engine can finish
        # during a pause, so this controls long-run load rather than peak load.
        period = 1.0
        run_for = period * percent / 100
        while not self.stopping.wait(run_for) and self.process.poll() is None:
            if suspend(self.handle):
                self.pacing_error()
                return
            self.stopping.wait(period - run_for)
            if resume(self.handle):
                self.pacing_error()
                return

    def pacing_error(self):
        if self.process.poll() is None:
            self.error = ResourceLimitError('GPU 限速无法应用到当前的处理进程。')
            try:
                self.process.terminate()
            except OSError:
                pass

    def close(self):
        if self.timer is not None:
            self.stopping.set()
            self.timer.join()
        if self.job:
            self.close_handle(self.job)
            self.job = None
        if self.handle:
            self.close_handle(self.handle)
            self.handle = None

    def check_result(self, code, stderr):
        if self.error:
            raise self.error
        if code and self.memory_bytes:
            out_of_memory = ('cannot allocate memory', 'out of memory', 'not enough memory',
                             'not enough storage', 'commitment limit')
            if ((code & 0xffffffff) in (0xc0000017, 0xc000012d, 0xfffffff4)
                    or any(message in stderr.lower() for message in out_of_memory)):
                raise ResourceLimitError(f'处理需要更多内存，当前上限为 {self.memory_bytes // 1024**2} MB。',
                                         '调高“资源限制”里的内存上限，或选择“不限”后重试。')
