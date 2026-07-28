"""Windows 单机版启动器(PyInstaller 入口)。

行为:
- 首次运行在 %LOCALAPPDATA%\\LLMGateway 下建数据目录,生成 admin token 与 Fernet 密钥
- 起 uvicorn(SQLite,零外部依赖)
- 用 pywebview 开一个原生应用窗口(Windows 走 WebView2,与 Tauri 同一套渲染),
  有自己的标题栏和图标,没有地址栏/标签页——和普通桌面软件无异
- 窗口不可用时(缺 pywebview/WebView2)回退到系统浏览器 + 托盘图标
"""
import os
import secrets
import sys
import threading
import webbrowser
from pathlib import Path

APP_NAME = "Ying"
LEGACY_APP_NAME = "LLMGateway"  # 改名前的数据目录,继续沿用避免用户配置丢失
WINDOW_TITLE = "Ying"
DEFAULT_PORT = 8080
MUTEX_NAME = r"Local\Ying-8F3C1A62-9E4D-4B7A-9C21-5D0E7A1B2C33"
INSTANCE_MUTEX: int | None = None
ERROR_ALREADY_EXISTS = 183


def data_dir() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA") or str(Path.home()))
    legacy = base / LEGACY_APP_NAME
    path = base / APP_NAME
    # 老版本装过就继续用老目录,不搬家、不丢配置
    if legacy.exists() and not path.exists():
        return legacy
    path.mkdir(parents=True, exist_ok=True)
    return path


def bundle_dir() -> Path:
    """PyInstaller 解包目录(打包后)或仓库根目录(源码运行)。"""
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS)  # type: ignore[attr-defined]
    return Path(__file__).resolve().parent.parent


def redirect_std_streams() -> Path | None:
    """窗口模式(console=False)下 sys.stdout/stderr 为 None,任何 print/logging 都会崩。

    重定向到数据目录的日志文件——顺带给单机版一份可排障的运行日志。
    必须在 import uvicorn / app.main(会 logging.basicConfig)之前调用。
    """
    if sys.stdout is not None and sys.stderr is not None:
        return None
    log_path = data_dir() / "gateway.log"
    stream = open(log_path, "a", encoding="utf-8", buffering=1)
    sys.stdout = stream
    sys.stderr = stream
    return log_path


def acquire_single_instance(name: str = MUTEX_NAME) -> int | None:
    """Return a Windows mutex handle, or None when another Ying already owns it."""
    if sys.platform != "win32":
        return -1
    import ctypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_mutex = kernel32.CreateMutexW
    create_mutex.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
    create_mutex.restype = ctypes.c_void_p
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [ctypes.c_void_p]
    close_handle.restype = ctypes.c_bool
    handle = create_mutex(None, False, name)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        close_handle(handle)
        return None
    return int(handle)


def release_single_instance(handle: int | None) -> None:
    if sys.platform == "win32" and handle and handle != -1:
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel32.CloseHandle.restype = ctypes.c_bool
        kernel32.CloseHandle(handle)


def show_message(message: str, title: str = APP_NAME, flags: int = 0x10) -> int:
    try:
        import ctypes

        return int(ctypes.windll.user32.MessageBoxW(0, message, title, flags))
    except Exception:
        print(message)
        return 0


def pick_port(preferred: int) -> int:
    """选一个能用的端口:首选被占就顺延,避免"双击没反应"这种最难排查的失败。"""
    import socket

    for candidate in [preferred] + [preferred + i for i in range(1, 40)]:
        with socket.socket() as sock:
            try:
                sock.bind(("127.0.0.1", candidate))
                return candidate
            except OSError:
                continue
    return 0  # 交给系统随机分配


def ensure_config() -> dict:
    """准备数据目录内的密钥与 token,并写入环境变量。"""
    from cryptography.fernet import Fernet

    root = data_dir()
    token_file = root / "admin_token.txt"
    secret_file = root / "secret.key"

    if not token_file.exists():
        token_file.write_text(secrets.token_urlsafe(24), encoding="utf-8")
    if not secret_file.exists():
        secret_file.write_bytes(Fernet.generate_key())

    admin_token = token_file.read_text(encoding="utf-8").strip()
    secret_key = secret_file.read_bytes().decode().strip()
    db_path = (root / "gateway.db").as_posix()

    port = pick_port(int(os.environ.get("GW_PORT", DEFAULT_PORT)))

    os.environ.setdefault("GW_ADMIN_TOKEN", admin_token)
    os.environ.setdefault("GW_SECRET_KEY", secret_key)
    os.environ.setdefault("GW_DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")
    os.environ.setdefault("GW_AUTO_CREATE_TABLES", "true")
    # 单机形态:控制台静态资源在解包目录里
    os.environ.setdefault("GW_CONSOLE_DIR", str(bundle_dir() / "console" / "dist"))
    # 端口写进环境,控制台里展示给用户抄配置用
    os.environ["GW_PUBLIC_PORT"] = str(port)

    return {"token": admin_token, "port": port, "root": root, "url": f"http://127.0.0.1:{port}/console/"}


def webview2_available() -> bool:
    """检测 WebView2 Runtime(Win11 与新版 Win10 自带;老机器可能没有)。"""
    if sys.platform != "win32":
        return True
    import winreg

    keys = [
        (winreg.HKEY_LOCAL_MACHINE,
         r"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"),
        (winreg.HKEY_LOCAL_MACHINE,
         r"SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"),
        (winreg.HKEY_CURRENT_USER,
         r"SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"),
    ]
    for root_key, path in keys:
        try:
            with winreg.OpenKey(root_key, path) as key:
                version, _ = winreg.QueryValueEx(key, "pv")
                if version and version != "0.0.0.0":
                    return True
        except OSError:
            continue
    return False


def prompt_install_webview2(url: str) -> None:
    """没有 WebView2 时给出可操作的指引,而不是静默失败。"""
    message = (
        "未检测到 Microsoft Edge WebView2 运行时,无法打开应用窗口。\n\n"
        "点「是」前往微软官网下载(选 Evergreen Bootstrapper,装完重新打开本程序);\n"
        "点「否」用系统浏览器打开控制台,功能完全一样。"
    )
    choice = show_message(message, flags=0x04 | 0x20)
    if choice == 6:  # IDYES
        webbrowser.open("https://developer.microsoft.com/microsoft-edge/webview2/")
        return
    webbrowser.open(url)


def run_server(port: int) -> None:
    import uvicorn

    from app.main import create_app

    uvicorn.run(create_app(), host="127.0.0.1", port=port, log_level="warning")


def run_tray(config: dict, server_thread: threading.Thread) -> None:
    """系统托盘图标;pystray/Pillow 缺失时回退到前台等待。"""
    try:
        import pystray
        from PIL import Image, ImageDraw
    except ImportError:
        print(f"网关已启动:{config['url']}")
        print(f"管理 token:{config['token']}")
        print("按 Ctrl+C 退出")
        try:
            server_thread.join()
        except KeyboardInterrupt:
            pass
        return

    image = Image.new("RGB", (64, 64), "#0f172a")
    draw = ImageDraw.Draw(image)
    draw.ellipse((14, 14, 50, 50), fill="#06b6d4")

    def open_console(_icon=None, _item=None):
        webbrowser.open(config["url"])

    def copy_token(_icon=None, _item=None):
        try:
            import subprocess

            subprocess.run("clip", input=config["token"].encode("utf-16le"), check=False, shell=True)
        except Exception:
            pass

    def open_folder(_icon=None, _item=None):
        os.startfile(str(config["root"]))  # noqa: S606 - Windows only

    def quit_app(icon, _item=None):
        icon.stop()
        os._exit(0)

    icon = pystray.Icon(
        APP_NAME,
        image,
        f"Ying · LLM 网关 (127.0.0.1:{config['port']})",
        menu=pystray.Menu(
            pystray.MenuItem("打开控制台", open_console, default=True),
            pystray.MenuItem("复制管理 token", copy_token),
            pystray.MenuItem("打开数据目录", open_folder),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("退出", quit_app),
        ),
    )
    icon.run()


def wait_for_server(port: int, timeout_s: float = 20.0) -> bool:
    import socket
    import time

    deadline = time.time() + timeout_s
    while time.time() < deadline:
        with socket.socket() as sock:
            sock.settimeout(0.3)
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.25)
    return False


def run_window(config: dict) -> bool:
    """原生应用窗口(pywebview)。成功运行并关闭返回 True;不可用返回 False。"""
    try:
        import webview
    except ImportError:
        return False
    try:
        # window 只能由闭包持有:pywebview 会遍历 js_api 对象的属性暴露给 JS,
        # 若把 window 存成 api 的属性会沿 native 控件树无限递归(把日志刷爆)。
        state: dict = {"window": None, "maximized": False}

        class WindowApi:
            """暴露给前端标题栏的窗口控制(window.pywebview.api.*)。只放方法。"""

            def minimize(self):
                w = state["window"]
                if w:
                    w.minimize()

            def toggle_maximize(self):
                w = state["window"]
                if not w:
                    return
                if state["maximized"]:
                    w.restore()
                else:
                    w.maximize()
                state["maximized"] = not state["maximized"]

            def close(self):
                w = state["window"]
                if w:
                    w.destroy()

        # 控制台自带鉴权页;把 token 预置进 localStorage 免得每次手输
        token_js = "try{localStorage.setItem('gw_admin_token', %r);}catch(e){}" % config["token"]
        window = webview.create_window(
            WINDOW_TITLE,
            config["url"],
            width=1360,
            height=900,
            min_size=(1024, 680),
            background_color="#f4f6fa",
            frameless=True,   # 用控制台自绘的标题栏
            easy_drag=False,  # 拖拽由 .pywebview-drag-region 标记
            js_api=WindowApi(),
        )
        state["window"] = window

        def on_loaded():
            try:
                window.evaluate_js(token_js)
            except Exception:
                pass

        window.events.loaded += on_loaded
        webview.start()  # 阻塞到窗口关闭
        return True
    except Exception:
        import traceback

        traceback.print_exc()
        return False


def run_ui(config: dict, server_thread: threading.Thread) -> None:
    if os.environ.get("GW_WINDOW") != "0":
        if not webview2_available():
            prompt_install_webview2(config["url"])
            run_tray(config, server_thread)
            return
        if run_window(config):
            return
        show_message(
            f"无法打开 Ying 原生窗口，已改用系统浏览器。\n\n日志见:\n"
            f"{config.get('log') or data_dir() / 'gateway.log'}",
            flags=0x30,
        )
    if os.environ.get("GW_NO_BROWSER") != "1":
        webbrowser.open(config["url"])
    run_tray(config, server_thread)


def main() -> None:
    global INSTANCE_MUTEX
    INSTANCE_MUTEX = acquire_single_instance()
    if INSTANCE_MUTEX is None:
        show_message("Ying 已在运行。", flags=0x40)
        return

    log_path = redirect_std_streams()
    config = ensure_config()
    config["log"] = log_path
    server_thread = threading.Thread(target=run_server, args=(config["port"],), daemon=True)
    server_thread.start()

    if not wait_for_server(config["port"], timeout_s=30):
        show_message(
            f"网关服务启动失败。\n\n日志见:\n{log_path or data_dir() / 'gateway.log'}",
            flags=0x10,
        )
        os._exit(1)

    run_ui(config, server_thread)
    os._exit(0)


if __name__ == "__main__":
    main()
