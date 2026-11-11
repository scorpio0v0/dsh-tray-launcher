# -*- coding: utf-8 -*-
"""DeepSeek Harness 托盘启动器

双击桌面快捷方式后：
  * 在后台静默启动 `pnpm dsh web`（不弹任何命令行黑框）
  * 常驻 Windows 系统托盘，提供打开界面 / 查看日志 / 重启 / 停止 / 开机自启 / 退出
  * 窗口关闭、最小化都不影响服务

所有功能均基于本机已安装的 Python 3.13 + PyQt5，无需额外依赖。
"""

import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from collections import deque

from PyQt5.QtCore import QObject, QThread, QTimer, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QFont, QIcon, QPainter, QPixmap
from PyQt5.QtWidgets import (
    QAction,
    QApplication,
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QSystemTrayIcon,
    QVBoxLayout,
)

# --------------------------------------------------------------------------
# 可调整的配置
# 优先级：环境变量 > tray.config.json > 自动探测 > 默认值
# --------------------------------------------------------------------------
APP_TITLE = "DeepSeek Harness"
HERE = os.path.dirname(os.path.abspath(__file__))
CREATE_NO_WINDOW = 0x08000000             # Windows: 不创建控制台窗口
LOG_LIMIT = 5000                          # 内存中保留的日志行数
LOCK_PORT = int(os.environ.get("DSH_TRAY_LOCK_PORT") or 50991)        # 单实例锁端口

CONFIG_FILE = os.path.join(HERE, "tray.config.json")


def _load_file_config() -> dict:
    """读取可选的 tray.config.json（同目录），损坏或缺失时返回空字典。"""
    try:
        with open(CONFIG_FILE, encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


_FILE_CFG = _load_file_config()


def _detect_dsh_dir() -> str:
    """在常见位置寻找 dsh 仓库（以 package.json + apps/cli 作为特征）。"""
    candidates = [
        r"D:\deepseek-harness",
        os.path.join(os.path.expanduser("~"), "deepseek-harness"),
        os.path.join(os.path.expanduser("~"), "Documents", "deepseek-harness"),
        os.path.join(os.path.expanduser("~"), "code", "deepseek-harness"),
        os.path.join(os.path.expanduser("~"), "projects", "deepseek-harness"),
    ]
    for path in candidates:
        if os.path.isfile(os.path.join(path, "package.json")) and os.path.isdir(os.path.join(path, "apps", "cli")):
            return path
    return candidates[0]  # 兜底：交给启动时的存在性检查去报错


def _cfg(key: str, env_name: str, default):
    """配置优先级：环境变量 > tray.config.json > 默认值。"""
    env = os.environ.get(env_name)
    if env:
        return env
    value = _FILE_CFG.get(key)
    if value not in (None, ""):
        return value
    return default


DSH_DIR = str(_cfg("dsh_dir", "DSH_TRAY_DIR", _detect_dsh_dir()))     # dsh 仓库根目录
PORT = int(_cfg("port", "DSH_TRAY_PORT", 3080))                        # dsh web 监听端口
URL = f"http://127.0.0.1:{PORT}"
# Edge 远程调试端口：Edge 必须以 --remote-debugging-port=<此端口> 启动
CDP_PORT = int(_cfg("cdp_port", "DSH_TRAY_CDP_PORT", 9222))
# 仅用于自检：把启动命令替换成任意可执行命令
TEST_CMD = os.environ.get("DSH_TRAY_TEST_CMD")
# 就绪后不自动打开浏览器（自检/静默场景）
NO_AUTO_OPEN = os.environ.get("DSH_TRAY_NO_AUTO_OPEN") == "1"
# 完全禁止打开浏览器（自检用）
NO_BROWSER = os.environ.get("DSH_TRAY_NO_BROWSER") == "1"


def _default_edge_profile() -> str:
    """托盘托管 Edge 时使用的独立配置目录。

    Edge 有一个硬限制：同一个 user-data-dir 只能有一个主实例，且远程调试端口
    只对「第一个带该参数启动的进程」生效。若默认配置已被日常 Edge 占用，再用
    默认配置加调试参数启动，参数会被现有实例吞掉、端口根本不会打开。
    因此这里固定使用独立配置目录，保证调试端口一定由托盘拉起。
    """
    override = _cfg("edge_profile", "DSH_TRAY_EDGE_PROFILE", "")
    if override:
        return str(override)
    return os.path.join(HERE, "edge-profile")


EDGE_PROFILE_DIR = _default_edge_profile()

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
AUTOSTART_NAME = "DeepSeekHarnessTray"

# dsh web 启动时会打印带一次性令牌的完整地址，例如：
#   dsh web: http://127.0.0.1:3080/?token=xxxx
READY_URL_RE = re.compile(r"dsh web:\s+(\S+)")


# --------------------------------------------------------------------------
# 工具函数
# --------------------------------------------------------------------------
def resolve_pnpm() -> str:
    """找到可用的 pnpm 可执行文件。"""
    candidates = [
        os.path.join(DSH_DIR, "node_modules", ".bin", "pnpm.CMD"),
        os.path.join(DSH_DIR, "node_modules", ".bin", "pnpm.cmd"),
        os.path.join(os.environ.get("APPDATA", ""), "npm", "pnpm.cmd"),
    ]
    for path in candidates:
        if path and os.path.isfile(path):
            return path
    # 退回到 PATH 查找
    for name in ("pnpm.cmd", "pnpm"):
        for root in os.environ.get("PATH", "").split(os.pathsep):
            if not root:
                continue
            candidate = os.path.join(root, name)
            if os.path.isfile(candidate):
                return candidate
    return "pnpm.cmd"


def find_icon() -> QIcon:
    """优先使用本目录的自有图标，其次本机 dsh 仓库里的图标，最后程序现画一个。"""
    for path in (
        os.path.join(HERE, "icon.ico"),
        os.path.join(HERE, "tray.ico"),
        os.path.join(DSH_DIR, "apps", "desktop", "resources", "tray-windows.ico"),
        os.path.join(DSH_DIR, "apps", "desktop", "resources", "icon-windows.png"),
    ):
        if os.path.isfile(path):
            icon = QIcon(path)
            if not icon.isNull():
                return icon
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setBrush(QColor("#1f6feb"))
    painter.setPen(Qt.NoPen)
    painter.drawEllipse(4, 4, 56, 56)
    painter.setPen(QColor("#ffffff"))
    font = QFont("Segoe UI", 28, QFont.Bold)
    painter.setFont(font)
    painter.drawText(pixmap.rect(), Qt.AlignCenter, "D")
    painter.end()
    return QIcon(pixmap)


def port_open(port: int, timeout: float = 0.35) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(timeout)
        return probe.connect_ex(("127.0.0.1", port)) == 0


def port_owner_pid(port: int):
    """返回正在监听该端口的进程 PID（取不到则返回 None）。"""
    try:
        import psutil
    except ImportError:
        return None
    try:
        for conn in psutil.net_connections(kind="tcp"):
            if conn.status == psutil.CONN_LISTEN and conn.laddr and conn.laddr.port == port:
                return conn.pid
    except Exception:  # noqa: BLE001 - 权限不足等情况直接放弃
        return None
    return None


def kill_process_tree(pid: int) -> bool:
    try:
        result = subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(pid)],
            creationflags=CREATE_NO_WINDOW,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        return result.returncode == 0
    except Exception:  # noqa: BLE001
        return False


# --------------------------------------------------------------------------
# Edge CDP：复用已有标签页，避免反复点击新建多个标签页
# --------------------------------------------------------------------------
def _cdp_http(path: str, cdp_port: int = CDP_PORT, timeout: float = 1.0, method: str = "GET"):
    """向 Edge CDP 发 HTTP 请求，返回 (状态码, 响应文本)；失败返回 (None, "")。

    注意：/json/activate 成功时返回的是纯文本 "Target activated" 而不是 JSON，
    因此这里只负责取状态码和原文，JSON 解析交给调用方。
    """
    url = f"http://127.0.0.1:{cdp_port}{path}"
    try:
        req = urllib.request.Request(url, method=method)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return resp.status, body
    except urllib.error.HTTPError as exc:  # 有状态码但非 2xx
        try:
            body = exc.read().decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            body = ""
        return exc.code, body
    except Exception:  # noqa: BLE001 - Edge 未开 CDP/未运行/网络问题均兜底
        return None, ""


def cdp_available(cdp_port: int = CDP_PORT, timeout: float = 0.6) -> bool:
    """CDP 端口是否可访问。"""
    status, _ = _cdp_http("/json/version", cdp_port, timeout)
    return status is not None and 200 <= status < 300


def cdp_list_tabs(cdp_port: int = CDP_PORT, timeout: float = 1.0) -> list:
    """GET http://127.0.0.1:9222/json/list，返回标签页列表；失败返回空列表。"""
    status, body = _cdp_http("/json/list", cdp_port, timeout)
    if status is None or not (200 <= status < 300):
        return []
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, TypeError):
        return []
    return data if isinstance(data, list) else []


def cdp_activate_tab(target_id: str, cdp_port: int = CDP_PORT, timeout: float = 1.5) -> bool:
    """GET http://127.0.0.1:9222/json/activate/<targetId>，把标签页拉到前台。

    成功响应是纯文本 "Target activated"（HTTP 200），不是 JSON。
    """
    status, _ = _cdp_http(f"/json/activate/{target_id}", cdp_port, timeout)
    return status is not None and 200 <= status < 300


def cdp_new_tab(url: str, cdp_port: int = CDP_PORT, timeout: float = 4.0) -> bool:
    """在开启了调试端口的 Edge 实例里新建标签页（PUT /json/new?<url>）。"""
    encoded = urllib.parse.quote(url, safe=":/?&=%#")
    for method in ("PUT", "GET"):
        status, _ = _cdp_http(f"/json/new?{encoded}", cdp_port, timeout, method)
        if status is not None and 200 <= status < 300:
            return True
    return False


def _url_matches_dsh(tab_url: str, port: int) -> bool:
    """标签页地址是否指向本机 DSH 服务（兼容 127.0.0.1 与 localhost）。"""
    if not tab_url:
        return False
    return f"127.0.0.1:{port}" in tab_url or f"localhost:{port}" in tab_url


def find_edge_executable() -> str | None:
    """定位 msedge.exe。"""
    candidates = [
        os.path.join(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
                     r"Microsoft\Edge\Application\msedge.exe"),
        os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"),
                     r"Microsoft\Edge\Application\msedge.exe"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""),
                     r"Microsoft\Edge\Application\msedge.exe"),
    ]
    for path in candidates:
        if path and os.path.isfile(path):
            return path
    try:
        import winreg

        for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            for sub in (
                r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\msedge.exe",
                r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths\msedge.exe",
            ):
                try:
                    with winreg.OpenKey(root, sub) as key:
                        value, _ = winreg.QueryValueEx(key, "")
                        if value and os.path.isfile(value):
                            return value
                except OSError:
                    continue
    except OSError:
        pass
    return None


def launch_cdp_edge(url: str = "") -> bool:
    """用独立的配置目录启动一个带远程调试端口的 Edge 实例。

    只有这个实例才能被 CDP 管理，从而做到「多次点击只复用一个标签页」。
    """
    exe = find_edge_executable()
    if not exe:
        return False
    try:
        os.makedirs(EDGE_PROFILE_DIR, exist_ok=True)
    except OSError:
        return False
    args = [
        exe,
        f"--remote-debugging-port={CDP_PORT}",
        f"--user-data-dir={EDGE_PROFILE_DIR}",
        "--ignore-certificate-errors",
        "--no-first-run",
        "--no-default-browser-check",
    ]
    if url:
        args.append(url)
    try:
        subprocess.Popen(args)
        return True
    except OSError:
        return False


def find_existing_dsh_tab(port: int, cdp_port: int = CDP_PORT, timeout: float = 1.0) -> str | None:
    """在 Edge CDP 标签页中查找 DSH 页面，返回目标 id；找不到返回 None。"""
    tabs = cdp_list_tabs(cdp_port, timeout)
    if not tabs:
        return None
    # 先找带 token 的完整匹配，再找裸地址匹配
    for tab in tabs:
        if tab.get("type") != "page":
            continue
        url = tab.get("url") or ""
        if _url_matches_dsh(url, port) and "?" in url:
            return tab.get("id")
    for tab in tabs:
        if tab.get("type") != "page":
            continue
        url = tab.get("url") or ""
        if _url_matches_dsh(url, port):
            return tab.get("id")
    return None


CRASH_LOG = os.path.join(HERE, "tray-error.log")
LOG_FILE = os.path.join(HERE, "tray.log")


def install_crash_log():
    """pythonw 没有控制台，把未捕获异常写到文件里，便于排查。"""
    import traceback

    def hook(exc_type, exc_value, exc_tb):
        try:
            with open(CRASH_LOG, "a", encoding="utf-8") as handle:
                handle.write(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] 未捕获异常\n")
                handle.write("".join(traceback.format_exception(exc_type, exc_value, exc_tb)))
        except OSError:
            pass

    sys.excepthook = hook


def pythonw_path() -> str:
    """返回 pythonw.exe 路径（无控制台窗口的解释器）。"""
    exe = sys.executable or ""
    if exe:
        candidate = os.path.join(os.path.dirname(exe), "pythonw.exe")
        if os.path.isfile(candidate):
            return candidate
    for candidate in (r"D:\Python\Python313\pythonw.exe", "pythonw.exe"):
        if os.path.isfile(candidate):
            return candidate
    return "pythonw.exe"


def autostart_command() -> str:
    return f'"{pythonw_path()}" "{os.path.join(HERE, "dsh_tray.pyw")}"'


def is_autostart_enabled() -> bool:
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            value, _ = winreg.QueryValueEx(key, AUTOSTART_NAME)
            return bool(value)
    except FileNotFoundError:
        return False
    except OSError:
        return False


def set_autostart(enabled: bool) -> bool:
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            if enabled:
                winreg.SetValueEx(key, AUTOSTART_NAME, 0, winreg.REG_SZ, autostart_command())
            else:
                try:
                    winreg.DeleteValue(key, AUTOSTART_NAME)
                except FileNotFoundError:
                    pass
        return True
    except OSError:
        return False


# --------------------------------------------------------------------------
# 后台服务进程
# --------------------------------------------------------------------------
class ServiceRunner(QThread):
    """在独立线程中托管 `pnpm dsh web`，逐行收集输出。"""

    line = pyqtSignal(str)
    stopped = pyqtSignal()

    def __init__(self, directory: str, pnpm: str, port: int):
        super().__init__()
        self.directory = directory
        self.pnpm = pnpm
        if TEST_CMD:
            import shlex

            self.command = shlex.split(TEST_CMD, posix=False)
        else:
            self.command = [pnpm, "dsh", "web", "--no-open", "--port", str(port)]
        self.proc = None

    def run(self):
        try:
            env = dict(os.environ)
            env["NO_COLOR"] = "1"
            env["FORCE_COLOR"] = "0"
            env["PYTHONIOENCODING"] = "utf-8"
            self.line.emit("[托盘] 启动命令: " + " ".join(self.command))
            self.line.emit("[托盘] 工作目录: " + self.directory)
            self.proc = subprocess.Popen(
                self.command,
                cwd=self.directory,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=CREATE_NO_WINDOW,
                env=env,
            )
            for raw in self.proc.stdout:
                self.line.emit(raw.rstrip("\r\n"))
            self.proc.wait()
            self.line.emit(f"[托盘] 服务进程已退出，返回码 {self.proc.returncode}")
        except Exception as exc:  # noqa: BLE001 - 兜底提示，避免托盘静默失败
            self.line.emit(f"[托盘] 启动失败: {exc}")
        finally:
            self.proc = None
            self.stopped.emit()

    def kill(self):
        proc = self.proc
        if proc is not None and proc.poll() is None:
            try:
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                    creationflags=CREATE_NO_WINDOW,
                    capture_output=True,
                )
            except Exception:  # noqa: BLE001
                pass


# --------------------------------------------------------------------------
# 日志窗口
# --------------------------------------------------------------------------
class LogWindow(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"{APP_TITLE} - 运行日志")
        self.resize(860, 520)
        self.setWindowFlag(Qt.WindowMinMaxButtonsHint, True)

        layout = QVBoxLayout(self)

        self.view = QPlainTextEdit(self)
        self.view.setReadOnly(True)
        self.view.setMaximumBlockCount(LOG_LIMIT)
        self.view.setStyleSheet(
            "QPlainTextEdit{background:#1e1e1e;color:#d4d4d4;font-family:'Cascadia Mono','Consolas',monospace;font-size:12px;}"
        )
        layout.addWidget(self.view)

        row = QHBoxLayout()
        self.autoscroll = QCheckBox("自动滚动到最新", self)
        self.autoscroll.setChecked(True)
        row.addWidget(self.autoscroll)
        row.addStretch(1)

        clear_btn = QPushButton("清空", self)
        clear_btn.clicked.connect(self.view.clear)
        row.addWidget(clear_btn)

        copy_btn = QPushButton("复制全部", self)
        copy_btn.clicked.connect(self.copy_all)
        row.addWidget(copy_btn)

        close_btn = QPushButton("关闭", self)
        close_btn.clicked.connect(self.hide)
        row.addWidget(close_btn)

        layout.addLayout(row)
        self.hint = QLabel(f"访问地址: {URL}    （关闭本窗口不影响后台服务）", self)
        self.hint.setStyleSheet("color:#666;")
        layout.addWidget(self.hint)

    def append(self, text: str):
        self.view.appendPlainText(text)
        if self.autoscroll.isChecked():
            bar = self.view.verticalScrollBar()
            bar.setValue(bar.maximum())

    def copy_all(self):
        QApplication.clipboard().setText(self.view.toPlainText())

    def closeEvent(self, event):  # noqa: N802 - Qt 回调
        event.ignore()
        self.hide()


# --------------------------------------------------------------------------
# 托盘主程序
# --------------------------------------------------------------------------
class TrayApp(QObject):
    open_requested = pyqtSignal()
    quit_requested = pyqtSignal()

    def __init__(self, app: QApplication, lock_socket: socket.socket):
        super().__init__()
        self.app = app
        self.lock_socket = lock_socket
        self.logs = deque(maxlen=LOG_LIMIT)
        self.runner = None
        self.port_ready = False
        self.ready_url = None
        self.auto_open_pending = not NO_AUTO_OPEN
        self.log_window = None
        # 托盘托管 Edge（带调试端口）启动中状态，防止连点重复拉起
        self._edge_pending = False
        self._edge_tries = 0
        self._edge_target = ""
        self._edge_timer = QTimer(self)
        self._edge_timer.setInterval(500)
        self._edge_timer.timeout.connect(self._edge_start_tick)

        self.tray = QSystemTrayIcon(find_icon(), self)
        self.tray.setToolTip(f"{APP_TITLE} - 正在启动…")
        self.tray.activated.connect(self.on_tray_activated)

        self.menu = QMenu()
        self.build_menu()
        self.tray.setContextMenu(self.menu)
        self.tray.show()

        self.health_timer = QTimer(self)
        self.health_timer.setInterval(2000)
        self.health_timer.timeout.connect(self.check_health)
        self.health_timer.start()

        self.open_requested.connect(self.open_web)
        self.quit_requested.connect(self.quit_app)
        threading.Thread(target=self.accept_secondary_instance, daemon=True).start()

        self.boot()

    # ---------------- 菜单 ----------------
    def build_menu(self):
        self.status_action = QAction(f"{APP_TITLE}：正在启动…", self)
        self.status_action.setEnabled(False)
        self.menu.addAction(self.status_action)
        self.menu.addSeparator()

        self.open_action = QAction("打开 Web 界面", self)
        self.open_action.triggered.connect(self.open_web)
        self.menu.addAction(self.open_action)

        self.copy_action = QAction("复制访问地址", self)
        self.copy_action.triggered.connect(self.copy_url)
        self.menu.addAction(self.copy_action)

        self.menu.addSeparator()

        self.log_action = QAction("查看运行日志…", self)
        self.log_action.triggered.connect(self.show_logs)
        self.menu.addAction(self.log_action)

        self.restart_action = QAction("重启服务", self)
        self.restart_action.triggered.connect(self.restart_service)
        self.menu.addAction(self.restart_action)

        self.takeover_action = QAction("结束占用端口的旧实例并重新启动", self)
        self.takeover_action.triggered.connect(self.takeover_restart)
        self.takeover_action.setVisible(False)
        self.menu.addAction(self.takeover_action)

        self.start_action = QAction("启动服务", self)
        self.start_action.triggered.connect(self.start_service)
        self.start_action.setEnabled(False)
        self.menu.addAction(self.start_action)

        self.stop_action = QAction("停止服务", self)
        self.stop_action.triggered.connect(self.stop_service)
        self.menu.addAction(self.stop_action)

        self.menu.addSeparator()

        self.folder_action = QAction("打开项目文件夹", self)
        self.folder_action.triggered.connect(self.open_folder)
        self.menu.addAction(self.folder_action)

        self.autostart_action = QAction("开机自启动", self)
        self.autostart_action.setCheckable(True)
        self.autostart_action.setChecked(is_autostart_enabled())
        self.autostart_action.triggered.connect(self.toggle_autostart)
        self.menu.addAction(self.autostart_action)

        self.menu.addSeparator()

        self.exit_action = QAction("退出（并停止服务）", self)
        self.exit_action.triggered.connect(self.quit_app)
        self.menu.addAction(self.exit_action)

    # ---------------- 单实例 ----------------
    def accept_secondary_instance(self):
        while True:
            try:
                conn, _ = self.lock_socket.accept()
            except OSError:
                return
            payload = b""
            try:
                conn.settimeout(0.5)
                payload = conn.recv(16) or b""
            except OSError:
                pass
            finally:
                conn.close()
            if payload.strip().startswith(b"quit"):
                self.quit_requested.emit()
                return
            self.open_requested.emit()

    # ---------------- 服务生命周期 ----------------
    def boot(self):
        """启动时判断：端口已占用则托管既有实例，否则自己拉起服务。"""
        if port_open(PORT):
            self.port_ready = True
            self.auto_open_pending = False
            self.set_status(f"运行中（已存在实例，端口 {PORT}）", ready=True)
            self.start_action.setEnabled(False)
            self.stop_action.setEnabled(False)
            self.log(f"[托盘] 检测到 {URL} 已在运行，直接接管显示，不重复启动。")
            self.log("[托盘] 该实例不是由托盘启动的，因此“停止服务”对它无效；")
            self.log("[托盘] 如需由托盘接管，请用右键菜单“结束占用端口的旧实例并重新启动”。")
            self.takeover_action.setVisible(True)
            # 已有实例时没有令牌，尝试从日志无果，仍用裸地址（浏览器若已有 Cookie 可直接进入）
            self.ready_url = self.ready_url or URL
        else:
            self.start_service()

    def start_service(self):
        if self.runner is not None and self.runner.isRunning():
            return
        if port_open(PORT):
            self.port_ready = True
            self.set_status(f"运行中（端口 {PORT} 已被占用）", ready=True)
            self.tray.showMessage(
                APP_TITLE,
                f"端口 {PORT} 已被其他进程占用，未重复启动。",
                QSystemTrayIcon.Warning,
                3500,
            )
            return
        self.port_ready = False
        self.set_status("正在启动…", ready=False)
        self.start_action.setEnabled(False)
        self.stop_action.setEnabled(True)
        self.takeover_action.setVisible(False)
        self.log("")
        self.log("=" * 60)
        self.log(f"[托盘] {time.strftime('%Y-%m-%d %H:%M:%S')} 正在启动 dsh web")
        self.runner = ServiceRunner(DSH_DIR, resolve_pnpm(), PORT)
        self.runner.line.connect(self.on_line)
        self.runner.stopped.connect(self.on_stopped)
        self.runner.start()

    def stop_service(self, notify=True):
        if self.runner is not None and self.runner.isRunning():
            self.log("[托盘] 正在停止服务…")
            self.runner.kill()
        self.port_ready = False
        self.set_status("已停止", ready=False)
        self.start_action.setEnabled(True)
        self.stop_action.setEnabled(False)
        if notify:
            self.tray.showMessage(APP_TITLE, "后台服务已停止。", QSystemTrayIcon.Information, 2500)

    def restart_service(self):
        self.log("[托盘] 重启服务…")
        self.stop_service(notify=False)
        self.auto_open_pending = False
        QTimer.singleShot(900, self.start_service)

    def takeover_restart(self):
        """结束不是由托盘启动、但占着端口的旧实例，然后由托盘接管启动。"""
        pid = port_owner_pid(PORT)
        if pid is None:
            self.tray.showMessage(APP_TITLE, "找不到占用端口的进程，请改用“查看运行日志”排查。", QSystemTrayIcon.Warning, 3500)
            return
        from PyQt5.QtWidgets import QMessageBox

        answer = QMessageBox.question(
            None,
            APP_TITLE,
            f"将强制结束占用端口 {PORT} 的进程（PID {pid}）并重新启动服务。\n"
            "该进程里正在运行的任务会被中断，确定继续吗？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        self.log(f"[托盘] 结束占用端口的旧实例 PID={pid}")
        if not kill_process_tree(pid):
            self.tray.showMessage(APP_TITLE, "结束旧实例失败（可能权限不足）。", QSystemTrayIcon.Warning, 3500)
            return
        self.port_ready = False
        self.takeover_action.setVisible(False)
        QTimer.singleShot(1500, self.start_service)

    def on_stopped(self):
        self.port_ready = False
        self.set_status("已停止", ready=False)
        self.start_action.setEnabled(True)
        self.stop_action.setEnabled(False)

    def on_line(self, text: str):
        self.log(text)
        match = READY_URL_RE.search(text)
        if match:
            self.ready_url = match.group(1)
        if "dsh web: http" in text and not self.port_ready:
            self.port_ready = True
            self.set_status("运行中", ready=True)
            self.start_action.setEnabled(False)
            self.stop_action.setEnabled(True)
            self.tray.showMessage(APP_TITLE, f"服务已就绪：{URL}", QSystemTrayIcon.Information, 3000)
            if self.auto_open_pending:
                self.auto_open_pending = False
                QTimer.singleShot(300, self.open_web)

    def check_health(self):
        alive = port_open(PORT)
        if alive and not self.port_ready:
            self.port_ready = True
            self.set_status("运行中", ready=True)
        elif not alive and self.port_ready:
            self.port_ready = False
            self.set_status("正在启动…" if (self.runner and self.runner.isRunning()) else "已停止", ready=False)

    # ---------------- 状态与日志 ----------------
    def set_status(self, text: str, ready: bool):
        dot = "●" if ready else "○"
        self.status_action.setText(f"{APP_TITLE}：{dot} {text}")
        self.tray.setToolTip(f"{APP_TITLE} - {text}\n{URL}")

    def log(self, text: str):
        self.logs.append(text)
        self._write_log_line(text)
        if self.log_window is not None and self.log_window.isVisible():
            self.log_window.append(text)

    @staticmethod
    def _write_log_line(text: str):
        # 日志超过 2 MB 就先滚动一份旧文件，避免无限增长
        try:
            if os.path.isfile(LOG_FILE) and os.path.getsize(LOG_FILE) > 2 * 1024 * 1024:
                backup = LOG_FILE + ".1"
                try:
                    os.replace(LOG_FILE, backup)
                except OSError:
                    open(LOG_FILE, "w", encoding="utf-8").close()
        except OSError:
            pass
        try:
            with open(LOG_FILE, "a", encoding="utf-8") as handle:
                handle.write(text + "\n")
        except OSError:
            pass

    def show_logs(self):
        if self.log_window is None:
            self.log_window = LogWindow()
            self.log_window.view.setPlainText("\n".join(self.logs))
        self.log_window.show()
        self.log_window.raise_()
        self.log_window.activateWindow()

    # ---------------- 动作 ----------------
    def on_tray_activated(self, reason):
        if reason in (QSystemTrayIcon.DoubleClick, QSystemTrayIcon.Trigger):
            self.open_web()

    def open_web(self):
        # dsh web 的首次访问需要启动日志里带的一次性令牌，否则浏览器会得到 401。
        target = self.ready_url or URL
        if NO_BROWSER:
            self.log(f"[自检] 已请求打开 {target}（浏览器被禁用）")
            return
        if not self.port_ready:
            self.tray.showMessage(APP_TITLE, "服务尚未就绪，请稍候…", QSystemTrayIcon.Information, 2000)

        # 关键：CDP 只能管理「以 --remote-debugging-port 启动的那个 Edge 实例」。
        # webbrowser.open 永远把 URL 交给默认 Edge 实例（不带调试端口），两者不是同一进程，
        # 所以只要走 webbrowser.open，下一次点击必然又查不到 CDP，从而无限新建标签页。
        # 因此这里绝不把它当作正常路径：
        #   1) CDP 已就绪  -> 有 DSH 标签页就激活，没有就在该实例里新建；
        #   2) CDP 未就绪  -> 由托盘用调试端口拉起 Edge，等它就绪后回到 1)；
        #   3) 连 Edge 都找不到 -> 才退回 webbrowser.open。
        if cdp_available():
            self._ensure_dsh_tab(target)
            return

        if self._edge_pending:
            self.log("[托盘] 托管 Edge 正在启动，请稍候…")
            return

        if not find_edge_executable():
            self.log("[托盘] 找不到 msedge.exe，回退默认浏览器（无法复用标签页）。")
            webbrowser.open(target)
            return

        # 带 URL 启动：窗口一出现就直接落到 DSH 页面；随后轮询等 CDP 就绪再激活复用。
        if launch_cdp_edge(target):
            self._edge_pending = True
            self._edge_tries = 0
            self._edge_target = target
            self._edge_timer.start()
            self.log(f"[托盘] 正在启动带调试端口 {CDP_PORT} 的 Edge 实例（配置：{EDGE_PROFILE_DIR}）")
            self.tray.showMessage(
                APP_TITLE,
                "正在启动用于复用标签页的 Edge 实例…",
                QSystemTrayIcon.Information,
                2500,
            )
            return

        self.log("[托盘] 启动 Edge 失败，回退默认浏览器。")
        webbrowser.open(target)

    def _edge_start_tick(self):
        """等待托管 Edge 的调试端口就绪，然后复用/新建标签页。"""
        if not self._edge_pending:
            self._edge_timer.stop()
            return
        self._edge_tries += 1
        if cdp_available():
            self._edge_timer.stop()
            self._edge_pending = False
            self.log("[托盘] 托管 Edge 调试端口已就绪。")
            self._ensure_dsh_tab(self._edge_target or URL)
            return
        # 约 30 秒（500ms × 60）仍没起来就放弃，避免无限等待
        if self._edge_tries >= 60:
            self._edge_timer.stop()
            self._edge_pending = False
            self.log("[托盘] 等待 Edge 调试端口超时，回退默认浏览器。")
            webbrowser.open(self._edge_target or URL)

    def _ensure_dsh_tab(self, target: str):
        """在 CDP 实例内复用或新建 DSH 标签页。"""
        existing_id = find_existing_dsh_tab(PORT)
        if existing_id:
            if cdp_activate_tab(existing_id):
                self.log(f"[托盘] 已激活现有 Edge 标签页：{target}")
                return
            self.log("[托盘] 找到 DSH 标签页但激活失败，改为新建。")
        if cdp_new_tab(target):
            self.log(f"[托盘] 已在 Edge 调试实例中新建标签页：{target}")
            return
        self.log("[托盘] CDP 新建标签页失败，回退默认浏览器。")
        webbrowser.open(target)

    def copy_url(self):
        target = self.ready_url or URL
        QApplication.clipboard().setText(target)
        self.tray.showMessage(APP_TITLE, "访问地址已复制到剪贴板。", QSystemTrayIcon.Information, 2000)

    def open_folder(self):
        try:
            os.startfile(DSH_DIR)  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            subprocess.Popen(["explorer", DSH_DIR], creationflags=CREATE_NO_WINDOW)

    def toggle_autostart(self):
        want = self.autostart_action.isChecked()
        if set_autostart(want):
            self.tray.showMessage(
                APP_TITLE,
                "已开启开机自启动。" if want else "已关闭开机自启动。",
                QSystemTrayIcon.Information,
                2500,
            )
        else:
            self.autostart_action.setChecked(not want)
            self.tray.showMessage(APP_TITLE, "写入注册表失败，未能修改开机自启动。", QSystemTrayIcon.Warning, 3000)

    def quit_app(self):
        self.health_timer.stop()
        self.stop_service(notify=False)
        try:
            self.lock_socket.close()
        except OSError:
            pass
        self.tray.hide()
        self.app.quit()


def main():
    install_crash_log()

    # 命令行控制：--open 打开界面，--quit 退出已在运行的托盘
    args = [a.lower() for a in sys.argv[1:]]
    want_quit = "--quit" in args
    want_open = "--open" in args and not want_quit

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    if not QSystemTrayIcon.isSystemTrayAvailable():
        from PyQt5.QtWidgets import QMessageBox

        QMessageBox.critical(None, APP_TITLE, "当前系统不支持系统托盘，无法启动托盘管理器。")
        return 1

    # 单实例：占住回环端口，失败说明已有实例在跑，把请求转发给它后退出
    lock_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        lock_socket.bind(("127.0.0.1", LOCK_PORT))
        lock_socket.listen(5)
    except OSError:
        try:
            with socket.create_connection(("127.0.0.1", LOCK_PORT), timeout=1.0) as client:
                client.sendall(b"quit" if want_quit else b"open")
        except OSError:
            pass
        return 0

    if want_quit:
        # 没有已运行的实例
        return 0

    if not os.path.isdir(DSH_DIR):
        from PyQt5.QtWidgets import QMessageBox

        QMessageBox.critical(None, APP_TITLE, f"找不到 dsh 仓库目录：\n{DSH_DIR}")
        return 1

    tray_app = TrayApp(app, lock_socket)
    if want_open:
        QTimer.singleShot(1200, tray_app.open_web)
    code = app.exec_()
    tray_app.stop_service(notify=False)
    return code


if __name__ == "__main__":
    sys.exit(main())
