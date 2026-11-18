"""批量导入 Claude Code skill 包 —— 目录里的 .zip 逐个导进专家库。

用法:
    cd backend && .venv/bin/python scripts/import_skill_packs.py \
        --dir <安装包目录> --map scripts/skill_pack_categories.json [--overwrite]

映射文件 (JSON) 决定专家叫什么名字、归到哪个分类:

    {
      "categories": ["价值投资", "技术分析"],
      "experts": {
        "打板回封": {"category": "打板与情绪", "name": "limit-up-reentry-framework"}
      }
    }

    experts 的键是包内目录名; name 可选 —— 不给就用包里的 frontmatter name,
    再没有就退到拼音; category 是这个专家的分类归属。

外部包不一定带 frontmatter (用途说明/名字都没有), 也能导: 用途从正文开头的
「定位 / 简介」小节兜底, 名字退到拼音。脚本把每个包规整成本应用的包格式
再走 import_zip —— 导入规则只有一处 (experts.import_zip)。
"""
from __future__ import annotations

import argparse
import io
import json
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings
from app.custom.assistant import experts, skill_loader


def _skill_dirs(zf: zipfile.ZipFile) -> list[str]:
    """包里的 skill 目录 (每个目录一份 SKILL.md)。"""
    dirs: list[str] = []
    for info in zf.infolist():
        parts = info.filename.split("/")
        if len(parts) != 2 or parts[1] != "SKILL.md":
            continue
        if parts[0] in ("", ".", "..") or parts[0] in dirs:
            continue
        dirs.append(parts[0])
    return dirs


def _normalize(
    blob: bytes,
    dir_name: str,
    name_hint: str,
) -> tuple[str, bytes] | None:
    """把外部 skill 目录规整成本应用包格式 —— 返回 (专家名, 包字节)。

    专家名: 调用方给的 name → frontmatter name → 目录名 → 拼音。
    """
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        try:
            text = zf.read(f"{dir_name}/SKILL.md").decode("utf-8")
        except (KeyError, UnicodeDecodeError, OSError, zipfile.BadZipFile):
            return None
        meta, body = skill_loader.split_frontmatter(text)
        name = (
            name_hint.strip()
            or str(meta.get("name") or "").strip()
            or (dir_name if skill_loader.is_valid_name(dir_name) else "")
            or skill_loader.slugify(dir_name)
        )
        if not skill_loader.is_valid_name(name):
            return None
        description = str(meta.get("description") or "").strip() or skill_loader.derive_description(text)
        if not description or not body.strip():
            return None
        files: dict[str, str] = {}
        prefix = f"{dir_name}/"
        for info in zf.infolist():
            if not info.filename.startswith(prefix) or info.is_dir():
                continue
            rel = info.filename[len(prefix):]
            if not rel or rel == "SKILL.md":
                continue
            if Path(rel).suffix.lower() not in skill_loader.REFERENCE_SUFFIXES:
                continue  # 脚本与二进制不进 —— skill 目录里的脚本本就不执行
            try:
                files[rel] = zf.read(info).decode("utf-8")
            except (UnicodeDecodeError, OSError, zipfile.BadZipFile):
                continue
            if len(files) >= skill_loader.MAX_BUNDLED_FILES:
                break
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as out:
            out.writestr(
                f"{name}/SKILL.md",
                skill_loader.render_skill_md(
                    name,
                    description,
                    body.strip(),
                    allowed_tools=skill_loader.allowed_tools_of(meta),
                ),
            )
            for rel, content in files.items():
                out.writestr(f"{name}/{rel}", content)
        return name, buf.getvalue()


def main() -> int:
    parser = argparse.ArgumentParser(description="批量导入 Claude Code skill 包")
    parser.add_argument("--dir", required=True, help="安装包目录 (含 .zip)")
    parser.add_argument("--map", required=True, help="分类映射 JSON")
    parser.add_argument("--data-dir", default=None, help="数据目录 (默认取应用设置)")
    parser.add_argument("--overwrite", action="store_true", help="覆盖同名专家")
    args = parser.parse_args()

    data_dir = Path(args.data_dir).resolve() if args.data_dir else Path(settings.data_dir)
    mapping = json.loads(Path(args.map).read_text(encoding="utf-8"))
    categories = [str(item).strip() for item in mapping.get("categories") or [] if str(item).strip()]
    experts_map: dict[str, dict] = mapping.get("experts") or {}

    packs = sorted(Path(args.dir).glob("*.zip"))
    if not packs:
        print(f"目录里没有 .zip: {args.dir}")
        return 1

    assignments: dict[str, dict] = {}
    imported: list[str] = []
    failed: list[str] = []
    for pack in packs:
        blob = pack.read_bytes()
        with zipfile.ZipFile(io.BytesIO(blob)) as zf:
            dirs = _skill_dirs(zf)
        if not dirs:
            failed.append(pack.name)
            continue
        for dir_name in dirs:
            hint = str((experts_map.get(dir_name) or {}).get("name") or "")
            result = _normalize(blob, dir_name, hint)
            if result is None:
                failed.append(f"{pack.name}:{dir_name}")
                continue
            name, normalized = result
            report = experts.import_zip(data_dir, normalized, overwrite=args.overwrite)
            if name in report["imported"] or name in report["updated"]:
                imported.append(name)
                category = str((experts_map.get(dir_name) or {}).get("category") or "").strip()
                assignments[name] = {"category": category, "enabled": True}
            else:
                failed.append(f"{pack.name}:{name}")

    # 分类体系 + 归属一次写回 (与设置页保存同一口径)
    # 本机已有的分类保留在前面 —— 批量导入是追加, 不是推倒重来。
    config = experts.load_config(data_dir)
    merged_categories = [
        str(item).strip() for item in config["categories"] if str(item).strip()
    ]
    for item in categories:
        if item not in merged_categories:
            merged_categories.append(item)
    merged = {**config["experts"], **assignments}
    experts.save_layout(data_dir, merged_categories, merged)

    print(f"导入 {len(imported)} 个专家, 分类 {len(merged_categories)} 个, 数据目录 {data_dir}")
    for name in imported:
        print(f"  + {name} [{assignments.get(name, {}).get('category') or '未分类'}]")
    if failed:
        print(f"失败 {len(failed)} 个:")
        for item in failed:
            print(f"  ! {item}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
