# PyInstaller 规格文件:把网关 + 控制台静态资源打成单目录发行包。
# 构建:pyinstaller packaging/gateway.spec --noconfirm
# 产物:dist/LLMGateway/LLMGateway.exe(单目录比单文件启动快得多,安装包场景更合适)
import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPECPATH).parent  # noqa: F821 - SPECPATH 由 PyInstaller 注入
# 调试构建:GW_BUILD_CONSOLE=1 保留控制台窗口以便看 traceback
SHOW_CONSOLE = os.environ.get("GW_BUILD_CONSOLE") == "1"

datas = []
console_dist = ROOT / "console" / "dist"
if console_dist.exists():
    datas.append((str(console_dist), "console/dist"))
# alembic 迁移脚本随包分发(生产用 auto_create_tables,迁移供排障/升级)
datas.append((str(ROOT / "alembic"), "alembic"))
datas.append((str(ROOT / "alembic.ini"), "."))

hiddenimports = [
    "aiosqlite",
    "sqlalchemy.dialects.sqlite.aiosqlite",
    "uvicorn.logging",
    "uvicorn.loops.auto",
    "uvicorn.loops.asyncio",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.protocols.websockets.websockets_impl",
    "uvicorn.lifespan.on",
]
hiddenimports += collect_submodules("app")

a = Analysis(  # noqa: F821
    [str(ROOT / "packaging" / "launcher.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    excludes=["tkinter", "matplotlib", "numpy", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)  # noqa: F821

exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="LLMGateway",
    console=SHOW_CONSOLE,  # 发行版无控制台窗口(托盘模式);调试版可开
    icon=None,
)
coll = COLLECT(  # noqa: F821
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="LLMGateway",
)
