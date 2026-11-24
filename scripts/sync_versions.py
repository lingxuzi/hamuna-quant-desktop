"""版本号统一派生 — 从 frontend_v2/package.json 同步四处伴生版本标记。

版本号唯一权威 = frontend_v2/package.json (issue #513: 发版只改它时, VERSION /
backend/pyproject.toml / _FALLBACK_VERSION 三处伴生标记会漂移; Docker 部署
下运行时读不到 frontend_v2/package.json, 实际生效的是 _FALLBACK_VERSION, 于是
界面/日志/version API 显示旧版本)。

第四处 = release.yml 的发版输入默认值: workflow_dispatch 的 default 只能写
静态值, 无法从 package.json 求值 —— 不跟着同步就会漂移到旧版本, 而 prepare
作业会拿它跟 package.json 校验, 于是发版直接失败 (v0.3.17 vs v0.3.31)。

用法:
    python3 scripts/sync_versions.py            # 幂等写回四处 (无变化则零写入)
    python3 scripts/sync_versions.py --check    # 只校验不一致并退出码 1 (CI 可选)

release.yml / docker.yml 在检出后调用本脚本, 保证构建产物内版本一致;
本地改完 package.json 后也可手动跑一次。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "frontend_v2" / "package.json"


def pkg_version() -> str:
    return str(json.loads(PKG.read_text(encoding="utf-8"))["version"]).strip()


def _replace_once(text: str, pattern: str, repl: str, path: Path) -> str:
    new, n = re.subn(pattern, repl, text, count=1, flags=re.MULTILINE)
    if n != 1:
        raise SystemExit(f"{path}: 未找到版本标记 (pattern={pattern!r})")
    return new


def sync(ver: str, *, check: bool = False, root: Path = ROOT) -> list[str]:
    """幂等写回四处伴生标记, 返回发生变化的文件 (相对 root)。

    root 可指向别处 —— 自检脚本在临时目录里造一份假仓库来验证, 不碰真仓库。
    """
    # (路径, 匹配单行的正则, 写回的整行)
    targets = [
        (root / "VERSION", r"^[^\n]*\n?", f"v{ver}\n"),
        (root / "backend" / "pyproject.toml", r'^version = "[^"]*"\n', f'version = "{ver}"\n'),
        (root / "backend" / "app" / "__init__.py", r'^_FALLBACK_VERSION = "[^"]*"\n', f'_FALLBACK_VERSION = "{ver}"\n'),
        # release.yml 里唯一以 v 开头的 default 就是 version 输入的默认值
        # (platforms 是 'windows', prerelease 是 False) —— 按 v 前缀精确命中。
        (root / ".github" / "workflows" / "release.yml", r"^(?P<pre>\s*default: ')v[\d.]+(?P<post>')$", f"\\g<pre>v{ver}\\g<post>"),
    ]
    changed: list[str] = []
    for path, pattern, want_line in targets:
        old = path.read_text(encoding="utf-8")
        new = _replace_once(old, pattern, want_line, path)
        if new != old:
            changed.append(str(path.relative_to(root)))
            if not check:
                path.write_text(new, encoding="utf-8")
    return changed


def main() -> int:
    # Windows runner 的 stdout/stderr 默认 cp1252, 中文输出直接 UnicodeEncodeError
    # (CI 里杀掉整个作业) — 统一重配为 UTF-8, replace 兜底极端控制台代码页。
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="只报告不一致, 不写文件")
    args = parser.parse_args()

    ver = pkg_version()
    changed = sync(ver, check=args.check)
    if changed:
        action = "需要同步" if args.check else "已同步"
        print(f"{action} → v{ver}:")
        for rel in changed:
            print(f"  - {rel}")
        if args.check:
            return 1
    else:
        print(f"版本一致: v{ver} (VERSION / pyproject / _FALLBACK_VERSION / release.yml)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
