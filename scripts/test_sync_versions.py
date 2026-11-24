"""sync_versions.py 自检 —— 在临时目录里造一份假仓库跑同步。

跑法: python3 scripts/test_sync_versions.py

重点验两件事:
1. 四处伴生标记 (VERSION / pyproject / _FALLBACK_VERSION / release.yml 的发版
   输入默认值) 都被写成权威版本号 —— 漏掉 release.yml 就是发版直接失败的那次。
2. release.yml 里另外两个 input 的默认值 (platforms='windows', prerelease=False)
   不能被误伤 —— 只按 "以 v 开头的 default" 命中。
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from sync_versions import sync  # noqa: E402

RELEASE_YML = """\
name: 桌面客户端发布
on:
  workflow_dispatch:
    inputs:
      version:
        description: '版本号'
        required: True
        default: 'v0.3.17'
      platforms:
        description: '要构建的平台'
        required: True
        default: 'windows'
      prerelease:
        description: '是否为预发布'
        type: boolean
        default: False
jobs:
  prepare:
    runs-on: ubuntu-latest
    steps: []
"""


def _make_repo(root: Path) -> None:
    (root / "VERSION").write_text("v0.3.17\n", encoding="utf-8")
    (root / "backend" / "app").mkdir(parents=True)
    (root / "backend" / "pyproject.toml").write_text(
        '[project]\nname = "x"\nversion = "0.3.17"\n', encoding="utf-8"
    )
    (root / "backend" / "app" / "__init__.py").write_text(
        '_FALLBACK_VERSION = "0.3.17"\n', encoding="utf-8"
    )
    (root / ".github" / "workflows").mkdir(parents=True)
    (root / ".github" / "workflows" / "release.yml").write_text(RELEASE_YML, encoding="utf-8")


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _make_repo(root)

        changed = sync("0.3.31", root=root)
        assert sorted(changed) == sorted([
            ".github/workflows/release.yml",
            "VERSION",
            "backend/app/__init__.py",
            "backend/pyproject.toml",
        ]), changed

        assert (root / "VERSION").read_text() == "v0.3.31\n"
        assert 'version = "0.3.31"' in (root / "backend" / "pyproject.toml").read_text()
        assert '_FALLBACK_VERSION = "0.3.31"' in (root / "backend" / "app" / "__init__.py").read_text()

        wf = (root / ".github" / "workflows" / "release.yml").read_text()
        assert "default: 'v0.3.31'" in wf, wf
        # 另外两个 input 的默认值不能被动
        assert "default: 'windows'" in wf, wf
        assert "default: False" in wf, wf
        assert "default: 'v0.3.17'" not in wf, wf

        # 幂等: 再跑一次没有变更
        assert sync("0.3.31", root=root) == []
        # --check 模式只报告不写
        assert sync("0.3.99", root=root, check=True) == [
            "VERSION",
            "backend/pyproject.toml",
            "backend/app/__init__.py",
            ".github/workflows/release.yml",
        ]
        assert (root / "VERSION").read_text() == "v0.3.31\n"

    print("sync_versions 自检通过: 四处标记同步 + 幂等 + check 模式")
    return 0


if __name__ == "__main__":
    sys.exit(main())
