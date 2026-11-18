"""专家 (skill) 的分类与配置 —— 一个 skill = 一个专家。

两处存储, 职责分开:
- 专家正文: SKILL.md (由 skill_loader 管) —— 跟着 skill 走, 内置专家随仓库分发。
- 分类归属与启用状态: 默认随仓库分发 (app/custom/assistant/experts.json),
  用户改过之后写 <data_dir>/assistant/experts.json 覆盖 —— 跟 data/ 一起, 不进 Git。

内置专家 (仓库 skills/ 根) 正文只读: 设置页能配它的分类与启用状态, 但改不了
正文 —— 改了会在下次部署时被覆盖, 也不该把运行时产物脏进仓库。
"""
from __future__ import annotations

import io
import json
import os
import re
import zipfile
from pathlib import Path
from typing import Any

from app.custom.assistant import skill_loader

_CONFIG_REL = ("assistant", "experts.json")
# 内置专家的默认分类配置 —— 随仓库分发。用户没配过 (data_dir 里没有配置文件) 时
# 用它, 新装的机器开箱就有完整分类体系, 不必先导一遍专家包。
_BUILTIN_CONFIG_PATH = Path(__file__).resolve().parent / "experts.json"

# 分类体系上限 (配置是给界面用的, 太多分类等于没有分类)。
MAX_CATEGORIES = 50
MAX_CATEGORY_LEN = 40

# 分类名保留词: 界面上「全部」表示不筛选、「未分类」表示没有归属的专家,
# 分类本身叫这两个名字, 筛选标签和归属标签就分不清了。
RESERVED_CATEGORY_NAMES = frozenset({"全部", "未分类"})

# 专家正文上限 (与 skill_loader 的读取上限一致, 免得写进去就读不全)。
MAX_CONTENT_CHARS = skill_loader.MAX_BODY_CHARS

# 导入包格式版本 + 单次导入条数上限 (外部文件是不可信输入, 得有上限)。
EXPORT_VERSION = 1
MAX_IMPORT_EXPERTS = 200

# 专家包 (zip) 的体积上限。压缩包是可执行入口 (解压炸弹/超量文件), 上限按
# 压缩后体积、解压后总量、单文件三条各卡一次。
MAX_PACK_BYTES = 10 * 1024 * 1024
MAX_PACK_ENTRIES = 500
MAX_PACK_FILE_BYTES = 512 * 1024
MAX_PACK_TOTAL_BYTES = 20 * 1024 * 1024

# 一轮对话里 @ 引用的专家数量与正文总长上限 —— 引用是给这一轮加料, 不是把
# 整个专家库背进上下文。
MAX_REFERENCED_EXPERTS = 8
MAX_REFERENCED_CHARS = 60_000


def _config_path(data_dir: Path | str | None) -> Path | None:
    if not data_dir:
        return None
    return Path(data_dir).joinpath(*_CONFIG_REL)


def _empty_config() -> dict[str, Any]:
    return {"categories": [], "experts": {}}


def load_config(data_dir: Path | str | None) -> dict[str, Any]:
    """读专家配置 (分类体系 + 每个专家的归属/启用状态)。

    优先级: 用户配置 (<data_dir>/assistant/experts.json) > 仓库内置默认
    (app/custom/assistant/experts.json) —— 新装的机器没有用户配置时用内置默认,
    内置专家直接带着分类出现。

    配置损坏时直接报错, 不静默当成空配置 —— 静默会把用户的分类体系清空。
    """
    path = _config_path(data_dir)
    if path is None or not path.is_file():
        path = _BUILTIN_CONFIG_PATH
    if not path.is_file():
        return _empty_config()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"专家配置文件损坏 (JSON 解析失败): {path}") from exc
    except OSError as exc:
        raise ValueError(f"专家配置文件读取失败: {path}") from exc
    if not isinstance(raw, dict):
        raise ValueError(f"专家配置文件格式错误: {path}")
    categories = [str(item).strip() for item in (raw.get("categories") or []) if str(item).strip()]
    experts: dict[str, dict[str, Any]] = {}
    for key, value in (raw.get("experts") or {}).items():
        if not isinstance(value, dict):
            continue
        experts[str(key)] = {
            "category": str(value.get("category") or "").strip(),
            "enabled": bool(value.get("enabled", True)),
        }
    return {"categories": categories, "experts": experts}


def save_config(data_dir: Path | str | None, config: dict[str, Any]) -> None:
    """原子写专家配置。"""
    path = _config_path(data_dir)
    if path is None:
        raise ValueError("数据目录未就绪。")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name("experts.json.tmp")
    tmp.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def list_experts(data_dir: Path | str | None) -> dict[str, Any]:
    """专家总览: 内置 + 用户, 带分类归属与启用状态。"""
    config = load_config(data_dir)
    assignments = config["experts"]
    items: list[dict[str, Any]] = []
    for skill in skill_loader.discover(data_dir):
        info = assignments.get(skill.name, {})
        items.append({
            "name": skill.name,
            "title": skill.title or skill.name,
            "description": skill.description,
            "source": skill.source,
            "builtin": skill.source == "builtin",
            "category": info.get("category", ""),
            "enabled": info.get("enabled", True),
            "references": skill_loader.reference_names(skill),
        })
    return {"categories": config["categories"], "experts": items}


def is_enabled(data_dir: Path | str | None, name: str) -> bool:
    """某个专家是否可用 (默认启用; 配置里显式停用才为 False)。"""
    key = (name or "").strip()
    if not key:
        return False
    return bool(load_config(data_dir)["experts"].get(key, {}).get("enabled", True))


def usable_skills(data_dir: Path | str | None) -> list[dict[str, Any]]:
    """助手可用的技能目录 (停用的专家不进目录) —— 只带 name + description。"""
    config = load_config(data_dir)
    assignments = config["experts"]
    out: list[dict[str, Any]] = []
    for skill in skill_loader.discover(data_dir):
        if not assignments.get(skill.name, {}).get("enabled", True):
            continue
        out.append({
            "name": skill.name,
            "title": skill.title or skill.name,
            "description": skill.description,
        })
    return out


def _clean_categories(categories: list[Any]) -> list[str]:
    cleaned: list[str] = []
    for item in categories:
        text = str(item).strip()
        if not text:
            continue
        if len(text) > MAX_CATEGORY_LEN:
            raise ValueError(f"分类名过长 (上限 {MAX_CATEGORY_LEN} 字): {text[:20]}…")
        if text in RESERVED_CATEGORY_NAMES:
            raise ValueError(f"分类名是保留词, 不能用: {text}")
        if text not in cleaned:
            cleaned.append(text)
    if len(cleaned) > MAX_CATEGORIES:
        raise ValueError(f"分类数量超出上限 ({MAX_CATEGORIES})")
    return cleaned


def save_layout(
    data_dir: Path | str | None,
    categories: list[Any],
    assignments: dict[str, Any] | None,
) -> dict[str, Any]:
    """保存分类体系 + 专家的分类归属/启用状态 (整份保存)。

    分类被删 → 其下专家回到未分类; 归属到不存在的分类 → 拒绝。
    """
    config = load_config(data_dir)
    cleaned = _clean_categories(categories)
    previous = set(config["categories"])
    known = {skill.name for skill in skill_loader.discover(data_dir)}
    next_assignments: dict[str, dict[str, Any]] = {}
    for name, value in (assignments or {}).items():
        key = str(name).strip()
        if key not in known:
            continue  # 专家已不存在 (被外部删掉) → 顺手清掉这条配置
        if not isinstance(value, dict):
            continue
        category = str(value.get("category") or "").strip()
        if category and category not in cleaned:
            if category in previous:
                category = ""  # 分类被删 → 该专家回到未分类
            else:
                raise ValueError(f"分类不存在: {category}")
        next_assignments[key] = {
            "category": category,
            "enabled": bool(value.get("enabled", True)),
        }
    config["categories"] = cleaned
    config["experts"] = next_assignments
    save_config(data_dir, config)
    return list_experts(data_dir)


def create_expert(
    data_dir: Path | str | None,
    *,
    name: str,
    description: str,
    content: str,
    category: str = "",
) -> dict[str, Any]:
    """新建用户专家 (写用户根的 SKILL.md)。"""
    key = (name or "").strip()
    if skill_loader.find(data_dir, key) is not None:
        raise ValueError(f"专家已存在: {key}")
    body = (content or "").strip()
    if len(body) > MAX_CONTENT_CHARS:
        raise ValueError(f"专家正文过长 (上限 {MAX_CONTENT_CHARS} 字)")
    config = load_config(data_dir)
    cat = (category or "").strip()
    if cat and cat not in config["categories"]:
        # 归属到不存在的分类 → 这个专家在界面上无处可寻, 直接拒绝 (先于写盘, 不留孤儿文件)
        raise ValueError(f"分类不存在: {cat}")
    skill_loader.write_skill(data_dir, key, description, body)
    entry = config["experts"].setdefault(key, {"category": "", "enabled": True})
    entry["category"] = cat
    save_config(data_dir, config)
    return {"name": key, "description": description, "content": body, "category": entry["category"]}


def update_expert(
    data_dir: Path | str | None,
    name: str,
    *,
    description: str | None = None,
    content: str | None = None,
) -> dict[str, Any]:
    """改专家正文 (description / content)。内置专家只读 —— 只能改分类与启用状态。"""
    key = (name or "").strip()
    skill = skill_loader.find(data_dir, key)
    if skill is None:
        raise ValueError(f"未知专家: {key}")
    if skill.source == "builtin":
        raise ValueError("内置专家正文只读, 只能配置分类与启用状态。")
    current = skill_loader.read_skill(data_dir, key)
    body = current["content"] if content is None else content.strip()
    if len(body) > MAX_CONTENT_CHARS:
        raise ValueError(f"专家正文过长 (上限 {MAX_CONTENT_CHARS} 字)")
    desc = current["description"] if description is None else (description or "").strip()
    skill_loader.write_skill(data_dir, key, desc, body)
    return {"name": key, "description": desc, "content": body}


def read_expert(data_dir: Path | str | None, name: str) -> dict[str, Any]:
    """读单个专家的详情 (编辑表单回填用): 正文 + 分类 + 启用状态。"""
    key = (name or "").strip()
    skill = skill_loader.find(data_dir, key)
    if skill is None:
        raise ValueError(f"未知专家: {key}")
    detail = skill_loader.read_skill(data_dir, key)
    config = load_config(data_dir)
    info = config["experts"].get(key, {})
    return {
        "name": skill.name,
        "title": skill.title or skill.name,
        "description": detail["description"],
        "content": detail["content"],
        "category": info.get("category", ""),
        "enabled": info.get("enabled", True),
        "builtin": skill.source == "builtin",
    }


def delete_expert(data_dir: Path | str | None, name: str) -> None:
    """删除用户专家 (内置专家不可删, 只能停用)。"""
    key = (name or "").strip()
    skill = skill_loader.find(data_dir, key)
    if skill is None:
        raise ValueError(f"未知专家: {key}")
    if skill.source == "builtin":
        raise ValueError("内置专家不可删除, 可将其停用。")
    skill_loader.delete_skill(data_dir, key)
    config = load_config(data_dir)
    config["experts"].pop(key, None)
    save_config(data_dir, config)


# ----------------------------------------------------------------
# 导入导出 —— 专家包 (zip)。两种包都收:
#
# 1. 本应用格式: manifest.json (分类体系 + 清单) + 每个专家一个 <name>/SKILL.md。
# 2. Claude Code skill 包: 每个技能一个 <skill-name>/SKILL.md (frontmatter 带
#    name/description), 可带 references/ 等附属文件 —— 没有清单也能导。
#
# 内置专家不进包: 它们随仓库分发, 正文只读。把它导成用户副本再导回去,
# 反而会用同名用户 skill 盖住内置版本 (用户目录优先), 升级就丢更新。
#
# 导出时目录名用 Claude Code 的技能名规范 (小写字母/数字/连字符, 且与
# frontmatter 的 name 一致), 原名留在 manifest 里 —— 导回本应用还是原名,
# 装进 Claude Code 也是合法 skill。
# ----------------------------------------------------------------
def _claude_slug(name: str, taken: set[str]) -> str:
    """Claude Code 技能名: 小写字母/数字/连字符, 与目录名一致。"""
    base = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")[:64] or "skill"
    slug = base
    index = 2
    while slug in taken:
        slug = f"{base}-{index}"
        index += 1
    taken.add(slug)
    return slug


def export_zip(data_dir: Path | str | None) -> bytes:
    """导出专家包 (zip 字节流): 分类体系 + 自定义专家正文。"""
    config = load_config(data_dir)
    manifest: dict[str, Any] = {
        "version": EXPORT_VERSION,
        "categories": config["categories"],
        "experts": [],
    }
    buf = io.BytesIO()
    taken: set[str] = set()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for skill in skill_loader.discover(data_dir):
            if skill.source != "user":
                continue
            detail = skill_loader.read_skill(data_dir, skill.name)
            info = config["experts"].get(skill.name, {})
            slug = _claude_slug(skill.name, taken)
            manifest["experts"].append({
                "name": skill.name,
                "dir": slug,
                "description": detail["description"],
                "category": info.get("category", ""),
                "enabled": info.get("enabled", True),
            })
            zf.writestr(
                f"{slug}/SKILL.md",
                skill_loader.render_skill_md(
                    slug,
                    detail["description"],
                    detail["content"],
                    allowed_tools=detail.get("allowed_tools"),
                ),
            )
            for rel in skill_loader.reference_names(skill):
                try:
                    text = (skill.path / rel).read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    continue
                zf.writestr(f"{slug}/{rel}", text)
        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    return buf.getvalue()


def _clean_tools(raw: Any) -> list[str]:
    """包里的可调用工具名 —— 外部文件不可信, 条数与长度都要封顶。"""
    if not isinstance(raw, (list, tuple)):
        return []
    out: list[str] = []
    for item in raw:
        text = str(item).strip()[:64]
        if text and text not in out:
            out.append(text)
        if len(out) >= 50:
            break
    return out


def _read_manifest(zf: zipfile.ZipFile) -> dict[str, Any] | None:
    """读并校验包内清单 —— 没有清单的包 (Claude Code skill 包) 返回 None。"""
    try:
        raw = zf.read("manifest.json")
    except KeyError:
        return None
    try:
        manifest = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("导入文件格式不对: 清单文件解析失败。") from exc
    if not isinstance(manifest, dict):
        raise ValueError("导入文件格式不对: 清单文件顶层必须是对象。")
    raw_experts = manifest.get("experts")
    if raw_experts is None:
        raise ValueError("导入文件格式不对: 缺少专家列表。")
    if not isinstance(raw_experts, list):
        raise ValueError("导入文件格式不对: 专家列表必须是数组。")
    if len(raw_experts) > MAX_IMPORT_EXPERTS:
        raise ValueError(f"单次导入专家数超出上限 ({MAX_IMPORT_EXPERTS})")
    return manifest


def _pack_skill_dirs(zf: zipfile.ZipFile) -> list[str]:
    """包里的 skill 目录 (每个目录一份 SKILL.md) —— Claude Code 的包结构。"""
    dirs: list[str] = []
    for info in zf.infolist():
        parts = info.filename.split("/")
        if len(parts) != 2 or parts[1] != "SKILL.md":
            continue
        if parts[0] in ("", ".", "..") or parts[0] in dirs:
            continue
        dirs.append(parts[0])
    return dirs


def _resolve_name(dir_name: str, meta_name: str) -> str:
    """专家名: frontmatter name → 目录名 → 拼音 slug。

    Claude Code 包要求 name 与目录名一致; 目录名是中文 (外部包常见) 时退到
    拼音, 保证导进来的技能在应用里有能用的 ASCII 名字。
    """
    for candidate in (meta_name, dir_name):
        key = (candidate or "").strip()
        if skill_loader.is_valid_name(key):
            return key
    return skill_loader.slugify(dir_name)


def _read_pack_skill(zf: zipfile.ZipFile, dir_name: str) -> dict[str, Any] | None:
    """读包内某个 skill 目录 —— 只按精确文件名取, 不落盘不按路径解压。

    返回 frontmatter 的 name/description、正文、允许的工具, 以及附属文本
    文件 (references/ 等)。脚本与二进制不带 —— skill 目录里的脚本本就不执行。
    """
    entry = f"{dir_name}/SKILL.md"
    try:
        info = zf.getinfo(entry)
    except KeyError:
        return None
    if info.file_size > MAX_PACK_FILE_BYTES:
        raise ValueError(f"导入文件格式不对: 专家 {dir_name} 的正文过大。")
    try:
        text = zf.read(entry).decode("utf-8")
    except (UnicodeDecodeError, OSError, zipfile.BadZipFile) as exc:
        raise ValueError(f"导入文件格式不对: 专家 {dir_name} 的正文读取失败。") from exc
    meta, body = skill_loader.split_frontmatter(text)
    files: dict[str, str] = {}
    prefix = f"{dir_name}/"
    for extra in zf.infolist():
        if not extra.filename.startswith(prefix) or extra.filename == entry or extra.is_dir():
            continue
        rel = extra.filename[len(prefix):]
        if not rel or Path(rel).suffix.lower() not in skill_loader.REFERENCE_SUFFIXES:
            continue
        if extra.file_size > MAX_PACK_FILE_BYTES:
            continue
        try:
            files[rel] = zf.read(extra).decode("utf-8")
        except (UnicodeDecodeError, OSError, zipfile.BadZipFile):
            continue
        if len(files) >= skill_loader.MAX_BUNDLED_FILES:
            break
    return {
        "name": str(meta.get("name") or "").strip(),
        "description": str(meta.get("description") or "").strip() or skill_loader.derive_description(text),
        "title": str(meta.get("title") or "").strip() or skill_loader.derive_title(text),
        "content": body.strip(),
        "allowed_tools": list(skill_loader.allowed_tools_of(meta)),
        "files": files,
    }


def import_zip(
    data_dir: Path | str | None,
    blob: bytes,
    *,
    overwrite: bool = False,
) -> dict[str, Any]:
    """导入专家包 (zip 字节流): 合并分类 + 写入专家。

    两种包都收:
    - 本应用格式 (manifest.json + <name>/SKILL.md): 分类与启用状态随包走。
    - Claude Code skill 包 (每个技能一个 <skill-name>/SKILL.md, 无清单):
      名称/用途取自 frontmatter, 附属文件一并带入, 分类归「未分类」。

    - 包里的分类本地没有 → 追加到分类体系末尾。
    - 专家归属的分类合并后仍不存在 → 落到「未分类」(专家不丢)。
    - 同名专家: overwrite=False 跳过 (保住本机正文), overwrite=True 用包内版本替换。
    - 内置专家: 一律跳过 (正文只读, 也不该被用户包顶掉)。

    包内容全程留在内存里按文件名精确读取, 不解压到磁盘 —— 越界路径拿不到
    落地的机会。返回本次的处置报告: 导入 / 覆盖 / 跳过各是哪些。
    """
    raw = bytes(blob or b"")
    if not raw:
        raise ValueError("导入文件为空。")
    if len(raw) > MAX_PACK_BYTES:
        raise ValueError(f"导入文件过大 (上限 {MAX_PACK_BYTES // 1024 // 1024} MB)")
    try:
        zf = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile as exc:
        raise ValueError("导入文件格式不对: 不是有效的压缩包。") from exc

    with zf:
        infos = zf.infolist()
        if len(infos) > MAX_PACK_ENTRIES:
            raise ValueError(f"导入文件内条目数超出上限 ({MAX_PACK_ENTRIES})")
        if sum(info.file_size for info in infos) > MAX_PACK_TOTAL_BYTES:
            raise ValueError("导入文件解压后体积过大。")
        manifest = _read_manifest(zf)

        config = load_config(data_dir)
        categories = list(config["categories"])
        added_categories: list[str] = []
        for raw_category in (manifest or {}).get("categories") or []:
            category = str(raw_category).strip()
            if not category or category in RESERVED_CATEGORY_NAMES or category in categories:
                continue
            categories.append(category)
            added_categories.append(category)
        if len(categories) > MAX_CATEGORIES:
            raise ValueError(f"导入后分类数量超出上限 ({MAX_CATEGORIES})")

        # 清单包: 按清单条目走 (清单里的名字/用途是权威, 目录名另存于 dir);
        # Claude Code 包: 按包内 skill 目录走, 名字与用途从 SKILL.md 兜底。
        planned: list[tuple[str, str, dict[str, Any]]] = []
        if manifest:
            for item in manifest["experts"]:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name") or "").strip()
                dir_key = str(item.get("dir") or "").strip() or name
                planned.append((name, dir_key, item))
        else:
            for dir_name in _pack_skill_dirs(zf):
                planned.append(("", dir_name, {}))
        if not planned:
            raise ValueError("导入文件格式不对: 包里没有 <技能名>/SKILL.md。")

        imported: list[str] = []
        updated: list[str] = []
        skipped: list[str] = []
        for manifest_name, dir_name, entry in planned:
            detail = _read_pack_skill(zf, dir_name)
            if detail is None:
                skipped.append(manifest_name or dir_name)
                continue
            name = manifest_name or _resolve_name(dir_name, detail["name"])
            if not skill_loader.is_valid_name(name):
                skipped.append(name or dir_name)
                continue
            # 清单里的用途是权威 (空就是空); 没有清单才从正文兜底。
            description = (
                str(entry.get("description") or "").strip() if entry else detail["description"]
            )
            content = detail["content"]
            if not description or not content or len(content) > MAX_CONTENT_CHARS:
                skipped.append(name)
                continue
            existing = skill_loader.find(data_dir, name)
            if existing is not None and existing.source == "builtin":
                skipped.append(name)
                continue
            if existing is not None and not overwrite:
                skipped.append(name)
                continue
            category = str(entry.get("category") or "").strip()
            if category not in categories:
                category = ""
            skill_loader.write_skill(
                data_dir, name, description, content,
                allowed_tools=_clean_tools(detail["allowed_tools"]),
                extra_files=detail["files"],
                title=detail["title"],
            )
            config_entry = config["experts"].setdefault(name, {"category": "", "enabled": True})
            config_entry["category"] = category
            config_entry["enabled"] = bool(entry.get("enabled", True))
            (updated if existing is not None else imported).append(name)

    config["categories"] = categories
    # 清掉已不存在的专家的配置 (与 save_layout 同一口径)
    known = {skill.name for skill in skill_loader.discover(data_dir)}
    config["experts"] = {k: v for k, v in config["experts"].items() if k in known}
    save_config(data_dir, config)
    return {
        "imported": imported,
        "updated": updated,
        "skipped": skipped,
        "categories_added": added_categories,
    }


# ----------------------------------------------------------------
# @ 引用 —— 本轮点名的专家正文直接进 system prompt。
# ----------------------------------------------------------------
def referenced_bodies(
    data_dir: Path | str | None,
    names: list[str] | None,
) -> list[dict[str, Any]]:
    """@ 引用到的专家正文 (停用 / 不存在的跳过)。

    数量与总长都有上限 —— 引用是给这一轮加料, 不是把整个专家库背进上下文。
    """
    if not names:
        return []
    assignments = load_config(data_dir)["experts"]
    out: list[dict[str, Any]] = []
    used = 0
    for raw in names:
        name = str(raw or "").strip()
        if not name or not skill_loader.is_valid_name(name):
            continue
        if any(item["name"] == name for item in out):
            continue
        if not assignments.get(name, {}).get("enabled", True):
            continue
        skill = skill_loader.find(data_dir, name)
        if skill is None:
            continue
        detail = skill_loader.read_skill(data_dir, name)
        content = detail["content"]
        if not content:
            continue
        if used + len(content) > MAX_REFERENCED_CHARS:
            break
        used += len(content)
        out.append({
            "name": name,
            "title": skill.title or name,
            "description": detail["description"],
            "content": content,
        })
        if len(out) >= MAX_REFERENCED_EXPERTS:
            break
    return out
