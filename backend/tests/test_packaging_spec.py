"""hquant.spec 打包不变量 — 运行时动态发现的模块必须随冻结产物发布 (issue #459)。

app/extensions/loader.py 用 importlib.import_module + pkgutil.iter_modules 在
运行时发现 app/custom/*, 两条链路对 PyInstaller 静态分析都不可见; hquant.spec
未声明时冻结产物里扩展静默消失(桌面版 AI 对话助手 405, 且无任何日志)。
锁定双声明契约: 
  - hiddenimports(collect_submodules) 进 PYZ —— 保 import_module 可导入; 
  - datas 落盘到 app/custom —— frozen 下 app.__path__ 解析到 _internal/app, 
    保 pkgutil.iter_modules 可枚举(与 fuyao 插件同一处理方式)。
"""

import re
from pathlib import Path
from xml.etree import ElementTree

REPO_ROOT = Path(__file__).resolve().parents[2]
SPEC_TEXT = (REPO_ROOT / "packaging" / "hquant.spec").read_text(encoding="utf-8")
# 只断言代码行, 避免被注释里的字样误判为已声明
SPEC_CODE = "\n".join(
    line for line in SPEC_TEXT.splitlines() if not line.lstrip().startswith("#")
)


def test_spec_declares_custom_extensions():
    """app/custom/* 双声明: PYZ 可导入 + 磁盘可枚举, 缺一冻结后扩展静默消失。"""
    assert 'collect_submodules("app.custom")' in SPEC_CODE
    assert '"app/custom"' in SPEC_CODE


def test_spec_declares_builtin_plugin_fuyao():
    """fuyao 插件双声明不被回归 (同类动态发现问题, 见 spec 内注释)。"""
    assert "app.plugins.fuyao.provider" in SPEC_CODE
    assert "app/plugins/fuyao" in SPEC_CODE


def test_spec_declares_builtin_plugin_easytdx():
    """easytdx 清单 + provider 双声明不被回归 (与 fuyao 同一处理方式)。"""
    assert "app.plugins.easytdx.provider" in SPEC_CODE
    assert '"plugins" / "easytdx" / "plugin.yaml"' in SPEC_CODE  # 源路径必须指向现存目录


def test_spec_collects_easytdx_runtime():
    """内置数据源的运行库必须进冻结包。

    app/plugins/easytdx/client.py 用 __import__("easy_tdx.mac.client") 动态加载,
    静态分析看不见 —— 不显式收集则桌面端内置数据源不可用, 而冻结包内装不了依赖
    (用户只会看到一个点了必失败的「安装依赖」按钮)。
    """
    assert 'collect_all("easy_tdx"' in SPEC_CODE


def test_installer_launches_desktop_only_once():
    """重复 postinstall 条目会同时拉起两个桌面后端。"""
    text = (REPO_ROOT / "packaging" / "hquant.iss").read_text(encoding="utf-8")
    section = text.split("[Run]", 1)[1].split("[UninstallRun]", 1)[0]
    launches = [
        line for line in section.splitlines()
        if line.startswith("Filename:") and "{#MyAppExeName}" in line
    ]
    assert len(launches) == 1


def test_installer_deploys_dotnet_config_beside_executable():
    """.NET 只读取 EXE 同目录的配置，放进 _internal 无法解除组件来源限制。"""
    installer = REPO_ROOT / "packaging" / "hquant.iss"
    text = installer.read_text(encoding="utf-8")
    exe_name = re.search(r'#define MyAppExeName\s+"([^"]+)"', text)
    assert exe_name is not None
    config_name = f"{exe_name.group(1)}.config"
    section = text.split("[Files]", 1)[1].split("[Icons]", 1)[0]
    entries = [
        line for line in section.splitlines()
        if line.startswith(f'Source: "{config_name}";')
    ]
    assert len(entries) == 1, "Windows 安装包必须显式携带 EXE 配置文件"
    assert 'DestDir: "{app}";' in entries[0]
    assert "ignoreversion" in entries[0]

    config = ElementTree.parse(installer.parent / config_name)
    assert config.getroot().tag == "configuration"
    setting = config.find("./runtime/loadFromRemoteSources")
    assert setting is not None
    assert setting.get("enabled") == "true"


def _pascal_function(code: str, name: str) -> str:
    """截出 [Code] 里名为 name 的函数体 (到下一个顶格 function/procedure 为止)。

    不能只 split("end;") —— 函数体内有 if/for 的 end;, 会提前截断。
    """
    start = code.index(f"function {name}(")
    rest = code[start + 1:]
    end = len(rest)
    for marker in ("\nfunction ", "\nprocedure "):
        idx = rest.find(marker)
        if idx != -1:
            end = min(end, idx)
    return rest[:end]


def test_installer_kills_running_app_before_writing_files():
    """升级时应用还在跑 → _internal 里的文件被占用, 替换失败。

    Inno 自带的 CloseApplications 靠 Restart Manager 请求关闭并等待,
    应用不理会时照样继续往下写。必须在写文件前自己 taskkill。
    顺序也重要: PrepareToInstall 走的是 [Code], 而 /F 兜底必须先尝试
    正常关闭 (不带 /F), 否则正在落盘的 parquet 会被打断。
    """
    text = (REPO_ROOT / "packaging" / "hquant.iss").read_text(encoding="utf-8")
    code = text.split("[Code]", 1)[1]

    assert "function PrepareToInstall(" in code, "缺少安装前钩子, 应用运行时会装不上"
    hook = _pascal_function(code, "PrepareToInstall")
    assert "taskkill /IM {#MyAppExeName}" in hook, "先发正常关闭请求, 不能用 /F 直接杀"
    assert "taskkill /F /IM {#MyAppExeName}" in hook, "正常关闭失败要有 /F 兜底"
    assert hook.index("taskkill /IM") < hook.index("taskkill /F"), "强行结束必须在正常关闭之后"

    # CloseApplications 交给 Restart Manager, 与本钩子重复且会先弹窗询问
    assert "CloseApplications=no" in text


def test_installer_force_kill_is_not_gated_on_soft_kill_success():
    """回归: 0.3.35 装 0.3.35 时应用根本没被关掉。

    taskkill 不加 /F 时, 找不到可见窗口会返回 128。上一版把 /F 兜底写在
    `if Exec(软杀) and (ResultCode = 0) then` 里, 于是那个分支永远进不去,
    一次 /F 都没执行过。这里断言 /F 的执行不受软杀返回码约束。
    """
    text = (REPO_ROOT / "packaging" / "hquant.iss").read_text(encoding="utf-8")
    code = text.split("[Code]", 1)[1]
    body = _pascal_function(code, "PrepareToInstall")

    # 防回归的核心: 强杀不能挂在软杀的返回值上。
    # 上一版的写法是 `if Exec(...软杀...) and (ResultCode = 0) then begin ... /F ... end;`
    assert "and (ResultCode = 0) then" not in body, (
        "强制结束又被软杀的返回码门控了 —— taskkill 不带 /F 找不到窗口时返回 128, "
        "这个分支永远不会进"
    )
    # 强杀必须存在且出现在软杀之后
    assert "taskkill /F /IM {#MyAppExeName}" in body
    assert body.index("taskkill /IM") < body.index("taskkill /F /IM")

    # 必须有独立于 taskkill 返回码的运行检测
    assert "function IsAppRunning" in code
    assert "tasklist" in code

    # 确认真的关不掉时要中止安装并说明原因, 不能静默失败
    assert "Result := " in body


def test_installer_relaunches_app_after_silent_update():
    """应用内自动更新以 /SILENT 跑安装器, 装完必须自动拉起新版本。

    0.3.37 前 [Run] 带 skipifsilent: 静默安装装完什么都不做, 用户点完
    「立即更新」后应用消失, 得自己去找图标 —— 与「自动重启」的承诺不符。
    """
    text = (REPO_ROOT / "packaging" / "hquant.iss").read_text(encoding="utf-8")
    run_section = text.split("[Run]", 1)[1].split("[UninstallRun]", 1)[0]
    launches = [line for line in run_section.splitlines() if line.startswith("Filename:")]
    assert len(launches) == 1
    assert "skipifsilent" not in launches[0], (
        "静默安装 (应用内自动更新) 装完后不会启动新版本"
    )
    assert "shellexec" in launches[0]
