"""助手 skill 加载器 —— SKILL.md 目录发现 / frontmatter 解析 / 渐进披露。

一个 skill 是一个目录, 里面一份 SKILL.md (frontmatter + 正文) 与若干参考文件::

    <root>/<skill-name>/SKILL.md
    <root>/<skill-name>/references/fields.md

SKILL.md frontmatter (与 Claude Code skills 同构, 这里只取用得到的字段)::

    ---
    name: limit-up            # 必填
    description: 涨停板分析   # 必填, 一句话说明什么时候该用
    allowed-tools: run_strategy, get_stock_daily   # 可选, 仅作提示
    ---

根目录两个: 内置 ``app/custom/assistant/skills/`` 与用户 ``<data_dir>/assistant/skills/``,
同名时用户目录覆盖内置。

渐进披露: system prompt 只放 name + description, 正文用 load_skill 取, 更长的
参考材料用 load_skill_reference 分段取 —— 不让每轮对话都背着全部 skill 正文。

安全边界:
- 只读文本, 不执行 skill 目录里的任何脚本 (仓库内没有可信执行沙箱)。
- 参考文件必须落在 skill 目录内 (路径穿越防护), 且后缀在白名单内。
- 单文件大小上限, 防一次性灌爆上下文。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

# 内置 skill 根目录 (与本模块同目录下的 skills/)。
BUILTIN_SKILLS_DIR = Path(__file__).resolve().parent / "skills"

# 单文件正文上限 (字符)。超过只给前缀 + 提示, 不整篇灌进上下文。
_MAX_BODY_CHARS = 60_000
_MAX_REFERENCE_CHARS = 60_000

# 单个 skill 最多暴露的参考文件数。
_MAX_REFERENCES = 50

_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

# 可作为参考材料读取的文本后缀 (只读文本, 不含可执行与二进制)。
_REFERENCE_SUFFIXES = frozenset({".md", ".txt", ".csv", ".json"})


@dataclass(frozen=True)
class Skill:
    """一个已解析的 skill (正文按需再读, 这里只留元信息)。"""

    name: str
    description: str
    path: Path
    source: str = "builtin"
    allowed_tools: tuple[str, ...] = ()


def skill_roots(data_dir: Path | str | None) -> list[Path]:
    """skill 根目录, 按优先级排列 (先到先得): 用户目录 > 内置目录。"""
    roots: list[Path] = []
    if data_dir:
        roots.append(Path(data_dir) / "assistant" / "skills")
    roots.append(BUILTIN_SKILLS_DIR)
    return roots


def _split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """拆 SKILL.md 的 YAML frontmatter 与正文。

    没有 frontmatter 时返回 ({}, 全文) —— 由调用方决定是否算合法 skill。
    """
    stripped = text.lstrip("﻿")
    if not stripped.startswith("---"):
        return {}, text
    lines = stripped.splitlines()
    for idx in range(1, len(lines)):
        if lines[idx].strip() in {"---", "..."}:
            raw = "\n".join(lines[1:idx])
            body = "\n".join(lines[idx + 1:])
            try:
                meta = yaml.safe_load(raw) or {}
            except yaml.YAMLError:
                meta = {}
            return (meta if isinstance(meta, dict) else {}), body
    return {}, text


def _allowed_tools(meta: dict[str, Any]) -> tuple[str, ...]:
    raw = meta.get("allowed-tools") or meta.get("allowed_tools") or []
    if isinstance(raw, str):
        raw = [part.strip() for part in raw.split(",")]
    if not isinstance(raw, (list, tuple)):
        return ()
    return tuple(str(item).strip() for item in raw if str(item).strip())


def _read_skill_dir(entry: Path, source: str) -> Skill | None:
    """解析一个 skill 目录; 缺 SKILL.md / frontmatter 不合法时返回 None。"""
    md = entry / "SKILL.md"
    if not entry.is_dir() or not md.is_file():
        return None
    try:
        text = md.read_text(encoding="utf-8")
    except OSError:
        return None
    meta, _body = _split_frontmatter(text)
    name = str(meta.get("name") or "").strip()
    description = str(meta.get("description") or "").strip()
    if not name or not description or not _NAME_RE.match(name):
        return None
    return Skill(
        name=name,
        description=description,
        path=entry,
        source=source,
        allowed_tools=_allowed_tools(meta),
    )


def discover(data_dir: Path | str | None = None) -> list[Skill]:
    """扫描所有根目录, 同名 skill 取优先级最高的那个, 按名称排序返回。"""
    found: dict[str, Skill] = {}
    for root in skill_roots(data_dir):
        if not root.is_dir():
            continue
        source = "builtin" if root == BUILTIN_SKILLS_DIR else "user"
        try:
            entries = sorted(root.iterdir())
        except OSError:
            continue
        for entry in entries:
            skill = _read_skill_dir(entry, source)
            if skill is None:
                continue
            found.setdefault(skill.name, skill)
    return sorted(found.values(), key=lambda item: item.name)


def _reference_names(skill: Skill) -> list[str]:
    """skill 目录下的可读参考文件 (相对路径, posix 风格)。"""
    names: list[str] = []
    try:
        paths = sorted(skill.path.rglob("*"))
    except OSError:
        return names
    for path in paths:
        if not path.is_file() or path.name == "SKILL.md":
            continue
        if path.suffix.lower() not in _REFERENCE_SUFFIXES:
            continue
        rel = path.relative_to(skill.path)
        if any(part.startswith(".") for part in rel.parts):
            continue
        names.append(rel.as_posix())
        if len(names) >= _MAX_REFERENCES:
            break
    return names


def _find(data_dir: Path | str | None, name: str) -> Skill | None:
    key = (name or "").strip()
    if not key:
        return None
    for skill in discover(data_dir):
        if skill.name == key:
            return skill
    return None


def list_skills(data_dir: Path | str | None = None) -> list[dict[str, Any]]:
    """技能目录: name / description / source / references。

    system prompt 只取 name + description (渐进披露), 完整目录给 list_skills 工具。
    """
    return [
        {
            "name": skill.name,
            "description": skill.description,
            "source": skill.source,
            "references": _reference_names(skill),
        }
        for skill in discover(data_dir)
    ]


def load_skill(data_dir: Path | str | None, name: str) -> dict[str, Any]:
    """读取 skill 正文 (SKILL.md 去掉 frontmatter) 与其参考文件清单。"""
    skill = _find(data_dir, name)
    if skill is None:
        raise ValueError(f"未知技能: {name}")
    try:
        text = (skill.path / "SKILL.md").read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"技能文件读取失败: {name}") from exc
    _meta, body = _split_frontmatter(text)
    truncated = len(body) > _MAX_BODY_CHARS
    content = body.strip()
    if truncated:
        content = content[:_MAX_BODY_CHARS]
    return {
        "name": skill.name,
        "description": skill.description,
        "source": skill.source,
        "allowed_tools": list(skill.allowed_tools),
        "content": content,
        "truncated": truncated,
        "references": _reference_names(skill),
    }


def _safe_reference_path(skill: Skill, rel_path: str) -> Path:
    """把相对路径解析到 skill 目录内; 越界/类型不支持/不存在都拒绝。"""
    cleaned = (rel_path or "").strip().replace("\\", "/")
    if not cleaned:
        raise ValueError("参考文件路径为空。")
    if cleaned.startswith("/"):
        raise ValueError("参考文件路径必须是相对路径。")
    base = skill.path.resolve()
    target = (base / cleaned).resolve()
    if base not in target.parents:
        raise ValueError(f"参考文件路径越界: {rel_path}")
    if target.suffix.lower() not in _REFERENCE_SUFFIXES:
        raise ValueError(f"参考文件类型不支持: {rel_path}")
    if not target.is_file():
        raise ValueError(f"参考文件不存在: {rel_path}")
    return target


def load_reference(data_dir: Path | str | None, name: str, rel_path: str) -> dict[str, Any]:
    """读取 skill 目录下的参考文件内容。"""
    skill = _find(data_dir, name)
    if skill is None:
        raise ValueError(f"未知技能: {name}")
    target = _safe_reference_path(skill, rel_path)
    try:
        text = target.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ValueError(f"参考文件读取失败: {rel_path}") from exc
    truncated = len(text) > _MAX_REFERENCE_CHARS
    return {
        "name": skill.name,
        "path": rel_path,
        "content": text[:_MAX_REFERENCE_CHARS],
        "truncated": truncated,
    }
