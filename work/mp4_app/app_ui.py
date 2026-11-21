"""Themed local UI. All Tk operations stay on the main thread."""
import datetime
import json
import math
import os
from pathlib import Path
import queue
import threading
import time
import uuid

THEMES = {'深海蓝': 'superhero', '极夜黑': 'darkly', '明亮蓝': 'cosmo', '薄荷绿': 'minty'}
MODES = {'自动修复（推荐）': 'auto', '仅无损封装': 'remux', '强制转换 H.264': 'encode'}
SPEEDS = {'均衡（推荐）': 'balanced', '速度优先': 'fast', '画质优先': 'quality'}
CHECKS = {'快速检查': False, '完整检查': True}


def config_file():
    override = os.environ.get('MP4FIX_CONFIG')
    return Path(override) if override else Path(os.environ.get('LOCALAPPDATA', '.'))/'MP4一拖修复'/'设置.json'


def load_config():
    try:
        value = json.loads(config_file().read_text(encoding='utf-8'))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def size_text(n):
    return f'{n/1024**3:.2f} GB' if n >= 1024**3 else f'{n/1024**2:.1f} MB'


def run_gui(core, initial_files, smoke_seconds=None, result_file=None, smoke_cancel_after=None,
            initial_full_check=False, smoke_theme=None, smoke_font=None):
    import tkinter as tk
    import tkinter.font as tkfont
    from tkinter import filedialog, messagebox
    import ttkbootstrap as ttk
    from tkinterdnd2 import TkinterDnD, DND_FILES
    try:
        core.ctypes.windll.user32.SetProcessDpiAwarenessContext(core.ctypes.c_void_p(-4))
    except Exception:
        pass
    root = TkinterDnD.Tk()
    cfg = load_config()
    theme_name = cfg.get('theme', '深海蓝')
    if theme_name not in THEMES:
        theme_name = '深海蓝'
    style = ttk.Style(theme=THEMES[theme_name])
    root.title(core.APP_NAME+' v'+core.VERSION+' · 快速检查')
    w = min(1180, root.winfo_screenwidth()-70)
    h = min(840, root.winfo_screenheight()-85)
    root.geometry(f'{w}x{h}')
    root.minsize(min(w, 980), min(h, 680))
    if smoke_seconds and os.environ.get('MP4FIX_TEST_SIZE'):
        root.geometry(os.environ['MP4FIX_TEST_SIZE'])
    if (core.ROOT/'app.ico').exists():
        root.iconbitmap(str(core.ROOT/'app.ico'))
    errors = []
    def callback_error(kind, value, tb):
        errors.append(str(value))
        if not smoke_seconds:
            messagebox.showerror(core.APP_NAME, '窗口遇到错误：'+str(value))
    root.report_callback_exception = callback_error
    started = time.monotonic()
    font_size = tk.StringVar(value=str(cfg.get('font_size', 14)))
    if font_size.get() not in ('12', '14', '16'):
        font_size.set('14')
    theme_var = tk.StringVar(value=theme_name)
    named_fonts = {name: tkfont.nametofont(name) for name in ('TkDefaultFont', 'TkTextFont', 'TkMenuFont', 'TkHeadingFont')}
    title_font = tkfont.Font(family='Microsoft YaHei UI', size=24, weight='bold')
    drop_font = tkfont.Font(family='Microsoft YaHei UI', size=20, weight='bold')
    small_font = tkfont.Font(family='Microsoft YaHei UI', size=12)
    settings_font = tkfont.Font(family='Microsoft YaHei UI', size=13)

    class OptionTip:
        delay = 450

        def __init__(self, widget, text):
            self.widget = widget
            self.text = text
            self.timer = None
            self.toplevel = None
            widget.bind('<Enter>', self.enter, add='+')
            widget.bind('<Leave>', self.leave, add='+')
            widget.bind('<ButtonPress>', self.leave, add='+')

        def enter(self, event):
            self.leave()
            self.timer = root.after(self.delay, self.show)

        def leave(self, event=None):
            if self.timer is not None:
                root.after_cancel(self.timer)
                self.timer = None
            if self.toplevel is not None:
                self.toplevel.destroy()
                self.toplevel = None

        def show(self):
            self.timer = None
            pointer_x, pointer_y = self.widget.winfo_pointerxy()
            widget_x, widget_y = self.widget.winfo_rootx(), self.widget.winfo_rooty()
            widget_width, widget_height = self.widget.winfo_width(), self.widget.winfo_height()
            if not (widget_x <= pointer_x < widget_x+widget_width and widget_y <= pointer_y < widget_y+widget_height):
                pointer_x, pointer_y = widget_x+widget_width//2, widget_y+widget_height//2
            popup = self.toplevel = tk.Toplevel(root)
            popup.withdraw()
            popup.overrideredirect(True)
            popup.attributes('-topmost', True)
            popup.attributes('-transparentcolor', '#ff00ff')
            canvas = tk.Canvas(popup, bd=0, highlightthickness=0)
            canvas.configure(bg='#ff00ff')
            canvas.pack()
            text = canvas.create_text(0, 0, text=self.text, font=settings_font, fill='white',
                                      width=305, anchor='nw')
            x1, y1, x2, y2 = canvas.bbox(text)
            width, height = x2-x1+24, y2-y1+16
            tail, gap, radius = 9, 12, 9
            canvas.configure(width=width, height=height+tail)
            left, top = root.winfo_rootx(), root.winfo_rooty()
            right, bottom = left+root.winfo_width(), top+root.winfo_height()
            x = min(max(pointer_x-width//2, left+4), right-width-4)
            above = pointer_y+gap+tail+height > bottom
            y = pointer_y-gap-tail-height if above else pointer_y+gap
            start = 0 if above else tail
            end = start+height
            canvas.create_polygon(
                0,start+radius, 0,start, radius,start, width-radius,start, width,start, width,start+radius,
                width,end-radius, width,end, width-radius,end, radius,end, 0,end, 0,end-radius,
                fill='black', smooth=True)
            anchor = min(max(pointer_x-x, 14), width-14)
            base = end-1 if above else start+1
            canvas.create_polygon(anchor-8,base, anchor,end+tail if above else 0, anchor+8,base, fill='black')
            canvas.coords(text, 12, start+8)
            canvas.tag_raise(text)
            popup.geometry(f'+{x}+{y}')
            popup.deiconify()
    def apply_fonts(event=None):
        n = int(font_size.get())
        for font in named_fonts.values():
            font.configure(family='Microsoft YaHei UI', size=n)
        root.option_add('*Font', named_fonts['TkDefaultFont'])
        title_font.configure(size=n+10)
        drop_font.configure(size=n+6)
        small_font.configure(size=max(12, n-1))
        settings_font.configure(size=max(11, n-1))
        style.configure('Treeview', font=named_fonts['TkDefaultFont'], rowheight=max(38, int(n*2.8)))
        style.configure('Treeview.Heading', font=('Microsoft YaHei UI', n, 'bold'))
        style.configure('TButton', padding=(12, 8))
        style.configure('TCombobox', padding=(6, 5))
        style.configure('TNotebook.Tab', padding=(14, 8))
    apply_fonts()
    shell = ttk.Frame(root, padding=(26, 22, 26, 18))
    shell.pack(fill='both', expand=True)
    shell.columnconfigure(0, weight=1)
    shell.rowconfigure(2, weight=1)
    header = ttk.Frame(shell)
    header.grid(row=0, column=0, sticky='ew')
    titles = ttk.Frame(header)
    titles.pack(side='left', fill='x', expand=True)
    ttk.Label(titles, text='MP4 一拖修复', font=title_font).pack(anchor='w')
    ttk.Label(titles, text='猫抓大文件转换工具 · 生成标准 MP4 · 保留原文件',
              font=small_font, bootstyle='secondary').pack(anchor='w', pady=(4, 0))
    appearance = ttk.Frame(header)
    appearance.pack(side='right', anchor='n', pady=6)
    ttk.Label(appearance, text='主题', font=small_font).grid(row=0, column=0, padx=(0, 8))
    theme_box = ttk.Combobox(appearance, textvariable=theme_var, values=list(THEMES), state='readonly', width=8)
    theme_box.grid(row=0, column=1)
    ttk.Label(appearance, text='字号', font=small_font).grid(row=0, column=2, padx=(14, 8))
    font_box = ttk.Combobox(appearance, textvariable=font_size, values=['12', '14', '16'], state='readonly', width=3)
    font_box.grid(row=0, column=3)
    font_box.bind('<<ComboboxSelected>>', apply_fonts)

    drop = ttk.Frame(shell, bootstyle='secondary', padding=2, cursor='hand2')
    drop.grid(row=1, column=0, sticky='ew', pady=(14, 12))
    drop_inner = ttk.Frame(drop, padding=(16, 9))
    drop_inner.pack(fill='both')
    ttk.Label(drop_inner, text='＋  把一个或多个视频拖到这里', font=drop_font,
              bootstyle='primary', anchor='center').pack(fill='x')
    ttk.Label(drop_inner, text='支持猫抓 .vss / .ssv / .css、TS、MP4 等下载文件，无需先改名',
              font=small_font, bootstyle='secondary', anchor='center').pack(fill='x', pady=(7, 0))

    body = ttk.Frame(shell)
    body.grid(row=2, column=0, sticky='nsew')
    body.columnconfigure(0, weight=1)
    body.rowconfigure(0, weight=1)
    left = ttk.Frame(body)
    left.grid(row=0, column=0, sticky='nsew', padx=(0, 16))
    left.columnconfigure(0, weight=1)
    left.rowconfigure(0, weight=1)
    right_panel = ttk.Frame(body)
    right_panel.grid(row=0, column=1, sticky='nsew')
    ttk.Label(right_panel, text='处理设置', font=('Microsoft YaHei UI', 15, 'bold')).grid(
        row=0, column=0, sticky='w', padx=8, pady=(0, 6))
    right_panel.columnconfigure(0, weight=1)
    right_panel.rowconfigure(1, weight=1)
    settings_canvas = tk.Canvas(right_panel, width=352, bd=0, highlightthickness=0, yscrollincrement=1)
    settings_canvas.grid(row=1, column=0, sticky='nsew')
    settings_scroll = ttk.Scrollbar(right_panel, orient='vertical', command=settings_canvas.yview)
    settings_scroll.grid(row=1, column=1, sticky='ns')
    settings_canvas.configure(yscrollcommand=settings_scroll.set)
    right = ttk.Frame(settings_canvas, padding=(8, 2, 8, 6))
    settings_window = settings_canvas.create_window((0, 0), window=right, anchor='nw')
    right.bind('<Configure>', lambda event: settings_canvas.configure(scrollregion=settings_canvas.bbox('all')))
    settings_canvas.bind('<Configure>', lambda event: settings_canvas.itemconfigure(settings_window, width=event.width))
    option_tips = []

    def scroll_settings(event):
        if event.delta and settings_canvas.yview() != (0.0, 1.0):
            for tip in option_tips:
                tip.leave()
            pixels = max(1, round(abs(event.delta)*.4))
            settings_canvas.yview_scroll(-pixels if event.delta > 0 else pixels, 'units')
        return 'break'

    wheel_tag = 'SettingsWheel'
    root.bind_class(wheel_tag, '<MouseWheel>', scroll_settings)

    def bind_settings_wheel(widget):
        # Run before the combobox class binding so scrolling never changes a setting.
        widget.bindtags((wheel_tag, *widget.bindtags()))
        for child in widget.winfo_children():
            bind_settings_wheel(child)
    notebook = ttk.Notebook(left)
    notebook.grid(row=0, column=0, sticky='nsew')
    task_tab, log_tab, help_tab = (ttk.Frame(notebook, padding=8) for _ in range(3))
    notebook.add(task_tab, text='任务列表')
    notebook.add(log_tab, text='处理记录')
    notebook.add(help_tab, text='使用帮助')
    columns = ('name', 'size', 'status', 'time', 'eta')
    tree = ttk.Treeview(task_tab, columns=columns, show='headings', selectmode='extended', height=7)
    for col, title, width in [('name', '文件', 230), ('size', '大小', 90), ('status', '状态 / 处理方式', 210), ('time', '用时', 100), ('eta', '预计剩余', 120)]:
        tree.heading(col, text=title)
        tree.column(col, width=width, minwidth=70, stretch=(col in ('name', 'status')))
    task_tab.rowconfigure(0, weight=1)
    task_tab.columnconfigure(0, weight=1)
    tree.grid(row=0, column=0, sticky='nsew')
    yscroll = ttk.Scrollbar(task_tab, orient='vertical', command=tree.yview)
    yscroll.grid(row=0, column=1, sticky='ns')
    xscroll = ttk.Scrollbar(task_tab, orient='horizontal', command=tree.xview)
    xscroll.grid(row=1, column=0, sticky='ew')
    tree.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
    log_text = tk.Text(log_tab, wrap='word', height=8, bd=0, font=named_fonts['TkTextFont'], state='disabled')
    log_text.pack(side='left', fill='both', expand=True)
    log_scroll = ttk.Scrollbar(log_tab, command=log_text.yview)
    log_scroll.pack(side='right', fill='y')
    log_text.configure(yscrollcommand=log_scroll.set)
    help_text = tk.Text(help_tab, wrap='word', bd=0, height=8, font=named_fonts['TkTextFont'])
    help_text.pack(fill='both', expand=True)
    help_text.insert('end', '怎么用\n拖入文件就会按右侧设置开始；关闭“拖入即开始”可以先排好任务。\n\n'
        '怎么更快\n推荐“自动修复 + 均衡 + 快速检查”。H.264 兼容文件直接封装，画质无损；'
        '需要转码时实测 NVIDIA / Intel / AMD 硬件编码器，自动选择可用 GPU，否则使用 CPU。速度优先可进一步减少编码计算，但压缩效率和细节可能变化。'
        '分辨率与帧率不会为提速而降低。\n\n'
        '检查的区别\n快速检查会验证容器、编码、时长、帧数，并解码 5 个位置；不等于全片检查。'
        '完整检查会逐帧解码，长视频会明显更慢。\n\n'
        '预计时间\n分析后给出初步用时范围；处理过程中根据实际速度更新剩余时间。'
        '会记住这台电脑成功任务的平均速度，逐步校准估计；不会保存视频内容或文件名。'
        '首次估计较粗，文件损坏、长关键帧间隔和磁盘忙碌均可能增加时间。\n\n'
        '批量智能筛选\n可一次拖入多个文件。跳过通过兼容性和解码检查的 MP4，'
        '其余文件尝试修复为 H.264/AAC MP4。默认快速抽查、替换源文件。同名时自动编号，每批保存一份日志。\n\n'
        '输出与日志\n普通模式默认生成“_正常版.mp4”。替换前默认完整解码。'
        '勾选“快速替换验证”且检查方式为快速检查时，只解码关键位置，适合想缩短大文件处理时间时使用。'
        '双击已完成任务播放；选择任务可查看日志或视频信息。\n\n'
        '边界\n不会恢复下载时已丢失的视频数据。修复格式不能解除 Telegram 上传大小限制。'
        '保留主视频及全部音轨，移除封面、字幕、数据轨和章节以提高兼容性。\n\n'
        '分享给别人\n支持 Windows 10 / 11 的 64 位电脑。自带 Python 运行环境和 FFmpeg，'
        '无需另装依赖，不要求安装 NVIDIA 显卡。GPU 编码不可用时自动使用 CPU。\n\n'
        '开源组件\nFFmpeg / FFprobe（GPLv3），ttkbootstrap 1.20.3（MIT），tkinterdnd2。\n'
        'https://github.com/israel-dryer/ttkbootstrap\n'
        '更多通用功能可参考：\nhttps://github.com/eibols/ffmpeg_batch\nhttps://github.com/mifi/lossless-cut')
    help_text.configure(state='disabled')

    mode_var = tk.StringVar(value=next((k for k, v in MODES.items() if v == cfg.get('mode')), next(iter(MODES))))
    speed_var = tk.StringVar(value=next((k for k, v in SPEEDS.items() if v == cfg.get('speed')), next(iter(SPEEDS))))
    check_var = tk.StringVar(value=list(CHECKS)[1] if initial_full_check or cfg.get('full_check_enabled', False)
                             else list(CHECKS)[0])
    destination_var = tk.StringVar(value=cfg.get('destination', ''))
    autostart = tk.BooleanVar(value=cfg.get('autostart', True))
    smart_scan = tk.BooleanVar(value=cfg.get('smart_scan', False))
    replace_source = tk.BooleanVar(value=cfg.get('replace_source', smart_scan.get()))
    fast_replace = tk.BooleanVar(value=cfg.get('fast_replace', False))
    original_name = tk.BooleanVar(value=cfg.get('original_name', smart_scan.get()))
    use_gpu = tk.BooleanVar(value=cfg.get('use_gpu', True))
    auto_close = tk.BooleanVar(value=cfg.get('auto_close', False))
    configured_limits = cfg.get('resource_limits', {})
    if not isinstance(configured_limits, dict):
        configured_limits = {}
    limits = core.ResourceLimits(*(value if type(value) is int and 0 <= value <= 100 else 0
                                   for value in (configured_limits.get(key) for key in ('cpu', 'memory', 'gpu'))))
    regular_settings = [False, False, next(iter(MODES))]
    def smart_defaults():
        if smart_scan.get():
            regular_settings[:] = [replace_source.get(), original_name.get(), mode_var.get()]
            replace_source.set(True)
            original_name.set(True)
            mode_var.set(next(iter(MODES)))
        else:
            for variable, previous in zip((replace_source, original_name, mode_var), regular_settings):
                variable.set(previous)

    options_grid = ttk.Frame(right)
    options_grid.pack(fill='x')
    options_grid.columnconfigure(0, weight=1)
    options_grid.columnconfigure(1, weight=1)
    smart_toggle = ttk.Checkbutton(options_grid, text='批量智能筛选', variable=smart_scan,
                                   command=smart_defaults, bootstyle='round-toggle')
    smart_toggle.grid(row=0, column=0, sticky='w', pady=3)
    option_tips.append(OptionTip(smart_toggle, '批量检查文件，跳过通过检查的 MP4，只修复需要处理的文件。开启时默认替换源文件，同名时自动编号。'))
    for index, (label, var, tip_text) in enumerate([
        ('使用 GPU 加速', use_gpu, '需要转码时优先使用可用的 GPU 编码器，失败时自动改用 CPU。无损封装用不到 GPU。'),
        ('替换原文件', replace_source, '修复结果通过检查后，替换原目录中的源文件。已有同名 MP4 时自动加（1）、（2）等编号。检查失败时保留源文件。'),
        ('快速替换验证', fast_replace, '选择快速检查时，替换前只解码抽查关键位置，可缩短处理时间。选择完整检查时仍逐帧验证。'),
        ('使用原文件名', original_name, '另存时沿用原文件主名，扩展名为 .mp4。遇到同名文件会自动编号。'),
        ('拖入即开始', autostart, '拖入或添加文件后立即按当前设置处理。关闭后可先整理队列，再点击“开始任务”。'),
        ('成功后关闭', auto_close, '队列里的文件全部修复或跳过后自动关闭窗口。遇到失败或取消时保留窗口。'),
    ], start=1):
        option = ttk.Checkbutton(options_grid, text=label, variable=var)
        option.grid(row=index//2, column=index%2, sticky='w', pady=3)
        option_tips.append(OptionTip(option, tip_text))
    ttk.Button(options_grid, text='查看日志', command=lambda: open_log_directory(), bootstyle='info-outline',
               padding=(8, 3)).grid(row=3, column=1, sticky='w', pady=3)

    ttk.Separator(right).pack(fill='x', pady=8)
    selectors = ttk.Frame(right)
    selectors.pack(fill='x')
    selectors.columnconfigure(1, weight=1)
    for row, (label, var, choices) in enumerate([
        ('修复方式', mode_var, MODES), ('转码速度', speed_var, SPEEDS), ('检查方式', check_var, CHECKS),
    ]):
        ttk.Label(selectors, text=label, font=settings_font).grid(row=row, column=0, sticky='w', padx=(0, 12))
        ttk.Combobox(selectors, textvariable=var, values=list(choices), state='readonly', width=1,
                     font=settings_font).grid(row=row, column=1, sticky='ew', pady=2)
    mode_box = selectors.grid_slaves(row=0, column=1)[0]
    ttk.Separator(right).pack(fill='x', pady=8)
    dest_label = ttk.Label(right, font=settings_font, wraplength=318, bootstyle='info')
    dest_label.pack(fill='x', pady=(0, 4))
    def update_dest():
        destination = '原目录（替换原文件）' if replace_source.get() else destination_var.get() or '原文件所在文件夹'
        dest_label.configure(text='输出到 '+destination)
    update_dest()
    replace_source.trace_add('write', lambda *_: update_dest())
    def choose_destination():
        folder = filedialog.askdirectory(title='选择正常版的保存位置', parent=root)
        if folder:
            destination_var.set(folder)
            update_dest()
    dest_buttons = ttk.Frame(right)
    dest_buttons.pack(fill='x')
    ttk.Button(dest_buttons, text='选择目录', command=choose_destination, bootstyle='primary-outline').pack(side='left')
    ttk.Button(dest_buttons, text='恢复默认', command=lambda: (destination_var.set(''), update_dest()),
               bootstyle='info-outline').pack(side='left', padx=6)

    def edit_resource_limits():
        dialog = tk.Toplevel(root)
        dialog.title('资源限制')
        dialog.transient(root)
        dialog.resizable(False, False)
        body = ttk.Frame(dialog, padding=16)
        body.pack(fill='both', expand=True)
        choices = ('不限', *(str(n) for n in range(1, 101)))
        entries = []
        for row, (label, value) in enumerate(zip(('CPU 上限（%）', '内存上限（%）', 'GPU 平均占用目标（%）'), limits)):
            ttk.Label(body, text=label).grid(row=row, column=0, sticky='w', pady=4, padx=(0, 14))
            entry = ttk.Spinbox(body, values=choices, width=8)
            entry.set(str(value) if value else '不限')
            entry.grid(row=row, column=1, sticky='e', pady=4)
            entries.append(entry)
        ttk.Label(body, text='CPU 和内存限制视频处理进程。内存百分比按电脑总内存换算。'
                             '\nGPU 通过间歇限速降低平均占用，瞬时读数可能更高。'
                             '\n设置用于新提交或重试的任务。',
                  justify='left', wraplength=430).grid(row=3, columnspan=2, sticky='w', pady=(10, 12))
        buttons = ttk.Frame(body)
        buttons.grid(row=4, columnspan=2, sticky='ew')

        def apply():
            nonlocal limits
            values = [entry.get().strip().removesuffix('%').strip() for entry in entries]
            try:
                selected = [0 if value == '不限' else int(value) for value in values]
                if any(not 0 <= value <= 100 for value in selected):
                    raise ValueError()
            except ValueError:
                messagebox.showerror('资源限制', '请输入 1–100 之间的整数，或选“不限”。', parent=dialog)
                return
            limits = core.ResourceLimits(*selected)
            limit_label.configure(text=limits.describe() or '资源不限')
            save_config()
            dialog.destroy()

        ttk.Button(buttons, text='保存', command=apply, bootstyle='primary').pack(side='right')
        ttk.Button(buttons, text='取消', command=dialog.destroy).pack(side='right', padx=8)
        dialog.bind('<Escape>', lambda event: dialog.destroy())
        dialog.grab_set()
        entries[0].focus_set()

    ttk.Button(dest_buttons, text='资源限制', command=edit_resource_limits,
               bootstyle='info-outline').pack(side='left')
    limit_label = ttk.Label(right, text=limits.describe() or '资源不限', bootstyle='info', wraplength=318)
    limit_label.pack(anchor='w')
    bind_settings_wheel(right_panel)
    actions = ttk.Frame(left)
    actions.grid(row=1, column=0, sticky='ew', pady=(12, 0))
    secondary_actions = ttk.Frame(left)
    secondary_actions.grid(row=2, column=0, sticky='ew', pady=(8, 0))
    footer = ttk.Frame(shell)
    footer.grid(row=3, column=0, sticky='ew', pady=(16, 0))
    state = tk.StringVar(value='等待拖入视频 · 自动选择处理方法')
    summary = tk.StringVar(value='0 个任务')
    hardware_var = tk.StringVar(value='正在识别 CPU 和 GPU…')
    estimate_var = tk.StringVar(value='预计用时：分析视频后显示；处理时自动更新')
    batch_estimate_var = tk.StringVar()
    ttk.Label(footer, textvariable=hardware_var, font=small_font, bootstyle='secondary', wraplength=900).pack(anchor='w', pady=(0, 4))
    estimate_line = ttk.Frame(footer)
    estimate_line.pack(fill='x', pady=(0, 5))
    ttk.Label(estimate_line, textvariable=estimate_var, bootstyle='info',
              font=small_font, wraplength=520).pack(side='left', anchor='w')
    ttk.Label(estimate_line, textvariable=batch_estimate_var, bootstyle='info',
              font=small_font).pack(side='left', padx=(16, 0))
    ttk.Label(footer, textvariable=state, wraplength=900).pack(anchor='w')
    progress = ttk.Progressbar(footer, maximum=100, bootstyle='primary')
    progress.pack(fill='x', pady=(8, 8))
    bottom = ttk.Frame(footer)
    bottom.pack(fill='x')
    ttk.Label(bottom, textvariable=summary, font=small_font, bootstyle='secondary').pack(side='left')
    mode_badge = tk.StringVar()
    ttk.Label(bottom, textvariable=mode_badge, font=small_font, bootstyle='secondary').pack(side='right')

    events, pending = queue.Queue(), queue.Queue()
    job_lock = threading.Lock()
    generation = [0]
    rows = {}
    busy, quitting = threading.Event(), threading.Event()
    last_output = [None]
    current = [None]
    current_started = [0]
    engine = core.Engine(lambda stage, val: events.put(('progress', stage, val)))
    engine.on_estimate = lambda value: events.put(('estimate', '', value))
    hardware_data = [None]
    estimate_events = []
    batch_estimates = []
    if smoke_seconds:
        batch_estimate_var.trace_add('write', lambda *_: batch_estimates.append(batch_estimate_var.get()))
    inspection_running = threading.Event()
    inspector = core.Engine()
    failures = {}
    failure_window = None
    def log_ui(text):
        log_text.configure(state='normal')
        log_text.insert('end', datetime.datetime.now().strftime('%H:%M:%S')+'  '+text+'\n')
        if int(log_text.index('end-1c').split('.')[0]) > 900:
            log_text.delete('1.0', '200.0')
        log_text.see('end')
        log_text.configure(state='disabled')
    def paint_text_widgets():
        settings_canvas.configure(background=style.colors.bg)
        for widget in (log_text, help_text):
            widget.configure(background=style.colors.inputbg, foreground=style.colors.inputfg,
                             insertbackground=style.colors.inputfg, selectbackground=style.colors.primary)
    def change_theme(event=None):
        for tip in option_tips:
            tip.leave()
        style.theme_use(THEMES[theme_var.get()])
        apply_fonts()
        paint_text_widgets()
    theme_box.bind('<<ComboboxSelected>>', change_theme)
    paint_text_widgets()
    def show_mode(check_all=None):
        mode = '完整检查（较慢）' if (CHECKS[check_var.get()] if check_all is None else check_all) else '快速检查'
        mode_badge.set('v'+core.VERSION+' · '+mode+' · 本机离线处理')
        root.title(core.APP_NAME+' v'+core.VERSION+' · '+mode)
    show_mode()
    def row_update(iid, status=None, elapsed=None):
        row = rows[iid]
        if status is not None:
            row['status'] = status
        if elapsed is not None:
            row['time'] = core.Engine._clock(elapsed)
        tree.item(iid, values=(Path(row['source']).name, size_text(row['size']), row['status'], row['time'], row.get('eta', '分析后显示')))
    active_run = {}
    batch_rates = {}
    def submit(only=None, overrides=None):
        waiting = [(iid, row) for iid, row in rows.items()
                   if (row['status'] == '等待开始' if only is None else iid in only)]
        if not waiting:
            return
        options = {'mode': MODES[mode_var.get()], 'speed': SPEEDS[speed_var.get()],
                   'full_check': CHECKS[check_var.get()],
                   'destination': None if replace_source.get() else destination_var.get() or None,
                   'smart_scan': smart_scan.get(), 'replace_source': replace_source.get(),
                   'fast_replace': fast_replace.get(),
                   'original_name': original_name.get(),
                   'use_gpu': use_gpu.get(), 'batch_log': None, 'resolution': None}
        options.update(overrides or {})
        options['resource_limits'] = limits
        if options['smart_scan']:
            try:
                batch_log = core.create_processing_log('批量修复', f'批量智能筛选 · {len(waiting)} 个文件')
            except OSError as exc:
                messagebox.showerror(core.APP_NAME, '无法创建批量日志：'+str(exc))
                return False
            options['batch_log'] = str(batch_log)
        profile = tuple(options[key] for key in ('mode', 'speed', 'smart_scan', 'use_gpu',
                                                'full_check', 'replace_source', 'fast_replace', 'resolution',
                                                'resource_limits'))
        with job_lock:
            for iid, row in waiting:
                failures.pop(iid, None)
                active_run[iid] = (auto_close.get(), None)
                row.update(profile=profile, options=options, prediction=None, remaining=None, eta='分析后显示', time='—')
                row_update(iid, '排队中')
                row['result'] = None
                pending.put((iid, row['source'], generation[0], options))
        return True
    extensions = {'.mp4', '.mkv', '.mov', '.m4v', '.ts', '.mts', '.m2ts', '.vss', '.ssv', '.tsv', '.css', '.avi', '.webm', '.flv', '.wmv'}
    def add_files(paths, start=True):
        expanded = []
        for item in paths:
            path = Path(item)
            if path.is_dir():
                expanded.extend(p for p in sorted(path.iterdir()) if p.is_file() and p.suffix.lower() in extensions
                                and (smart_scan.get() or '_正常版' not in p.stem))
            else:
                expanded.append(path)
        active_sources = {row['source'].casefold() for row in rows.values()
                          if row['status'] in ('等待开始', '排队中', '处理中')}
        added = []
        for path in expanded:
            try:
                path = path.resolve()
                if not path.is_file() or str(path).casefold() in active_sources:
                    continue
                iid = uuid.uuid4().hex
                rows[iid] = {'source': str(path), 'size': path.stat().st_size, 'status': '等待开始', 'time': '—', 'result': None}
                tree.insert('', 'end', iid=iid)
                row_update(iid)
                added.append(iid)
                active_sources.add(str(path).casefold())
            except OSError as exc:
                log_ui('无法添加 '+str(path)+'：'+str(exc))
        if start and autostart.get():
            submit()
        return added
    def select_files(event=None):
        add_files(filedialog.askopenfilenames(title='选择下载文件（无需先改后缀）',
                  filetypes=[('所有文件', '*.*'), ('视频文件', '*.mp4 *.mkv *.ts *.css *.ssv *.mov')]))
    def select_folder():
        folder = filedialog.askdirectory(title='批量添加文件夹（仅当前层，不重复添加正常版）')
        if folder:
            add_files([folder])
    def selected_row():
        selection = tree.selection()
        return rows.get(selection[0]) if selection else None
    def open_output():
        row = selected_row()
        output = (row.get('result') or {}).get('output') if row else last_output[0]
        folder = Path(output).parent if output else Path(row['source']).parent if row else None
        if folder and folder.is_dir():
            os.startfile(str(folder))
    def play(event=None):
        row = selected_row()
        output = (row.get('result') or {}).get('output') if row else None
        if output and Path(output).is_file():
            os.startfile(output)
    def open_log_directory():
        try:
            core.LOG_DIR.mkdir(parents=True, exist_ok=True)
            os.startfile(str(core.LOG_DIR))
        except OSError as exc:
            state.set('无法打开日志目录：'+str(exc))
    def inspect_file(source, purpose='info'):
        if inspection_running.is_set():
            state.set('正在读取视频信息，请稍候')
            return
        inspection_running.set()
        inspector.cancelled.clear()
        inspector.resource_limits = limits
        def job():
            try:
                data = inspector.probe(source)
                events.put((purpose, source, data))
            except Exception as exc:
                events.put(('info_error', purpose, str(exc)))
            finally:
                inspection_running.clear()
        threading.Thread(target=job, daemon=True).start()
        state.set('正在读取视频信息…')

    def inspect_selected():
        row = selected_row()
        if row:
            inspect_file(row['source'])
        else:
            state.set('请先选择一个任务，再查看视频信息')

    def reduce_resolution():
        row = selected_row()
        source = row['source'] if row else None
        if source and not Path(source).is_file():
            source = (row.get('result') or {}).get('output')
        if not source:
            source = filedialog.askopenfilename(title='选择要降低分辨率的视频', parent=root)
        if source:
            inspect_file(str(Path(source).resolve()), 'resolution')

    def show_resolution(source, info):
        video = core.main_video(info)
        width, height = core.video_dimensions(video) if video else (0, 0)
        choices = {label: edge for label, edge in core.RESOLUTIONS.items() if edge < min(width, height)}
        if not choices:
            messagebox.showinfo('降分辨率', '此视频没有可用的降分辨率档位。', parent=root)
            return
        dialog = tk.Toplevel(root)
        dialog.title('降分辨率')
        dialog.transient(root)
        dialog.resizable(False, False)
        body = ttk.Frame(dialog, padding=18)
        body.pack(fill='both', expand=True)
        ttk.Label(body, text=f'{Path(source).name}\n源分辨率 {width} × {height}', wraplength=450).pack(anchor='w')
        target = tk.StringVar(value=next(reversed(choices)))
        box = ttk.Combobox(body, textvariable=target, values=list(choices), state='readonly')
        box.pack(fill='x', pady=(8, 12))
        ttk.Label(body, text='转码速度').pack(anchor='w')
        compression_speed = tk.StringVar(value='速度优先')
        ttk.Combobox(body, textvariable=compression_speed, values=list(SPEEDS), state='readonly').pack(fill='x', pady=(4, 12))
        ttk.Label(body, text='输出将重新编码并另存为 MP4，保留原文件。', wraplength=450).pack(anchor='w')
        buttons = ttk.Frame(body)
        buttons.pack(fill='x', pady=(12, 0))

        def compress():
            waiting = [iid for iid, row in rows.items() if row['source'].casefold() == source.casefold()
                       and row['status'] == '等待开始']
            iids = waiting or add_files([source], start=False)
            if not iids:
                messagebox.showinfo('降分辨率', '此文件正在处理，请在当前任务结束后再试。', parent=dialog)
                return
            if submit(iids, {'resolution': choices[target.get()], 'mode': 'encode', 'smart_scan': False,
                             'speed': SPEEDS[compression_speed.get()],
                             'replace_source': False, 'original_name': False,
                             'destination': destination_var.get() or None}):
                dialog.destroy()

        ttk.Button(buttons, text='另存并开始', command=compress, bootstyle='primary').pack(side='right')
        ttk.Button(buttons, text='取消', command=dialog.destroy).pack(side='right', padx=8)
        box.focus_set()
        dialog.grab_set()

    def clear_completed():
        for iid in list(rows):
            if rows[iid]['status'] not in ('排队中', '处理中'):
                tree.delete(iid)
                del rows[iid]
                failures.pop(iid, None)
    def retry_rows(candidates, statuses):
        active = {row['source'].casefold() for row in rows.values()
                  if row['status'] in ('等待开始', '排队中', '处理中')}
        retry_ids = set()
        for iid in candidates:
            row = rows[iid]
            source = row['source'].casefold()
            if row['status'] in statuses and source not in active:
                retry_ids.add(iid)
                active.add(source)
        if not retry_ids:
            state.set('没有可重试的任务')
            return
        normal_ids = []
        for iid in retry_ids:
            options = rows[iid]['options']
            if options['resolution'] is None:
                normal_ids.append(iid)
            else:
                submit([iid], {**options, 'destination': destination_var.get() or options['destination']})
        submit(normal_ids)

    def retry_failed():
        retry_rows(rows, ('失败',))

    def show_failures():
        nonlocal failure_window
        if failure_window is not None or not failures or root.grab_current() is not None:
            return
        items = list(failures)
        failures.clear()
        dialog = failure_window = tk.Toplevel(root)
        dialog.title('修复建议')
        dialog.transient(root)
        body = ttk.Frame(dialog, padding=16)
        body.pack(fill='both', expand=True)
        count = tk.StringVar(value=f'{len(items)} 个文件需要处理')
        ttk.Label(body, textvariable=count).pack(anchor='w')
        listing = ttk.Treeview(body, show='tree', selectmode='browse', height=min(len(items), 5))
        listing.column('#0', width=500)
        listing.pack(fill='x', pady=(8, 10))
        for iid in items:
            listing.insert('', 'end', iid=iid, text=Path(rows[iid]['source']).name)
        detail = tk.StringVar()
        ttk.Label(body, textvariable=detail, wraplength=500, justify='left').pack(anchor='w', fill='x')
        buttons = ttk.Frame(body)
        buttons.pack(fill='x', pady=(12, 0))

        def close_dialog():
            nonlocal failure_window
            failure_window = None
            dialog.destroy()

        def selection_changed(event=None):
            if not listing.selection():
                return
            result = rows[listing.selection()[0]]['result']
            detail.set(result['error']+'\n\n'+result['suggestion'])
            action = result['retry']
            apply_retry.configure(text='选目录并重试' if action == 'destination' else '按建议重试',
                                  state='normal' if action else 'disabled')

        def retry():
            iid = listing.selection()[0]
            row = rows[iid]
            action = row['result']['retry']
            options = dict(row['result']['options'])
            if action == 'destination':
                folder = filedialog.askdirectory(title='选择可用的输出目录', parent=dialog)
                if not folder:
                    return
                options.update(destination=folder, replace_source=False)
            else:
                options['mode'] = action
            options['batch_log'] = None
            if submit([iid], options):
                listing.delete(iid)
                remaining = listing.get_children()
                if remaining:
                    count.set(f'{len(remaining)} 个文件需要处理')
                    listing.selection_set(remaining[0])
                    selection_changed()
                else:
                    close_dialog()

        def open_failure_log():
            result = rows[listing.selection()[0]]['result']
            if result['log'] and Path(result['log']).is_file():
                os.startfile(result['log'])
            else:
                open_log_directory()

        apply_retry = ttk.Button(buttons, text='按建议重试', command=retry, bootstyle='primary')
        apply_retry.pack(side='right')
        ttk.Button(buttons, text='关闭', command=close_dialog).pack(side='right', padx=8)
        ttk.Button(buttons, text='查看日志', command=open_failure_log).pack(side='left')
        listing.bind('<<TreeviewSelect>>', selection_changed)
        listing.selection_set(items[0])
        selection_changed()
        dialog.protocol('WM_DELETE_WINDOW', close_dialog)
        dialog.bind('<Escape>', lambda event: close_dialog())
        dialog.grab_set()
        listing.focus_set()

    def update_batch_estimate():
        waiting = [row for row in rows.values() if row['status'] == '排队中']
        running = rows.get(current[0])
        if not running and not waiting:
            batch_estimate_var.set('')
            return
        if running is None or running['remaining'] is None:
            batch_estimate_var.set('全部剩余 · 正在估算')
            return
        total = running['remaining']
        for row in waiting:
            rate = batch_rates.get(row['profile'])
            if rate is None and row['profile'] == running['profile'] and running['prediction'] is not None:
                rate = running['prediction']/max(running['size'], 1)
            if rate is None:
                batch_estimate_var.set('全部剩余 · 正在估算')
                return
            total += max(.2, row['size']*rate)
        batch_estimate_var.set(f'全部剩余约 {core.Engine._clock(max(1, math.ceil(total)))}')
    def stop():
        with job_lock:
            generation[0] += 1
            engine.stop()
            while True:
                try:
                    iid, path, epoch, opts = pending.get_nowait()
                    events.put(('cancelled', iid, '已取消，原文件保留'))
                    pending.task_done()
                except queue.Empty:
                    break
    def worker():
        while not quitting.is_set():
            try:
                iid, path, epoch, opts = pending.get(timeout=.2)
            except queue.Empty:
                continue
            with job_lock:
                if quitting.is_set() or epoch != generation[0]:
                    events.put(('cancelled', iid, '已取消，原文件保留'))
                    pending.task_done()
                    continue
                engine.cancelled.clear()
                for name, value in opts.items():
                    setattr(engine, name, Path(value) if name in ('destination', 'batch_log') and value else value)
                busy.set()
            events.put(('start', iid, {'source': path, **opts}))
            try:
                events.put(('done', iid, engine.convert(path)))
            except core.Cancelled:
                engine.log('已取消：'+path)
                events.put(('cancelled', iid, '已取消，原文件保留'))
            except Exception as exc:
                engine.log('失败：'+path+' · '+str(exc))
                retry = getattr(exc, 'retry', None)
                suggestion = getattr(exc, 'suggestion', None)
                if isinstance(exc, OSError) and getattr(exc, 'winerror', None) in (5, 32, 112):
                    retry = 'destination'
                    suggestion = '检查源文件是否被占用，或选择另一个可写的输出目录重试。'
                events.put(('error', iid, {'error': str(exc), 'log': str(engine.log_path) if engine.log_path else None,
                                           'retry': retry, 'options': opts,
                                           'suggestion': suggestion or '查看日志确认原因；文件下载不完整时，请重新下载后用“自动修复”。'}))
            finally:
                pending.task_done()
                busy.clear()
    def poll():
        try:
            while True:
                kind, iid, value = events.get_nowait()
                if kind == 'progress':
                    state.set(iid)
                    progress['value'] = value
                elif kind == 'start':
                    current[0], current_started[0] = iid, time.monotonic()
                    row_update(iid, '处理中')
                    tree.see(iid)
                    show_mode(value['full_check'])
                    estimate_var.set('正在分析当前文件，稍后显示预计用时')
                    log_ui('开始：'+value['source']+'\n设置：'+json.dumps(value, ensure_ascii=False))
                elif kind == 'done':
                    row = rows[iid]
                    row['result'] = value
                    measured = value['elapsed_seconds']/max(row['size'], 1)
                    previous = batch_rates.get(row['profile'], measured)
                    batch_rates[row['profile']] = .6*previous+.4*measured
                    active_run[iid] = (active_run[iid][0], True)
                    current[0] = None
                    progress['value'] = 100
                    if value.get('skipped'):
                        row['eta'] = '—'
                        row_update(iid, '已跳过 · 正常 MP4', value['elapsed_seconds'])
                        state.set('正常 MP4，已跳过')
                        estimate_var.set('已完成原文件检查')
                        log_ui('已跳过：'+value['source'])
                        continue
                    row['eta'] = '已完成'
                    label = next((name for name, edge in core.RESOLUTIONS.items() if edge == value.get('resolution')), None)
                    method = '降至 '+label if label else 'H.264 转换' if value['reencoded_video'] else '视频无损'
                    row_update(iid, '完成 · '+method, value['elapsed_seconds'])
                    last_output[0] = value['output']
                    mode = '完整检查' if value['validation_mode'] == 'full' else '快速检查'
                    state.set('完成 · '+core.Engine._clock(value['elapsed_seconds'])+' · '+mode+' · '+size_text(value['bytes']))
                    estimate_var.set('已完成 · 实际用时 '+core.Engine._clock(value['elapsed_seconds'])+' · '+value['selected_encoder']['name'])
                    log_ui('完成：'+value['output']+'\n方式：'+value['method']+'\n耗时：'+str(value['elapsed_seconds'])+' 秒\n'
                           +'阶段：'+json.dumps(value['phase_times'], ensure_ascii=False)
                           + ('\n日志：'+value['log'] if value['log'] else ''))
                    if value['large_for_free_telegram']:
                        log_ui('此输出超过 2 GB；请另行确认 Telegram 账户上传上限。')
                elif kind in ('error', 'cancelled'):
                    active_run[iid] = (active_run[iid][0], False)
                    current[0] = None
                    rows[iid]['eta'] = '—'
                    row_update(iid, '失败' if kind == 'error' else '已取消')
                    message = value['error'] if kind == 'error' else value
                    if kind == 'error':
                        rows[iid]['result'] = value
                        failures[iid] = None
                    state.set(message)
                    estimate_var.set('任务已停止，原文件保留')
                    log_ui(message)
                    progress['value'] = 0
                elif kind == 'info':
                    state.set('视频信息已显示在“处理记录”')
                    log_ui('视频信息：'+iid+'\n'+json.dumps(value, ensure_ascii=False, indent=2))
                    notebook.select(log_tab)
                elif kind == 'info_error':
                    state.set(value)
                    log_ui(value)
                    if iid == 'resolution':
                        messagebox.showerror('降分辨率', value, parent=root)
                elif kind == 'resolution':
                    state.set('已读取源视频分辨率')
                    show_resolution(iid, value)
                elif kind == 'hardware':
                    hardware_data[0] = value
                    hardware_var.set('CPU：'+value['cpu']+'（'+str(value['logical_cores'])+' 线程） · GPU：'+('、'.join(value['gpus']) or '未识别到型号，转码时仍会实测'))
                    log_ui('本机硬件：'+json.dumps(value, ensure_ascii=False))
                elif kind == 'estimate':
                    estimate_events.append(value)
                    if len(estimate_events) > 20:
                        del estimate_events[:-20]
                    if current[0] in rows:
                        row = rows[current[0]]
                        row['remaining'] = value['remaining_seconds']
                        row['prediction'] = value['elapsed_seconds']+value['remaining_seconds']
                    if value['kind'] == 'initial':
                        low, high = core.Engine._clock(value['low_seconds']), core.Engine._clock(value['high_seconds'])
                        estimate_var.set('当前文件预计总用时 '+low+'～'+high+' · '+value['method']
                                         +(' · 根据本机历史估计' if value['learned'] else ' · 初步估计'))
                        log_ui('自动选择：'+value['method']+'；预计总用时 '+low+'～'+high)
                        if current[0] in rows:
                            rows[current[0]]['eta'] = '约 '+core.Engine._clock(max(1, value['remaining_seconds']))
                    else:
                        remaining = core.Engine._clock(max(1, value['remaining_seconds']))
                        estimate_var.set('当前文件预计还需 '+remaining+' · 已用时 '+core.Engine._clock(value['elapsed_seconds'])+' · '+value['method'])
                        if current[0] in rows:
                            rows[current[0]]['eta'] = remaining
        except queue.Empty:
            pass
        if current[0] in rows:
            row_update(current[0], elapsed=time.monotonic()-current_started[0])
        update_batch_estimate()
        completed = sum(row['status'].startswith('完成') for row in rows.values())
        skipped = sum(row['status'].startswith('已跳过') for row in rows.values())
        failed = sum(row['status'] == '失败' for row in rows.values())
        summary.set(f'{len(rows)} 个任务 · 修复 {completed} · 跳过 {skipped} · 失败 {failed} · 等待 {sum(r["status"] in ("等待开始", "排队中") for r in rows.values())}')
        if not busy.is_set() and pending.empty():
            show_mode()
            if not smoke_seconds:
                show_failures()
            if active_run and all(done is not None for _, done in active_run.values()):
                close_when_done = all(requested and done for requested, done in active_run.values())
                active_run.clear()
                if close_when_done and not any(row['status'] == '等待开始' for row in rows.values()):
                    close()
                    return
        if not quitting.is_set():
            root.after(120, poll)

    for text, action, bs in [('添加文件', select_files, 'primary-outline'), ('添加文件夹', select_folder, 'primary-outline'),
                             ('开始任务', submit, 'primary'), ('停止', stop, 'danger-outline'),
                             ('重试失败', retry_failed, 'warning-outline')]:
        ttk.Button(actions, text=text, command=action, bootstyle=bs, padding=(7, 6)).pack(side='left', padx=(0, 6))
    for text, action in [('视频信息', inspect_selected), ('降分辨率', reduce_resolution),
                         ('打开目录', open_output), ('日志目录', open_log_directory),
                         ('重试选中', lambda: retry_rows(tree.selection(), ('失败', '已取消'))), ('清空记录', clear_completed)]:
        ttk.Button(secondary_actions, text=text, command=action, bootstyle='info-outline',
                   padding=(7, 5)).pack(side='left', padx=(0, 6))
    tree.bind('<Double-1>', play)
    def bind_click(widget):
        widget.bind('<Button-1>', select_files)
        for child in widget.winfo_children():
            bind_click(child)
    bind_click(drop)
    def receive_drop(event):
        add_files(root.tk.splitlist(event.data))
        return 'copy'
    def register_drop(widget):
        widget.drop_target_register(DND_FILES)
        widget.dnd_bind('<<Drop>>', receive_drop)
        for child in widget.winfo_children():
            register_drop(child)
    register_drop(root)
    dnd_version = str(root.tk.call('package', 'present', 'tkdnd'))
    def save_config():
        if smoke_seconds:
            return
        try:
            path = config_file()
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = {'theme': theme_var.get(), 'font_size': int(font_size.get()), 'mode': MODES[mode_var.get()],
                       'speed': SPEEDS[speed_var.get()], 'destination': destination_var.get(), 'autostart': autostart.get(),
                       'smart_scan': smart_scan.get(), 'replace_source': replace_source.get(),
                       'fast_replace': fast_replace.get(),
                       'original_name': original_name.get(),
                       'use_gpu': use_gpu.get(), 'auto_close': auto_close.get(), 'full_check_enabled': CHECKS[check_var.get()],
                       'resource_limits': limits._asdict()}
            temp = path.with_suffix('.tmp')
            temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
            temp.replace(path)
        except OSError:
            pass
    def close():
        if busy.is_set() and not smoke_seconds and not messagebox.askyesno('停止处理？', '关闭会停止当前任务，原文件会保留。'):
            return
        save_config()
        with job_lock:
            quitting.set()
            engine.stop()
        inspector.stop()
        state.set('正在停止，原文件保留…')
        def finish_close():
            if busy.is_set() or inspection_running.is_set():
                root.after(100, finish_close)
            else:
                root.destroy()
        finish_close()
    root.protocol('WM_DELETE_WINDOW', close)
    threading.Thread(target=worker, daemon=True).start()
    def discover_hardware():
        try:
            from hardware_info import hardware_info
            events.put(('hardware', '', hardware_info()))
        except Exception as exc:
            events.put(('info_error', '', '硬件型号读取失败：'+str(exc)+'；转码时仍会实测编码器。'))
    threading.Thread(target=discover_hardware, daemon=True).start()
    root.after(120, poll)
    if initial_files:
        if smoke_seconds:
            autostart.set(True)
            event = type('DropTestEvent', (), {'data': root.tk.call('list', *initial_files)})()
            root.after(350, lambda: receive_drop(event))
        else:
            root.after(350, lambda: add_files(initial_files))
    if smoke_theme:
        def theme_test():
            theme_var.set(smoke_theme)
            change_theme()
        root.after(700, theme_test)
    if smoke_font:
        def font_test():
            font_size.set(str(smoke_font))
            apply_fonts()
        root.after(900, font_test)
    if smoke_seconds:
        test_retry = bool(os.environ.get('MP4FIX_TEST_RETRY'))
        if smoke_cancel_after is not None:
            root.after(int(smoke_cancel_after*1000), stop)
        def finish_smoke():
            nonlocal test_retry
            if busy.is_set() or not pending.empty() or not events.empty():
                root.after(200, finish_smoke)
                return
            if test_retry:
                test_retry = False
                retry_failed()
                root.after(200, finish_smoke)
                return
            root.update_idletasks()
            clipped = []
            def inspect_layout(widget):
                if widget == right:
                    # Settings deliberately scroll inside a fixed viewport.
                    return
                if widget.winfo_viewable():
                    x = widget.winfo_rootx()-root.winfo_rootx()
                    y = widget.winfo_rooty()-root.winfo_rooty()
                    if widget.winfo_class() in ('TButton', 'TCheckbutton', 'TCombobox', 'TLabel') and (
                        x < 0 or y < 0 or x+widget.winfo_width() > root.winfo_width()+2
                        or y+widget.winfo_height() > root.winfo_height()+2
                        or widget.winfo_x()+widget.winfo_width() > widget.master.winfo_width()+2
                        or widget.winfo_y()+widget.winfo_height() > widget.master.winfo_height()+2):
                        clipped.append({'widget': str(widget), 'class': widget.winfo_class(),
                                        'bounds': [x, y, widget.winfo_width(), widget.winfo_height()]})
                    for child in widget.winfo_children():
                        inspect_layout(child)
            inspect_layout(root)
            hidden_settings = []
            def inspect_hidden(widget):
                if widget.winfo_class() in ('TButton', 'TCheckbutton', 'TCombobox', 'TLabel') and not widget.winfo_viewable():
                    hidden_settings.append(str(widget))
                for child in widget.winfo_children():
                    inspect_hidden(child)
            inspect_hidden(right)
            inspect_hidden(actions)
            inspect_hidden(secondary_actions)
            wheel = {}
            if os.environ.get('MP4FIX_TEST_WHEEL'):
                settings_canvas.yview_moveto(0)
                initial_mode = mode_var.get()
                wheel['overflow'] = settings_canvas.yview()[1] < 1
                for target in (settings_canvas, smart_toggle, mode_box, dest_label):
                    settings_canvas.yview_moveto(0)
                    target.event_generate('<MouseWheel>', delta=-120)
                    down = settings_canvas.yview()[0] > 0
                    target.event_generate('<MouseWheel>', delta=120)
                    wheel[target.winfo_class()] = down and settings_canvas.yview()[0] == 0
                tree.event_generate('<MouseWheel>', delta=-120)
                wheel['settings_only'] = settings_canvas.yview()[0] == 0
                wheel['mode_unchanged'] = mode_var.get() == initial_mode
            tips = {}
            if os.environ.get('MP4FIX_TEST_TIPS'):
                for tip in option_tips:
                    tip.widget.event_generate('<Enter>')
                    ready = tk.BooleanVar(root)
                    root.after(tip.delay+30, lambda: ready.set(True))
                    root.wait_variable(ready)
                    visible = bool(tip.toplevel and tip.toplevel.winfo_viewable())
                    if visible:
                        canvas, = tip.toplevel.winfo_children()
                        bubble, tail, text = canvas.find_all()
                        visible = (canvas.itemcget(text, 'fill') == 'white'
                                   and canvas.itemcget(bubble, 'fill') == 'black'
                                   and canvas.itemcget(tail, 'fill') == 'black')
                    tip.widget.event_generate('<Leave>')
                    tips[tip.widget.cget('text')] = visible and tip.toplevel is None
            payload = {'ok': not errors, 'version': core.VERSION, 'tkdnd': dnd_version,
                       'alive_seconds': time.monotonic()-started, 'errors': errors, 'last_output': last_output[0],
                       'jobs': [row['status']+' '+Path(row['source']).name for row in rows.values()],
                       'full_check': CHECKS[check_var.get()], 'title': root.title(), 'mode_badge': mode_badge.get(),
                       'theme': theme_var.get(), 'tk_theme': style.theme.name, 'font_size': int(font_size.get()),
                       'font_actual': named_fonts['TkDefaultFont'].actual(), 'rowheight': style.lookup('Treeview', 'rowheight'),
                       'window_size': [root.winfo_width(), root.winfo_height()], 'clipped_controls': clipped,
                       'hidden_settings': hidden_settings,
                       'settings_height': [right.winfo_height(), right.winfo_reqheight()],
                       'settings_viewport': settings_canvas.winfo_height(), 'settings_wheel': wheel,
                       'settings_tips': tips,
                       'hardware': hardware_data[0], 'estimate_events': estimate_events,
                       'estimate_display': estimate_var.get(),
                       'batch_estimates': list(dict.fromkeys(batch_estimates)),
                       'layout': {name: {'actual': [widget.winfo_width(), widget.winfo_height()],
                                         'requested': [widget.winfo_reqwidth(), widget.winfo_reqheight()],
                                         'mapped': bool(widget.winfo_ismapped())}
                                  for name, widget in [('shell',shell),('header',header),('drop',drop),('body',body),
                                                       ('left',left),('right',right),('footer',footer),('actions',actions)]}}
            if result_file:
                Path(result_file).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
            close()
        root.after(max(1000, int(smoke_seconds*1000)), finish_smoke)
    root.mainloop()
