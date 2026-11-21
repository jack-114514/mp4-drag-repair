import pathlib
import sys
from PIL import Image, ImageDraw
from mp4_normalizer import VERSION
B=pathlib.Path(__file__).resolve().parent
app_name = 'MP4一拖修复_v'+VERSION
version_tuple = tuple(map(int, VERSION.split('.')))+(0,)
folder_build = '--folder' in sys.argv
im=Image.new('RGBA',(256,256),(0,0,0,0));d=ImageDraw.Draw(im)
d.rounded_rectangle((8,8,248,248),48,fill='#2764e7')
d.rounded_rectangle((45,54,211,181),18,fill='white')
d.polygon([(111,83),(111,150),(168,116)],fill='#2764e7')
d.rounded_rectangle((83,198,173,210),6,fill='white')
im.save(B/'app.ico',sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])
spec=f'''# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_data_files
a = Analysis([{str(B/'mp4_normalizer.py')!r}], pathex=[{str(B)!r}], binaries=[],
    datas=[({str(B/'tools'/'ffmpeg.exe')!r},'tools'),({str(B/'tools'/'ffprobe.exe')!r},'tools'),
           ({str(B/'licenses')!r},'licenses'),({str(B/'mp4_normalizer.py')!r},'source'),({str(B/'app_ui.py')!r},'source'),({str(B/'hardware_info.py')!r},'source'),({str(B/'resource_limits.py')!r},'source'),({str(B/'app.ico')!r},'.')]+collect_data_files('tkinterdnd2')+collect_data_files('ttkbootstrap'),
    hiddenimports=['tkinter','tkinter.ttk','tkinter.filedialog','tkinter.messagebox','tkinterdnd2','ttkbootstrap','app_ui','hardware_info'],
    hookspath=[], hooksconfig={{}}, runtime_hooks=[],
    excludes=['numpy','pandas','matplotlib','scipy','IPython','pytest'], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz,a.scripts,{('[]' if folder_build else 'a.binaries,a.datas,[]')},name={app_name!r},
          exclude_binaries={folder_build!r},
          debug=False,bootloader_ignore_signals=False,strip=False,upx=False,
          console=False,disable_windowed_traceback=False,icon={str(B/'app.ico')!r},
          version={str(B/'version.txt')!r},manifest={str(B/'app.manifest')!r})
'''
if folder_build:
    spec += f"coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name={app_name!r})\n"
(B/'mp4_normalizer.spec').write_text(spec,encoding='utf-8')
(B/'version.txt').write_text(f'''VSVersionInfo(ffi=FixedFileInfo(filevers={version_tuple},prodvers={version_tuple},mask=0x3f,flags=0,OS=0x40004,fileType=0x1,subtype=0,date=(0,0)),kids=[StringFileInfo([StringTable('080404B0',[StringStruct('CompanyName','Local Tools'),StringStruct('FileDescription','MP4一拖修复'),StringStruct('FileVersion','{VERSION}'),StringStruct('InternalName','MP4Normalizer'),StringStruct('OriginalFilename','{app_name}.exe'),StringStruct('ProductName','MP4一拖修复'),StringStruct('ProductVersion','{VERSION}')])]),VarFileInfo([VarStruct('Translation',[2052,1200])])])''',encoding='utf-8')
(B/'app.manifest').write_text(f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<assembly xmlns="urn:schemas-microsoft-com:asm.v1" manifestVersion="1.0">
 <assemblyIdentity version="{VERSION}.0" processorArchitecture="amd64" name="LocalTools.MP4Normalizer" type="win32"/>
 <trustInfo xmlns="urn:schemas-microsoft-com:asm.v3"><security><requestedPrivileges><requestedExecutionLevel level="asInvoker" uiAccess="false"/></requestedPrivileges></security></trustInfo>
 <compatibility xmlns="urn:schemas-microsoft-com:compatibility.v1"><application><supportedOS Id="{{8e0f7a12-bfb3-4fe8-b9a5-48fd50a15a9a}}"/></application></compatibility>
 <application xmlns="urn:schemas-microsoft-com:asm.v3"><windowsSettings><longPathAware xmlns="http://schemas.microsoft.com/SMI/2016/WindowsSettings">true</longPathAware><dpiAwareness xmlns="http://schemas.microsoft.com/SMI/2016/WindowsSettings">PerMonitorV2</dpiAwareness></windowsSettings></application>
</assembly>''',encoding='utf-8')
(B/'licenses'/'NOTICE.txt').write_text(f'''MP4一拖修复 {VERSION}
所有视频均在本机处理，程序不上传视频，不读取 Telegram 账户。
视频转换使用独立运行的 FFmpeg/FFprobe 9.0.1 Essentials（Gyan.dev 构建），许可证 GPLv3。
FFmpeg 许可证、构建信息随包附带。FFmpeg 源码：https://ffmpeg.org/download.html
构建说明及外部依赖源码：https://www.gyan.dev/ffmpeg/builds/
本程序 Python 源文件随包放在 source/mp4_normalizer.py，采用 MIT License。
界面使用 ttkbootstrap 1.20.3（MIT）：https://github.com/israel-dryer/ttkbootstrap\nPython、Tcl/Tk 和 PyInstaller 采用其各自开源许可证。
''',encoding='utf-8')
print('构建描述与图标已生成')
