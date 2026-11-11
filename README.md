# MP4 一拖修复

Windows 10/11 x64 离线视频工具。将视频或文件夹拖入窗口，程序按实际内容识别视频。能无损封装的文件优先无损封装，需要转码时输出 H.264/AAC MP4。

- 批量智能筛选会跳过通过检查的 MP4，并可按原名替换需要修复的文件。
- 可选择 GPU 加速、完整检查或快速替换验证。完整检查逐帧解码，快速检查只抽查关键位置。
- 任务列表支持重试失败项，并显示当前文件和队列的预计剩余时间。
- 处理日志写入程序目录下的 `日志` 文件夹，点击“查看日志”在资源管理器中打开。

详细用法见 [使用说明](work/mp4_app/使用说明.txt)。

## 从源码构建

1. 准备 Windows x64、Python 3.11 或 3.12，以及 [Gyan.dev FFmpeg 9.0.1 Essentials](https://github.com/GyanD/codexffmpeg/releases/tag/9.0.1) 中的 `ffmpeg.exe` 和 `ffprobe.exe`。将两个 EXE 放入 `work/mp4_app/tools/`。
2. 在仓库根目录运行以下命令。

```powershell
python -m pip install pyinstaller==6.22.3 tkinterdnd2==0.6.4 ttkbootstrap==1.20.3 pillow
python work/mp4_app/build_app.py --folder
python -m PyInstaller --noconfirm --distpath outputs --workpath work/pyinstaller-build work/mp4_app/mp4_normalizer.spec
```

生成的目录版位于 `outputs/`。安装 [Inno Setup 6](https://jrsoftware.org/isdl.php) 后可用 `ISCC work/mp4_app/installer.iss` 构建安装包。

开发时可直接运行 `python work/mp4_app/mp4_normalizer.py`。使用 `python work/mp4_app/test_app.py` 和 `python work/mp4_app/test_smart_batch.py` 验证核心流程。测试使用合成视频。

## 许可证

本项目代码采用 [MIT](LICENSE)。媒体处理通过独立进程调用 FFmpeg 和 FFprobe。安装包所用构建的许可证、编译选项与依赖版本见 [第三方许可目录](work/mp4_app/licenses)。[FFmpeg 对应源码](https://github.com/FFmpeg/FFmpeg/tree/bf1b838f2a)。
