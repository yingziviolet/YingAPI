# Ying Windows 1.0 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the existing Ying gateway as a reliable Windows desktop application, publish an installer and portable archive, and attach both to GitHub Release `v1.0.0`.

**Architecture:** Keep the existing React console and FastAPI gateway. PyInstaller bundles Python, the console assets, and pywebview into `Ying.exe`; pywebview hosts the local console in a WebView2 window, while an explicit browser/tray path remains the fallback. Inno Setup wraps the directory build, and a manual GitHub Release publishes the two verified artifacts.

**Tech Stack:** Python 3.12+, FastAPI, React 19, TypeScript, Vite, pywebview 6, PyInstaller 6, Inno Setup 6, PowerShell, GitHub CLI

---

## File map

- `packaging/launcher.py`: Windows process lifecycle, local data, port selection, pywebview window, browser/tray fallback, single-instance guard
- `tests/test_launcher.py`: small runnable checks for launcher behavior
- `packaging/assets/ying-icon.png`: editable source icon
- `packaging/assets/ying.ico`: Windows multi-size application icon
- `packaging/gateway.spec`: PyInstaller inputs, dynamic imports, icon, and `Ying` output name
- `packaging/installer.iss`: installer metadata, shortcuts, icon, and uninstall behavior
- `packaging/build.ps1`: repeatable frontend, portable, installer, and checksum build
- `.gitignore`: local build/release outputs
- `pyproject.toml`: project release version
- `README.md`, `QUICKSTART.md`, `ROADMAP.md`: accurate Ying paths and release status
- `docs/releases/v1.0.0.md`: GitHub Release notes

### Task 1: Preserve and verify the existing Ying onboarding work

**Files:**
- Modify: `app/api/admin.py`
- Modify: `app/config.py`
- Modify: `app/main.py`
- Modify: `console/index.html`
- Modify: `console/src/App.tsx`
- Modify: `console/src/api.ts`
- Modify: `console/src/components/TitleBar.tsx`
- Modify: `console/src/pages/Keys.tsx`
- Create: `console/src/pages/Setup.tsx`
- Modify: `tests/conftest.py`
- Modify: `tests/test_import.py`
- Create: `tests/test_setup.py`
- Modify: `README.md`
- Create: `QUICKSTART.md`
- Delete: `INTERVIEW.md`

- [ ] **Step 1: Inspect the existing in-progress diff without changing it**

Run:

```powershell
git diff --check
git diff -- app/api/admin.py app/config.py app/main.py console/index.html console/src/App.tsx console/src/api.ts console/src/components/TitleBar.tsx console/src/pages/Keys.tsx console/src/pages/Setup.tsx tests/conftest.py tests/test_import.py tests/test_setup.py README.md QUICKSTART.md INTERVIEW.md
```

Expected: no whitespace errors; every diff belongs to the approved Ying rename, first-run setup, virtual-Key explanation, or local desktop authentication.

- [ ] **Step 2: Run the setup and import regression tests**

Run:

```powershell
python -m pytest tests/test_setup.py tests/test_import.py -q --basetemp 'H:\全栈+agent\.tmp\pytest-onboarding' -p no:cacheprovider
```

Expected: all tests pass. The workspace-local `--basetemp` avoids the known sandbox denial under the Windows system temp directory.

- [ ] **Step 3: Build the current React console**

Run:

```powershell
npm.cmd run build
```

Working directory: `H:\全栈+agent\console`

Expected: TypeScript and Vite finish with exit code 0. The large-chunk warning is informational and does not justify adding code splitting in this release.

- [ ] **Step 4: Scan only tracked/staged content for accidental local secrets**

Run:

```powershell
$matches = git grep -n -E 'sk-[A-Za-z0-9_-]{24,}|BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY|GW_SECRET_KEY=.{8,}|GW_ADMIN_TOKEN=.{8,}' -- ':!README.md' ':!QUICKSTART.md' ':!tests/**'
if ($LASTEXITCODE -eq 0) { $matches; throw 'Potential secret found' }
if ($LASTEXITCODE -ne 1) { throw "git grep failed: $LASTEXITCODE" }
```

Expected: no matches. Examples in documentation and tests are excluded; `.gateway_secret`, databases, and local `.env` files remain ignored.

- [ ] **Step 5: Commit the already-tested onboarding slice**

Run:

```powershell
git add -- INTERVIEW.md README.md QUICKSTART.md app/api/admin.py app/config.py app/main.py console/index.html console/src/App.tsx console/src/api.ts console/src/components/TitleBar.tsx console/src/pages/Keys.tsx console/src/pages/Setup.tsx tests/conftest.py tests/test_import.py tests/test_setup.py
git commit -m "feat: add Ying first-run setup"
```

Expected: one commit containing only the existing onboarding, branding, and associated test changes. Packaging files remain uncommitted for the launcher tasks below.

### Task 2: Make the Windows launcher observable, single-instance, and testable

**Files:**
- Create: `tests/test_launcher.py`
- Modify: `packaging/launcher.py:20-320`

- [ ] **Step 1: Capture the current native-window failure before editing**

Stop the old preview process, start the existing debug artifact with an isolated profile, and inspect its log:

```powershell
Get-Process -Name Ying -ErrorAction SilentlyContinue | Stop-Process
$env:LOCALAPPDATA='H:\全栈+agent\.tmp\ying-debug-profile'
$env:GW_PORT='8180'
$env:PYWEBVIEW_LOG='debug'
$debugProcess = Start-Process -FilePath 'H:\全栈+agent\dist-debug\LLMGateway\LLMGateway.exe' -PassThru
Start-Sleep -Seconds 5
Get-Content -Tail 100 -LiteralPath 'H:\全栈+agent\.tmp\ying-debug-profile\LLMGateway\gateway.log' -ErrorAction SilentlyContinue
Stop-Process -Id $debugProcess.Id -ErrorAction SilentlyContinue
```

Expected: reproduce either a pywebview/CLR traceback or an absent native window. Record the exact evidence in the task log before changing code.

- [ ] **Step 2: Write focused failing launcher tests**

Create `tests/test_launcher.py`:

```python
import ctypes
import importlib.util
import socket
import sys
import types
import uuid
from pathlib import Path

import pytest


SPEC = importlib.util.spec_from_file_location(
    "ying_launcher_test",
    Path(__file__).parents[1] / "packaging" / "launcher.py",
)
assert SPEC and SPEC.loader
launcher = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(launcher)


def test_data_dir_reuses_legacy_profile(monkeypatch, tmp_path):
    legacy = tmp_path / "LLMGateway"
    legacy.mkdir()
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert launcher.data_dir() == legacy
    assert not (tmp_path / "Ying").exists()


def test_pick_port_skips_busy_preferred_port():
    with socket.socket() as busy:
        busy.bind(("127.0.0.1", 0))
        port = busy.getsockname()[1]
        assert launcher.pick_port(port) != port


def test_run_window_forces_edgechromium(monkeypatch):
    calls = {}

    class Loaded:
        def __iadd__(self, callback):
            self.callback = callback
            return self

    window = types.SimpleNamespace(
        events=types.SimpleNamespace(loaded=Loaded()),
        evaluate_js=lambda _code: None,
        minimize=lambda: None,
        maximize=lambda: None,
        restore=lambda: None,
        destroy=lambda: None,
    )
    fake_webview = types.SimpleNamespace(
        create_window=lambda *_args, **_kwargs: window,
        start=lambda **kwargs: calls.update(kwargs),
    )
    monkeypatch.setitem(sys.modules, "webview", fake_webview)
    monkeypatch.delenv("GW_WEBVIEW_DEBUG", raising=False)

    assert launcher.run_window({"url": "http://127.0.0.1:8080/console/", "token": "test"})
    assert calls == {"gui": "edgechromium", "debug": False}


def test_window_failure_opens_browser_before_tray(monkeypatch):
    opened = []
    messages = []
    tray = []
    config = {"url": "http://127.0.0.1:8080/console/"}
    server_thread = object()

    monkeypatch.delenv("GW_NO_BROWSER", raising=False)
    monkeypatch.setattr(launcher, "webview2_available", lambda: True)
    monkeypatch.setattr(launcher, "run_window", lambda _config: False)
    monkeypatch.setattr(launcher, "show_message", lambda *args, **kwargs: messages.append((args, kwargs)))
    monkeypatch.setattr(launcher.webbrowser, "open", opened.append)
    monkeypatch.setattr(launcher, "run_tray", lambda c, s: tray.append((c, s)))

    launcher.run_ui(config, server_thread)

    assert opened == [config["url"]]
    assert len(messages) == 1
    assert tray == [(config, server_thread)]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows named mutex")
def test_single_instance_mutex():
    name = rf"Local\Ying-test-{uuid.uuid4().hex}"
    first = launcher.acquire_single_instance(name)
    try:
        assert first
        assert launcher.acquire_single_instance(name) is None
    finally:
        launcher.release_single_instance(first)
```

- [ ] **Step 3: Run the tests and verify the intended failures**

Run:

```powershell
python -m pytest tests/test_launcher.py -q --basetemp 'H:\全栈+agent\.tmp\pytest-launcher-red' -p no:cacheprovider
```

Expected: `test_run_window_forces_edgechromium`, `test_window_failure_opens_browser_before_tray`, and `test_single_instance_mutex` fail because the launcher does not yet expose the required behavior.

- [ ] **Step 4: Add the minimal launcher lifecycle code**

Add near the launcher constants:

```python
MUTEX_NAME = r"Local\Ying-8F3C1A62-9E4D-4B7A-9C21-5D0E7A1B2C33"
INSTANCE_MUTEX: int | None = None
ERROR_ALREADY_EXISTS = 183
```

Add these helpers after `redirect_std_streams()`:

```python
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
        kernel32.CloseHandle(handle)


def show_message(message: str, title: str = APP_NAME, flags: int = 0x10) -> int:
    try:
        import ctypes

        return int(ctypes.windll.user32.MessageBoxW(0, message, title, flags))
    except Exception:
        print(message)
        return 0
```

Use `show_message()` in `prompt_install_webview2()` and the server-start timeout path instead of separate `MessageBoxW` calls:

```python
choice = show_message(message, flags=0x04 | 0x20)
```

```python
show_message(
    f"网关服务启动失败。\n\n日志见:\n{log_path or data_dir() / 'gateway.log'}",
    flags=0x10,
)
```

Change the tray tooltip to:

```python
        f"Ying · LLM 网关 (127.0.0.1:{config['port']})",
```

Change the final pywebview call in `run_window()` to:

```python
        webview.start(
            gui="edgechromium",
            debug=os.environ.get("GW_WEBVIEW_DEBUG") == "1",
        )
```

Move the UI selection into a testable function:

```python
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
```

At the start of `main()`, acquire the mutex and show a visible duplicate-instance message:

```python
    global INSTANCE_MUTEX
    INSTANCE_MUTEX = acquire_single_instance()
    if INSTANCE_MUTEX is None:
        show_message("Ying 已在运行。", flags=0x40)
        return
```

Replace the existing window/tray branch at the end of `main()` with:

```python
    run_ui(config, server_thread)
    os._exit(0)
```

- [ ] **Step 5: Run launcher and full Python tests**

Run:

```powershell
python -m pytest tests/test_launcher.py -q --basetemp 'H:\全栈+agent\.tmp\pytest-launcher-green' -p no:cacheprovider
python -m pytest tests -q --basetemp 'H:\全栈+agent\.tmp\pytest-all-after-launcher' -p no:cacheprovider
```

Expected: 5 launcher tests pass on Windows and the complete suite reports 129 passed.

- [ ] **Step 6: Commit the launcher fix**

Run:

```powershell
git add -- packaging/launcher.py tests/test_launcher.py
git commit -m "fix: make Ying desktop startup reliable"
```

### Task 3: Add the Ying icon and reproducible release packaging

**Files:**
- Create: `packaging/assets/ying-icon.png`
- Create: `packaging/assets/ying.ico`
- Modify: `packaging/gateway.spec:9-68`
- Modify: `packaging/installer.iss:8-54`
- Modify: `packaging/build.ps1`
- Modify: `pyproject.toml:1-25`
- Modify: `.gitignore`

- [ ] **Step 1: Generate the approved icon source**

Use the imagegen skill with this exact prompt:

```text
Create a clean Windows desktop application icon for “Ying”, an LLM gateway.
Square 1024×1024 composition, deep navy rounded-square background, one bold
geometric capital Y in bright cyan/teal, subtle gateway/node connection motif
inside the Y, high contrast, flat vector-like finish, no words, no small text,
no mockup, no drop-shadow outside the square, legible at 16×16.
```

Save the selected result as `packaging/assets/ying-icon.png` and inspect it at full resolution before conversion.

- [ ] **Step 2: Convert the PNG into a multi-size Windows icon**

Run:

```powershell
python -c "from PIL import Image; p=Image.open(r'packaging/assets/ying-icon.png').convert('RGBA'); p.save(r'packaging/assets/ying.ico', format='ICO', sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])"
```

Expected: `packaging/assets/ying.ico` exists and contains the sizes Windows uses for Explorer, shortcuts, and the taskbar.

- [ ] **Step 3: Wire the icon and version into PyInstaller and Inno Setup**

In `packaging/gateway.spec`, define and use:

```python
ICON = ROOT / "packaging" / "assets" / "ying.ico"
```

Change the `EXE` icon argument to:

```python
    icon=str(ICON),
```

Add to `[Setup]` in `packaging/installer.iss`:

```ini
SetupIconFile=assets\ying.ico
UninstallDisplayIcon={app}\Ying.exe
VersionInfoVersion={#AppVersion}
```

Change the project version in `pyproject.toml` to:

```toml
version = "1.0.0"
```

- [ ] **Step 4: Make the build script emit both release artifacts and checksums**

Add a version parameter at the top of `packaging/build.ps1`:

```powershell
param([string]$Version = "1.0.0")
```

After the PyInstaller step, create the portable archive:

```powershell
$releaseDir = Join-Path $root "release"
New-Item -ItemType Directory -Force -Path $releaseDir | Out-Null
$portable = Join-Path $releaseDir "Ying-portable.zip"
if (Test-Path -LiteralPath $portable) {
    Remove-Item -LiteralPath $portable
}
Compress-Archive -Path (Join-Path $root "dist\Ying\*") -DestinationPath $portable -CompressionLevel Optimal
```

Pass the version to Inno Setup and copy its output:

```powershell
if ($iscc) {
    & $iscc "/DAppVersion=$Version" (Join-Path $PSScriptRoot "installer.iss")
    if ($LASTEXITCODE -ne 0) { throw "Inno Setup 构建失败: $LASTEXITCODE" }
    $installer = Join-Path $releaseDir "Ying-Setup-$Version.exe"
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot "output\Ying-Setup-$Version.exe") -Destination $installer -Force
} else {
    throw "未找到 Inno Setup 6，无法生成 Ying 安装包"
}
```

After both files exist, write deterministic checksum lines:

```powershell
$checksumFile = Join-Path $releaseDir "SHA256SUMS.txt"
@($installer, $portable) | ForEach-Object {
    $hash = Get-FileHash -Algorithm SHA256 -LiteralPath $_
    "$($hash.Hash.ToLower())  $(Split-Path -Leaf $_)"
} | Set-Content -LiteralPath $checksumFile -Encoding ASCII
```

Add the generated release directory to `.gitignore`:

```gitignore
release/
```

- [ ] **Step 5: Validate packaging syntax without performing the final release build**

Run:

```powershell
python -m PyInstaller --version
python -c "compile(open(r'packaging/gateway.spec', encoding='utf-8').read(), r'packaging/gateway.spec', 'exec')"
$null = [scriptblock]::Create((Get-Content -Raw -LiteralPath 'packaging\build.ps1'))
```

Expected: PyInstaller prints its version; Python and PowerShell parse the two build files without errors.

- [ ] **Step 6: Commit packaging metadata and icon**

Run:

```powershell
git add -- .gitignore pyproject.toml packaging/assets/ying-icon.png packaging/assets/ying.ico packaging/gateway.spec packaging/installer.iss packaging/build.ps1
git commit -m "build: package Ying 1.0 for Windows"
```

### Task 4: Verify the console in both themes

**Files:**
- Inspect: `console/src/index.css`
- Inspect: `console/src/components/Chart.tsx`
- Inspect: `console/src/pages/Dashboard.tsx`
- Inspect: `console/src/pages/Channels.tsx`
- Inspect: `console/src/pages/Keys.tsx`
- Inspect: `console/src/pages/LiveTail.tsx`
- Inspect: `console/src/pages/Insights.tsx`
- Inspect: `console/src/pages/Alerts.tsx`
- Inspect: `console/src/pages/Subscription.tsx`

- [ ] **Step 1: Start a disposable local console profile**

Run the source launcher with a workspace-local profile:

```powershell
$env:LOCALAPPDATA='H:\全栈+agent\.tmp\ying-ui-profile'
$env:GW_PORT='8181'
$env:GW_WEBVIEW_DEBUG='1'
$uiProcess = Start-Process -FilePath 'python' -ArgumentList @('packaging\launcher.py') -WorkingDirectory 'H:\全栈+agent' -PassThru
$uiProcess.Id | Set-Content -LiteralPath 'H:\全栈+agent\.tmp\ying-ui.pid' -Encoding ASCII
```

Expected: a visible Ying window opens at the first-run setup screen.

- [ ] **Step 2: Create minimal local demo records**

Read the generated admin token and use the existing admin API; the upstream URL is intentionally local and no request is sent during channel creation:

```powershell
$token = Get-Content -Raw -LiteralPath 'H:\全栈+agent\.tmp\ying-ui-profile\Ying\admin_token.txt'
$headers = @{ Authorization = "Bearer $($token.Trim())" }
$channel = @{
    name = 'UI Demo'
    provider = 'openai'
    base_url = 'http://127.0.0.1:9909/v1'
    api_key = 'sk-ui-demo-not-real'
    models = @('demo-model')
    model_map = @{}
    prices = @{ 'demo-model' = @{ input = 0.1; output = 0.2 } }
    priority = 10
    enabled = $true
} | ConvertTo-Json -Depth 5
Invoke-RestMethod -Uri 'http://127.0.0.1:8181/admin/channels' -Method Post -Headers $headers -ContentType 'application/json' -Body $channel
Invoke-RestMethod -Uri 'http://127.0.0.1:8181/admin/keys' -Method Post -Headers $headers -ContentType 'application/json' -Body '{"name":"UI Demo","monthly_budget_usd":10}'
```

Expected: one demo channel and one virtual Key appear; no real credential is used.

- [ ] **Step 3: Walk every page in light and dark mode**

Check Dashboard, Channels, Keys, Live Tail, Insights, Alerts, and Subscription in both themes.

Pass criteria:

- text and icons remain readable;
- badges do not lose foreground/background contrast;
- chart legends do not overlap axes;
- tooltips are readable;
- cards and modal boundaries are visible;
- no horizontal scrollbar appears at the 1024 px minimum window width.

No UI redesign is allowed in this task. If every item passes, make no source change.

- [ ] **Step 4: Run frontend checks after the visual walk**

Run:

```powershell
npm.cmd run lint
npm.cmd run build
$uiPid = Get-Content -Raw -LiteralPath 'H:\全栈+agent\.tmp\ying-ui.pid'
Stop-Process -Id ([int]$uiPid) -ErrorAction SilentlyContinue
```

Working directory: `H:\全栈+agent\console`

Expected: both commands exit 0. A Vite chunk-size warning remains informational.

### Task 5: Build and smoke-test the portable application and installer

**Files:**
- Generate: `dist/Ying/Ying.exe`
- Generate: `release/Ying-portable.zip`
- Generate: `release/Ying-Setup-1.0.0.exe`
- Generate: `release/SHA256SUMS.txt`

- [ ] **Step 1: Install the missing Inno Setup build tool**

Current preflight found no `ISCC.exe`. Run:

```powershell
winget install --id JRSoftware.InnoSetup --exact --accept-package-agreements --accept-source-agreements
```

Expected: Inno Setup 6 installs and one of these exists:

```text
C:\Program Files (x86)\Inno Setup 6\ISCC.exe
C:\Program Files\Inno Setup 6\ISCC.exe
```

- [ ] **Step 2: Stop preview processes before replacing `dist\Ying`**

Run:

```powershell
Get-Process -Name Ying,LLMGateway -ErrorAction SilentlyContinue | Stop-Process
```

Expected: no old process keeps the PyInstaller output directory locked.

- [ ] **Step 3: Run the complete release build**

Run:

```powershell
powershell -ExecutionPolicy Bypass -File packaging\build.ps1 -Version 1.0.0
```

Expected: frontend, PyInstaller, and Inno Setup all succeed; the four generated paths listed at the top of this task exist.

- [ ] **Step 4: Smoke-test the packaged backend with isolated data**

Run:

```powershell
$profile = 'H:\全栈+agent\.tmp\ying-portable-smoke'
New-Item -ItemType Directory -Force -Path $profile | Out-Null
$env:LOCALAPPDATA = $profile
$env:GW_PORT = '8182'
$env:GW_WINDOW = '0'
$env:GW_NO_BROWSER = '1'
$process = Start-Process -FilePath 'H:\全栈+agent\dist\Ying\Ying.exe' -PassThru
Start-Sleep -Seconds 5
$health = Invoke-RestMethod -Uri 'http://127.0.0.1:8182/healthz'
$console = Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:8182/console/'
if ($health.status -ne 'ok' -or $console.StatusCode -ne 200) { throw 'Portable smoke test failed' }
Stop-Process -Id $process.Id
```

Expected: `/healthz` reports `ok`, the console returns HTTP 200, and the process stops cleanly after verification.

- [ ] **Step 5: Smoke-test the final native window**

Run:

```powershell
$env:LOCALAPPDATA = 'H:\全栈+agent\.tmp\ying-native-smoke'
$env:GW_PORT = '8183'
Remove-Item Env:GW_WINDOW -ErrorAction SilentlyContinue
Remove-Item Env:GW_NO_BROWSER -ErrorAction SilentlyContinue
$process = Start-Process -FilePath 'H:\全栈+agent\dist\Ying\Ying.exe' -PassThru
Start-Sleep -Seconds 8
$visible = Get-Process -Id $process.Id
if ($visible.MainWindowHandle -eq 0) { throw 'Ying native window is not visible' }
Stop-Process -Id $process.Id
```

Expected: `MainWindowHandle` is nonzero and the visible window contains the Ying setup/dashboard panel without browser chrome.

- [ ] **Step 6: Test silent install and uninstall in an isolated directory**

Run the installer with an explicit workspace target:

```powershell
$installDir = 'H:\全栈+agent\.tmp\ying-installed'
$installProfile = 'H:\全栈+agent\.tmp\ying-installed-profile'
$desktopLinks = @(
    (Join-Path ([Environment]::GetFolderPath('Desktop')) 'Ying.lnk'),
    (Join-Path ([Environment]::GetFolderPath('CommonDesktopDirectory')) 'Ying.lnk')
)
Start-Process -FilePath 'H:\全栈+agent\release\Ying-Setup-1.0.0.exe' -ArgumentList @('/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART','/TASKS=desktopicon',"/DIR=$installDir") -Wait
if (-not (Test-Path -LiteralPath "$installDir\Ying.exe")) { throw 'Installer did not create Ying.exe' }
if (-not ($desktopLinks | Where-Object { Test-Path -LiteralPath $_ })) { throw 'Installer did not create the desktop shortcut' }
$env:LOCALAPPDATA = $installProfile
$env:GW_PORT = '8184'
$env:GW_WINDOW = '0'
$env:GW_NO_BROWSER = '1'
$installed = Start-Process -FilePath "$installDir\Ying.exe" -PassThru
Start-Sleep -Seconds 5
$health = Invoke-RestMethod -Uri 'http://127.0.0.1:8184/healthz'
if ($health.status -ne 'ok') { throw 'Installed Ying failed its health check' }
Stop-Process -Id $installed.Id
Start-Process -FilePath "$installDir\unins000.exe" -ArgumentList @('/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART') -Wait
if (Test-Path -LiteralPath "$installDir\Ying.exe") { throw 'Uninstaller left Ying.exe behind' }
if ($desktopLinks | Where-Object { Test-Path -LiteralPath $_ }) { throw 'Uninstaller left the desktop shortcut behind' }
if (-not (Test-Path -LiteralPath "$installProfile\Ying\gateway.db")) { throw 'Uninstaller removed user data' }
```

Expected: install creates the executable and desktop shortcut, the installed application responds, uninstall removes program files and the shortcut, and the isolated user database remains.

- [ ] **Step 7: Verify artifact names and hashes**

Run:

```powershell
Get-Item -LiteralPath 'release\Ying-portable.zip','release\Ying-Setup-1.0.0.exe','release\SHA256SUMS.txt' | Select-Object Name,Length,LastWriteTime
Get-Content -LiteralPath 'release\SHA256SUMS.txt'
```

Expected: both binaries are nonempty and the checksum file contains exactly two lowercase SHA-256 lines.

### Task 6: Update release documentation and run the final verification gate

**Files:**
- Modify: `README.md`
- Modify: `QUICKSTART.md`
- Modify: `ROADMAP.md`
- Create: `docs/releases/v1.0.0.md`

- [ ] **Step 1: Correct stale executable paths and roadmap state**

Use these exact release facts in the three existing documents:

```text
Portable executable: dist\Ying\Ying.exe
Portable download: Ying-portable.zip
Installer download: Ying-Setup-1.0.0.exe
User data: %LOCALAPPDATA%\Ying
Legacy data compatibility: %LOCALAPPDATA%\LLMGateway
```

Mark the Anthropic Messages API and channel-balance frontend as completed because their implementations and tests already exist. Mark the pywebview build verification complete only after Task 5 passes. Keep quality review, pgvector indexing, weighted routing, and non-Windows packaging as future work.

- [ ] **Step 2: Write the Release notes**

Create `docs/releases/v1.0.0.md`:

```markdown
# Ying 1.0.0

Ying 是一个本地运行的 LLM API 网关，提供 OpenAI/Anthropic 兼容入口、渠道故障切换、虚拟 Key、预算与用量统计，以及可视化控制台。

## 下载

- `Ying-Setup-1.0.0.exe`：推荐，Windows 安装版
- `Ying-portable.zip`：免安装版，解压后运行 `Ying.exe`
- `SHA256SUMS.txt`：文件完整性校验

## 首次使用

1. 启动 Ying。
2. 在首次配置页填入自己合法持有的上游 API Key。
3. 复制 Ying 生成的本地地址和 `sk-gw-` 虚拟 Key 到客户端。

用户数据库和密钥保存在 `%LOCALAPPDATA%\Ying`；卸载应用不会删除这些数据。

## Windows 提示

当前版本没有商业代码签名证书，Windows SmartScreen 可能显示“未知发布者”。请从本 Release 下载并用 `SHA256SUMS.txt` 核对文件。
```

- [ ] **Step 3: Run all automated verification**

Run:

```powershell
python -m pytest tests -q --basetemp 'H:\全栈+agent\.tmp\pytest-final' -p no:cacheprovider
npm.cmd run lint
npm.cmd run build
git diff --check
```

Run the npm commands from `H:\全栈+agent\console`.

Expected: 129 Python tests pass, lint and production build exit 0, and Git reports no whitespace errors.

- [ ] **Step 4: Review the final source diff and tracked-file list**

Run:

```powershell
git status --short
git diff --stat HEAD
git ls-files | Select-String -Pattern '\.env$|gateway\.db$|admin_token\.txt$|secret\.key$|gateway\.log$|release/'
```

Expected: no user data, secrets, logs, databases, or generated Release binaries are tracked.

- [ ] **Step 5: Commit documentation and any verified UI corrections**

Run:

```powershell
git add -- README.md QUICKSTART.md ROADMAP.md docs/releases/v1.0.0.md console/src
git commit -m "docs: prepare Ying 1.0 release"
```

Expected: the commit contains documentation plus only UI corrections proven necessary by Task 4.

### Task 7: Push and publish GitHub Release `v1.0.0`

**Files:**
- Upload: `release/Ying-Setup-1.0.0.exe`
- Upload: `release/Ying-portable.zip`
- Upload: `release/SHA256SUMS.txt`
- Use notes: `docs/releases/v1.0.0.md`

- [ ] **Step 1: Install GitHub CLI because the preflight found it missing**

Run:

```powershell
winget install --id GitHub.cli --exact --accept-package-agreements --accept-source-agreements
```

Expected: `gh --version` succeeds.

- [ ] **Step 2: Confirm GitHub authentication and target repository**

Run:

```powershell
gh auth status
git remote get-url origin
gh repo view yingziviolet/YingAPI
```

Expected: GitHub CLI is authenticated to an account with write access, and `origin` is `https://github.com/yingziviolet/YingAPI.git`. If authentication is absent, stop at this step and let the user complete `gh auth login`; never extract or print Git credential-store secrets.

- [ ] **Step 3: Run the final pre-push checks**

Run:

```powershell
git status --short
git log -5 --oneline
git ls-remote --tags origin
Get-Content -LiteralPath 'release\SHA256SUMS.txt'
```

Expected: source changes are committed, no `v1.0.0` tag exists remotely, and the two artifact hashes match Task 5.

- [ ] **Step 4: Push source commits**

Run:

```powershell
git push origin main
```

Expected: GitHub accepts all Ying 1.0 source commits.

- [ ] **Step 5: Create and push the release tag**

Run:

```powershell
git tag -a v1.0.0 -m "Ying 1.0.0"
git push origin v1.0.0
```

Expected: tag `v1.0.0` points at the verified release commit.

- [ ] **Step 6: Create the GitHub Release and upload assets**

Run:

```powershell
gh release create v1.0.0 'release\Ying-Setup-1.0.0.exe' 'release\Ying-portable.zip' 'release\SHA256SUMS.txt' --repo yingziviolet/YingAPI --title 'Ying 1.0.0' --notes-file 'docs\releases\v1.0.0.md'
```

Expected: GitHub returns the public Release URL.

- [ ] **Step 7: Verify the published download list**

Run:

```powershell
gh release view v1.0.0 --repo yingziviolet/YingAPI --json url,tagName,assets
```

Expected: the Release is public and lists exactly `Ying-Setup-1.0.0.exe`, `Ying-portable.zip`, and `SHA256SUMS.txt`.
