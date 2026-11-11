"""Regression checks for mixed batches and source replacement."""
import shutil
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
        corrupt.write_bytes(b'invalid MP4')
        collision = folder/'正常.ssv'
        shutil.copyfile(normal, collision)
        for source in (corrupt, collision):
            before = source.read_bytes()
            try:
                engine.convert(source)
            except ConversionError:
                pass
            else:
                raise AssertionError(f'Expected failure for {source.name}')
            assert source.read_bytes() == before
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
        assert Path(result['output']).name == '正常_1.mp4'
        assert result['selected_encoder']['backend'] == 'cpu' and result['log'] is None
        print('SMART BATCH CHECKS PASSED')


if __name__ == '__main__':
    main()
