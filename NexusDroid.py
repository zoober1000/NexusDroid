"""
NexusDroid - Android File Manager, Media Player & System Monitor
Built with Kivy for Android deployment via Buildozer
"""
 
import os
import sys
import threading
import time
import shutil
import mimetypes
from pathlib import Path
from datetime import datetime
 
# Kivy config must happen before importing kivy
from kivy.config import Config
Config.set('graphics', 'resizable', '0')
Config.set('kivy', 'keyboard_mode', 'systemandroid')
 
from kivy.app import App
from kivy.uix.screenmanager import ScreenManager, Screen, SlideTransition, FadeTransition
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.gridlayout import GridLayout
from kivy.uix.scrollview import ScrollView
from kivy.uix.label import Label
from kivy.uix.button import Button
from kivy.uix.image import Image
from kivy.uix.progressbar import ProgressBar
from kivy.uix.popup import Popup
from kivy.uix.textinput import TextInput
from kivy.uix.widget import Widget
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.anchorlayout import AnchorLayout
from kivy.graphics import Color, Rectangle, RoundedRectangle, Line, Ellipse
from kivy.clock import Clock
from kivy.metrics import dp, sp
from kivy.core.window import Window
from kivy.properties import (StringProperty, NumericProperty, 
                              BooleanProperty, ListProperty, ObjectProperty)
from kivy.animation import Animation
from kivy.utils import get_color_from_hex
 
# Platform detection
try:
    from android import activity
    from android.permissions import request_permissions, Permission
    IS_ANDROID = True
except ImportError:
    IS_ANDROID = False
 
# Media playback
try:
    from kivy.core.video import Video
    HAS_VIDEO = True
except Exception:
    HAS_VIDEO = False
 
try:
    from kivy.core.audio import SoundLoader
    HAS_AUDIO = True
except Exception:
    HAS_AUDIO = False
 
# ─── ANDROID-FRIENDLY SYSTEM INFO (no psutil) ────────────────────────────────

def _read_file(path):
    """Safely read a /proc or /sys file, return stripped string or None."""
    try:
        with open(path, 'r') as f:
            return f.read().strip()
    except Exception:
        return None

def get_cpu_percent():
    """
    Parse /proc/stat to compute CPU usage between two calls.
    Returns a float 0-100, or 0.0 on failure.
    """
    raw = _read_file('/proc/stat')
    if not raw:
        return 0.0
    for line in raw.splitlines():
        if line.startswith('cpu '):
            parts = list(map(int, line.split()[1:]))
            idle = parts[3] if len(parts) > 3 else 0
            total = sum(parts)
            prev = getattr(get_cpu_percent, '_prev', None)
            get_cpu_percent._prev = (total, idle)
            if prev is None:
                return 0.0
            prev_total, prev_idle = prev
            d_total = total - prev_total
            d_idle  = idle  - prev_idle
            if d_total == 0:
                return 0.0
            return max(0.0, min(100.0, (1.0 - d_idle / d_total) * 100.0))
    return 0.0

def get_memory_percent():
    """
    Parse /proc/meminfo. Returns (used_percent, total_bytes, free_bytes).
    """
    raw = _read_file('/proc/meminfo')
    if not raw:
        return 0.0, 0, 0
    info = {}
    for line in raw.splitlines():
        parts = line.split()
        if len(parts) >= 2:
            key = parts[0].rstrip(':')
            try:
                info[key] = int(parts[1]) * 1024  # kB → bytes
            except ValueError:
                pass
    total     = info.get('MemTotal', 0)
    free      = info.get('MemFree', 0)
    buffers   = info.get('Buffers', 0)
    cached    = info.get('Cached', 0)
    available = info.get('MemAvailable', free + buffers + cached)
    used      = total - available
    pct       = (used / total * 100.0) if total > 0 else 0.0
    return pct, total, free

def get_battery_percent():
    """
    Read battery level from /sys/class/power_supply/ sysfs nodes.
    Returns (percent_float, status_str).
    """
    for base in ['/sys/class/power_supply/battery',
                 '/sys/class/power_supply/BAT0',
                 '/sys/class/power_supply/BAT1']:
        cap = _read_file(f'{base}/capacity')
        if cap is not None:
            try:
                pct = float(cap)
                status = _read_file(f'{base}/status') or 'Unknown'
                return pct, status
            except ValueError:
                pass
    return 0.0, 'N/A'

def get_cpu_temp():
    """
    Read CPU temperature from /sys/class/thermal/thermal_zone* sysfs nodes.
    Returns degrees Celsius as float, or 0.0.
    """
    import glob
    best = 0.0
    for zone in sorted(glob.glob('/sys/class/thermal/thermal_zone*/temp')):
        raw = _read_file(zone)
        if raw:
            try:
                t = float(raw)
                if t > 1000:
                    t /= 1000.0
                best = max(best, t)
            except ValueError:
                pass
    return best

def get_disk_usage(path='/'):
    """
    Use stdlib shutil.disk_usage — works everywhere, no external deps.
    Returns (used_pct, used_bytes, free_bytes, total_bytes).
    """
    try:
        usage = shutil.disk_usage(path)
        pct = usage.used / usage.total * 100.0 if usage.total else 0.0
        return pct, usage.used, usage.free, usage.total
    except Exception:
        return 0.0, 0, 0, 0

def get_net_io():
    """
    Parse /proc/net/dev for cumulative bytes sent/received.
    Returns (bytes_sent, bytes_recv).
    """
    raw = _read_file('/proc/net/dev')
    if not raw:
        return 0, 0
    sent = recv = 0
    for line in raw.splitlines()[2:]:          # skip 2 header lines
        parts = line.split()
        if len(parts) < 10:
            continue
        iface = parts[0].rstrip(':')
        if iface in ('lo',):                   # skip loopback
            continue
        try:
            recv += int(parts[1])
            sent += int(parts[9])
        except (ValueError, IndexError):
            pass
    return sent, recv

def get_cpu_core_count():
    """Count logical CPU cores from /proc/cpuinfo."""
    raw = _read_file('/proc/cpuinfo')
    if not raw:
        return 1
    return raw.count('processor\t:')

def get_boot_time_str():
    """Parse /proc/uptime to derive approximate boot datetime."""
    raw = _read_file('/proc/uptime')
    if not raw:
        return 'N/A'
    try:
        uptime_secs = float(raw.split()[0])
        boot_dt = datetime.now() - __import__('datetime').timedelta(seconds=uptime_secs)
        return boot_dt.strftime("%d %b %H:%M")
    except Exception:
        return 'N/A'

def get_top_processes(n=6):
    """
    Scan /proc/<pid>/stat for process CPU ticks and memory.
    Returns list of dicts: {name, cpu_pct, mem_pct}.
    """
    procs = []
    try:
        total_mem_kb_raw = _read_file('/proc/meminfo')
        total_mem_kb = 1
        if total_mem_kb_raw:
            for line in total_mem_kb_raw.splitlines():
                if line.startswith('MemTotal'):
                    total_mem_kb = int(line.split()[1])
                    break

        for pid in os.listdir('/proc'):
            if not pid.isdigit():
                continue
            stat = _read_file(f'/proc/{pid}/stat')
            if not stat:
                continue
            fields = stat.split()
            if len(fields) < 14:
                continue
            name = fields[1].strip('()')
            utime = int(fields[13])
            stime = int(fields[14])
            cpu_ticks = utime + stime

            statm = _read_file(f'/proc/{pid}/statm')
            rss_pages = int(statm.split()[1]) if statm else 0
            rss_kb = rss_pages * 4
            mem_pct = (rss_kb / total_mem_kb * 100.0) if total_mem_kb > 0 else 0.0

            procs.append({
                'name': name,
                '_ticks': cpu_ticks,
                'mem_pct': round(mem_pct, 1),
            })
    except Exception:
        pass

    prev = getattr(get_top_processes, '_prev', {})
    clk = os.sysconf('SC_CLK_TCK') if hasattr(os, 'sysconf') else 100
    interval = 2.0
    for p in procs:
        old = prev.get(p['name'], p['_ticks'])
        delta = p['_ticks'] - old
        p['cpu_pct'] = round(min(100.0, delta / clk / interval * 100.0), 1)
    get_top_processes._prev = {p['name']: p['_ticks'] for p in procs}

    return sorted(procs, key=lambda x: x['cpu_pct'], reverse=True)[:n]
 
# ─── THEME ──────────────────────────────────────────────────────────────────
 
THEME = {
    'bg_dark':     '#0A0E1A',
    'bg_card':     '#111827',
    'bg_surface':  '#1C2333',
    'bg_elevated': '#232D42',
    'accent':      '#00D4FF',
    'accent2':     '#7C3AED',
    'accent3':     '#10B981',
    'accent4':     '#F59E0B',
    'danger':      '#EF4444',
    'text_pri':    '#F0F4FF',
    'text_sec':    '#8B9CC8',
    'text_dim':    '#4A5578',
    'border':      '#1E2A42',
    'glow':        '#00D4FF40',
}
 
def c(key): return get_color_from_hex(THEME[key])
 
 
# ─── UTILITIES ───────────────────────────────────────────────────────────────
 
def format_size(size_bytes):
    if size_bytes < 1024:       return f"{size_bytes} B"
    elif size_bytes < 1048576:  return f"{size_bytes/1024:.1f} KB"
    elif size_bytes < 1073741824: return f"{size_bytes/1048576:.1f} MB"
    else:                        return f"{size_bytes/1073741824:.2f} GB"
 
def format_time(seconds):
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h: return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"
 
def get_file_icon(filepath):
    ext = Path(filepath).suffix.lower()
    icons = {
        '.mp4': '🎬', '.mkv': '🎬', '.avi': '🎬', '.mov': '🎬', '.webm': '🎬',
        '.mp3': '🎵', '.wav': '🎵', '.flac': '🎵', '.aac': '🎵', '.ogg': '🎵',
        '.jpg': '🖼️', '.jpeg': '🖼️', '.png': '🖼️', '.gif': '🖼️', '.webp': '🖼️',
        '.pdf': '📄', '.doc': '📝', '.docx': '📝', '.txt': '📃',
        '.zip': '📦', '.rar': '📦', '.tar': '📦', '.gz': '📦',
        '.apk': '📱', '.py': '🐍', '.js': '⚡', '.html': '🌐',
        '.xls': '📊', '.xlsx': '📊', '.csv': '📊',
        '.ppt': '📋', '.pptx': '📋',
    }
    return icons.get(ext, '📄')
 
def get_mime_category(filepath):
    ext = Path(filepath).suffix.lower()
    if ext in ['.mp4','.mkv','.avi','.mov','.webm','.3gp']: return 'video'
    if ext in ['.mp3','.wav','.flac','.aac','.ogg','.m4a']: return 'audio'
    if ext in ['.jpg','.jpeg','.png','.gif','.bmp','.webp']: return 'image'
    if ext in ['.apk']: return 'apk'
    return 'file'
 
 
# ─── CUSTOM WIDGETS ──────────────────────────────────────────────────────────
 
class GlowButton(Button):
    btn_color = ListProperty([0, 0.83, 1, 1])
 
    def __init__(self, **kwargs):
        self.btn_color = kwargs.pop('btn_color', c('accent'))
        super().__init__(**kwargs)
        self.background_color = (0, 0, 0, 0)
        self.background_normal = ''
        self.color = c('text_pri')
        self.font_size = sp(13)
        self.bold = True
        self.bind(size=self._draw, pos=self._draw)
 
    def _draw(self, *_):
        self.canvas.before.clear()
        with self.canvas.before:
            Color(*self.btn_color[:3], 0.15)
            RoundedRectangle(pos=self.pos, size=self.size, radius=[dp(10)])
            Color(*self.btn_color)
            Line(rounded_rectangle=[*self.pos, *self.size, dp(10)], width=1.2)
 
 
class CardWidget(BoxLayout):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.bind(size=self._draw, pos=self._draw)
 
    def _draw(self, *_):
        self.canvas.before.clear()
        with self.canvas.before:
            Color(*c('bg_card'))
            RoundedRectangle(pos=self.pos, size=self.size, radius=[dp(14)])
            Color(*c('border'))
            Line(rounded_rectangle=[*self.pos, *self.size, dp(14)], width=0.8)
 
 
class CircularProgress(Widget):
    value = NumericProperty(0)
    max_val = NumericProperty(100)
    color = ListProperty([0, 0.83, 1, 1])
    label = StringProperty('')
    unit = StringProperty('%')
 
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.bind(value=self._draw, size=self._draw, pos=self._draw)
 
    def _draw(self, *_):
        self.canvas.clear()
        cx = self.center_x
        cy = self.center_y
        r = min(self.width, self.height) / 2 - dp(6)
        angle = (self.value / max(self.max_val, 1)) * 360
        with self.canvas:
            Color(*c('bg_surface'))
            Line(circle=(cx, cy, r), width=dp(5))
            Color(*self.color)
            Line(circle=(cx, cy, r, 90, 90 - angle), width=dp(5), cap='round')
            Color(*c('text_pri'))
 
        self.canvas.after.clear()
 
 
class NavBar(BoxLayout):
    current = StringProperty('dashboard')
 
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.orientation = 'horizontal'
        self.size_hint_y = None
        self.height = dp(64)
        self.padding = [dp(4), dp(6), dp(4), dp(6)]
        self.spacing = dp(2)
        self.bind(size=self._bg, pos=self._bg)
        self._build()
 
    def _bg(self, *_):
        self.canvas.before.clear()
        with self.canvas.before:
            Color(*c('bg_card'))
            Rectangle(pos=self.pos, size=self.size)
            Color(*c('border'))
            Line(points=[self.x, self.top, self.right, self.top], width=0.8)
 
    def _build(self):
        self.clear_widgets()
        tabs = [
            ('dashboard', '⚡', 'System'),
            ('files',     '📁', 'Files'),
            ('media',     '🎬', 'Media'),
            ('apps',      '📱', 'Apps'),
        ]
        for tid, icon, label in tabs:
            btn = Button(
                text=f"{icon}\n{label}",
                background_color=(0,0,0,0),
                background_normal='',
                halign='center',
                markup=True,
                font_size=sp(9.5),
            )
            is_active = (tid == self.current)
            btn.color = c('accent') if is_active else c('text_dim')
            btn.bold = is_active
            btn.id = tid
            btn.bind(on_release=lambda b, t=tid: self.navigate(t))
            self.add_widget(btn)
 
    def navigate(self, target):
        self.current = target
        self._build()
        App.get_running_app().navigate(target)
 
    def set_active(self, tab_id):
        self.current = tab_id
        self._build()
 
 
# ─── SCREENS ─────────────────────────────────────────────────────────────────
 
class DashboardScreen(Screen):
    """System performance monitor"""
 
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._circles = {}
        self._labels = {}
        self._bars = {}
        self._build_ui()
        Clock.schedule_interval(self._update_stats, 2.0)
        self._update_stats(0)
 
    def _build_ui(self):
        root = BoxLayout(orientation='vertical',
                         padding=[dp(12), dp(12), dp(12), dp(4)],
                         spacing=dp(10))
        hdr = BoxLayout(size_hint_y=None, height=dp(48))
        hdr.add_widget(Label(
            text='[b]⚡ SYSTEM MONITOR[/b]', markup=True,
            color=c('accent'), font_size=sp(17),
            halign='left', valign='middle', size_hint_x=0.7
        ))
        self._time_lbl = Label(
            text='', color=c('text_sec'), font_size=sp(11),
            halign='right', valign='middle', size_hint_x=0.3
        )
        hdr.add_widget(self._time_lbl)
        root.add_widget(hdr)
 
        circles_card = CardWidget(
            size_hint_y=None, height=dp(160),
            padding=dp(12), spacing=dp(8), orientation='horizontal'
        )
        metrics = [
            ('cpu',  'CPU',   c('accent'),  '%'),
            ('ram',  'RAM',   c('accent2'), '%'),
            ('bat',  'BAT',   c('accent3'), '%'),
            ('temp', 'TEMP',  c('accent4'), '°'),
        ]
        for key, label, col, unit in metrics:
            col_box = BoxLayout(orientation='vertical', spacing=dp(4))
            circ = CircularProgress(color=col, unit=unit)
            self._circles[key] = circ
            val_lbl = Label(text='--', color=c('text_pri'),
                            font_size=sp(13), bold=True,
                            size_hint_y=None, height=dp(20), halign='center')
            self._labels[key] = val_lbl
            name_lbl = Label(text=label, color=c('text_sec'),
                             font_size=sp(9), size_hint_y=None, height=dp(16), halign='center')
            col_box.add_widget(circ)
            col_box.add_widget(val_lbl)
            col_box.add_widget(name_lbl)
            circles_card.add_widget(col_box)
        root.add_widget(circles_card)
 
        stor_card = CardWidget(
            size_hint_y=None, height=dp(100),
            orientation='vertical', padding=dp(12), spacing=dp(8)
        )
        stor_hdr = BoxLayout(size_hint_y=None, height=dp(22))
        stor_hdr.add_widget(Label(text='[b]💾 Storage[/b]', markup=True,
                                   color=c('text_pri'), font_size=sp(13), halign='left'))
        self._stor_lbl = Label(text='', color=c('text_sec'), font_size=sp(11), halign='right')
        stor_hdr.add_widget(self._stor_lbl)
        stor_card.add_widget(stor_hdr)
 
        pb = ProgressBar(max=100, value=0, size_hint_y=None, height=dp(10))
        self._bars['storage'] = pb
        stor_card.add_widget(pb)
 
        self._stor_detail = Label(text='', color=c('text_sec'), font_size=sp(10))
        stor_card.add_widget(self._stor_detail)
        root.add_widget(stor_card)
 
        net_card = CardWidget(
            size_hint_y=None, height=dp(100),
            orientation='vertical', padding=dp(12), spacing=dp(6)
        )
        net_card.add_widget(Label(text='[b]🌐 Network & Info[/b]', markup=True,
                                   color=c('text_pri'), font_size=sp(13),
                                   size_hint_y=None, height=dp(22), halign='left'))
        self._net_lbl = Label(text='Fetching...', color=c('text_sec'),
                               font_size=sp(10), halign='left', valign='top')
        self._net_lbl.bind(size=self._net_lbl.setter('text_size'))
        net_card.add_widget(self._net_lbl)
        root.add_widget(net_card)
 
        proc_card = CardWidget(
            orientation='vertical', padding=dp(12), spacing=dp(6)
        )
        proc_hdr = BoxLayout(size_hint_y=None, height=dp(28))
        proc_hdr.add_widget(Label(text='[b]🔄 Top Processes[/b]', markup=True,
                                   color=c('text_pri'), font_size=sp(13), halign='left'))
        proc_card.add_widget(proc_hdr)
        sv = ScrollView()
        self._proc_layout = BoxLayout(orientation='vertical',
                                       size_hint_y=None, spacing=dp(4))
        self._proc_layout.bind(minimum_height=self._proc_layout.setter('height'))
        sv.add_widget(self._proc_layout)
        proc_card.add_widget(sv)
        root.add_widget(proc_card)
 
        self.add_widget(root)
 
    def _update_stats(self, dt):
        now = datetime.now().strftime("%H:%M:%S")
        self._time_lbl.text = now
 
        cpu = get_cpu_percent()
        self._circles['cpu'].value = cpu
        self._labels['cpu'].text = f"{cpu:.0f}%"
 
        ram_pct, _, _ = get_memory_percent()
        self._circles['ram'].value = ram_pct
        self._labels['ram'].text = f"{ram_pct:.0f}%"
 
        bat_pct, bat_status = get_battery_percent()
        self._circles['bat'].value = bat_pct
        self._labels['bat'].text = f"{bat_pct:.0f}%"
 
        temp = get_cpu_temp()
        self._circles['temp'].value = min(temp, 100)
        self._labels['temp'].text = f"{temp:.0f}°"
 
        disk_pct, disk_used, disk_free, disk_total = get_disk_usage('/')
        self._bars['storage'].value = disk_pct
        self._stor_lbl.text = f"{disk_pct:.1f}%"
        self._stor_detail.text = (
            f"Used: {format_size(disk_used)}  |  "
            f"Free: {format_size(disk_free)}  |  "
            f"Total: {format_size(disk_total)}"
        )
 
        sent, recv = get_net_io()
        cpu_cnt  = get_cpu_core_count()
        boot_str = get_boot_time_str()
        self._net_lbl.text = (
            f"\u2191 Sent: {format_size(sent)}  |  "
            f"\u2193 Recv: {format_size(recv)}\n"
            f"CPU Cores: {cpu_cnt}  |  Boot: {boot_str}"
        )
 
        try:
            procs = get_top_processes(6)
            self._proc_layout.clear_widgets()
            for p in procs:
                row = BoxLayout(size_hint_y=None, height=dp(26), spacing=dp(6))
                row.add_widget(Label(
                    text=p['name'][:18], color=c('text_sec'),
                    font_size=sp(10), size_hint_x=0.5, halign='left'
                ))
                row.add_widget(Label(
                    text=f"CPU {p['cpu_pct']:.1f}%",
                    color=c('accent'), font_size=sp(10), size_hint_x=0.25
                ))
                row.add_widget(Label(
                    text=f"MEM {p['mem_pct']:.1f}%",
                    color=c('accent2'), font_size=sp(10), size_hint_x=0.25
                ))
                self._proc_layout.add_widget(row)
        except Exception:
            pass

# ─── FILE MANAGER SCREEN FIXED DEFINITION ────────────────────────────────────

class FileManagerScreen(Screen):
    """File manager and directory browser"""
 
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.current_path = Path(os.path.expanduser('~'))
        self.selected_files = []
        self.clipboard = []
        self.clipboard_op = None
        self._build_ui()
        self._load_dir()
 
    def _build_ui(self):
        root = BoxLayout(orientation='vertical',
                         padding=[dp(10), dp(10), dp(10), dp(4)],
                         spacing=dp(8))
        hdr = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(8))
        hdr.add_widget(Label(text='[b]📁 FILE MANAGER[/b]', markup=True,
                              color=c('accent'), font_size=sp(16),
                              size_hint_x=0.6, halign='left'))
        self._search_input = TextInput(
            hint_text='🔍 Search...',
            size_hint_x=0.4, size_hint_y=None, height=dp(36),
            multiline=False, font_size=sp(12),
            background_color=(*c('bg_surface')[:3], 1),
            foreground_color=c('text_pri'),
            cursor_color=c('accent'),
        )
        self._search_input.bind(text=self._on_search)
        hdr.add_widget(self._search_input)
        root.add_widget(hdr)
 
        breadcrumb_sv = ScrollView(size_hint_y=None, height=dp(32), do_scroll_y=False)
        self._breadcrumb = BoxLayout(orientation='horizontal',
                                      size_hint_x=None, spacing=dp(4),
                                      padding=[dp(4), 0, dp(4), 0])
        self._breadcrumb.bind(minimum_width=self._breadcrumb.setter('width'))
        breadcrumb_sv.add_widget(self._breadcrumb)
        root.add_widget(breadcrumb_sv)
 
        toolbar = BoxLayout(size_hint_y=None, height=dp(42), spacing=dp(6))
        actions = [
            ('⬆ Up',     self._go_up),
            ('📂 New',   self._new_folder),
            ('📋 Paste', self._paste),
            ('⌂ Home',  self._go_home),
        ]
        for label, cb in actions:
            btn = GlowButton(text=label, btn_color=c('accent'), font_size=sp(11))
            btn.bind(on_release=lambda b, fn=cb: fn())
            toolbar.add_widget(btn)
        root.add_widget(toolbar)
 
        sort_bar = BoxLayout(size_hint_y=None, height=dp(30), spacing=dp(6))
        sort_bar.add_widget(Label(text='Sort:', color=c('text_dim'),
                                   font_size=sp(10), size_hint_x=None, width=dp(32)))
        for lbl, key in [('Name','name'), ('Size','size'), ('Date','date')]:
            b = Button(text=lbl, font_size=sp(10),
                       background_color=(0,0,0,0), background_normal='',
                       color=c('accent'), bold=True)
            b.bind(on_release=lambda x, k=key: self._sort_by(k))
            sort_bar.add_widget(b)
        root.add_widget(sort_bar)
 
        self._scroll = ScrollView()
        self._file_list = BoxLayout(orientation='vertical',
                                     size_hint_y=None, spacing=dp(2))
        self._file_list.bind(minimum_height=self._file_list.setter('height'))
        self._scroll.add_widget(self._file_list)
        root.add_widget(self._scroll)
 
        self._status = Label(text='', color=c('text_dim'),
                              font_size=sp(10), size_hint_y=None, height=dp(20))
        root.add_widget(self._status)
        self.add_widget(root)
 
    def _load_dir(self, filter_text=''):
        self._file_list.clear_widgets()
        self._update_breadcrumb()
        try:
            entries = list(self.current_path.iterdir())
        except PermissionError:
            self._status.text = "⛔ Permission denied"
            return
 
        dirs = [e for e in entries if e.is_dir()]
        files = [e for e in entries if e.is_file()]
        dirs.sort(key=lambda x: x.name.lower())
        files.sort(key=lambda x: x.name.lower())
        all_entries = dirs + files
 
        if filter_text:
            all_entries = [e for e in all_entries if filter_text.lower() in e.name.lower()]
 
        for entry in all_entries:
            self._add_entry_row(entry)
 
        count_d = len([e for e in all_entries if e.is_dir()])
        count_f = len([e for e in all_entries if e.is_file()])
        self._status.text = f"{count_d} folders, {count_f} files"
 
    def _add_entry_row(self, entry):
        row = BoxLayout(size_hint_y=None, height=dp(52), spacing=dp(8),
                        padding=[dp(10), dp(4), dp(8), dp(4)])
        row._entry = entry
 
        with row.canvas.before:
            Color(*c('bg_surface'))
            row._bg = RoundedRectangle(pos=row.pos, size=row.size, radius=[dp(8)])
        row.bind(pos=lambda w,_: setattr(w._bg, 'pos', w.pos),
                 size=lambda w,_: setattr(w._bg, 'size', w.size))
 
        icon_text = '📁' if entry.is_dir() else get_file_icon(str(entry))
        icon = Label(text=icon_text, font_size=sp(22), size_hint_x=None, width=dp(40))
        row.add_widget(icon)
 
        info = BoxLayout(orientation='vertical', spacing=dp(2))
        name_lbl = Label(text=entry.name, color=c('text_pri'), font_size=sp(12), bold=True, halign='left', valign='middle')
        name_lbl.bind(size=name_lbl.setter('text_size'))
 
        try:
            stat = entry.stat()
            size_str = 'Folder' if entry.is_dir() else format_size(stat.st_size)
            date_str = datetime.fromtimestamp(stat.st_mtime).strftime("%b %d, %Y")
            meta_text = f"{size_str}  ·  {date_str}"
        except Exception:
            meta_text = ''
 
        meta_lbl = Label(text=meta_text, color=c('text_dim'), font_size=sp(9.5), halign='left', valign='middle')
        meta_lbl.bind(size=meta_lbl.setter('text_size'))
        info.add_widget(name_lbl)
        info.add_widget(meta_lbl)
        row.add_widget(info)
 
        act_col = BoxLayout(orientation='vertical', size_hint_x=None, width=dp(28), spacing=dp(2))
        opts_btn = Button(text='⋮', font_size=sp(18), background_color=(0,0,0,0), background_normal='', color=c('text_sec'), size_hint_y=None, height=dp(44))
        opts_btn.bind(on_release=lambda b, e=entry: self._show_context_menu(e))
        act_col.add_widget(opts_btn)
        row.add_widget(act_col)
 
        row.bind(on_touch_down=lambda w, t: self._on_row_tap(w, t))
        self._file_list.add_widget(row)
 
    def _on_row_tap(self, widget, touch):
        if widget.collide_point(*touch.pos):
            if touch.is_double_tap:
                self._open_entry(widget._entry)
 
    def _open_entry(self, entry):
        if entry.is_dir():
            self.current_path = entry
            self._load_dir()
        else:
            cat = get_mime_category(str(entry))
            app = App.get_running_app()
            if cat in ('video', 'audio'):
                app.navigate('media', filepath=str(entry), media_type=cat)
            else:
                self._show_file_info(entry)
 
    def _update_breadcrumb(self):
        self._breadcrumb.clear_widgets()
        parts = list(self.current_path.parts)
        for i, part in enumerate(parts):
            path_so_far = Path(*parts[:i+1])
            btn = Button(text=part, font_size=sp(11), background_color=(0,0,0,0), background_normal='', color=c('accent'), bold=True, size_hint_x=None, width=dp(max(len(part)*8, 30)))
            btn.bind(on_release=lambda b, p=path_so_far: self._jump_path(p))
            self._breadcrumb.add_widget(btn)
            if i < len(parts)-1:
                self._breadcrumb.add_widget(Label(text='›', color=c('text_dim'), font_size=sp(14), size_hint_x=None, width=dp(14)))
 
    def _jump_path(self, path):
        self.current_path = path
        self._load_dir()
 
    def _go_up(self):
        parent = self.current_path.parent
        if parent != self.current_path:
            self.current_path = parent
            self._load_dir()
 
    def _go_home(self):
        self.current_path = Path(os.path.expanduser('~'))
        self._load_dir()
 
    def _on_search(self, inst, text):
        self._load_dir(filter_text=text)
 
    def _sort_by(self, key):
        self._load_dir(filter_text=self._search_input.text)
 
    def _new_folder(self):
        content = BoxLayout(orientation='vertical', padding=dp(12), spacing=dp(10))
        ti = TextInput(hint_text='Folder name', multiline=False, font_size=sp(14), size_hint_y=None, height=dp(42))
        content.add_widget(ti)
        btns = BoxLayout(size_hint_y=None, height=dp(42), spacing=dp(8))
        popup = Popup(title='New Folder', content=content, size_hint=(0.8, None), height=dp(180))
 
        def create(_):
            name = ti.text.strip()
            if name:
                try:
                    (self.current_path / name).mkdir(exist_ok=True)
                    self._load_dir()
                except Exception:
                    pass
            popup.dismiss()
 
        ok = GlowButton(text='Create', btn_color=c('accent3'))
        ok.bind(on_release=create)
        cn = GlowButton(text='Cancel', btn_color=c('danger'))
        cn.bind(on_release=popup.dismiss)
        btns.add_widget(cn)
        btns.add_widget(ok)
        content.add_widget(btns)
        popup.open()
 
    def _paste(self):
        if not self.clipboard:
            return
        for src in self.clipboard:
            dst = self.current_path / Path(src).name
            try:
                if Path(src).is_dir(): shutil.copytree(src, dst)
                else: shutil.copy2(src, dst)
                if self.clipboard_op == 'cut':
                    if Path(src).is_dir(): shutil.rmtree(src)
                    else: os.remove(src)
            except Exception:
                pass
        self.clipboard = []
        self.clipboard_op = None
        self._load_dir()
 
    def _show_context_menu(self, entry):
        content = BoxLayout(orientation='vertical', padding=dp(8), spacing=dp(6))
        options = [('📋 Copy', 'copy'), ('✂️ Cut', 'cut'), ('🗑️ Delete', 'delete'), ('ℹ️ Info', 'info')]
        popup = Popup(title=entry.name[:30], content=content, size_hint=(0.75, None), height=dp(280))
 
        for label, action in options:
            btn = GlowButton(text=label, btn_color=c('accent'), size_hint_y=None, height=dp(44))
            btn.bind(on_release=lambda b, a=action, e=entry, p=popup: self._ctx_action(a, e, p))
            content.add_widget(btn)
 
        cancel = GlowButton(text='✕ Cancel', btn_color=c('danger'), size_hint_y=None, height=dp(40))
        cancel.bind(on_release=popup.dismiss)
        content.add_widget(cancel)
        popup.open()
 
    def _ctx_action(self, action, entry, popup):
        popup.dismiss()
        if action == 'copy':
            self.clipboard = [str(entry)]
            self.clipboard_op = 'copy'
        elif action == 'cut':
            self.clipboard = [str(entry)]
            self.clipboard_op = 'cut'
        elif action == 'delete':
            self._confirm_delete(entry)
        elif action == 'info':
            self._show_file_info(entry)
 
    def _confirm_delete(self, entry):
        content = BoxLayout(orientation='vertical', padding=dp(12), spacing=dp(10))
        content.add_widget(Label(text=f"Delete '{entry.name}'?", color=c('text_pri'), font_size=sp(13)))
        btns = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(8))
        popup = Popup(title='Confirm Delete', content=content, size_hint=(0.8, None), height=dp(160))
        def delete(_):
            try:
                if entry.is_dir(): shutil.rmtree(entry)
                else: entry.unlink()
                self._load_dir()
            except Exception: pass
            popup.dismiss()
        ok = GlowButton(text='🗑️ Delete', btn_color=c('danger'))
        ok.bind(on_release=delete)
        cn = GlowButton(text='Cancel', btn_color=c('accent'))
        cn.bind(on_release=popup.dismiss)
        btns.add_widget(cn)
        btns.add_widget(ok)
        content.add_widget(btns)
        popup.open()
 
    def _show_file_info(self, entry):
        try:
            stat = entry.stat()
            size = format_size(stat.st_size) if entry.is_file() else 'Folder'
            modified = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
            info_text = f"Name: {entry.name}\nPath: {entry.parent}\nSize: {size}\nModified: {modified}\nType: {get_mime_category(str(entry))}"
        except Exception as e:
            info_text = str(e)
 
        content = BoxLayout(orientation='vertical', padding=dp(12), spacing=dp(8))
        lbl = Label(text=info_text, color=c('text_sec'), font_size=sp(12), halign='left', valign='top')
        lbl.bind(size=lbl.setter('text_size'))
        content.add_widget(lbl)
        close = GlowButton(text='Close', btn_color=c('accent'), size_hint_y=None, height=dp(44))
        popup = Popup(title='File Info', content=content, size_hint=(0.85, None), height=dp(260))
        close.bind(on_release=popup.dismiss)
        content.add_widget(close)
        popup.open()
 
 
class MediaScreen(Screen):
    """Video and Audio player with library browser"""
 
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._sound = None
        self._video = None
        self._is_playing = False
        self._current_file = None
        self._media_type = None
        self._play_timer = None
        self._build_ui()
        self._scan_media()
 
    def _build_ui(self):
        root = BoxLayout(orientation='vertical', padding=[dp(10), dp(10), dp(10), dp(4)], spacing=dp(8))
        hdr = BoxLayout(size_hint_y=None, height=dp(44))
        hdr.add_widget(Label(text='[b]🎬 MEDIA PLAYER[/b]', markup=True, color=c('accent'), font_size=sp(16), halign='left'))
        root.add_widget(hdr)
 
        player_card = CardWidget(size_hint_y=None, height=dp(220), orientation='vertical', padding=dp(12), spacing=dp(8))
        self._artwork = Label(text='🎵', font_size=sp(64), size_hint_y=0.55, halign='center')
        player_card.add_widget(self._artwork)
 
        self._track_name = Label(text='No media selected', color=c('text_pri'), font_size=sp(13), bold=True, size_hint_y=None, height=dp(22), halign='center')
        self._track_sub = Label(text='Tap a file below to play', color=c('text_sec'), font_size=sp(10), size_hint_y=None, height=dp(18), halign='center')
        player_card.add_widget(self._track_name)
        player_card.add_widget(self._track_sub)
 
        self._progress = ProgressBar(max=100, value=0, size_hint_y=None, height=dp(8))
        player_card.add_widget(self._progress)
 
        time_row = BoxLayout(size_hint_y=None, height=dp(16))
        self._time_elapsed = Label(text='0:00', color=c('text_dim'), font_size=sp(9))
        self._time_total = Label(text='0:00', color=c('text_dim'), font_size=sp(9), halign='right')
        time_row.add_widget(self._time_elapsed)
        time_row.add_widget(self._time_total)
        player_card.add_widget(time_row)
        root.add_widget(player_card)
 
        ctrl = BoxLayout(size_hint_y=None, height=dp(56), spacing=dp(10))
        self._prev_btn = GlowButton(text='⏮', btn_color=c('accent'), font_size=sp(20))
        self._play_btn = GlowButton(text='▶', btn_color=c('accent3'), font_size=sp(22))
        self._next_btn = GlowButton(text='⏭', btn_color=c('accent'), font_size=sp(20))
        self._stop_btn = GlowButton(text='⏹', btn_color=c('danger'), font_size=sp(18))
        self._prev_btn.bind(on_release=lambda _: self._prev_track())
        self._play_btn.bind(on_release=lambda _: self._toggle_play())
        self._next_btn.bind(on_release=lambda _: self._next_track())
        self._stop_btn.bind(on_release=lambda _: self._stop())
        for b in [self._prev_btn, self._play_btn, self._next_btn, self._stop_btn]:
            ctrl.add_widget(b)
        root.add_widget(ctrl)
 
        tab_row = BoxLayout(size_hint_y=None, height=dp(38), spacing=dp(6))
        self._tab_video = GlowButton(text='🎬 Videos', btn_color=c('accent'))
        self._tab_audio = GlowButton(text='🎵 Audio', btn_color=c('accent2'))
        self._tab_video.bind(on_release=lambda _: self._show_tab('video'))
        self._tab_audio.bind(on_release=lambda _: self._show_tab('audio'))
        tab_row.add_widget(self._tab_video)
        tab_row.add_widget(self._tab_audio)
        root.add_widget(tab_row)
 
        scroll = ScrollView()
        self._media_list = BoxLayout(orientation='vertical', size_hint_y=None, spacing=dp(3))
        self._media_list.bind(minimum_height=self._media_list.setter('height'))
        scroll.add_widget(self._media_list)
        root.add_widget(scroll)
 
        self.add_widget(root)
        self._current_tab = 'video'
        self._all_media = {'video': [], 'audio': []}
        self._track_index = 0
 
    def load_file(self, filepath, media_type):
        self._current_file = filepath
        self._media_type = media_type
        self._play_file(filepath, media_type)
 
    def _play_file(self, filepath, media_type):
        self._stop()
        name = Path(filepath).stem
        self._track_name.text = name[:30]
        self._track_sub.text = media_type.capitalize() + ' · ' + format_size(os.path.getsize(filepath) if os.path.exists(filepath) else 0)
 
        if media_type == 'audio' and HAS_AUDIO:
            self._artwork.text = '🎵'
            sound = SoundLoader.load(filepath)
            if sound:
                self._sound = sound
                self._sound.play()
                self._is_playing = True
                self._play_btn.text = '⏸'
                if self._play_timer: self._play_timer.cancel()
                self._play_timer = Clock.schedule_interval(self._update_progress, 0.5)
        elif media_type == 'video':
            self._artwork.text = '🎬'
            self._is_playing = True
            self._play_btn.text = '⏸'
        else:
            self._artwork.text = get_file_icon(filepath)
 
    def _toggle_play(self):
        if not self._current_file: return
        if self._is_playing:
            if self._sound: self._sound.stop()
            self._is_playing = False
            self._play_btn.text = '▶'
        else:
            if self._sound: self._sound.play()
            self._is_playing = True
            self._play_btn.text = '⏸'
 
    def _stop(self):
        if self._sound:
            self._sound.stop()
            self._sound = None
        if self._play_timer:
            self._play_timer.cancel()
            self._play_timer = None
        self._is_playing = False
        self._play_btn.text = '▶'
        self._progress.value = 0
        self._time_elapsed.text = '0:00'
 
    def _update_progress(self, dt):
        if self._sound and self._sound.length:
            pos = self._sound.get_pos()
            length = self._sound.length
            self._progress.value = (pos / length) * 100
            self._time_elapsed.text = format_time(pos)
            self._time_total.text = format_time(length)
 
    def _prev_track(self):
        lst = self._all_media.get(self._current_tab, [])
        if lst:
            self._track_index = (self._track_index - 1) % len(lst)
            self.load_file(lst[self._track_index], self._current_tab)
 
    def _next_track(self):
        lst = self._all_media.get(self._current_tab, [])
        if lst:
            self._track_index = (self._track_index + 1) % len(lst)
            self.load_file(lst[self._track_index], self._current_tab)
 
    def _show_tab(self, tab):
        self._current_tab = tab
        self._populate_list(tab)
 
    def _scan_media(self):
        def scan():
            home = Path(os.path.expanduser('~'))
            videos, audios = [], []
            for root_dir, dirs, files in os.walk(home):
                dirs[:] = [d for d in dirs if not d.startswith('.')]
                for f in files:
                    fp = os.path.join(root_dir, f)
                    cat = get_mime_category(fp)
                    if cat == 'video': videos.append(fp)
                    elif cat == 'audio': audios.append(fp)
            self._all_media['video'] = sorted(videos)
            self._all_media['audio'] = sorted(audios)
            Clock.schedule_once(lambda dt: self._populate_list('video'), 0)
        threading.Thread(target=scan, daemon=True).start()
 
    def _populate_list(self, tab):
        self._media_list.clear_widgets()
        items = self._all_media.get(tab, [])
        icon = '🎬' if tab == 'video' else '🎵'
        if not items:
            self._media_list.add_widget(Label(text=f'No {tab} files found', color=c('text_dim'), font_size=sp(12), size_hint_y=None, height=dp(40)))
            return
        for fp in items[:50]:
            row = BoxLayout(size_hint_y=None, height=dp(52), spacing=dp(8), padding=[dp(10), dp(4), dp(10), dp(4)])
            with row.canvas.before:
                Color(*c('bg_surface'))
                row._bg = RoundedRectangle(pos=row.pos, size=row.size, radius=[dp(8)])
            row.bind(pos=lambda w,_: setattr(w._bg,'pos',w.pos), size=lambda w,_: setattr(w._bg,'size',w.size))
            row.add_widget(Label(text=icon, font_size=sp(22), size_hint_x=None, width=dp(36)))
            info = BoxLayout(orientation='vertical')
            info.add_widget(Label(text=Path(fp).stem[:30], color=c('text_pri'), font_size=sp(12), bold=True, halign='left', valign='middle'))
            try: sz = format_size(os.path.getsize(fp))
            except Exception: sz = ''
            info.add_widget(Label(text=sz, color=c('text_dim'), font_size=sp(9.5), halign='left', valign='middle'))
            row.add_widget(info)
            play_btn = GlowButton(text='▶', btn_color=c('accent3'), size_hint_x=None, width=dp(40))
            play_btn.bind(on_release=lambda b, f=fp, t=tab: self.load_file(f, t))
            row.add_widget(play_btn)
            self._media_list.add_widget(row)
 
 
class AppManagerScreen(Screen):
    """App manager with installed apps list"""
 
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._build_ui()
        self._load_apps()
 
    def _build_ui(self):
        root = BoxLayout(orientation='vertical', padding=[dp(10), dp(10), dp(10), dp(4)], spacing=dp(8))
        hdr = BoxLayout(size_hint_y=None, height=dp(44))
        hdr.add_widget(Label(text='[b]📱 APP MANAGER[/b]', markup=True, color=c('accent'), font_size=sp(16), halign='left'))
        root.add_widget(hdr)
 
        self._search = TextInput(hint_text='🔍 Search apps...', multiline=False, size_hint_y=None, height=dp(40), font_size=sp(12), background_color=(*c('bg_surface')[:3], 1), foreground_color=c('text_pri'))
        self._search.bind(text=self._filter_apps)
        root.add_widget(self._search)
 
        stats_row = BoxLayout(size_hint_y=None, height=dp(70), spacing=dp(8))
        self._stat_cards = {}
        for key, label, col, icon in [('total', 'Total\nApps', c('accent'), '📦'), ('apks', 'APK\nFiles', c('accent2'), '📁'), ('user', 'User\nApps', c('accent3'), '👤')]:
            card = CardWidget(orientation='vertical', padding=dp(8))
            card.add_widget(Label(text=icon, font_size=sp(20), size_hint_y=0.5))
            val_lbl = Label(text='--', color=col, font_size=sp(14), bold=True)
            card.add_widget(val_lbl)
            card.add_widget(Label(text=label, color=c('text_sec'), font_size=sp(8.5)))
            self._stat_cards[key] = val_lbl
            stats_row.add_widget(card)
        root.add_widget(stats_row)
 
        apk_card = CardWidget(size_hint_y=None, height=dp(52), orientation='horizontal', padding=dp(10), spacing=dp(10))
        apk_card.add_widget(Label(text='📦 Install APK from Files', color=c('text_pri'), font_size=sp(12), halign='left'))
        install_btn = GlowButton(text='Browse', btn_color=c('accent4'), size_hint_x=None, width=dp(80))
        install_btn.bind(on_release=lambda _: self._browse_apk())
        apk_card.add_widget(install_btn)
        root.add_widget(apk_card)
 
        scroll = ScrollView()
        self._app_list = BoxLayout(orientation='vertical', size_hint_y=None, spacing=dp(3))
        self._app_list.bind(minimum_height=self._app_list.setter('height'))
        scroll.add_widget(self._app_list)
        root.add_widget(scroll)
 
        self.add_widget(root)
        self._all_apps = []
 
    def _load_apps(self):
        def scan():
            apks = []
            search_dirs = [os.path.expanduser('~/Downloads'), os.path.expanduser('~/'), '/sdcard/Download', '/sdcard/']
            for d in search_dirs:
                try:
                    for root_dir, dirs, files in os.walk(d):
                        dirs[:] = [x for x in dirs if not x.startswith('.')]
                        for f in files:
                            if f.lower().endswith('.apk'): apks.append(os.path.join(root_dir, f))
                except Exception: pass
 
            installed = []
            if IS_ANDROID:
                try:
                    from jnius import autoclass
                    pm = autoclass('android.content.pm.PackageManager')
                    installed = ['System Apps (Android PM)']
                except Exception: pass
 
            self._all_apps = apks
            Clock.schedule_once(lambda dt: self._populate_apps(apks, installed), 0)
        threading.Thread(target=scan, daemon=True).start()
 
    def _populate_apps(self, apks, installed):
        self._stat_cards['total'].text = str(len(apks) + len(installed))
        self._stat_cards['apks'].text = str(len(apks))
        self._stat_cards['user'].text = str(len(installed))
        self._app_list.clear_widgets()
 
        if not apks and not installed:
            self._app_list.add_widget(Label(text='No APK files found in Downloads', color=c('text_dim'), font_size=sp(12), size_hint_y=None, height=dp(60)))
            demo_apps = [
                ('YouTube', '🎬', '120 MB', 'com.google.youtube'),
                ('WhatsApp', '💬', '60 MB', 'com.whatsapp'),
                ('Chrome', '🌐', '80 MB', 'com.google.chrome'),
                ('Camera', '📷', '20 MB', 'com.android.camera'),
                ('Settings', '⚙️', '5 MB', 'com.android.settings'),
                ('Gallery', '🖼️', '15 MB', 'com.android.gallery'),
                ('Music', '🎵', '30 MB', 'com.android.music'),
                ('Maps', '🗺️', '100 MB', 'com.google.maps'),
            ]
            for name, icon, size, pkg in demo_apps:
                self._add_app_row(name, icon, size, pkg, None)
            return
 
        for fp in apks:
            name = Path(fp).stem
            try: size = format_size(os.path.getsize(fp))
            except Exception: size = '--'
            self._add_app_row(name, '📦', size, fp, fp)
 
    def _add_app_row(self, name, icon, size, subtitle, filepath):
        row = BoxLayout(size_hint_y=None, height=dp(60), spacing=dp(10), padding=[dp(10), dp(6), dp(8), dp(6)])
        with row.canvas.before:
            Color(*c('bg_surface'))
            row._bg = RoundedRectangle(pos=row.pos, size=row.size, radius=[dp(8)])
        row.bind(pos=lambda w,_: setattr(w._bg,'pos',w.pos), size=lambda w,_: setattr(w._bg,'size',w.size))
 
        row.add_widget(Label(text=icon, font_size=sp(24), size_hint_x=None, width=dp(44)))
        info = BoxLayout(orientation='vertical')
        info.add_widget(Label(text=name[:25], color=c('text_pri'), font_size=sp(12), bold=True, halign='left', valign='middle'))
        info.add_widget(Label(text=str(subtitle)[:30], color=c('text_dim'), font_size=sp(9.5), halign='left', valign='middle'))
        row.add_widget(info)
 
        size_lbl = Label(text=size, color=c('text_sec'), font_size=sp(10), size_hint_x=None, width=dp(60), halign='center')
        row.add_widget(size_lbl)
 
        if filepath and filepath.endswith('.apk'):
            inst_btn = GlowButton(text='Install', btn_color=c('accent3'), size_hint_x=None, width=dp(64), font_size=sp(10))
            inst_btn.bind(on_release=lambda b, fp=filepath: self._install_apk(fp))
            row.add_widget(inst_btn)
        self._app_list.add_widget(row)
 
    def _filter_apps(self, inst, text): pass
    def _browse_apk(self): App.get_running_app().navigate('files')
 
    def _install_apk(self, filepath):
        content = BoxLayout(orientation='vertical', padding=dp(12), spacing=dp(10))
        content.add_widget(Label(text=f"Install\n{Path(filepath).name}?", color=c('text_pri'), font_size=sp(13), halign='center'))
        btns = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(8))
        popup = Popup(title='Install APK', content=content, size_hint=(0.8, None), height=dp(180))
 
        def install(_):
            popup.dismiss()
            if IS_ANDROID:
                try:
                    from jnius import autoclass
                    Intent = autoclass('android.content.Intent')
                    Uri = autoclass('android.net.Uri')
                    File = autoclass('java.io.File')
                    intent = Intent(Intent.ACTION_VIEW)
                    intent.setDataAndType(Uri.fromFile(File(filepath)), 'application/vnd.android.package-archive')
                    intent.setFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                    activity.startActivity(intent)
                except Exception: pass
 
        ok = GlowButton(text='Install', btn_color=c('accent3'))
        ok.bind(on_release=install)
        cn = GlowButton(text='Cancel', btn_color=c('danger'))
        cn.bind(on_release=popup.dismiss)
        btns.add_widget(cn)
        btns.add_widget(ok)
        content.add_widget(btns)
        popup.open()
 
 
# ─── MAIN APP ────────────────────────────────────────────────────────────────
 
class NexusDroidApp(App):
    title = 'NexusDroid'
 
    def build(self):
        Window.clearcolor = c('bg_dark')
        self._request_permissions()
 
        root = BoxLayout(orientation='vertical')
        self.sm = ScreenManager(transition=FadeTransition(duration=0.15))
 
        self._screens = {}
        screen_classes = [
            ('dashboard', DashboardScreen),
            ('files',     FileManagerScreen),
            ('media',     MediaScreen),
            ('apps',      AppManagerScreen),
        ]
        for name, cls in screen_classes:
            s = cls(name=name)
            self._screens[name] = s
            self.sm.add_widget(s)
 
        root.add_widget(self.sm)
        self.navbar = NavBar()
        root.add_widget(self.navbar)
        return root
 
    def navigate(self, target, **kwargs):
        if target in self._screens:
            self.sm.current = target
            self.navbar.set_active(target)
            if target == 'media' and kwargs:
                screen = self._screens['media']
                Clock.schedule_once(lambda dt: screen.load_file(kwargs.get('filepath', ''), kwargs.get('media_type', 'audio')), 0.1)
 
    def _request_permissions(self):
        if IS_ANDROID:
            try:
                request_permissions([
                    Permission.READ_EXTERNAL_STORAGE,
                    Permission.WRITE_EXTERNAL_STORAGE,
                    Permission.REQUEST_INSTALL_PACKAGES,
                ])
            except Exception: pass
 
    def on_pause(self): return True
    def on_resume(self): pass
 
 
if __name__ == '__main__':
    NexusDroidApp().run()