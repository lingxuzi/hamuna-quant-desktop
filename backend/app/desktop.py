"""桌面客户端入口 — uvicorn 后台服务 + pywebview 桌面窗口。

运行方式:
  开发模式: python -m app.desktop  (需 pip install pywebview)
  打包后:   双击可执行文件即可

职责:
  1. 单实例锁 — 已运行则聚焦已有窗口并退出
  2. 选可用端口 — 从 settings.port 起, 被占则递增
  3. 后台线程起 uvicorn (仅监听 127.0.0.1, 不暴露外网)
  4. 主线程起 pywebview 窗口渲染前端
  5. 窗口关闭 → 优雅停止 uvicorn → 进程退出

不含: 业务逻辑、配置持久化、监控告警 (全在 app.main 里)。
"""
from __future__ import annotations

import contextlib
import errno
import html
import logging
import os
import re
import socket
import sys
import tempfile
import threading
import time
import traceback
from pathlib import Path
from typing import BinaryIO

logger = logging.getLogger(__name__)

_APP_NAME = "H-Quant"
_BASE_PORT = 3018
_PORT_PROBE_RANGE = 50  # 从 3018 起最多试 50 个端口
_instance_lock: BinaryIO | None = None

# 窗口关闭后要显式让后端停下并等它收尾 (见 _stop_backend)。
# 后端线程本身是 daemon, 但解释器退出时仍会 join 非 daemon 的线程池 worker
# 和 spawn 子进程, 长任务在跑时窗口关了进程还在。
_backend_server = None  # type: ignore[var-annotated]
_BACKEND_STOP_TIMEOUT = 10.0


def _failed_html(reason: str | None = None) -> str:
    """启动失败/超时的整页提示 (后端没起来时窗口也要能说明白)。

    reason=None → 沿用「启动超时」原文案; 传 reason → 换成具体失败原因,
    文案里的换行按 <br> 渲染, 用户照指引操作即可自助解决。
    """
    if reason is None:
        title = "H-Quant 启动超时"
        body = "<p>后端未在预期时间内就绪，请关闭窗口后重新打开。</p>"
    else:
        title = "H-Quant 启动失败"
        body = f"<p>{html.escape(reason).replace(chr(10), '<br>')}</p>"
    return f"""<!doctype html><html><head><meta charset="utf-8">
<style>
  html,body{{margin:0;height:100%;background:#0d1117;color:#c9d1d9;
    font-family:"Microsoft YaHei",system-ui,sans-serif;
    display:flex;align-items:center;justify-content:center}}
  .box{{text-align:center;max-width:460px}}
  h1{{font-size:18px;font-weight:500;margin:0 0 12px;color:#f85149}}
  p{{font-size:13px;color:#8b949e;margin:0 0 6px;line-height:1.7}}
</style></head><body><div class="box">
  <h1>{title}</h1>
  {body}
  <p>反复失败时查看日志：data\\logs\\desktop.log</p>
</div></body></html>"""


def _ensure_data_dir_writable() -> None:
    """确保用户数据目录可写 (lifespan 会创建子目录, 这里只验证根目录)。

    data_dir 在 frozen 模式下指向用户目录 (见 config.py), 非可写会导致
    DuckDB 视图 / parquet 落盘全失败。提前失败胜过启动后乱报错。
    """
    from app.config import settings

    data_root = settings.data_dir
    try:
        data_root.mkdir(parents=True, exist_ok=True)
        probe = data_root / ".write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
    except Exception as e:  # noqa: BLE001
        logger.error("数据目录不可写, 桌面版无法运行: %s (%s)", data_root, e)
        raise


def _precheck_mining_lock() -> str | None:
    """启动前预检挖掘锁。返回 None = 无冲突; 返回 str = 冲突原因 (仅日志)。

    app.main 的 lifespan 会抢 data_dir/.mining_process_lock; 被别的后端
    实例 (panel 模式、隧道后端或残留进程) 占住时, 现在 lifespan 只降级
    (挖掘入口 409, 面板可用, 挖掘页提供强制释放), 这里提前留一条日志
    方便排查。试抢成功立即释放, 真正的持有仍由 lifespan 负责。
    """
    from app.config import settings
    from app.services.mining_process_lock import (
        MiningProcessLock,
        MiningProcessLockError,
    )

    lock = MiningProcessLock(settings.data_dir)
    try:
        lock.acquire()
    except MiningProcessLockError:
        return (
            "检测到另一个后端实例正在占用数据目录\n"
            "(panel 模式、云隧道或残留进程)\n"
            "请先停止它再打开桌面版：\n"
            "1. 在任务管理器结束残留的 python / uvicorn 进程\n"
            "2. 或重启电脑\n"
            "完成后重新打开本应用"
        )
    lock.release()
    return None


def _acquire_single_instance() -> bool:
    """单实例锁。已运行返回 False (本进程应退出), 否则 True。

    文件内容仅供诊断, 实际以系统文件锁为准。进程崩溃后系统会自动释放,
    避免残留 PID 被其他进程复用时永远误判为「已运行」。
    """
    from app.config import settings

    global _instance_lock
    if _instance_lock is not None:
        return False

    lock_path = settings.data_dir / ".desktop.lock"
    stream = lock_path.open("a+b")
    try:
        os.set_inheritable(stream.fileno(), False)
        try:
            if os.name == "nt":
                import msvcrt

                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                raise
            logger.warning("检测到已有桌面实例持锁, 本进程退出")
            return False

        stream.seek(0)
        stream.truncate()
        stream.write(str(_current_pid()).encode("ascii"))
        stream.flush()
        _instance_lock = stream
        return True
    finally:
        if _instance_lock is not stream:
            stream.close()


def _release_single_instance() -> None:
    global _instance_lock
    stream, _instance_lock = _instance_lock, None
    if stream is not None:
        # 不 unlink: 删除再重建会让并发启动的进程锁住不同文件, 同时通过检查。
        stream.close()


def _guard_streams() -> None:
    """windowed 模式 (console=False) 下守护 stdout/stderr。

    PyInstaller console=False 用 runw.exe 启动器, 不分配控制台, 此时 sys.stdout /
    sys.stderr 可能为 None (或底层句柄无效)。后果:
      - app/__init__.py 早期 reconfigure 遇 None 虽有 hasattr 保护, 但若是个「写入即崩」
        的伪 stream 对象, reconfigure 会成功、后续写却崩;
      - logging.basicConfig() 默认建 StreamHandler(sys.stderr), stderr 为 None 时
        首次写日志调 None.write() 抛 AttributeError, 此时往往在导入早期,
        Python 异常处理未就绪 → 进程直接闪退, try/except 都拦不住。

    修法: console=False 下把 stdout/stderr 换成丢弃写入的空对象 (devnull),
    让 logging / reconfigure / 任何 print 都安全落地。console=True 不动 (有真控制台)。
    """
    class _NullStream:
        """丢弃所有写入的空流 (替代 None 的 stdout/stderr)。"""
        def write(self, _s): return 0
        def flush(self): pass
        def reconfigure(self, *a, **kw): pass
        def isatty(self): return False
        def fileno(self): raise OSError("no fileno")

    # 仅在 stdout/stderr 缺失或不可写时替换 (有真控制台时保持原样)
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is None:
            setattr(sys, name, _NullStream())


def _setup_logging() -> None:
    """配置日志落盘到 data/logs/desktop.log (10MB×5 切割)。

    背景: spec 里 console=False (桌面应用不弹黑窗), 导致 logging 默认输出的
    stderr 被吞掉 —— 启动期任何异常用户都看不到, 表现为「双击一闪退出、查无日志」。
    这里经共享的 attach_rotating_log 追加轮转文件 handler, 事后可查; 桌面版
    长期挂机, 不切割的追加日志会无限增长 (用户反馈的磁盘隐患)。
    旧版的 data/desktop.log 由 attach_rotating_log 一次性迁入 data/logs/。
    """
    from app.logfile import attach_rotating_log

    attach_rotating_log("desktop.log", max_mb=10, backup_count=5)


def _show_crash(title: str, text: str) -> None:
    """崩溃时弹原生 MessageBox 提示用户 (仅 Windows)。

    console=False 下用户看不到任何输出, 崩溃时弹一个原生错误框, 让用户至少
    知道「程序崩了 + 原因」, 并可截图反馈。非 Windows 用日志降级, 不调 ctypes。

    ctypes 是 Python 标准库, 不引入新依赖; MessageBoxW 是 Unicode 版本 (W 后缀),
    支持中文标题/正文。0x10 = MB_ICONERROR (红色错误图标)。
    """
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(0, text, title, 0x10)
        except Exception as e:  # noqa: BLE001
            logger.error("弹框失败 (已写日志文件): %s", e)
    else:
        logger.error("%s: %s", title, text)


def _pid_alive(pid: int) -> bool:
    """检查指定 PID 的进程是否存活。"""
    import os

    if os.name == "nt":
        # Windows 的 os.kill 没有 POSIX 探测语义: 除 CTRL_C/CTRL_BREAK 外
        # 任何信号值 (含 0) 都是 TerminateProcess 无条件杀进程。存活探测
        # 必须用 OpenProcess + GetExitCodeProcess, 否则单实例检查会误杀
        # 正在运行的实例 (例如探测子进程重启自身时)。
        import ctypes

        STILL_ACTIVE = 259
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        try:
            exit_code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return False
            return exit_code.value == STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    else:
        try:
            os.kill(pid, 0)  # signal 0 = 探测存活, 不实际发信号
            return True
        except OSError:
            return False


def _current_pid() -> int:
    import os

    return os.getpid()


def _find_free_port(start: int, count: int = _PORT_PROBE_RANGE) -> int:
    """从 start 起找第一个可用端口。全部被占则返回 start (交给 uvicorn 报错)。"""
    for port in range(start, start + count):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    return start


def _split_urls(raw: str) -> list[str]:
    """清单里的 urls 字段 → 逐个尝试的地址列表。

    发布流水线的 R2 公开域名是随机子域 (pub-<hash>.r2.dev), 换桶/换域名时
    老客户端手里只有旧地址; 清单里放多条 (新域名在前, 旧域名兜底) 让存量
    客户端至少还能拿到安装包。任一成功即止, 不要求全部可达。
    """
    return [u.strip() for u in (raw or "").replace(",", " ").split() if u.strip()]


def _download_installer(
    urls: list[str], version: str, sha256: str = "", size: int = 0
) -> Path:
    """把安装包下到临时目录, 返回本地路径。失败抛异常 (由调用方回传前端)。

    用户不需要选保存位置: 桌面版是「点更新 → 全自动」, 落地路径固定为
    %TEMP%\\HQuant-Update\\<version>\\HQuant-Setup-x64-<version>.exe。

    先试 urls 里每个地址, 任一成功即止。下完按清单的 size/sha256 校验 ——
    截断或下错文件会装到一半才炸, 用户只会觉得「更新把软件弄坏了」。
    """
    import hashlib
    import urllib.parse
    import urllib.request

    if not urls:
        raise ValueError("更新清单里没有可用的下载地址")
    # version 来自清单 (经前端传回), 会直接拼进文件路径 —— 只接受版本号字符,
    # 否则 "../.." 能把安装包写到任意位置。
    if not re.fullmatch(r"[0-9A-Za-z.\-_]{1,32}", version or ""):
        raise ValueError(f"版本号不合法: {version!r}")
    # urlopen 认 file:// 等协议; 更新地址只该是 http(s), 清单被篡改时不能
    # 变成「读本地任意文件」
    for url in urls:
        scheme = urllib.parse.urlparse(url).scheme.lower()
        if scheme not in ("http", "https"):
            raise ValueError(f"不支持的下载地址协议: {scheme or '(空)'}")

    target_dir = Path(tempfile.gettempdir()) / "HQuant-Update" / version
    target_dir.mkdir(parents=True, exist_ok=True)
    # 文件名带版本号: 安装器按 OutputBaseFilename=HQuant-Setup-<version> 自我识别,
    # 同名会撞上仍在被占用的旧安装包。
    exe = target_dir / f"HQuant-Setup-x64-{version}.exe"
    tmp = exe.with_suffix(".part")

    last: Exception | None = None
    for url in urls:
        try:
            logger.info("下载更新包: %s", url)
            digest = hashlib.sha256()
            total = 0
            with urllib.request.urlopen(url, timeout=30) as r, tmp.open("wb") as out:
                while True:  # 边下边写, 安装包 130MB+ 不能进内存
                    chunk = r.read(1 << 20)
                    if not chunk:
                        break
                    out.write(chunk)
                    digest.update(chunk)
                    total += len(chunk)
            if size and total != size:
                raise ValueError(f"安装包大小不符: 期望 {size}, 实际 {total}")
            if sha256 and digest.hexdigest().lower() != sha256.lower():
                raise ValueError("安装包 sha256 校验失败, 可能下载不完整或被篡改")
            tmp.replace(exe)
            logger.info("更新包就绪: %s (%d 字节)", exe, total)
            return exe
        except Exception as exc:  # noqa: BLE001
            last = exc
            with contextlib.suppress(Exception):
                tmp.unlink(missing_ok=True)
            logger.warning("下载失败 (%s): %s", url, exc)
    raise last or RuntimeError("下载失败")


def _can_auto_install() -> bool:
    """能否应用内自动安装。只有 Windows 的 Setup.exe 能静默装完自动重启。

    macOS 的产物是 .dmg —— 挂载后要用户把 .app 拖进应用程序目录, 没有静默
    安装这回事。在那里起 .dmg 再退出进程, 用户会看到「点了更新, 界面没了,
    什么也没装上」。非 Windows 一律退回浏览器下载。
    """
    return sys.platform == "win32"


def _launch_installer(exe: Path) -> None:
    """启动安装包并退出当前进程。

    安装器自己带 [Code] PrepareToInstall 结束残留进程, 但这里必须先把后端和
    子进程停干净再起它 —— 否则安装器杀进程的时机和 Python 收尾重叠, 会删到
    正在被写的文件。

    /SILENT: 用户已经点过「立即更新」, 再弹一遍向导是多余的; [Run] 段不带
    skipifsilent, 所以静默装完会自己拉起新版本。
    用 DETACHED_PROCESS 起, 安装器和本进程脱钩后才能删本进程的 exe。
    调用前须确认 _can_auto_install()。
    """
    import subprocess

    DETACHED_PROCESS = 0x00000008  # noqa: N806 — Win32 常量, 按原样写更好查
    CREATE_NEW_PROCESS_GROUP = 0x00000200  # noqa: N806
    subprocess.Popen(
        [str(exe), "/SILENT", "/NORESTART"],
        close_fds=True,
        creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
    )
    logger.info("安装器已启动, 本进程退出")


def _stop_backend(
    server_thread: threading.Thread | None = None, *, hard_exit: bool = True
) -> None:
    """请求后端优雅退出并等它收尾。窗口关掉后调用 (hard_exit=True)。

    不这么做的话: 窗口关了 main() 返回, 后端线程 (daemon) 被丢弃, 然后解释器
    收尾时对 ThreadPoolExecutor 的 worker 和 spawn 子进程 (回测/挖掘, daemon=False)
    逐个 join —— 两者都在跑长任务时无限期等待, 表现为「点了关闭, 进程还在
    任务管理器里」, 用户只能去结束进程。

    uvicorn 的 server.should_exit 会让它跑完 lifespan 的 shutdown (停调度器、
    释放挖掘锁、落盘), 这是必要的收尾, 因此先给 _BACKEND_STOP_TIMEOUT 秒。
    超时说明有任务卡住 (如 lifespan 里等挖掘锁), 此时不能再等, 直接杀子进程
    退出 —— 用户点了关闭, 进程必须消失。

    hard_exit=False 用于自动更新: 停完后端还要起安装器, 不能提前 os._exit。
    """
    server = _backend_server
    if server is not None:
        server.should_exit = True
    if server_thread is not None:
        server_thread.join(timeout=_BACKEND_STOP_TIMEOUT)
        if server_thread.is_alive():
            logger.warning(
                "后端 %ss 内未收尾, 强制结束 (残留的后台任务不写入)", _BACKEND_STOP_TIMEOUT
            )
    # spawn 出来的回测/挖掘子进程 daemon=False, 解释器退出时会 join 它们;
    # 在这里先终结, 否则关窗后进程照样挂着。
    try:
        import multiprocessing as mp

        for child in mp.active_children():
            with contextlib.suppress(Exception):
                child.terminate()
                child.join(timeout=3.0)
    except Exception:  # 子进程表可能已被 teardown 改过, 失败不影响退出
        logger.exception("结束残留子进程失败")
    if hard_exit:
        _hard_exit_if_stragglers()


def _hard_exit_if_stragglers(grace: float = 3.0) -> None:
    """等非 daemon 后台线程一小会儿, 还有没结束的就硬退出进程。

    ThreadPoolExecutor 的 worker 是非 daemon 线程, 解释器退出时 concurrent.futures
    的 atexit 钩子会无条件 join 它们 (_python_exit)。全市场同步、回测这类长任务
    正在 worker 里跑时, 那个 join 永不返回 —— 窗口关了、后端也停了, 进程却一直
    挂在任务管理器里, 用户只能手动结束。

    这里先给 grace 秒让快到期的任务自己收尾 (正常的解释器收尾, atexit 照跑),
    超时才是真卡住: 直接 os._exit, 跳过 atexit。用户按下关闭的意图优先于
    「让任务跑完」—— 数据落盘的收尾已经在后端 shutdown 阶段完成。
    """
    current = threading.current_thread()
    deadline = time.monotonic() + grace
    stragglers = [
        t for t in threading.enumerate()
        if t is not current and not t.daemon and t.is_alive()
    ]
    for thread in stragglers:
        thread.join(timeout=max(0.0, deadline - time.monotonic()))
    alive = [t for t in stragglers if t.is_alive()]
    if alive:
        logger.warning(
            "仍有 %d 个后台线程未结束 (%s), 强制退出进程",
            len(alive), ", ".join(t.name for t in alive[:5]),
        )
        logging.shutdown()
        os._exit(0)


def _run_uvmicorn(port: int, ready_event: threading.Event) -> None:
    """后台线程: 启动 uvicorn 服务。ready_event 在线程退出时置位 (通知主线程)。"""
    import uvicorn

    try:
        # 延迟 import app, 确保配置层已就绪 (frozen 检测在 config.py 导入时完成)
        # 放进 try: app.main 模块导入 (含 app.api.* 一长串 import) 若失败,
        # 异常必须落到 except 记录, 否则主线程只看到「后端超时」而查无 traceback。
        from app.main import app
    except Exception:
        logger.exception("后端模块导入失败 (app.main 或其依赖)")
        ready_event.set()
        return

    config = uvicorn.Config(
        app,
        host="127.0.0.1",  # 仅本机, 不暴露外网 (桌面版无需远程访问)
        port=port,
        log_level="info",
        access_log=False,    # 桌面版不需要访问日志
        loop="auto",
        # log_config=None: uvicorn 默认给 uvicorn.* 挂独立 console handler 且
        # propagate=False, windowed 模式下 stderr 已被守护替换, lifespan 启动
        # 失败的 "Application startup failed" 永远到不了 desktop.log (失败页
        # 承诺的排查入口)。置 None 让 uvicorn 日志走 root 的 FileHandler 落盘。
        log_config=None,
    )
    server = uvicorn.Server(config)
    # 暴露给 _stop_backend: 窗口关闭时要主动置 should_exit, 后端才会跑完
    # lifespan 的 shutdown 再退出, 而不是被 daemon 线程直接丢弃。
    global _backend_server
    _backend_server = server

    # 线程结束时通知主线程 (无论正常退出还是异常)
    def _signal_done(*exc):
        ready_event.set()
    server.config.callback_notify = None  # 不用 notify 机制

    try:
        server.run()
    except Exception:
        # server.run() 内部跑 lifespan 启动链, 任一步抛异常都会冒到这里。
        # 不捕获则线程静默死亡, 主线程 _wait_for_server 傻等满 60s 后报「超时」,
        # 真正的崩溃原因 (如某个原生库加载失败 / 缺 hidden import) 永远看不到。
        logger.exception("uvicorn 后端启动/运行失败")
    finally:
        ready_event.set()


def _wait_for_server(
    port: int, timeout: float = 60.0, done_event: threading.Event | None = None
) -> bool:
    """轮询 health 接口直到后端就绪或超时。

    比 monkey-patch uvicorn 内部方法更健壮, 不依赖版本内部实现。

    done_event 置位表示后端线程已退出 (lifespan 失败被 uvicorn 吞掉时不向
    调用方抛异常, 只表现为 server.run() 正常返回) —— 此时不可能出现就绪,
    立即返回 False, 不再傻等满 timeout 把「启动失败」伪装成「超时」。
    """
    import urllib.error
    import urllib.request

    url = f"http://127.0.0.1:{port}/health"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if done_event is not None and done_event.is_set():
            return False
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                if r.status == 200:
                    return True
        except (urllib.error.URLError, ConnectionError, OSError):
            pass
        time.sleep(0.5)
    return False


class _UpdateBridge:
    """暴露给前端 window.pywebview.api 的更新入口。

    前端 updateCheck 检测到新版本 → 用户点「立即更新」→ 这里下载安装包,
    校验 sha256/size, 然后停后端、起安装器、退出进程。前端不再出现保存
    文件对话框, 也不需要用户自己双击安装包。

    只在桌面版存在 (浏览器里没有 pywebview 对象), 所以前端必须先探测。
    """

    def __init__(self) -> None:
        self._busy = False
        self._error = ""

    def can_auto_install(self) -> bool:
        """前端据此决定显示「立即更新」还是普通下载链接 (浏览器里永远 false)。"""
        return _can_auto_install()

    def install_update(self, urls: str, version: str, sha256: str = "", size: int = 0) -> dict:
        """下载并安装指定版本。返回 {ok, error}; 成功时进程会自行退出。"""
        if not _can_auto_install():
            # 前端理论上不会调到这里 (先问 can_auto_install), 兜住误调用:
            # 在这里退出进程又不装, 用户会以为软件被更新弄坏了。
            return {"ok": False, "error": "当前平台不支持应用内自动更新"}
        if self._busy:
            return {"ok": False, "error": "更新正在进行中"}
        self._busy = True
        try:
            exe = _download_installer(_split_urls(urls), version, sha256, int(size or 0))
        except Exception as exc:
            self._busy = False
            self._error = str(exc)
            logger.exception("更新包下载失败")
            return {"ok": False, "error": str(exc)}

        # 下载完成 → 停后端 (让 lifespan 收尾、释放数据目录锁), 再起安装器。
        # 安装器随后会调用自己的 PrepareToInstall 兜底结束残留进程。
        logger.info("更新包下载完成, 停止后端并启动安装器")
        try:
            _stop_backend(_server_thread_ref[0], hard_exit=False)
        except Exception:
            logger.exception("停止后端失败, 继续启动安装器")

        try:
            _launch_installer(exe)
        except Exception as exc:
            self._busy = False
            logger.exception("启动安装器失败")
            return {"ok": False, "error": f"无法启动安装程序: {exc}"}

        # 安装器已脱离本进程, 现在必须真的退出 (否则文件被占, 安装失败)。
        # 给前端一点时间收到 {ok: True} 再退出 —— 前端点完就显示「正在重启」。
        threading.Timer(1.0, _force_exit).start()
        return {"ok": True, "error": ""}

    def last_error(self) -> str:
        return self._error


def _force_exit() -> None:
    """更新流程专用: 跳过所有收尾, 直接结束进程。"""
    logger.info("安装器已启动, 进程退出")
    with contextlib.suppress(Exception):
        logging.shutdown()
    os._exit(0)


# 主线程持有的后端线程, 供 _UpdateBridge 在更新时停后端 (类在 main 之前定义)
_server_thread_ref: list[threading.Thread | None] = [None]


def _open_window(url: str, server_thread: threading.Thread | None = None) -> None:
    """主线程: 用 pywebview 打开桌面窗口。"""
    import webview  # type: ignore[import-not-found]

    from app.config import settings

    _server_thread_ref[0] = server_thread
    # 默认隐私模式会在退出后丢弃 localStorage；用户目录中的固定 profile 才能跨重启保留。
    storage_path = settings.data_dir / "webview"
    storage_path.mkdir(parents=True, exist_ok=True)
    # pywebview 5.0 起默认取消所有文件下载 (Edge WebView2 / WKWebView 一致):
    # 配置备份导出、回测 CSV 等 <a download> 直链会点击无反应, 必须在 start() 前显式开启。
    webview.settings["ALLOW_DOWNLOADS"] = True
    # 桥始终注入 (can_auto_install 在桥上回答平台能力), 前端据此决定显示
    # 「立即更新」还是普通下载链接 —— 迁移到别的平台不必改前端。
    window = webview.create_window(
        _APP_NAME,
        url,
        js_api=_UpdateBridge(),
        width=1440,
        height=900,
        min_size=(1024, 700),
        # 桌面版固定单窗口, 禁用外部浏览器跳转
        confirm_close=False,
    )
    # 继承隐藏启动标志时首次显示可能被抑制, GUI 就绪后再显式显示 (同失败页)。
    webview.start(
        lambda: window.show(), debug=False,
        private_mode=False, storage_path=str(storage_path),
    )


def _open_static_window(html_text: str) -> None:
    """只弹一个静态内容窗口 (预检失败等场景: 后端根本没起, 无需等待切换)。"""
    import webview  # type: ignore[import-not-found]

    window = webview.create_window(
        _APP_NAME,
        html=html_text,
        width=560,
        height=360,
        confirm_close=False,
    )
    # 启动器以 SW_HIDE 隐藏控制台, Windows 会把隐藏标志传给进程第一个窗口
    # (即本窗口), 故 GUI 循环就绪后显式 show (同 _open_window 的就绪处理)
    webview.start(lambda: window.show(), debug=False)


def main() -> int:
    """桌面客户端主入口。返回进程退出码。"""
    # 必须最先执行: console=False 下 stdout/stderr 可能无效, 不守护会导致
    # 后续 logging.basicConfig 创建的 StreamHandler 写日志时进程崩溃。
    _guard_streams()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    # 追加文件日志: console=False 下 stderr 被吞, 必须落盘否则查无对证。
    # 放在 basicConfig 之后 (它先建好 root logger 的格式), 这里只追加 handler。
    _setup_logging()

    instance_acquired = False
    try:
        _ensure_data_dir_writable()

        if not _acquire_single_instance():
            return 0
        instance_acquired = True

        # 撞挖掘锁只降级, 其他预检异常必须同样落盘、弹框并释放桌面锁。
        conflict = _precheck_mining_lock()
        if conflict is not None:
            logger.warning("启动预检: %s", conflict.replace("\n", " "))

        port = _find_free_port(_BASE_PORT)
        logger.info("桌面版后端将监听 127.0.0.1:%d", port)

        # 后台线程起 uvicorn
        ready = threading.Event()
        server_thread = threading.Thread(
            target=_run_uvmicorn, args=(port, ready), daemon=True,
            name="uvicorn",
        )
        server_thread.start()

        # 轮询 health 接口等后端就绪 (含 lifespan 初始化, 最多 60s);
        # ready 由后端线程 finally 置位 → 线程已退出时立刻返回, 不等满超时
        if not _wait_for_server(port, timeout=60.0, done_event=ready):
            reason = (
                "后端进程已提前退出，详情见 data\\logs\\desktop.log"
                if ready.is_set() else None
            )
            logger.error("后端未就绪 (reason=%s), 桌面版退出", reason)
            if not ready.is_set():
                frame = sys._current_frames().get(server_thread.ident)
                if frame is not None:
                    logger.error(
                        "启动超时，后端线程调用栈:\n%s",
                        "".join(traceback.format_stack(frame)),
                    )
            _open_static_window(_failed_html(reason))
            # 超时但线程还活着时, 后端仍在跑 (lifespan 可能卡在某一步),
            # 同样要停下来, 否则用户关掉失败页后进程照样赖在后台。
            _stop_backend(server_thread)
            return 1

        url = f"http://127.0.0.1:{port}"
        logger.info("打开桌面窗口: %s", url)
        _open_window(url, server_thread)

        # 窗口关闭 → 后端优雅收尾后进程退出。不能只靠 daemon 线程被回收:
        # 线程池 worker 与 spawn 子进程在解释器收尾时被 join, 长任务在跑时
        # 进程会一直挂在任务管理器里 (用户以为没退出)。
        logger.info("窗口已关闭, 停止后端")
        _stop_backend(server_thread)
        logger.info("桌面版退出")
        return 0
    except KeyboardInterrupt:
        return 0
    except Exception:
        # 顶层兜底: console=False 下未捕获异常会「一闪退出」且无任何反馈。
        # 写完整 traceback 到 data/desktop.log, 并弹原生 MessageBox 让用户截图反馈。
        # 必须排在 KeyboardInterrupt 之后 —— Exception 是基类, 在前会遮蔽它。
        logger.exception("桌面客户端启动失败")
        _show_crash("H-Quant 启动失败", traceback.format_exc())
        return 1
    finally:
        if instance_acquired:
            _release_single_instance()


if __name__ == "__main__":
    import multiprocessing

    multiprocessing.freeze_support()
    sys.exit(main())
