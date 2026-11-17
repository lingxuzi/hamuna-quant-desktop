"""助手 skill 加载器 —— 目录发现、渐进披露与安全边界。

SKILL.md 的 frontmatter 与正文必须分开: system prompt 只带 name + description,
否则每个 skill 的正文都会常驻上下文。参考文件必须锁在 skill 目录内, 否则
load_skill_reference 就是一个任意文件读取口子。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.custom.assistant import skill_loader
from app.custom.assistant import tools as assistant_tools
from app.custom.assistant.prompt import build_system_prompt

SKILL_MD = """\
---
name: limit-up
description: 涨停板梯队与连板高度分析流程
allowed-tools: run_strategy, get_market_overview
---

# 涨停板分析

先看 get_market_overview 的涨停梯队, 再用 run_strategy 跑连板策略。
"""

REF_MD = """\
# 连板梯队字段

字段: 连板数 / 封板时间 / 首板时间。
"""


def _write_skill(root: Path, name: str, content: str = SKILL_MD, *,
                 references: dict[str, str] | None = None) -> Path:
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")
    for rel, text in (references or {}).items():
        target = skill_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return skill_dir


def _data_dir(tmp_path: Path) -> Path:
    """用户 skill 根目录: <data_dir>/assistant/skills。"""
    root = tmp_path / "assistant" / "skills"
    root.mkdir(parents=True, exist_ok=True)
    return tmp_path


def test_list_skills_reads_user_root(tmp_path: Path) -> None:
    """用户目录下的 SKILL.md 要能被列出, 且带出用途。"""
    data_dir = _data_dir(tmp_path)
    _write_skill(data_dir / "assistant" / "skills", "limit-up")

    skills = skill_loader.list_skills(data_dir)

    assert [item["name"] for item in skills] == ["limit-up"]
    assert skills[0]["description"] == "涨停板梯队与连板高度分析流程"
    assert skills[0]["source"] == "user"


def test_user_skill_overrides_builtin_same_name(tmp_path: Path, monkeypatch) -> None:
    """同名时用户目录覆盖内置目录 (内置根目录由 monkeypatch 指向临时目录)。"""
    builtin_root = tmp_path / "builtin"
    monkeypatch.setattr(skill_loader, "BUILTIN_SKILLS_DIR", builtin_root)
    _write_skill(builtin_root, "limit-up", SKILL_MD.replace("涨停板梯队与连板高度分析流程", "内置版本"))
    data_dir = _data_dir(tmp_path)
    _write_skill(data_dir / "assistant" / "skills", "limit-up")

    skills = skill_loader.list_skills(data_dir)

    assert len(skills) == 1
    assert skills[0]["description"] == "涨停板梯队与连板高度分析流程"
    assert skills[0]["source"] == "user"


def test_skill_without_description_is_skipped(tmp_path: Path) -> None:
    """frontmatter 缺 description 的目录不是合法 skill, 不进目录。"""
    data_dir = _data_dir(tmp_path)
    root = data_dir / "assistant" / "skills"
    _write_skill(root, "broken", "---\nname: broken\n---\n\n正文\n")

    assert skill_loader.list_skills(data_dir) == []


def test_skill_with_malformed_frontmatter_is_skipped(tmp_path: Path) -> None:
    """YAML 解析失败要 fail-closed: 当作无 frontmatter, 不进目录。"""
    data_dir = _data_dir(tmp_path)
    root = data_dir / "assistant" / "skills"
    _write_skill(root, "broken", "---\nname: [unclosed\n---\n\n正文\n")

    assert skill_loader.list_skills(data_dir) == []


def test_load_skill_returns_body_without_frontmatter(tmp_path: Path) -> None:
    """正文是给模型读的内容, frontmatter 是元数据, 两者不能混在一起。"""
    data_dir = _data_dir(tmp_path)
    _write_skill(data_dir / "assistant" / "skills", "limit-up",
                 references={"references/fields.md": REF_MD})

    payload = skill_loader.load_skill(data_dir, "limit-up")

    assert payload["name"] == "limit-up"
    assert "description:" not in payload["content"]
    assert "涨停板分析" in payload["content"]
    assert payload["references"] == ["references/fields.md"]
    assert payload["allowed_tools"] == ["run_strategy", "get_market_overview"]


def test_load_unknown_skill_raises(tmp_path: Path) -> None:
    data_dir = _data_dir(tmp_path)
    with pytest.raises(ValueError):
        skill_loader.load_skill(data_dir, "not-exist")


def test_load_reference_reads_file(tmp_path: Path) -> None:
    data_dir = _data_dir(tmp_path)
    _write_skill(data_dir / "assistant" / "skills", "limit-up",
                 references={"references/fields.md": REF_MD})

    payload = skill_loader.load_reference(data_dir, "limit-up", "references/fields.md")

    assert "连板梯队字段" in payload["content"]


@pytest.mark.parametrize("bad_path", ["../secret.md", "../../etc/passwd", "/etc/passwd"])
def test_load_reference_blocks_path_traversal(tmp_path: Path, bad_path: str) -> None:
    """参考文件必须锁在 skill 目录内, 否则就是任意文件读取口子。"""
    data_dir = _data_dir(tmp_path)
    root = data_dir / "assistant" / "skills"
    _write_skill(root, "limit-up", references={"references/fields.md": REF_MD})
    (root / "limit-up" / "secret.md").write_text("不该被读到", encoding="utf-8")

    with pytest.raises(ValueError):
        skill_loader.load_reference(data_dir, "limit-up", bad_path)


def test_load_reference_rejects_non_text_file(tmp_path: Path) -> None:
    """只读文本参考材料, 不给二进制/可执行后缀开口子。"""
    data_dir = _data_dir(tmp_path)
    root = data_dir / "assistant" / "skills"
    _write_skill(root, "limit-up", references={"run.py": "print(1)\n"})

    with pytest.raises(ValueError):
        skill_loader.load_reference(data_dir, "limit-up", "run.py")


def test_prompt_injects_catalog_without_body() -> None:
    """渐进披露: prompt 只带 name + description, 不带正文。"""
    prompt = build_system_prompt(None, skills=[
        {"name": "limit-up", "description": "涨停板梯队与连板高度分析流程"},
    ])

    assert "limit-up" in prompt
    assert "涨停板梯队与连板高度分析流程" in prompt
    assert "# 涨停板分析" not in prompt


def test_prompt_omits_skill_section_when_empty() -> None:
    prompt = build_system_prompt(None, skills=[])

    assert "可用技能" not in prompt


def test_tool_schemas_expose_skill_tools() -> None:
    names = {item["function"]["name"] for item in assistant_tools.build_tool_schemas()}

    assert {"list_skills", "load_skill", "load_skill_reference"} <= names


async def test_execute_load_skill_tool(tmp_path: Path) -> None:
    data_dir = _data_dir(tmp_path)
    _write_skill(data_dir / "assistant" / "skills", "limit-up")
    ctx = assistant_tools.ToolContext.build(data_dir=data_dir)

    payload = await assistant_tools.execute_assistant_tool("load_skill", {"name": "limit-up"}, ctx)

    assert payload["ok"] is True
    assert "涨停板分析" in payload["result"]["content"]
