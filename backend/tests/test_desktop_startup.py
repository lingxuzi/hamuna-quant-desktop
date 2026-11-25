"""desktop.py 启动失败路径测试。

复现的原始问题 (2026-10-07): panel 模式/残留后端占住 data_dir/.mining_process.lock,
桌面版 lifespan 抢锁失败被 uvicorn 吞掉, 主线程傻等 150s 后只显示笼统的
"启动超时", 真因不可见。修复 = 预检秒失败 + 等待循环感知后端线程退出 +
失败页透传原因。
"""
from __future__ import annotations

import errno
import os
import sys
import threading
import time
from types import SimpleNamespace

import pytest

import app.desktop as desktop
from app.config import settings
from app.desktop import (
    _failed_html,
    _precheck_mining_lock,
    _wait_for_server,
)
from app.services.mining_process_lock import MiningProcessLock


@pytest.fixture()
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path, raising=False)
    yield tmp_path
    desktop._release_single_instance()


def test_single_instance_ignores_unlocked_file_with_live_pid(data_dir):
    """崩溃残留的 PID 被复用, 没有实际持锁者时仍须允许启动。"""
    (data_dir / ".desktop.lock").write_text(str(os.getpid()), encoding="utf-8")
    assert desktop._acquire_single_instance() is True


@pytest.mark.parametrize("lock_error", [None, errno.EACCES, errno.EIO])
def test_single_instance_windows_lock_errors(data_dir, monkeypatch, lock_error):
    """锁冲突才作为已有实例处理, 磁盘/系统错误须交给启动错误提示。"""
    opened = []

    def lock(fd, mode, count):
        assert os.get_inheritable(fd) is False
        opened.append(fd)
        if lock_error is not None:
            raise OSError(lock_error, "locking failed")

    monkeypatch.setattr(
        desktop, "os", SimpleNamespace(name="nt", set_inheritable=os.set_inheritable)
    )
    monkeypatch.setitem(sys.modules, "msvcrt", SimpleNamespace(LK_NBLCK=2, locking=lock))

    if lock_error == errno.EIO:
        with pytest.raises(OSError, match="locking failed"):
            desktop._acquire_single_instance()
    else:
        assert desktop._acquire_single_instance() is (lock_error is None)
    desktop._release_single_instance()
    assert len(opened) == 1
    with pytest.raises(OSError):
        os.fstat(opened[0])  # 成功、冲突、系统错误三条路径都必须关闭句柄。


def test_single_instance_rejects_other_process_then_allows_after_release(data_dir):
    import subprocess

    script = (
        "from app.desktop import _acquire_single_instance; "
        "print(_acquire_single_instance())"
    )

    def acquire_in_child():
        result = subprocess.run(
            [sys.executable, "-c", script],
            env={**os.environ, "DATA_DIR": str(data_dir)},
            capture_output=True, text=True, timeout=15, check=True,
        )
        return result.stdout.strip()

    assert desktop._acquire_single_instance() is True
    assert acquire_in_child() == "False"
    desktop._release_single_instance()
    assert acquire_in_child() == "True"
    # 子进程没有手动释放; 进程结束仍须自动解锁。
    assert desktop._acquire_single_instance() is True


# ---------- _failed_html ----------

def test_failed_html_timeout_keeps_original_copy():
    html_text = _failed_html(None)
    assert "启动超时" in html_text
    assert "后端未在预期时间内就绪" in html_text


def test_failed_html_shows_reason_with_title_change():
    html_text = _failed_html("另一个后端实例正在占用数据目录")
    assert "启动失败" in html_text
    assert "另一个后端实例正在占用数据目录" in html_text


def test_failed_html_escapes_reason_text():
    html_text = _failed_html("<script>alert(1)</script>")
    assert "<script>" not in html_text
    assert "&lt;script&gt;" in html_text


def test_failed_html_renders_newlines():
    html_text = _failed_html("第一行\n第二行")
    assert "第一行<br>第二行" in html_text


# ---------- _wait_for_server ----------

def test_wait_for_server_returns_immediately_when_backend_thread_dead():
    """后端线程已退出 (done_event 置位) 时不得等满 timeout —— 把启动失败伪装成超时的根源。"""
    done = threading.Event()
    done.set()
    start = time.monotonic()
    assert _wait_for_server(port=59999, timeout=10.0, done_event=done) is False
    assert time.monotonic() - start < 1.0


def test_wait_for_server_times_out_when_alive_but_not_ready():
    """后端线程活着但 health 不通: 等满 timeout 返回 False (原行为不变)。"""
    done = threading.Event()
    start = time.monotonic()
    assert _wait_for_server(port=59999, timeout=0.6, done_event=done) is False
    assert time.monotonic() - start >= 0.5


def test_main_timeout_records_stalled_backend_stack(data_dir, monkeypatch, caplog):
    """慢启动时记录真正阻塞的后端调用, 不能只有笼统的 60 秒超时。"""
    entered = threading.Event()
    release = threading.Event()
    stopped = threading.Event()
    opened = []

    def _slow_backend_import(port, done):
        entered.set()
        try:
            release.wait(timeout=5)
        finally:
            done.set()
            stopped.set()

    def timeout(port, timeout, done_event):
        assert entered.wait(timeout=1)
        return False

    monkeypatch.delenv("TDX_PROBE_WORKER", raising=False)
    monkeypatch.setattr(desktop, "_guard_streams", lambda: None)
    monkeypatch.setattr(desktop, "_setup_logging", lambda: None)
    monkeypatch.setattr(desktop, "_precheck_mining_lock", lambda: None)
    monkeypatch.setattr(desktop, "_find_free_port", lambda _: 59998)
    monkeypatch.setattr(desktop, "_run_uvmicorn", _slow_backend_import)
    monkeypatch.setattr(desktop, "_wait_for_server", timeout)
    monkeypatch.setattr(desktop, "_open_static_window", opened.append)
    try:
        with caplog.at_level("ERROR", logger="app.desktop"):
            assert desktop.main() == 1
        assert len(opened) == 1
        assert "启动超时" in opened[0]
        assert "_slow_backend_import" in caplog.text
    finally:
        release.set()
        assert stopped.wait(timeout=1)


# ---------- _precheck_mining_lock ----------

def test_precheck_mining_lock_passes_when_free(data_dir):
    assert _precheck_mining_lock() is None


def test_precheck_mining_lock_pass_releases_lock(data_dir):
    """预检通过后必须释放 —— 否则预检自己挡死 lifespan。"""
    assert _precheck_mining_lock() is None
    holder = MiningProcessLock(data_dir)
    holder.acquire()  # 仍能抢到 = 预检确实释放了
    holder.release()


def test_precheck_mining_lock_detects_conflict(data_dir):
    holder = MiningProcessLock(data_dir)
    holder.acquire()
    reason = _precheck_mining_lock()
    assert reason is not None
    # 失败页要给出可操作的下一步, 不是一句"无法启动"
    assert "结束残留的 python" in reason
    holder.release()
    assert _precheck_mining_lock() is None


# ---------- main() 预检冲突路径 ----------

def test_main_proceeds_when_mining_lock_precheck_conflicts(
    tmp_path, monkeypatch, caplog
):
    """预检撞锁只降级: 留 WARNING 日志并继续启动, 不再提前退出失败页。

    lifespan 会以降级模式起面板 (挖掘入口 409, 挖掘页提供强制释放),
    桌面版必须照常开窗, 否则用户没有入口处理锁冲突。
    """
    import logging

    monkeypatch.setattr(settings, "data_dir", tmp_path, raising=False)
    monkeypatch.setattr(desktop, "_guard_streams", lambda: None)
    monkeypatch.setattr(desktop, "_setup_logging", lambda: None)
    monkeypatch.setattr(
        desktop, "_precheck_mining_lock", lambda: "另一个后端实例正在占用数据目录"
    )
    monkeypatch.setattr(desktop, "_find_free_port", lambda base: 59998)
    monkeypatch.setattr(
        desktop, "_wait_for_server",
        lambda port, timeout, done_event: True,
    )
    opened = []
    monkeypatch.setattr(desktop, "_open_window", lambda url, *a: opened.append(url))

    def _fake_uvmicorn(port, ready):
        ready.set()

    monkeypatch.setattr(desktop, "_run_uvmicorn", _fake_uvmicorn)

    with caplog.at_level(logging.WARNING, logger="app.desktop"):
        assert desktop.main() == 0

    assert opened and opened[0].startswith("http://127.0.0.1:")
    assert "启动预检" in caplog.text
    assert desktop._acquire_single_instance() is True
    desktop._release_single_instance()


@pytest.mark.parametrize("stage", ["data_dir", "single_instance", "mining_precheck"])
def test_main_reports_early_startup_errors(data_dir, monkeypatch, stage):
    monkeypatch.setattr(desktop, "_guard_streams", lambda: None)
    monkeypatch.setattr(desktop, "_setup_logging", lambda: None)
    monkeypatch.delenv("TDX_PROBE_WORKER", raising=False)
    errors = []
    monkeypatch.setattr(desktop, "_show_crash", lambda *args: errors.append(args))
    target = {
        "data_dir": "_ensure_data_dir_writable",
        "single_instance": "_acquire_single_instance",
        "mining_precheck": "_precheck_mining_lock",
    }[stage]

    def fail():
        raise PermissionError(f"startup failed at {stage}")

    with monkeypatch.context() as patch:
        patch.setattr(desktop, target, fail)
        assert desktop.main() == 1
    assert len(errors) == 1
    assert "H-Quant 启动失败" in errors[0][0]
    assert f"startup failed at {stage}" in errors[0][1]
    assert desktop._acquire_single_instance() is True


# ---------- _open_window: pywebview 下载启用 ----------

def test_open_window_enables_downloads_before_start(data_dir, monkeypatch):
    """pywebview 5.0+ 默认取消所有文件下载 (Edge WebView2 / WKWebView 一致) —
    必须在 webview.start() 前显式开启 ALLOW_DOWNLOADS, 否则配置备份导出、
    回测 CSV 等 <a download> 直链在桌面版点击无反应 (2026-10-27 用户反馈)。"""
    import types

    calls: list[str] = []

    class _RecordingSettings(dict):
        def __setitem__(self, key, value):
            calls.append(f"set:{key}={value}")
            super().__setitem__(key, value)

    fake = types.ModuleType("webview")
    fake.settings = _RecordingSettings()
    fake.create_window = lambda *a, **k: calls.append("create_window") or object()
    fake.start = lambda *a, **k: calls.append("start")
    monkeypatch.setitem(sys.modules, "webview", fake)

    desktop._open_window("http://127.0.0.1:1")

    assert fake.settings.get("ALLOW_DOWNLOADS") is True
    assert calls == ["set:ALLOW_DOWNLOADS=True", "create_window", "start"]


def test_open_window_explicitly_shows_window_after_gui_start(data_dir, monkeypatch):
    """Windows 继承 SW_HIDE 时, GUI 就绪后应显式显示主窗口。"""
    shown = []
    window = SimpleNamespace(show=lambda: shown.append(True))
    fake = SimpleNamespace(
        settings={},
        create_window=lambda *args, **kwargs: window,
        start=lambda func=None, **kwargs: func() if func else None,
    )
    monkeypatch.setitem(sys.modules, "webview", fake)

    desktop._open_window("http://127.0.0.1:3018")
    assert shown == [True]


def test_open_window_preserves_browser_storage_across_restarts_and_ports(data_dir, monkeypatch):
    starts = []
    fake = SimpleNamespace(
        settings={},
        create_window=lambda *args, **kwargs: SimpleNamespace(show=lambda: None),
        start=lambda *args, **kwargs: starts.append(kwargs),
    )
    monkeypatch.setitem(sys.modules, "webview", fake)

    desktop._open_window("http://127.0.0.1:3018")
    desktop._open_window("http://127.0.0.1:3019")

    assert all(call.get("private_mode") is False for call in starts)
    assert starts[0]["storage_path"] == starts[1]["storage_path"] == str(data_dir / "webview")
    assert (data_dir / "webview").is_dir()


def test_open_window_does_not_clear_existing_browser_profile(data_dir, monkeypatch):
    profile = data_dir / "webview"
    profile.mkdir()
    marker = profile / "existing-profile-data"
    marker.write_bytes(b"keep me")
    fake = SimpleNamespace(
        settings={},
        create_window=lambda *args, **kwargs: SimpleNamespace(show=lambda: None),
        start=lambda *args, **kwargs: None,
    )
    monkeypatch.setitem(sys.modules, "webview", fake)

    desktop._open_window("http://127.0.0.1:3018")

    assert marker.read_bytes() == b"keep me"


# ---------- 关窗 → 进程真的退出 ----------
# 用户反馈 (2026-11-25): 「关闭应用程序并没有退出」。根因不是窗口没关, 而是
# 解释器收尾时 join 非 daemon 线程 (ThreadPoolExecutor worker) 和 spawn 子进程
# (回测/挖掘, daemon=False), 长任务在跑时那个 join 永不返回。

def test_stop_backend_signals_uvicorn_and_waits_for_shutdown(monkeypatch):
    """关窗后必须置 should_exit 并等 lifespan shutdown 跑完 (不然锁/调度器不收尾)。"""
    server = SimpleNamespace(should_exit=False)
    joined: list[float | None] = []

    class _Thread:
        def join(self, timeout=None):
            joined.append(timeout)

        def is_alive(self):
            return False

    monkeypatch.setattr(desktop, "_backend_server", server)
    desktop._stop_backend(_Thread())

    assert server.should_exit is True
    assert joined == [desktop._BACKEND_STOP_TIMEOUT]


def test_stop_backend_warns_when_shutdown_hangs(monkeypatch, caplog):
    """后端 10s 内没停下要留日志 (否则用户只看到「关不掉」, 无从排查)。"""
    import logging

    server = SimpleNamespace(should_exit=False)

    class _StuckThread:
        def join(self, timeout=None):
            pass

        def is_alive(self):
            return True

    monkeypatch.setattr(desktop, "_backend_server", server)
    with caplog.at_level(logging.WARNING, logger="app.desktop"):
        desktop._stop_backend(_StuckThread())

    assert server.should_exit is True
    assert "未收尾" in caplog.text


def test_hard_exit_kills_process_when_thread_pool_worker_would_block_exit(monkeypatch):
    """ThreadPoolExecutor worker 是非 daemon 线程 —— 它不收尾, 解释器退出就会挂住。

    这里让 worker 永远不结束, 断言 _hard_exit_if_stragglers 走 os._exit 而不是
    傻等 (真实场景: 全市场同步/回测正在 worker 里跑)。
    """
    stop = threading.Event()
    worker = threading.Thread(target=stop.wait, name="ThreadPoolExecutor-0_0")
    worker.start()
    exits: list[int] = []
    monkeypatch.setattr(desktop.os, "_exit", lambda code: exits.append(code))
    monkeypatch.setattr(desktop.logging, "shutdown", lambda: None)
    try:
        desktop._hard_exit_if_stragglers(grace=0.05)
    finally:
        stop.set()
        worker.join(timeout=5)

    assert exits == [0], "还有非 daemon 线程在跑时必须硬退出, 否则进程挂在后台"


def test_hard_exit_defers_to_normal_teardown_when_threads_finish(monkeypatch):
    """后台线程能在宽限期内自己结束时不抢跑 os._exit, 让 atexit 正常收尾。"""
    stop = threading.Event()
    worker = threading.Thread(target=stop.wait, name="ThreadPoolExecutor-0_1")
    worker.start()
    exits: list[int] = []
    monkeypatch.setattr(desktop.os, "_exit", lambda code: exits.append(code))
    threading.Timer(0.05, stop.set).start()
    try:
        desktop._hard_exit_if_stragglers(grace=5.0)
    finally:
        stop.set()
        worker.join(timeout=5)

    assert exits == []


# ---------- 应用内自动更新 ----------
# 需求 (2026-11-25): 「下载不要用户保存, 更新自动下载, 然后自动关闭程序, 自动启动安装」。
# 下载/校验/停后端/起安装器都在桌面进程里做, 前端只点一次「立即更新」。

def test_split_urls_supports_multiple_endpoints():
    """R2 公开域名是随机子域, 换域名时老客户端只有旧地址 → 清单可放多条兜底。"""
    assert desktop._split_urls("https://a/x.exe, https://b/x.exe  https://c/x.exe") == [
        "https://a/x.exe", "https://b/x.exe", "https://c/x.exe",
    ]
    assert desktop._split_urls("") == []


def test_download_installer_writes_into_temp_and_verifies_sha256(monkeypatch, tmp_path):
    """下载落临时目录 (无「另存为」), 并按清单的 size/sha256 校验, 损坏即失败。"""
    import hashlib
    import io

    payload = b"installer-bytes" * 100
    sha = hashlib.sha256(payload).hexdigest()

    class _Resp(io.BytesIO):
        headers = {"content-length": str(len(payload))}  # noqa: RUF012

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_path))
    monkeypatch.setattr(
        "urllib.request.urlopen", lambda url, timeout=0: _Resp(payload)
    )

    path = desktop._download_installer(
        ["https://r2.example.com/x.exe"], "0.3.37", sha, len(payload)
    )

    assert path.parent == tmp_path / "HQuant-Update" / "0.3.37"
    assert path.name == "HQuant-Setup-x64-0.3.37.exe"
    assert path.read_bytes() == payload


def test_download_installer_rejects_corrupted_payload(monkeypatch, tmp_path):
    """sha256 不符必须失败 —— 否则装到一半才炸, 用户会以为更新把软件弄坏了。"""
    import io

    class _Resp(io.BytesIO):
        headers = {"content-length": "4"}  # noqa: RUF012

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_path))
    monkeypatch.setattr("urllib.request.urlopen", lambda url, timeout=0: _Resp(b"abcd"))

    with pytest.raises(ValueError, match="sha256"):
        desktop._download_installer(["https://r2.example.com/x.exe"], "0.3.37", "deadbeef", 4)


def test_download_installer_falls_back_to_next_url(monkeypatch, tmp_path):
    """第一个地址失败要换下一个, 而不是直接报错 (域名迁移期的关键)。"""
    import io

    payload = b"ok"

    class _Resp(io.BytesIO):
        headers = {"content-length": "2"}  # noqa: RUF012

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    tried: list[str] = []

    def _urlopen(url, timeout=0):
        tried.append(url)
        if "old-domain" in url:
            raise OSError("dns failure")
        return _Resp(payload)

    monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_path))
    monkeypatch.setattr("urllib.request.urlopen", _urlopen)

    path = desktop._download_installer(
        ["https://old-domain/x.exe", "https://new-domain/x.exe"], "0.3.37", "", 2
    )

    assert tried == ["https://old-domain/x.exe", "https://new-domain/x.exe"]
    assert path.read_bytes() == payload


def test_update_bridge_downloads_then_stops_backend_and_launches_installer(
    monkeypatch, tmp_path
):
    """更新桥: 下载 → 停后端 → 起安装器, 成功后由定时器强退进程。"""
    installer = tmp_path / "HQuant-Setup-x64-0.3.37.exe"
    installer.write_bytes(b"x")
    stopped: list[bool] = []
    launched: list = []
    exited: list[float] = []

    monkeypatch.setattr(desktop, "_can_auto_install", lambda: True)
    monkeypatch.setattr(desktop, "_split_urls", lambda raw: ["https://r2/x.exe"])
    monkeypatch.setattr(desktop, "_download_installer", lambda *a, **k: installer)
    monkeypatch.setattr(
        desktop, "_stop_backend", lambda thread=None, hard_exit=True: stopped.append(hard_exit)
    )
    monkeypatch.setattr(desktop, "_launch_installer", lambda exe: launched.append(exe))
    monkeypatch.setattr(
        desktop.threading, "Timer", lambda delay, fn: type(
            "_T", (), {"start": lambda self: exited.append(delay)}
        )()
    )

    result = desktop._UpdateBridge().install_update("https://r2/x.exe", "0.3.37", "", 0)

    assert result == {"ok": True, "error": ""}
    assert launched == [installer]
    # 更新路径不能提前 os._exit (还要起安装器), 所以 hard_exit=False
    assert stopped == [False]
    assert exited == [1.0]


def test_update_bridge_reports_download_failure_without_quitting(monkeypatch):
    """下载失败要回传原因, 绝不能退出进程 (用户还在用着旧版本)。"""
    launched: list = []
    monkeypatch.setattr(desktop, "_can_auto_install", lambda: True)
    monkeypatch.setattr(desktop, "_split_urls", lambda raw: ["https://r2/x.exe"])

    def _boom(*a, **k):
        raise OSError("network down")

    monkeypatch.setattr(desktop, "_download_installer", _boom)
    monkeypatch.setattr(desktop, "_launch_installer", lambda exe: launched.append(exe))

    bridge = desktop._UpdateBridge()
    result = bridge.install_update("https://r2/x.exe", "0.3.37", "", 0)

    assert result["ok"] is False
    assert "network down" in result["error"]
    assert launched == []
    assert bridge.last_error() == "network down"


def test_update_bridge_is_not_reentrant(monkeypatch):
    """连点两次「立即更新」不能并发下载两个安装包。"""
    monkeypatch.setattr(desktop, "_can_auto_install", lambda: True)
    bridge = desktop._UpdateBridge()
    bridge._busy = True

    result = bridge.install_update("https://r2/x.exe", "0.3.37", "", 0)

    assert result == {"ok": False, "error": "更新正在进行中"}


def test_download_installer_rejects_path_traversal_version(monkeypatch, tmp_path):
    """version 来自清单并直接拼进路径 —— "../.." 能把安装包写到任意位置。"""
    monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_path))
    with pytest.raises(ValueError, match="版本号不合法"):
        desktop._download_installer(["https://r2/x.exe"], "../../evil")


def test_download_installer_rejects_non_http_scheme(monkeypatch, tmp_path):
    """urlopen 认 file://; 清单被篡改时不能变成读本地任意文件。"""
    monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_path))
    with pytest.raises(ValueError, match="协议"):
        desktop._download_installer(["file:///etc/passwd"], "0.3.37")


def test_auto_install_is_windows_only():
    """macOS 的产物是 .dmg, 没有静默安装 —— 起它再退出进程会让界面凭空消失。"""
    import sys as _sys

    original = _sys.platform
    try:
        _sys.platform = "darwin"
        assert desktop._can_auto_install() is False
        _sys.platform = "win32"
        assert desktop._can_auto_install() is True
    finally:
        _sys.platform = original


def test_update_bridge_refuses_install_on_unsupported_platform(monkeypatch):
    """误调用时也必须拒绝: 退出进程却不安装, 用户以为软件坏了。"""
    monkeypatch.setattr(desktop, "_can_auto_install", lambda: False)
    called: list = []
    monkeypatch.setattr(desktop, "_download_installer", lambda *a, **k: called.append(1))

    result = desktop._UpdateBridge().install_update("https://r2/x.exe", "0.3.37")

    assert result["ok"] is False
    assert "不支持应用内自动更新" in result["error"]
    assert called == []
