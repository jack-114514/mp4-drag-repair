"""Windows drag-and-drop MP4 normalizer with local processing."""
import argparse
from collections import deque
import ctypes
from ctypes import wintypes
import datetime
import hashlib
import json
import os
from pathlib import Path
import queue
import shutil
import struct
import subprocess
import sys
import threading
import time
import uuid
import math
from resource_limits import ProcessLimits, ResourceLimits

APP_NAME = 'MP4一拖修复'
VERSION = '1.2.10'
RESOLUTIONS = {'480p': 480, '720p': 720, '1080p': 1080, '2K': 1440, '4K': 2160}
ROOT = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))
LOG_DIR = (Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False) else ROOT)/'日志'
NO_WINDOW = 0x08000000 if os.name == 'nt' else 0

class Cancelled(Exception):
    pass

class ConversionError(Exception):
    def __init__(self, message, suggestion=None, retry=None):
        super().__init__(message)
        self.suggestion = suggestion
        self.retry = retry


def video_dimensions(video):
    width, height = int(video.get('width', 0)), int(video.get('height', 0))
    return (height, width) if video_rotation(video) % 180 == 90 else (width, height)


def video_rotation(video):
    for side_data in video.get('side_data_list', []):
        if 'rotation' in side_data:
            return round(float(side_data['rotation']))
    return 0


def main_video(info):
    return next((s for s in info.get('streams', []) if s.get('codec_type') == 'video'
                 and not s.get('disposition', {}).get('attached_pic')), None)

def create_processing_log(prefix, header):
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
    path = LOG_DIR/f'{prefix}-{stamp}-{uuid.uuid4().hex[:8]}.log'
    with path.open('x', encoding='utf-8') as log:
        log.write(header+'\n')
    return path

def unique_output(source, folder=None, original_name=False):
    # Leave room for suffixes even when the source has a very long Chinese name.
    stem = source.stem
    folder = Path(folder) if folder else source.parent
    if original_name:
        target = folder/source.with_suffix('.mp4').name
        if not os.path.lexists(target):
            return target
    max_stem = min(180, max(24, 235 - len(str(folder))))
    if len(stem) > max_stem:
        stem = stem[:max_stem-9] + '_' + hashlib.sha256(stem.encode()).hexdigest()[:8]
    if not original_name:
        stem += '_正常版'
        target = folder/(stem + '.mp4')
        if not os.path.lexists(target):
            return target
    for i in range(1, 10000):
        target = folder/(f'{stem}（{i}）.mp4')
        if not os.path.lexists(target):
            return target
    raise ConversionError('同名输出文件过多，请换一个文件夹。')

def mp4_boxes(path, require_faststart=True):
    """Check top-level bounds without loading media data."""
    size = path.stat().st_size
    boxes = []
    with path.open('rb') as f:
        pos = 0
        while pos < size:
            if size-pos < 8:
                raise ConversionError('输出 MP4 结构不完整。')
            f.seek(pos)
            n, typ = struct.unpack('>I4s', f.read(8))
            header = 8
            if n == 1:
                if size-pos < 16:
                    raise ConversionError('输出 MP4 扩展头不完整。')
                n = struct.unpack('>Q', f.read(8))[0]
                header = 16
            elif n == 0:
                n = size-pos
            if n < header or pos+n > size:
                raise ConversionError('输出 MP4 存在越界结构。')
            boxes.append((typ.decode('latin1'), pos, n))
            pos += n
    types = [b[0] for b in boxes]
    if not all(t in types for t in ('ftyp', 'moov', 'mdat')) or 'moof' in types:
        raise ConversionError('输出不是完整的标准 MP4。')
    if require_faststart and types.index('moov') > types.index('mdat'):
        raise ConversionError('输出索引没有前置。')
    return boxes

class Engine:
    def __init__(self, on_progress=None, full_check=False, mode='auto', speed='balanced', destination=None,
                 resolution=None, resource_limits=ResourceLimits()):
        self.ffmpeg = ROOT/'tools'/'ffmpeg.exe'
        self.ffprobe = ROOT/'tools'/'ffprobe.exe'
        self.on_progress = on_progress or (lambda stage, value: None)
        self.cancelled = threading.Event()
        self.process = None
        self.log_path = None
        self.gpu_available = None
        self.full_check = full_check
        self.output_directory = None
        self.log_lock = threading.Lock()
        self.phase_times = []
        self.mode = mode
        self.speed = speed
        self.resolution = resolution
        self.resource_limits = resource_limits
        self.destination = Path(destination) if destination else None
        self.smart_scan = False
        self.replace_source = False
        self.fast_replace = False
        self.original_name = False
        self.use_gpu = True
        self.write_logs = True
        self.batch_log = None
        self.last_stderr = ''
        self.on_estimate = lambda value: None
        self.operation_started = 0
        self.eta_tail = 0
        self.encoder_choice = None

    def stop(self):
        self.cancelled.set()
        p = self.process
        if p and p.poll() is None:
            try:
                p.terminate()
            except OSError:
                pass

    def check_cancel(self):
        if self.cancelled.is_set():
            raise Cancelled('已取消，原文件保留。')

    def log(self, text):
        if self.log_path:
            with self.log_lock:
                try:
                    with self.log_path.open('a', encoding='utf-8') as f:
                        f.write(str(text)+'\n')
                except OSError as exc:
                    self.on_progress('写入日志失败：'+str(exc), 0)

    def capture(self, args, timeout=60):
        self.check_cancel()
        if self.log_path:
            self.log('命令：'+json.dumps([str(a) for a in args], ensure_ascii=False))
        p = subprocess.Popen([str(a) for a in args], stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, creationflags=NO_WINDOW)
        self.process = p
        start = time.monotonic()
        limits = None
        try:
            if self.resource_limits.cpu or self.resource_limits.memory:
                limits = ProcessLimits(p, self.resource_limits)
            while True:
                try:
                    out, err = p.communicate(timeout=.25)
                    break
                except subprocess.TimeoutExpired:
                    self.check_cancel()
                    if time.monotonic()-start > timeout:
                        raise ConversionError('分析超时，文件可能损坏或无法读取。')
            self.log(err.decode('utf-8', 'replace'))
            if limits:
                limits.check_result(p.returncode, err.decode('utf-8', 'replace'))
            return p.returncode, out, err
        finally:
            if p.poll() is None:
                p.kill()
                p.communicate()
            if limits:
                limits.close()
            self.process = None

    def probe(self, path, deep=False):
        analysis = ['-probesize', '64M', '-analyzeduration', '30000000'] if deep else []
        code, out, err = self.capture([self.ffprobe, '-v', 'warning', '-protocol_whitelist', 'file,pipe,crypto']
                                     +analysis+['-show_format', '-show_streams', '-of', 'json', path])
        self.check_cancel()
        if code:
            raise ConversionError('无法读取视频，请确认文件已下载完整。')
        try:
            data = json.loads(out.decode('utf-8-sig'))
        except (ValueError, UnicodeError):
            raise ConversionError('无法读取视频信息。')
        if self.log_path:
            self.log(json.dumps(data, ensure_ascii=False, indent=2))
        return data

    def run_ffmpeg(self, args, duration, stage, start, span, writes_output=False):
        self.check_cancel()
        command = [str(self.ffmpeg), '-hide_banner', '-nostdin', '-nostats',
                   '-stats_period', '0.4', '-progress', 'pipe:1'] + [str(a) for a in args]
        if self.log_path:
            self.log('阶段：'+stage+'\n命令：'+json.dumps(command, ensure_ascii=False))
        self.on_progress(stage, start)
        started = time.monotonic()
        last_log = started
        elapsed_media = 0.0
        samples = deque()
        p = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             creationflags=NO_WINDOW, bufsize=1, text=True,
                             encoding='utf-8', errors='replace')
        self.process = p
        self.last_stderr = ''
        error_tail = []
        uses_gpu = any(a in ('h264_nvenc', 'h264_amf', 'h264_qsv') for a in args)
        limits = None
        error_reader = None
        def collect_errors():
            batch = []
            for error_line in p.stderr:
                batch.append(error_line.rstrip('\r\n'))
                error_tail.append(error_line)
                if len(error_tail) > 80:
                    del error_tail[:40]
                if len(batch) >= 64:
                    self.log('\n'.join(batch))
                    batch.clear()
            if batch:
                self.log('\n'.join(batch))
        try:
            if any(self.resource_limits):
                limits = ProcessLimits(p, self.resource_limits, uses_gpu)
            error_reader = threading.Thread(target=collect_errors, daemon=True)
            error_reader.start()
            for line in p.stdout:
                self.check_cancel()
                key, _, val = line.strip().partition('=')
                if key == 'out_time_us' and duration > 0:
                    try:
                        if writes_output and shutil.disk_usage(self.output_directory).free < 32*1024**2:
                            raise ConversionError('磁盘空间不足，已停止。原文件未修改。')
                        elapsed_media = max(elapsed_media, min(duration, int(val)/1e6))
                        ratio = min(.995, elapsed_media/duration)
                        elapsed_wall = time.monotonic()-started
                        detail = stage+'  '+self._clock(min(elapsed_media, duration))+' / '+self._clock(duration)
                        if elapsed_media > 0:
                            samples.append((elapsed_wall, elapsed_media))
                            while len(samples) > 2 and elapsed_wall-samples[0][0] > 6:
                                samples.popleft()
                            first_time, first_media = samples[0]
                            if elapsed_wall-first_time >= 1 and elapsed_media > first_media:
                                rate = (elapsed_media-first_media)/(elapsed_wall-first_time)
                                remaining = (duration-elapsed_media)/rate
                                self.on_estimate({'kind': 'live', 'remaining_seconds': max(1, remaining+self.eta_tail),
                                                  'elapsed_seconds': time.monotonic()-self.operation_started,
                                                  'method': stage})
                        self.on_progress(detail, start+span*ratio)
                        if time.monotonic()-last_log >= 10:
                            self.log('进度：'+detail)
                            last_log = time.monotonic()
                    except ValueError:
                        pass
            code = p.wait()
            self.check_cancel()
        finally:
            if p.poll() is None:
                p.kill()
                p.wait()
            if limits:
                limits.close()
            p.stdout.close()
            if error_reader:
                error_reader.join(timeout=5)
            p.stderr.close()
            self.process = None
        self.last_stderr = ''.join(error_tail)
        self.log('退出码：'+str(code))
        phase_seconds = round(time.monotonic()-started, 3)
        self.phase_times.append({'stage': stage, 'seconds': phase_seconds, 'exit_code': code})
        self.log('阶段耗时：'+str(phase_seconds)+' 秒')
        if limits:
            if limits.memory_bytes:
                self.log(f'处理进程内存上限：{limits.memory_bytes // 1024**2} MB（提交量）')
            limits.check_result(code, self.last_stderr)
        if code == 0:
            self.on_progress(stage+'完成', start+span)
        return code

    @staticmethod
    def index_reservation(video, audios, duration):
        # Conservative sample-table bound. Reserve at the front to avoid a second
        # full-file faststart move. Excess space is a legal MP4 free atom.
        frames = duration * Engine._fps(video)
        try:
            frames = max(frames, int(video.get('nb_frames', 0)))
        except (TypeError, ValueError):
            pass
        samples = 0
        for audio in audios:
            try:
                rate = max(48000, int(audio.get('sample_rate', 48000)))
            except (TypeError, ValueError):
                rate = 48000
            samples += duration * rate / 960
        estimate = 256*1024 + frames*32 + samples*24
        return min(256*1024**2, max(512*1024, int(math.ceil(estimate/65536))*65536))

    def mux(self, args, temp, duration, title, reservation):
        out = ['-map_metadata', '-1', '-map_chapters', '-1',
               '-max_muxing_queue_size', '4096', '-f', 'mp4']
        code = self.run_ffmpeg(args+out+['-moov_size', str(reservation), temp], duration, title, 5, 60, writes_output=True)
        if code and ('moov atom not large enough' in self.last_stderr.lower()
                     or 'reserved_moov_size' in self.last_stderr.lower()):
            self.log('预留索引空间不足，以相同编码设置重试 faststart；不因此重新编码。')
            code = self.run_ffmpeg(args+out+['-movflags', '+faststart', temp], duration, title+'（索引回退）',
                                   5, 60, writes_output=True)
        return code

    @staticmethod
    def _clock(seconds):
        value = max(0, int(seconds))
        return f'{value//3600:02d}:{value//60%60:02d}:{value%60:02d}'

    def announce_estimate(self, remaining, learned, method, kind='initial'):
        elapsed = time.monotonic()-self.operation_started
        factor = 1.4 if learned else 2
        estimate = {'kind': kind, 'method': method, 'learned': learned,
                    'estimated_seconds': round(elapsed+remaining, 2),
                    'remaining_seconds': remaining, 'elapsed_seconds': elapsed,
                    'low_seconds': math.floor(elapsed+remaining/factor),
                    'high_seconds': math.ceil(elapsed+remaining*factor)}
        self.on_estimate(estimate)
        return estimate

    def validate_decode(self, path, duration, video, timing, checking_source=False, force_full=False,
                        preserve_timestamps=False):
        full = force_full or (self.full_check if checking_source else self.full_output_check())
        stage = ('筛选源文件' if checking_source else '验证输出')+(' · 完整解码' if full else ' · 快速抽查')
        plan = timing.validation(video, duration, self._fps(video), full)
        self.eta_tail = 0
        self.announce_estimate(plan['seconds'], plan['learned'], stage, kind='phase')
        self.log('阶段预计：'+stage+' · '+str(round(plan['seconds'], 2))+' 秒')
        start = 5 if checking_source else 68
        base = ['-v', 'error', '-xerror']
        decoder = ['-protocol_whitelist', 'file,pipe,crypto', '-err_detect', 'explode']
        if full or duration <= 2:
            timestamps = ['-fps_mode', 'passthrough', '-enc_time_base:v', video['time_base']] if preserve_timestamps else []
            args = base+decoder+['-threads', '4', '-i', path, '-map', '0:'+str(video['index']),
                                 '-map', '0:a?']+timestamps+['-f', 'null', '-']
            code = self.run_ffmpeg(args, duration, stage, start, 98-start)
        else:
            positions = sorted(set(round(t, 6) for t in [0, duration*.25, duration*.5, duration*.75, duration-2]))
            inputs, outputs = [], []
            for i, position in enumerate(positions):
                inputs += decoder+['-threads', '2', '-ss', str(position), '-i', path]
                length = max(.05, min(1.0, duration-position))
                outputs += ['-map', f"{i}:{video['index']}", '-map', f'{i}:a?', '-t', str(length), '-f', 'null', '-']
            # The parallel seeks share one process. Its timestamps do not represent total work.
            code = self.run_ffmpeg(base+inputs+outputs, 0, stage, start, 98-start)
        if code != 0:
            raise ConversionError('全片解码检查失败。' if full else '关键位置解码检查失败。')
        if not self.resource_limits.cpu and not self.resource_limits.memory:
            timing.record(plan, self.phase_times[-1]['seconds'])
        return 'full' if full else 'quick'

    def full_output_check(self):
        return self.full_check or (self.resolution is None and self.replace_source and not self.fast_replace)

    def can_nvenc(self):
        return self.choose_encoder()['backend'] == 'nvenc'

    def choose_encoder(self):
        if not self.use_gpu:
            return {'backend': 'cpu', 'encoder': 'libx264', 'name': 'CPU 软件编码'}
        from hardware_info import select_encoder
        self.on_progress('正在选择硬件编码器', 3)
        choice = select_encoder(self.ffmpeg, self.capture)
        self.check_cancel()
        self.log('硬件编码实测：'+json.dumps(choice, ensure_ascii=False))
        return choice

    def standard_input(self, source, info, video, audios, duration, timing):
        fmt = info.get('format', {})
        brand = fmt.get('tags', {}).get('major_brand', '').strip().lower()
        if (source.suffix.lower() != '.mp4' or 'mp4' not in fmt.get('format_name', '').split(',')
                or brand in ('qt', 'm4a') or brand.startswith(('3gp', '3g2'))
                or video.get('codec_tag_string') != 'avc1' or video.get('codec_name') != 'h264'
                or video.get('pix_fmt') not in ('yuv420p', 'yuvj420p')
                or any(audio.get('codec_name') != 'aac' for audio in audios)):
            return False
        try:
            mp4_boxes(source, require_faststart=False)
            self.on_progress('检查原 MP4 是否需要修复', 2)
            self.validate_decode(source, duration, video, timing, checking_source=True)
        except ConversionError as exc:
            self.log('源文件需要修复：'+str(exc))
            return False
        return True

    def publish(self, temp, source, before):
        self.check_cancel()
        after = source.stat()
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
                after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
            raise ConversionError('处理期间源文件发生变化，请待下载完成后重试。')
        replace_source = self.replace_source and self.resolution is None
        if replace_source and source.suffix.lower() == '.mp4':
            temp.replace(source)
            return source
        while True:
            self.check_cancel()
            target = unique_output(source, temp.parent, self.original_name or replace_source)
            # Windows rename refuses an existing target.
            try:
                temp.rename(target)
            except FileExistsError:
                continue
            break
        if replace_source:
            try:
                source.unlink()
            except OSError as exc:
                raise ConversionError('标准 MP4 已保存至 '+str(target)+'，原文件未能移除：'+str(exc)) from exc
        return target

    def convert(self, input_path):
        operation_started = time.monotonic()
        self.operation_started = operation_started
        self.phase_times = []
        self.log_path = self.batch_log if self.write_logs else None
        self.output_directory = None
        self.encoder_choice = None
        self.eta_tail = 0
        source = Path(input_path).resolve()
        self.check_cancel()
        if self.write_logs and self.log_path is None:
            self.log_path = create_processing_log('处理', APP_NAME+' '+VERSION)
        if not source.is_file():
            raise ConversionError('找不到源视频。', '确认文件仍在原位置，或重新添加已移动的文件。')
        if not self.ffmpeg.is_file() or not self.ffprobe.is_file():
            raise ConversionError('程序组件不完整，请重新下载 EXE。')
        if self.mode not in ('auto', 'remux', 'encode') or self.speed not in ('quality', 'balanced', 'fast'):
            raise ConversionError('处理设置无效。')
        replace_source = self.replace_source and self.resolution is None
        folder = source.parent if replace_source or not self.destination else self.destination.resolve()
        if not folder.is_dir():
            raise ConversionError('输出文件夹不存在。', '选择一个可写的目录，另存修复结果。', 'destination')
        token = uuid.uuid4().hex
        temp = folder/('.mp4fix-'+token+'.tmp')
        before = source.stat()
        self.log(APP_NAME+' '+VERSION+'\n'+datetime.datetime.now().isoformat())
        self.log('FFmpeg 9.0.1 (GPLv3)：https://ffmpeg.org/ ；构建与源码说明：https://www.gyan.dev/ffmpeg/builds/ ；许可证随 EXE 内置。')
        self.log('原文件：'+str(source))
        if any(self.resource_limits):
            self.log('资源限制：'+self.resource_limits.describe())
        self.on_progress('正在分析视频', 2)
        try:
            info = self.probe(source)
        except ConversionError:
            info = self.probe(source, deep=True)
        if not info.get('streams') or not info.get('format', {}).get('duration'):
            info = self.probe(source, deep=True)
        streams = info.get('streams', [])
        audios = [s for s in streams if s.get('codec_type') == 'audio']
        video = main_video(info)
        if not video:
            raise ConversionError('文件没有可用的视频轨道。')
        width, height = int(video.get('width', 0)), int(video.get('height', 0))
        if width <= 0 or height <= 0:
            raise ConversionError('视频分辨率无效，无法可靠修复。')
        try:
            duration = float(info.get('format', {}).get('duration', 0))
        except (TypeError, ValueError):
            duration = 0.0
        if not math.isfinite(duration) or duration <= 0:
            raise ConversionError('无法确定视频时长，文件可能不完整。')
        resized = self.resolution is not None
        expected = None
        if resized:
            display_width, display_height = video_dimensions(video)
            short_edge = min(display_width, display_height)
            if self.resolution not in RESOLUTIONS.values() or self.resolution >= short_edge:
                raise ConversionError('所选分辨率必须低于源视频。', '重新打开“降分辨率”，选择更低的档位。')
            expected = tuple(max(2, (edge*self.resolution//short_edge)//2*2)
                             for edge in (display_width, display_height))
        from hardware_info import hardware_info, TimingHistory
        timing = TimingHistory()
        if self.smart_scan and not resized and self.standard_input(source, info, video, audios, duration, timing):
            self.log('已跳过：原文件通过 MP4 兼容性及解码检查。')
            return {'ok': True, 'skipped': True, 'source': str(source), 'output': str(source),
                    'log': str(self.log_path) if self.log_path else None,
                    'elapsed_seconds': round(time.monotonic()-operation_started, 3),
                    'phase_times': list(self.phase_times)}
        self.output_directory = folder
        self.phase_times.clear()
        compatible = video.get('codec_name') == 'h264' and video.get('pix_fmt') in ('yuv420p', 'yuvj420p')
        if self.mode == 'remux' and not compatible and not resized:
            raise ConversionError('源视频无法直接封装成兼容的 H.264 MP4。',
                                  '切换到“自动修复”，允许重新编码。', 'auto')
        copy_video = compatible and self.mode != 'encode' and not resized
        reservation = self.index_reservation(video, audios, duration)
        self.log('处理模式：'+self.mode+'；速度：'+self.speed+'；索引预留：'+str(reservation)+' 字节（避免完成后搬移整片）')
        free = shutil.disk_usage(self.output_directory).free
        required = max(128*1024**2, before.st_size + 64*1024**2)
        if not copy_video and not resized:
            required = max(required, int(width*height*max(24, self._fps(video))*duration*.08/8))
        if free < required:
            raise ConversionError('输出磁盘剩余空间不足。', '选择剩余空间充足的目录，另存处理结果。', 'destination')
        maps = ['-map', '0:'+str(video['index'])]
        for audio in audios:
            maps += ['-map', '0:'+str(audio['index'])]
        is_mpegts = info.get('format', {}).get('format_name') == 'mpegts'
        audio_options = []
        for i, audio in enumerate(audios):
            if audio.get('codec_name') == 'aac':
                audio_options += ['-c:a:'+str(i), 'copy']
                if is_mpegts:
                    audio_options += ['-bsf:a:'+str(i), 'aac_adtstoasc']
            else:
                bitrate = '384k' if int(audio.get('channels', 2)) > 2 else '192k'
                audio_options += ['-c:a:'+str(i), 'aac', '-b:a:'+str(i), bitrate]
        rate_limit = []
        if resized:
            self.log(f'降分辨率：{display_width}×{display_height} → {expected[0]}×{expected[1]}')
            audio_rate = 0
            for audio in audios:
                estimate = 384000 if int(audio.get('channels', 2)) > 2 else 192000
                bitrate = str(audio.get('bit_rate', ''))
                audio_rate += int(bitrate) if audio.get('codec_name') == 'aac' and bitrate.isdecimal() else estimate
            budget = int(before.st_size*8/duration*.9) - audio_rate
            rate = max(64000, budget)
            rate_limit = ['-maxrate:v', str(rate), '-bufsize:v', str(rate)]
        self.encoder_choice = {'backend': 'copy', 'encoder': 'copy', 'name': '无损封装（无需 GPU 编码）'} if copy_video else self.choose_encoder()
        hardware = hardware_info()
        if self.log_path:
            self.log('本机硬件：'+json.dumps(hardware, ensure_ascii=False))
        input_args = ['-y', '-fflags', '+genpts', '-protocol_whitelist', 'file,pipe,crypto']
        recovered_ts = False
        repaired_timestamps = False
        fps = self._fps(video)
        transcode_audio = any(audio.get('codec_name') != 'aac' for audio in audios)
        resize_pipeline = None

        def estimate_conversion():
            full = self.full_output_check() or recovered_ts or repaired_timestamps
            output_video = {**video, 'codec_name': 'h264'}
            if expected:
                output_video.update(width=expected[0], height=expected[1])
            validation = timing.validation(output_video, duration, fps, full)
            backend = self.encoder_choice['backend']
            phase = timing.conversion(video, duration, before.st_size, fps, backend == 'copy',
                                      backend, self.speed, transcode_audio,
                                      resize_pipeline=resize_pipeline, resize_target=self.resolution)
            if backend in ('nvenc', 'qsv', 'amf') and 0 < self.resource_limits.gpu < 100:
                phase['seconds'] *= 100 / self.resource_limits.gpu
            self.eta_tail = validation['seconds']
            prediction = self.announce_estimate(phase['seconds']+self.eta_tail,
                                                phase['learned'] and validation['learned'],
                                                self.encoder_choice['name'])
            if self.log_path:
                self.log('预计耗时：'+json.dumps(prediction, ensure_ascii=False))
            return phase, prediction

        def mux_attempt(args, title):
            nonlocal recovered_ts
            code = self.mux(['-xerror']+args, temp, duration, title, reservation)
            if code and is_mpegts and 'corrupt input packet' in self.last_stderr.lower():
                # A TS discontinuity can flag a packet whose payload still decodes.
                # Keep the packets and require strict decoding of the complete MP4.
                self.log('TS 数据包被标记异常；保留数据包重试，输出须通过全片解码。')
                recovered_ts = True
                estimate_conversion()
                code = self.mux(['-max_error_rate', '0']+args, temp, duration,
                                title+'（TS 容错）', reservation)
                recovered_ts = code == 0
            return code

        conversion_plan, initial_estimate = estimate_conversion()
        method = ''
        success = False
        try:
            # A random .tmp filename avoids Explorer probing an unfinished MP4.
            with temp.open('xb'):
                pass
            if copy_video:
                args = input_args+['-i', source]+maps+['-c:v', 'copy', '-tag:v', 'avc1']+audio_options
                code = mux_attempt(args, '无损封装为标准 MP4')
                if code == 0:
                    method = '无损封装' if all(a.get('codec_name') == 'aac' for a in audios) else '视频无损封装，音频转换为 AAC'
                elif self.mode == 'remux':
                    raise ConversionError('无损封装失败。', '切换到“自动修复”，允许重新编码。', 'auto')
            if not method:
                encoded = False
                options = []
                if self.encoder_choice['backend'] == 'copy':
                    self.encoder_choice = self.choose_encoder()
                    conversion_plan, _ = estimate_conversion()
                if self.encoder_choice['backend'] == 'nvenc':
                    preset = {'quality': 'p6', 'balanced': 'p4', 'fast': 'p2'}[self.speed]
                    lookahead = {'quality': '32', 'balanced': '16', 'fast': '0'}[self.speed]
                    quick_resize = resized and self.speed == 'fast'
                    nvenc = ['-c:v', 'h264_nvenc', '-preset', preset, '-tune', 'hq',
                             '-rc', 'vbr', '-cq', '18', '-b:v', '0', '-spatial-aq', '1',
                             '-temporal-aq', '0' if quick_resize else '1', '-rc-lookahead', lookahead,
                             '-bf', '2' if quick_resize else '3',
                             '-profile:v', 'high']
                    cuda_compatible = (width % 2 == 0 and height % 2 == 0
                                       and video.get('codec_name') in ('h264', 'hevc')
                                       and (not resized or video_rotation(video) % 360 == 0))
                    if cuda_compatible:
                        title = 'GPU 解码、缩放和编码（NVIDIA）' if resized else 'H.264 高质量硬件转换'
                        options.append((['-hwaccel', 'cuda', '-hwaccel_output_format', 'cuda'], nvenc, title, 'nvenc'))
                    # CUDA decode may be unsupported even when NVENC is available.
                    title = 'CPU 缩放、GPU 编码（NVIDIA）' if resized else 'H.264 高质量硬件转换'
                    options.append(([], nvenc+['-pix_fmt', 'yuv420p'], title, 'nvenc'))
                elif self.encoder_choice['backend'] == 'qsv':
                    preset = {'quality': 'slow', 'balanced': 'medium', 'fast': 'veryfast'}[self.speed]
                    options.append(([], ['-c:v', 'h264_qsv', '-preset', preset, '-global_quality', '18',
                                         '-pix_fmt', 'nv12'], 'H.264 Intel 硬件转换', 'qsv'))
                elif self.encoder_choice['backend'] == 'amf':
                    quality = {'quality': 'quality', 'balanced': 'balanced', 'fast': 'speed'}[self.speed]
                    options.append(([], ['-c:v', 'h264_amf', '-quality', quality, '-rc', 'cqp',
                                         '-qp_i', '18', '-qp_p', '18', '-qp_b', '18', '-pix_fmt', 'nv12'],
                                    'H.264 AMD 硬件转换', 'amf'))
                options.append(([], ['-c:v', 'libx264', '-preset', 'veryfast' if self.speed == 'fast' else 'fast', '-crf', '18',
                                     '-pix_fmt', 'yuv420p'], 'H.264 高质量转换', 'cpu'))
                for input_options, encoder, title, backend in options:
                    self.check_cancel()
                    if resized or backend != self.encoder_choice['backend']:
                        self.encoder_choice = {**self.encoder_choice, 'backend': backend, 'encoder': encoder[1], 'name': title}
                        if resized:
                            resize_pipeline = 'cuda' if input_options else 'software'
                        conversion_plan, initial_estimate = estimate_conversion()
                    filters = [] if width % 2 == 0 and height % 2 == 0 else ['pad=ceil(iw/2)*2:ceil(ih/2)*2']
                    if resized:
                        target_width, target_height = expected
                        if resize_pipeline == 'cuda':
                            algo = {'fast': 'bilinear', 'balanced': 'bicubic', 'quality': 'lanczos'}[self.speed]
                            filters = [f'scale_cuda={target_width}:{target_height}:interp_algo={algo}:format=nv12']
                        else:
                            algo = 'fast_bilinear' if self.speed == 'fast' else 'lanczos'
                            filters = [f'scale={target_width}:{target_height}:flags={algo}']
                    # Bounded GOP improves normal seeking and avoids decoding a
                    # long pre-roll for each quick-validation checkpoint.
                    gop = max(1, round(self._fps(video)*2))
                    encode_args = input_args+input_options+['-i', source]+maps+encoder+rate_limit
                    output_args = ['-g', str(gop), '-fps_mode', 'passthrough', '-tag:v', 'avc1']+audio_options
                    args = encode_args+(['-vf', ','.join(filters)] if filters else [])+output_args
                    code = mux_attempt(args, title)
                    error = self.last_stderr.lower()
                    if code and ('non-monotonic dts' in error or 'non-strictly-monotonic pts' in error):
                        # Preserve all decoded frames and adjust only timestamps that
                        # would collide, using one tick on the 90 kHz video clock.
                        self.log('发现重复或倒退的时间戳，修正视频时间戳后重试。')
                        repaired_timestamps = True
                        estimate_conversion()
                        timestamp_filters = filters+['settb=1/90000', "setpts='if(isnan(PREV_OUTPTS),PTS,max(PTS,PREV_OUTPTS+1))'"]
                        args = encode_args+['-vf', ','.join(timestamp_filters), '-enc_time_base:v', '1:90000',
                                            '-video_track_timescale', '90000']+output_args
                        code = mux_attempt(args, title+'（时间戳修复）')
                        repaired_timestamps = code == 0
                    if code == 0:
                        if resized:
                            method = f'{title}；降至 {expected[0]}×{expected[1]}'
                        else:
                            method = title+'；原帧率、尺寸保留' if not filters else title+'；奇数尺寸边缘补 1 像素'
                        encoded = True
                        break
                if not encoded:
                    if 'no space left' in error:
                        raise ConversionError('转换时输出磁盘空间耗尽。', '换到空间更充足的目录后重试。', 'destination')
                    if any(term in error for term in ('corrupt input packet', 'invalid data', 'error while decoding')):
                        raise ConversionError('转码时读到无法处理的视频数据。', '请重新下载源视频，再用“自动修复”处理。')
                    if self.mode == 'encode' and compatible and not resized:
                        raise ConversionError('强制转码失败。', '切换到“仅无损封装”，直接使用原视频轨道。', 'remux')
                    raise ConversionError('转换失败，详情见处理日志。')
            conversion_seconds = self.phase_times[-1]['seconds']
            if recovered_ts:
                method += '；TS 容错'
            if repaired_timestamps:
                method += '；时间戳修复'
            self.check_cancel()
            self.on_progress('检查 MP4 结构', 66)
            boxes = mp4_boxes(temp)
            output = self.probe(temp)
            out_streams = output.get('streams', [])
            ov = [s for s in out_streams if s.get('codec_type') == 'video']
            oa = [s for s in out_streams if s.get('codec_type') == 'audio']
            if len(ov) != 1 or ov[0].get('codec_name') != 'h264' or ov[0].get('pix_fmt') not in ('yuv420p', 'yuvj420p'):
                raise ConversionError('输出视频编码检查失败。')
            if len(oa) != len(audios) or any(a.get('codec_name') != 'aac' for a in oa):
                raise ConversionError('输出音频轨道检查失败。')
            actual_reencoded = '无损封装' not in method
            if expected is None:
                expected = (width+(width%2 if actual_reencoded else 0), height+(height%2 if actual_reencoded else 0))
            if (ov[0].get('width'), ov[0].get('height')) != expected:
                raise ConversionError('输出尺寸与预期不一致。')
            actual = float(output.get('format', {}).get('duration', 0))
            if not math.isfinite(actual) or actual <= 0:
                raise ConversionError('输出视频时长无效。')
            if abs(actual-duration) > max(.25, 2/max(1, self._fps(video))):
                raise ConversionError('修复前后时长不一致，未发布不完整的输出。')
            try:
                original_frames = int(video.get('nb_frames', 0))
                output_frames = int(ov[0].get('nb_frames', 0))
            except (TypeError, ValueError):
                original_frames = output_frames = 0
            if original_frames > 0 and output_frames > 0 and original_frames != output_frames:
                raise ConversionError('修复前后视频帧数不一致，未发布不完整的输出。')
            try:
                validation_mode = self.validate_decode(temp, actual, ov[0], timing,
                                                       force_full=recovered_ts or repaired_timestamps,
                                                       preserve_timestamps=repaired_timestamps)
            except ConversionError as exc:
                if not actual_reencoded:
                    raise ConversionError(str(exc), '切换到“强制转换 H.264”，重建视频轨道后再验证。', 'encode') from exc
                raise
            self.check_cancel()
            output_size = temp.stat().st_size
            if resized and output_size >= before.st_size:
                raise ConversionError('降分辨率后的文件仍未小于原文件。',
                                      '请在“降分辨率”中选择更低的档位。源视频码率很低时，可降低的体积有限。')
            target = self.publish(temp, source, before)
            success = True
            result = {'ok': True, 'source': str(source), 'output': str(target),
                      'bytes': output_size, 'method': method,
                      'reencoded_video': actual_reencoded,
                      'resolution': self.resolution,
                      'duration': actual, 'log': str(self.log_path) if self.log_path else None, 'boxes': boxes,
                      'validation_mode': validation_mode,
                      'elapsed_seconds': round(time.monotonic()-operation_started, 3),
                      'phase_times': list(self.phase_times),
                      'speed_profile': self.speed, 'processing_mode': self.mode,
                      'index_reserved_bytes': reservation,
                      'hardware': hardware, 'selected_encoder': dict(self.encoder_choice),
                      'initial_estimate_seconds': initial_estimate['estimated_seconds'],
                      'large_for_free_telegram': output_size > 2147483648}
            if self.log_path:
                self.log('成功：'+json.dumps(result, ensure_ascii=False))
            if not any(self.resource_limits):
                timing.record(conversion_plan, conversion_seconds)
            self.on_progress('完成', 100)
            return result
        except Exception as exc:
            self.log('未完成：'+repr(exc))
            raise
        finally:
            if not success and temp.exists():
                try:
                    temp.unlink()
                except OSError as exc:
                    self.log('未完成临时文件清理失败：'+repr(exc))

    @staticmethod
    def _fps(video):
        for value in (video.get('avg_frame_rate'), video.get('r_frame_rate')):
            try:
                a, b = value.split('/')
                fps = float(a)/float(b)
                if math.isfinite(fps) and fps > 0:
                    return fps
            except (AttributeError, TypeError, ValueError, ZeroDivisionError):
                continue
        return 30.0


def gui(initial_files, smoke_seconds=None, result_file=None, smoke_cancel_after=None, initial_full_check=False,
        smoke_theme=None, smoke_font=None):
    from app_ui import run_gui
    return run_gui(sys.modules[__name__], initial_files, smoke_seconds, result_file,
                   smoke_cancel_after, initial_full_check, smoke_theme, smoke_font)


def main():
    parser = argparse.ArgumentParser(description=APP_NAME)
    parser.add_argument('--batch-test', nargs='+', help=argparse.SUPPRESS)
    parser.add_argument('--result', help=argparse.SUPPRESS)
    parser.add_argument('--gui-smoke-test', type=float, help=argparse.SUPPRESS)
    parser.add_argument('--smoke-cancel-after', type=float, help=argparse.SUPPRESS)
    parser.add_argument('--full-check', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--speed', choices=['quality', 'balanced', 'fast'], default='balanced', help=argparse.SUPPRESS)
    parser.add_argument('--mode', choices=['auto', 'remux', 'encode'], default='auto', help=argparse.SUPPRESS)
    parser.add_argument('--output-directory', help=argparse.SUPPRESS)
    parser.add_argument('--smoke-theme', help=argparse.SUPPRESS)
    parser.add_argument('--smoke-font', type=int, help=argparse.SUPPRESS)
    parser.add_argument('files', nargs='*')
    args = parser.parse_args()
    if args.batch_test:
        results = []
        for item in args.batch_test:
            engine = Engine(full_check=args.full_check, mode=args.mode, speed=args.speed, destination=args.output_directory)
            try:
                result = engine.convert(item)
            except Exception as exc:
                result = {'ok': False, 'source': item, 'error': str(exc),
                          'log': str(engine.log_path) if engine.log_path else None}
            results.append(result)
        if args.result:
            Path(args.result).write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
        return 0 if all(r['ok'] for r in results) else 1
    gui(args.files, args.gui_smoke_test, args.result, args.smoke_cancel_after, args.full_check, args.smoke_theme, args.smoke_font)
    return 0

if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as exc:
        if '--batch-test' not in sys.argv:
            import tkinter.messagebox
            tkinter.messagebox.showerror(APP_NAME, '启动失败：'+str(exc))
        raise
