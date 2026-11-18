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

import json
import os
import re
import shutil
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

# 内置 skill 根目录 (与本模块同目录下的 skills/)。
BUILTIN_SKILLS_DIR = Path(__file__).resolve().parent / "skills"

# 单文件正文上限 (字符)。超过只给前缀 + 提示, 不整篇灌进上下文。
MAX_BODY_CHARS = 60_000
MAX_REFERENCE_CHARS = 60_000

# 单个 skill 最多暴露的参考文件数。
_MAX_REFERENCES = 50

_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def is_valid_name(name: str) -> bool:
    """专家名是否合法 (会变成目录名) —— 导入外部文件时要先过这一关。"""
    return bool(_NAME_RE.match((name or "").strip()))

# 可作为参考材料读取的文本后缀 (只读文本, 不含可执行与二进制)。
REFERENCE_SUFFIXES = frozenset({".md", ".txt", ".csv", ".json"})

# 单个 skill 最多带多少附属文件 (导入外部包时封顶)。
MAX_BUNDLED_FILES = 50

# 从正文兜底用途说明时按优先级找的小节标题 (先命中先用)。
# 「定位」类在最前 —— 外部包的用途说明基本都写在那儿;
# 「什么是」放最后, 它常是文档深处的子节, 优先级高了会抢掉真正的定位段。
_DESC_SECTIONS = (
    "定位",
    "简介",
    "概述",
    "角色定义",
    "框架概览",
    "用途",
    "基本信息",
    "适用场景",
    "适用人群",
    "什么是",
)
_MAX_DESCRIPTION_CHARS = 200


@dataclass(frozen=True)
class Skill:
    """一个已解析的 skill (正文按需再读, 这里只留元信息)。"""

    name: str
    description: str
    path: Path
    source: str = "builtin"
    allowed_tools: tuple[str, ...] = ()
    # 界面显示名 (中文)。name 是目录名/工具入参, 只能 ASCII; 显示走 title。
    title: str = ""


def skill_roots(data_dir: Path | str | None) -> list[Path]:
    """skill 根目录, 按优先级排列 (先到先得): 用户目录 > 内置目录。"""
    roots: list[Path] = []
    if data_dir:
        roots.append(Path(data_dir) / "assistant" / "skills")
    roots.append(BUILTIN_SKILLS_DIR)
    return roots


def split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
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


def allowed_tools_of(meta: dict[str, Any]) -> tuple[str, ...]:
    raw = meta.get("allowed-tools") or meta.get("allowed_tools") or []
    if isinstance(raw, str):
        raw = [part.strip() for part in raw.split(",")]
    if not isinstance(raw, (list, tuple)):
        return ()
    return tuple(str(item).strip() for item in raw if str(item).strip())


# 列表项/提示符前缀 (去掉后正文才读得顺)。
_ITEM_MARKER = re.compile(r"^(?:[-*•·]|\s|✅|❌|✔|⚠|❗)+")

# 段首的加粗标签 (「**Skill定位**：全能技术分析智能体是…」) —— 标签不是正文。
# 要在 _ITEM_MARKER 之前去掉: 那个正则会把前导的 ** 当成列表符号吃掉。
_BOLD_LABEL = re.compile(r"^(?:[-*•·]\s*)?\*{1,2}[^*\n]{0,20}\*{1,2}\s*[:：]\s*")


def _first_paragraph(lines: list[str]) -> str:
    """取小节里的第一段正文 (跳过标题与空行); 是列表就用分号连起来。"""
    buf: list[str] = []
    bullets = True
    for line in lines:
        text = line.strip()
        if not text:
            if buf:
                break
            continue
        if text.startswith("#"):
            continue
        if not text.startswith(("-", "*", "•", "·")):
            bullets = False
        buf.append(_ITEM_MARKER.sub("", _BOLD_LABEL.sub("", text)))
    if not buf:
        return ""
    if bullets and len(buf) > 1:
        return "；".join(buf)
    return " ".join(buf)


def _clean_heading(heading: str) -> str:
    """标题归一: 去掉编号前缀与文件名残留。

    「1.1 什么是量化交易」→「什么是量化交易」;
    「SKILL.md — 仓位与资金管理框架」→「仓位与资金管理框架」;
    「公告解读框架 · SKILL.md」→「公告解读框架」。
    """
    text = heading.strip()
    text = text.replace("*", "")  # 「**Skill定位**」里的加粗标记
    text = re.sub(r"^[\s\W_]*", "", text)
    text = re.sub(r"^\d+(?:\.\d+)*[.、)）:：]?\s*", "", text)
    text = re.sub(r"^SKILL\.md\s*[·\-–—_|]\s*", "", text, flags=re.I)
    text = re.sub(r"[\s·\-–—_|]*\(?SKILL\.md\)?\s*$", "", text, flags=re.I)
    text = re.sub(r"[\s·\-–—_|]+Skill\s*$", "", text, flags=re.I)
    return text.strip(" ·-—_|(")


def _heading_has(heading: str, keyword: str) -> bool:
    """标题里是否命中关键词。

    关键词要么在词首 (「什么是量化交易」), 要么后面跟分隔符 —— 否则
    「业绩说明会」里的「说明」会被当成命中, 把背景材料当用途。
    """
    if heading.startswith(keyword):
        return True
    idx = heading.find(keyword)
    while idx >= 0:
        tail = heading[idx + len(keyword):]
        if not tail or not tail[0].isalnum():
            return True
        idx = heading.find(keyword, idx + 1)
    return False


def derive_description(text: str) -> str:
    """没有 frontmatter description 时, 从正文里兜底出一句用途说明。

    Claude Code 的 skill 必须有 description, 但外部包不一定带 frontmatter ——
    正文开头通常就写着这技能干什么: 按优先级找「定位 / 简介 / 框架概览」这类
    小节的第一段, 都没有就退到文档标题。
    """
    body = split_frontmatter(text)[1]
    lines = body.splitlines()
    headings = [
        (idx, _clean_heading(line.lstrip("#").strip()))
        for idx, line in enumerate(lines)
        if line.startswith("#") and line.lstrip("#").strip()
    ]
    for keyword in _DESC_SECTIONS:
        for pos, (idx, heading) in enumerate(headings):
            if not heading or not _heading_has(heading, keyword):
                continue
            end = headings[pos + 1][0] if pos + 1 < len(headings) else len(lines)
            para = _first_paragraph(lines[idx + 1:end])
            if para:
                return para[:_MAX_DESCRIPTION_CHARS]
    if headings:
        title = headings[0][1]
        if title:
            return title[:_MAX_DESCRIPTION_CHARS]
    return ""


def slugify(text: str) -> str:
    """中文名 → ASCII slug (拼音连字符), 给没有 frontmatter name 的外部包用。

    应用内专家名只允许 ASCII, 外部包目录名是中文时得有个能用的名字;
    拼音是本仓库已有的依赖 (股票名拼音搜索), 不为这件事再加一个。
    """
    cleaned = re.sub(r"[^A-Za-z0-9]+", "-", text or "").strip("-").lower()
    if cleaned:
        return cleaned[:64]
    try:
        from pypinyin import lazy_pinyin
    except ImportError:
        return ""
    return "-".join(part for part in lazy_pinyin(text or "") if part).lower()[:64]


def derive_title(text: str) -> str:
    """从正文 H1 取显示名 (中文标题); 取不到返回空串。

    外部 skill 的 name 是 ASCII slug (目录名), 界面上显示 slug 等于让用户
    读拼音。正文一级标题就是作者写的名字, 直接用它。
    """
    body = split_frontmatter(text)[1]
    for line in body.splitlines():
        if line.startswith("# "):
            return _clean_heading(line.lstrip("#").strip())[:80]
    return ""


def _read_skill_dir(entry: Path, source: str) -> Skill | None:
    """解析一个 skill 目录; 缺 SKILL.md / frontmatter 不合法时返回 None。"""
    md = entry / "SKILL.md"
    if not entry.is_dir() or not md.is_file():
        return None
    try:
        text = md.read_text(encoding="utf-8")
    except OSError:
        return None
    meta, _body = split_frontmatter(text)
    name = str(meta.get("name") or "").strip()
    description = str(meta.get("description") or "").strip()
    if not name or not description or not _NAME_RE.match(name):
        return None
    return Skill(
        name=name,
        description=description,
        path=entry,
        source=source,
        allowed_tools=allowed_tools_of(meta),
        title=str(meta.get("title") or "").strip() or derive_title(text) or name,
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


def reference_names(skill: Skill) -> list[str]:
    """skill 目录下的可读参考文件 (相对路径, posix 风格)。"""
    names: list[str] = []
    try:
        paths = sorted(skill.path.rglob("*"))
    except OSError:
        return names
    for path in paths:
        if not path.is_file() or path.name == "SKILL.md":
            continue
        if path.suffix.lower() not in REFERENCE_SUFFIXES:
            continue
        rel = path.relative_to(skill.path)
        if any(part.startswith(".") for part in rel.parts):
            continue
        names.append(rel.as_posix())
        if len(names) >= _MAX_REFERENCES:
            break
    return names


def find(data_dir: Path | str | None, name: str) -> Skill | None:
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
            "references": reference_names(skill),
        }
        for skill in discover(data_dir)
    ]


def load_skill(data_dir: Path | str | None, name: str) -> dict[str, Any]:
    """读取 skill 正文 (SKILL.md 去掉 frontmatter) 与其参考文件清单。"""
    skill = find(data_dir, name)
    if skill is None:
        raise ValueError(f"未知技能: {name}")
    try:
        text = (skill.path / "SKILL.md").read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"技能文件读取失败: {name}") from exc
    _meta, body = split_frontmatter(text)
    truncated = len(body) > MAX_BODY_CHARS
    content = body.strip()
    if truncated:
        content = content[:MAX_BODY_CHARS]
    return {
        "name": skill.name,
        "description": skill.description,
        "source": skill.source,
        "allowed_tools": list(skill.allowed_tools),
        "content": content,
        "truncated": truncated,
        "references": reference_names(skill),
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
    if target.suffix.lower() not in REFERENCE_SUFFIXES:
        raise ValueError(f"参考文件类型不支持: {rel_path}")
    if not target.is_file():
        raise ValueError(f"参考文件不存在: {rel_path}")
    return target


def load_reference(data_dir: Path | str | None, name: str, rel_path: str) -> dict[str, Any]:
    """读取 skill 目录下的参考文件内容。"""
    skill = find(data_dir, name)
    if skill is None:
        raise ValueError(f"未知技能: {name}")
    target = _safe_reference_path(skill, rel_path)
    try:
        text = target.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ValueError(f"参考文件读取失败: {rel_path}") from exc
    truncated = len(text) > MAX_REFERENCE_CHARS
    return {
        "name": skill.name,
        "path": rel_path,
        "content": text[:MAX_REFERENCE_CHARS],
        "truncated": truncated,
    }


# ----------------------------------------------------------------
# 写入侧 —— 专家正文 (SKILL.md) 的增删改。
# 只写用户根: 内置根随仓库分发, 正文只读 —— 改了下次部署就被覆盖,
# 也会脏了工作区 (仓库里的运行时产物不该进 PR)。
# ----------------------------------------------------------------
def user_root(data_dir: Path | str | None) -> Path | None:
    """用户 skill 根目录 (<data_dir>/assistant/skills); 数据目录未就绪时返回 None。"""
    if not data_dir:
        return None
    return Path(data_dir) / "assistant" / "skills"


def read_skill(data_dir: Path | str | None, name: str) -> dict[str, Any]:
    """读专家的 description / 正文 / allowed-tools (编辑表单回填用)。"""
    skill = find(data_dir, name)
    if skill is None:
        raise ValueError(f"未知技能: {name}")
    try:
        text = (skill.path / "SKILL.md").read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"技能文件读取失败: {name}") from exc
    meta, body = split_frontmatter(text)
    return {
        "name": skill.name,
        "description": str(meta.get("description") or "").strip(),
        "content": body.strip(),
        "allowed_tools": list(skill.allowed_tools),
    }


def render_skill_md(
    name: str,
    description: str,
    content: str,
    *,
    allowed_tools: Sequence[str] | None = None,
    title: str | None = None,
) -> str:
    """把专家渲染成 SKILL.md 全文 (frontmatter + 正文)。

    写盘与导出专家包共用这一份渲染 —— 格式只有一处, 导出的包导回来还是
    同一个 skill。description 用 JSON 字符串写法: JSON 标量是合法 YAML,
    描述里带冒号也不会解析坏。
    """
    key = (name or "").strip()
    # 折行压成一行: description 进 UI 卡片与 system prompt, 带换行会撑破排版。
    desc = " ".join((description or "").split())
    lines = [
        "---",
        f"name: {json.dumps(key, ensure_ascii=False)}",
        f"description: {json.dumps(desc, ensure_ascii=False)}",
    ]
    # 显示名与 name 不同才写: 多数 skill 的 H1 就是中文名, 写了也是重复。
    label = " ".join((title or "").split())
    if label and label != key:
        lines.append(f"title: {json.dumps(label, ensure_ascii=False)}")
    tools = [str(item).strip() for item in (allowed_tools or []) if str(item).strip()]
    if tools:
        lines.append("allowed-tools: " + json.dumps(tools, ensure_ascii=False))
    lines += ["---", "", (content or "").strip(), ""]
    return "\n".join(lines)


def _write_bundled(skill_dir: Path, rel_path: str, text: str) -> None:
    """写 skill 目录下的附属文本文件 (Claude Code 包里的 references/ 等)。

    路径必须落在 skill 目录内, 后缀在白名单内 —— 只放文本, 脚本与二进制不进。
    """
    cleaned = (rel_path or "").strip().replace("\\", "/")
    parts = [part for part in cleaned.split("/") if part]
    if not parts or cleaned.startswith("/") or any(part in (".", "..") for part in parts):
        raise ValueError(f"附属文件路径不合法: {rel_path}")
    target = skill_dir.joinpath(*parts)
    if target.suffix.lower() not in REFERENCE_SUFFIXES:
        raise ValueError(f"附属文件类型不支持: {rel_path}")
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, target)


def write_skill(
    data_dir: Path | str | None,
    name: str,
    description: str,
    content: str,
    *,
    allowed_tools: Sequence[str] | None = None,
    extra_files: Mapping[str, str] | None = None,
    title: str | None = None,
) -> Path:
    """写专家正文到用户根的 SKILL.md (临时文件 + 原子替换)。

    allowed_tools=None 表示沿用文件里原有的值 —— 页面不管理该字段, 改写正文
    时不能把它抹掉。extra_files 是附属文本文件 (相对 skill 目录的路径 → 内容),
    导入 Claude Code 包时用它把 references/ 一并带过来。
    title=None 同样沿用原值; 传入则覆盖显示名。
    """
    root = user_root(data_dir)
    if root is None:
        raise ValueError("数据目录未就绪。")
    key = (name or "").strip()
    if not _NAME_RE.match(key):
        raise ValueError(f"技能名不合法: {name}")
    desc = (description or "").strip()
    if not desc:
        raise ValueError("专家用途 (description) 不能为空。")
    target_dir = root / key
    target_dir.mkdir(parents=True, exist_ok=True)
    md = target_dir / "SKILL.md"
    old_meta: dict[str, Any] = {}
    if md.exists():
        try:
            old_meta, _body = split_frontmatter(md.read_text(encoding="utf-8"))
        except OSError:
            old_meta = {}
    if allowed_tools is None:
        allowed_tools = allowed_tools_of(old_meta)
    tmp = md.with_name("SKILL.md.tmp")
    tmp.write_text(
        render_skill_md(
            key, desc, content,
            allowed_tools=allowed_tools,
            title=title if title is not None else str(old_meta.get("title") or ""),
        ),
        encoding="utf-8",
    )
    os.replace(tmp, md)
    for rel, text in list((extra_files or {}).items())[:MAX_BUNDLED_FILES]:
        _write_bundled(target_dir, rel, text)
    return md


def delete_skill(data_dir: Path | str | None, name: str) -> None:
    """删除用户根下的专家目录 (内置根不动)。"""
    root = user_root(data_dir)
    if root is None:
        raise ValueError("数据目录未就绪。")
    key = (name or "").strip()
    if not _NAME_RE.match(key):
        raise ValueError(f"技能名不合法: {name}")
    target = root / key
    if not target.is_dir():
        raise ValueError(f"专家不存在: {name}")
    shutil.rmtree(target)
