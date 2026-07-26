"""Windows 单机版启动器(PyInstaller 入口)。

行为:
- 首次运行在 %LOCALAPPDATA%\\LLMGateway 下建数据目录,生成 admin token 与 Fernet 密钥
- 起 uvicorn(SQLite,零外部依赖),打开浏览器进控制台
- 有 pystray 时驻留系统托盘(打开控制台 / 复制 token / 数据目录 / 退出);
  没有则前台运行,Ctrl+C 退出
"""
import os
import secrets
import sys
import threading
import webbrowser
from pathlib import Path

APP_NAME = "LLMGateway"
DEFAULT_PORT = 8080


def data_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    path = Path(base) / APP_NAME
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

    os.environ.setdefault("GW_ADMIN_TOKEN", admin_token)
    os.environ.setdefault("GW_SECRET_KEY", secret_key)
    os.environ.setdefault("GW_DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")
    os.environ.setdefault("GW_AUTO_CREATE_TABLES", "true")
    # 单机形态:控制台静态资源在解包目录里
    os.environ.setdefault("GW_CONSOLE_DIR", str(bundle_dir() / "console" / "dist"))

    port = int(os.environ.get("GW_PORT", DEFAULT_PORT))
    return {"token": admin_token, "port": port, "root": root, "url": f"http://127.0.0.1:{port}/console/"}


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
        f"LLM 网关 (127.0.0.1:{config['port']})",
        menu=pystray.Menu(
            pystray.MenuItem("打开控制台", open_console, default=True),
            pystray.MenuItem("复制管理 token", copy_token),
            pystray.MenuItem("打开数据目录", open_folder),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("退出", quit_app),
        ),
    )
    icon.run()


def main() -> None:
    log_path = redirect_std_streams()
    config = ensure_config()
    config["log"] = log_path
    server_thread = threading.Thread(target=run_server, args=(config["port"],), daemon=True)
    server_thread.start()

    # 等端口起来再开浏览器
    import socket
    import time

    for _ in range(60):
        with socket.socket() as sock:
            sock.settimeout(0.3)
            if sock.connect_ex(("127.0.0.1", config["port"])) == 0:
                break
        time.sleep(0.25)

    if os.environ.get("GW_NO_BROWSER") != "1":
        webbrowser.open(config["url"])
    run_tray(config, server_thread)


if __name__ == "__main__":
    main()
