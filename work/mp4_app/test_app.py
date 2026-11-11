import pathlib,subprocess,json,hashlib,sys,importlib.util,ctypes,struct
B=pathlib.Path(__file__).resolve().parent
T=B.parent/'app_tests';T.mkdir(exist_ok=True)
F=B/'tools'/'ffmpeg.exe';P=B/'tools'/'ffprobe.exe'
def generate(name,options,audio=True):
    out=T/name
    cmd=[str(F),'-v','error','-nostdin','-y','-f','lavfi','-i','testsrc2=size=320x180:rate=24']
    if audio:cmd+=['-f','lavfi','-i','sine=frequency=440:sample_rate=48000']
    cmd+=['-t','2']+options
    if audio:cmd+=['-c:a','aac','-b:a','128k']
    cmd+=[str(out)]
    subprocess.run(cmd,check=True,creationflags=0x08000000,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
    return out
files=[generate('中文 未改名的下载.ssv',['-c:v','libx264','-preset','ultrafast','-pix_fmt','yuv420p','-f','mpegts']),
       generate('中文 HEVC.mp4',['-c:v','libx265','-preset','ultrafast','-x265-params','log-level=error','-tag:v','hev1','-pix_fmt','yuv420p','-f','mp4']),
       generate('无音频.mkv',['-c:v','mpeg4','-q:v','4','-f','matroska'],audio=False)]
before={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
result=T/'source_test_results.json'
subprocess.run([sys.executable,str(B/'mp4_normalizer.py'),'--batch-test']+[str(p) for p in files]+['--result',str(result)],check=True)
results=json.loads(result.read_text(encoding='utf-8'))
assert not results[0]['reencoded_video']
assert results[1]['reencoded_video'] and results[2]['reencoded_video']
for r in results:
    assert r['ok'] and pathlib.Path(r['output']).is_file()
    assert hashlib.sha256(pathlib.Path(r['source']).read_bytes()).hexdigest()==before[r['source']]
    data=json.loads(subprocess.check_output([str(P),'-v','error','-show_streams','-show_format','-of','json',r['output']],creationflags=0x08000000))
    assert data['streams'][0]['codec_name']=='h264'
    assert (data['streams'][0]['width'],data['streams'][0]['height'])==(320,180)
    assert data['streams'][0]['avg_frame_rate']=='24/1'
    assert data['streams'][0]['nb_frames']=='48'
    if '无音频' in r['source']:assert len(data['streams'])==1
# Verify no-overwrite behavior when the same source is processed again.
subprocess.run([sys.executable,str(B/'mp4_normalizer.py'),'--batch-test',str(files[0]),'--result',str(T/'repeat.json')],check=True)
repeat=json.loads((T/'repeat.json').read_text(encoding='utf-8'))[0]
assert repeat['output']!=results[0]['output'] and pathlib.Path(repeat['output']).exists()
bad=T/'不是视频.txt';bad.write_text('not a video',encoding='utf-8')
failed=subprocess.run([sys.executable,str(B/'mp4_normalizer.py'),'--batch-test',str(bad),'--result',str(T/'failure.json')])
assert failed.returncode==1 and not bad.with_name('不是视频_正常版.mp4').exists()
spec=importlib.util.spec_from_file_location('normalizer',B/'mp4_normalizer.py');module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
engine=module.Engine();engine.stop()
try:engine.convert(files[0]);raise AssertionError('cancel failed')
except module.Cancelled:pass
# Verify Tcl list parsing for the new native drag-and-drop component.
import tkinter as tk
tcl=tk.Tcl()
raw=tcl.call('list',*[str(p) for p in files[:2]])
assert list(tcl.splitlist(raw))==[str(p) for p in files[:2]]
(T/'test_summary.json').write_text(json.dumps({'passed':True,'checks':['Chinese SSV MPEG-TS lossless remux','HEVC to H264','silent source without added audio','dimensions/fps/frame counts','no overwrite','source hashes unchanged','invalid file rejection','cancellation','Unicode drag-and-drop Tcl list parsing'],'results':results},ensure_ascii=False,indent=2),encoding='utf-8')
print('ALL TESTS PASSED')
