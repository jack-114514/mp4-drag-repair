"""Regression checks for mixed batches and source replacement."""
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

from mp4_normalizer import ConversionError, Engine, NO_WINDOW, mp4_boxes


def main():
    root = Path(__file__).resolve().parent
    with TemporaryDirectory(dir=root) as directory:
        folder = Path(directory)
        engine = Engine(full_check=True)
        engine.smart_scan = True
        engine.replace_source = True
        engine.original_name = True
        engine.use_gpu = False
        engine.write_logs = True
        engine.batch_log = folder/'批量.log'

        def sample(name, options):
            path = folder/name
            subprocess.run([str(engine.ffmpeg), '-v', 'error', '-nostdin', '-f', 'lavfi', '-i',
                            'testsrc2=size=160x90:rate=24', '-t', '2', '-c:v', 'libx264',
                            '-g', '12', *options, str(path)], creationflags=NO_WINDOW, check=True)
            return path

        normal = sample('正常.mp4', [])
        original = normal.read_bytes()
        assert engine.convert(normal)['skipped']
        assert normal.read_bytes() == original

        fragment = sample('分片.mp4', ['-movflags', '+frag_keyframe+empty_moov'])
        renamed_ts = sample('实际为 TS.mp4', ['-f', 'mpegts'])
        legacy = sample('原始文件.ssv', ['-f', 'mpegts'])
        mov = sample('实际为 MOV.mp4', ['-f', 'mov'])
        for source in (fragment, renamed_ts, legacy, mov):
            result = engine.convert(source)
            assert not result.get('skipped') and not result['reencoded_video']
            output = source.with_suffix('.mp4')
            assert Path(result['output']) == output
            assert output.is_file() and (source == output or not source.exists())
            assert result['validation_mode'] == 'full'
            assert result['log'] == str(engine.batch_log)
            mp4_boxes(output)

        corrupt = folder/'无法读取.mp4'
        corrupt_data = b'invalid MP4'
        corrupt.write_bytes(corrupt_data)
        try:
            engine.convert(corrupt)
        except ConversionError:
            pass
        else:
            raise AssertionError(f'Expected failure for {corrupt.name}')
        assert corrupt.read_bytes() == corrupt_data

        existing = sample('重名.mp4', [])
        existing_bytes = existing.read_bytes()
        numbered = {}
        for number in (1, 2):
            source = sample('重名.css', ['-f', 'mpegts'])
            result = engine.convert(source)
            output = folder/f'重名（{number}）.mp4'
            assert Path(result['output']) == output and not source.exists()
            mp4_boxes(output)
            numbered[output] = output.read_bytes()
        assert existing.read_bytes() == existing_bytes
        assert all(path.read_bytes() == data for path, data in numbered.items())
        assert normal.read_bytes() == original
        assert list(folder.glob('*.log')) == [engine.batch_log]
        assert not list(folder.glob('*.处理日志.txt'))
        assert not list(folder.glob('.mp4fix-*.tmp'))

        engine.fast_replace = True
        engine.full_check = False
        fast_source = sample('快速替换.mp4', ['-f', 'mpegts'])
        assert engine.convert(fast_source)['validation_mode'] == 'quick'
        mp4_boxes(fast_source)
        engine.full_check = True
        strict_source = sample('完整替换.mp4', ['-f', 'mpegts'])
        assert engine.convert(strict_source)['validation_mode'] == 'full'

        engine.smart_scan = False
        engine.replace_source = False
        engine.write_logs = False
        engine.mode = 'encode'
        result = engine.convert(normal)
        assert Path(result['output']).name == '正常（1）.mp4'
        assert result['selected_encoder']['backend'] == 'cpu' and result['log'] is None

        # Segment boundaries reset the MPEG-TS continuity counters while keeping
        # a continuous timeline. The flagged packets still contain valid frames.
        sample('分段-%d.ts', ['-t', '6', '-g', '24', '-f', 'segment', '-segment_time', '2',
                               '-segment_format', 'mpegts'])
        ts = folder/'分段下载.css'
        ts_data = b''.join((folder/f'分段-{i}.ts').read_bytes() for i in range(3))
        ts.write_bytes(ts_data)
        engine.mode = 'auto'
        engine.full_check = False
        result = engine.convert(ts)
        assert 'TS 容错' in result['method'] and not result['reencoded_video']
        assert result['validation_mode'] == 'full' and ts.read_bytes() == ts_data
        assert engine.probe(result['output'])['streams'][0]['nb_frames'] == '144'

        # Two frames 1 ms apart collide at the default 24 fps encoder time base.
        mov = sample('相邻时间戳.mov', ['-t', '4', '-c:v', 'libx265', '-preset', 'ultrafast',
                                         '-x265-params', 'log-level=error:bframes=0',
                                         '-vf', "settb=1/1000,setpts='if(eq(N,25),PREV_OUTPTS+1,PTS)'",
                                         '-fps_mode', 'passthrough', '-enc_time_base:v', '1:1000',
                                         '-video_track_timescale', '1000', '-tag:v', 'hvc1'])
        result = engine.convert(mov)
        assert '时间戳修复' in result['method'] and result['validation_mode'] == 'full'
        assert engine.probe(result['output'])['streams'][0]['nb_frames'] == '96'
        print('SMART BATCH CHECKS PASSED')


if __name__ == '__main__':
    main()
