"""Local hardware discovery, tested encoder selection and aggregate timing history."""
import hashlib
import json
import os
from pathlib import Path
import platform
import threading
import time
from resource_limits import ResourceLimitError

_lock = threading.RLock()
_encoders = {}
_hardware = None


def hardware_info():
    global _hardware
    with _lock:
        if _hardware is not None:
            return dict(_hardware)
        cpu = platform.processor() or '未知处理器'
        gpus = []
        if os.name == 'nt':
            import winreg
            try:
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r'HARDWARE\DESCRIPTION\System\CentralProcessor\0') as key:
                    cpu = str(winreg.QueryValueEx(key, 'ProcessorNameString')[0]).strip()
            except OSError:
                pass
            # The display-adapter class exposes driver descriptions without WMI,
            # admin rights, downloads, or fragile native callback hooks.
            try:
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                    r'SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}') as adapters:
                    for i in range(winreg.QueryInfoKey(adapters)[0]):
                        sub = winreg.EnumKey(adapters, i)
                        if len(sub) != 4 or not sub.isdigit():
                            continue
                        try:
                            with winreg.OpenKey(adapters, sub) as key:
                                name = str(winreg.QueryValueEx(key, 'DriverDesc')[0]).strip()
                                if name and name not in gpus:
                                    gpus.append(name)
                        except OSError:
                            continue
            except OSError:
                pass
        _hardware = {'cpu': cpu, 'logical_cores': os.cpu_count() or 1, 'gpus': gpus}
        return dict(_hardware)


def select_encoder(ffmpeg, capture):
    """Test encoders instead of trusting model names or FFmpeg's compiled list."""
    key = str(ffmpeg)
    with _lock:
        if key in _encoders:
            return dict(_encoders[key])
        adapters = ' '.join(hardware_info()['gpus']).lower()
        candidates = [
            ('nvenc', 'h264_nvenc', 'NVIDIA NVENC', ['-preset', 'p4']),
            ('qsv', 'h264_qsv', 'Intel Quick Sync', ['-preset', 'fast']),
            ('amf', 'h264_amf', 'AMD AMF', ['-quality', 'balanced'])]
        if 'amd' in adapters and 'nvidia' not in adapters:
            candidates.sort(key=lambda x: x[0] != 'amf')
        elif 'intel' in adapters and 'nvidia' not in adapters:
            candidates.sort(key=lambda x: x[0] != 'qsv')
        attempts = []
        for backend, encoder, name, options in candidates:
            started = time.monotonic()
            try:
                code, _, error = capture([ffmpeg, '-v', 'error', '-nostdin', '-f', 'lavfi', '-i',
                    'testsrc2=size=640x360:rate=30', '-frames:v', '12', '-an', '-pix_fmt', 'nv12',
                    '-c:v', encoder]+options+['-f', 'null', '-'], 20)
                attempts.append({'encoder': encoder, 'ok': code == 0,
                                 'seconds': round(time.monotonic()-started, 3),
                                 'error': error.decode('utf-8', 'replace')[-1200:] if code else ''})
                if code == 0:
                    result = {'backend': backend, 'encoder': encoder, 'name': name, 'attempts': attempts}
                    _encoders[key] = result
                    return dict(result)
            except Exception as exc:
                if isinstance(exc, ResourceLimitError) or exc.__class__.__name__ == 'Cancelled':
                    raise
                attempts.append({'encoder': encoder, 'ok': False, 'error': str(exc)})
        result = {'backend': 'cpu', 'encoder': 'libx264', 'name': 'CPU 软件编码', 'attempts': attempts}
        _encoders[key] = result
        return dict(result)


class TimingHistory:
    """Stores only throughput averages; no filenames or media content."""
    def __init__(self):
        override = os.environ.get('MP4FIX_STATS')
        self.path = Path(override) if override else Path(os.environ.get('LOCALAPPDATA', '.'))/'MP4一拖修复'/'速度记录.json'
        hardware = hardware_info()
        self.machine = hashlib.sha256(json.dumps(hardware, sort_keys=True).encode()).hexdigest()[:12]
        self.data = {}
        try:
            data = json.loads(self.path.read_text(encoding='utf-8'))
            if isinstance(data, dict) and data.get('machine') == self.machine and isinstance(data.get('rates'), dict):
                self.data = data['rates']
        except (OSError, ValueError):
            pass

    @staticmethod
    def work(video, duration, size, fps, copying):
        return size/1024**2 if copying else video['width']*video['height']*fps*duration/1e6

    def phase(self, key, work, default, overhead):
        row = self.data.get(key, {})
        try:
            rate = float(row.get('rate', default))
            if not 0 < rate < 100000:
                rate = default
        except (TypeError, ValueError, AttributeError):
            rate = default
        try:
            count = max(0, int(row.get('count', 0))) if isinstance(row, dict) else 0
        except (TypeError, ValueError):
            count = 0
        return {'seconds': work/rate+overhead, 'learned': count > 0, 'overhead': overhead,
                'rate_key': key, 'work_units': work}

    def conversion(self, video, duration, size, fps, copying, backend, speed, transcode_audio,
                   resize_pipeline=None, resize_target=None):
        if copying and transcode_audio:
            return self.phase('remux_aac', max(size/1024**2, duration*2), 85, .15)
        key = 'remux' if copying else backend+'_'+speed+'_'+video.get('codec_name', 'unknown')
        default = 85 if copying else 90 if backend == 'cpu' else 450
        if resize_pipeline:
            key += f'_resize_{resize_pipeline}_{resize_target}'
            if resize_pipeline == 'cuda':
                default = {'fast': 740, 'balanced': 560, 'quality': 420}[speed]
            else:
                default = min(default, 320)
        return self.phase(key, self.work(video, duration, size, fps, copying), default, .15)

    def validation(self, video, duration, fps, full):
        if full:
            return self.phase('decode_'+video.get('codec_name', 'unknown'),
                              self.work(video, duration, 0, fps, False), 900, .15)
        # Seeking starts at the preceding keyframe, so decode can exceed the sampled seconds.
        return self.phase('sample_'+video.get('codec_name', 'unknown'),
                          self.work(video, min(duration, 10), 0, fps, False), 1800, .15)

    def record(self, phase, seconds):
        seconds -= phase['overhead']
        if seconds <= .15 or phase['work_units'] <= 0:
            return
        key = phase['rate_key']
        rate = phase['work_units']/seconds
        old = self.data.get(key, {})
        if not isinstance(old, dict):
            old = {}
        previous = old.get('rate')
        try:
            count = max(0, int(old.get('count', 0)))
        except (TypeError, ValueError):
            count = 0
        self.data[key] = {'rate': round(rate*.5+previous*.5 if isinstance(previous, (int, float)) and previous > 0 else rate, 3),
                          'count': min(999, count+1)}
        try:
            with _lock:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                temp = self.path.with_suffix('.tmp')
                temp.write_text(json.dumps({'machine': self.machine, 'rates': self.data}, indent=2), encoding='utf-8')
                temp.replace(self.path)
        except OSError:
            pass
