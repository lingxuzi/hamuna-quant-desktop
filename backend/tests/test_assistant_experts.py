"""专家 (skill) 管理 —— 分类体系、专家正文增删改与内置专家只读边界。

一个 skill = 一个专家: 正文在 SKILL.md, 分类归属与启用状态在
<data_dir>/assistant/experts.json。内置专家 (仓库根) 正文只读 —— 页面只能
配它的分类与启用状态, 改了正文会在下次部署被覆盖, 也会脏了仓库。
"""
from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.custom.assistant import experts as assistant_experts
from app.custom.assistant import routes as assistant_routes
from app.custom.assistant import skill_loader
from app.custom.assistant import tools as assistant_tools

BUILTIN_SKILL = """\
---
name: limit-up
description: 涨停板梯队与连板高度分析流程
---

先看 get_market_overview 的涨停梯队。
"""


def _builtin_root(tmp_path: Path, monkeypatch) -> Path:
    root = tmp_path / "builtin"
    (root / "limit-up").mkdir(parents=True)
    (root / "limit-up" / "SKILL.md").write_text(BUILTIN_SKILL, encoding="utf-8")
    monkeypatch.setattr(skill_loader, "BUILTIN_SKILLS_DIR", root)
    return root


@pytest.fixture(autouse=True)
def _no_repo_builtin_skills(tmp_path: Path, monkeypatch) -> None:
    """仓库自带的内置技能不进测试 —— 断言只盯测试自己造的专家。"""
    empty = tmp_path / "builtin-empty"
    empty.mkdir()
    monkeypatch.setattr(skill_loader, "BUILTIN_SKILLS_DIR", empty)


def _create(data_dir: Path, name: str = "my-expert", category: str = "") -> None:
    if category:
        # 归属的分类必须先存在 (后端拒绝挂到不存在的分类下)
        assistant_experts.save_layout(data_dir, [category], {})
    assistant_experts.create_expert(
        data_dir, name=name, description="我的专家用途", content="# 我的专家\n\n正文。", category=category,
    )


def test_create_expert_writes_user_root(tmp_path: Path) -> None:
    """新建专家 → 用户根下多一个 SKILL.md, 且能被 loader 读回来。"""
    _create(tmp_path)

    skill = skill_loader.find(tmp_path, "my-expert")
    assert skill is not None
    assert skill.source == "user"
    assert skill.description == "我的专家用途"
    assert (tmp_path / "assistant" / "skills" / "my-expert" / "SKILL.md").is_file()


def test_create_expert_rejects_duplicate(tmp_path: Path) -> None:
    _create(tmp_path)
    with pytest.raises(ValueError):
        _create(tmp_path)


@pytest.mark.parametrize("bad_name", ["../evil", "a/b", "", "名字带空格"])
def test_create_expert_rejects_bad_name(tmp_path: Path, bad_name: str) -> None:
    """专家名会变成目录名 → 必须是安全 slug。"""
    with pytest.raises(ValueError):
        assistant_experts.create_expert(
            tmp_path, name=bad_name, description="用途", content="正文",
        )


def test_builtin_expert_body_is_read_only(tmp_path: Path, monkeypatch) -> None:
    """内置专家正文只读: 改不了、删不掉, 只能配分类与启用状态。"""
    _builtin_root(tmp_path, monkeypatch)

    with pytest.raises(ValueError):
        assistant_experts.update_expert(tmp_path, "limit-up", content="偷偷改")
    with pytest.raises(ValueError):
        assistant_experts.delete_expert(tmp_path, "limit-up")


def test_delete_expert_removes_dir_and_config(tmp_path: Path) -> None:
    _create(tmp_path, category="自选")
    assistant_experts.delete_expert(tmp_path, "my-expert")

    assert not (tmp_path / "assistant" / "skills" / "my-expert").exists()
    assert assistant_experts.list_experts(tmp_path)["experts"] == []


def test_layout_saves_categories_and_assignments(tmp_path: Path) -> None:
    _create(tmp_path)
    assistant_experts.save_layout(
        tmp_path, ["自选", "基本面"], {"my-expert": {"category": "自选", "enabled": True}},
    )

    body = assistant_experts.list_experts(tmp_path)
    assert body["categories"] == ["自选", "基本面"]
    assert body["experts"][0]["category"] == "自选"


def test_deleting_category_returns_experts_to_uncategorized(tmp_path: Path) -> None:
    """分类被删 → 其下专家回到未分类, 不能悬在已不存在的分类里。"""
    _create(tmp_path)
    assistant_experts.save_layout(tmp_path, ["自选"], {"my-expert": {"category": "自选"}})

    assistant_experts.save_layout(tmp_path, [], {"my-expert": {"category": "自选"}})

    assert assistant_experts.list_experts(tmp_path)["experts"][0]["category"] == ""


def test_assignment_to_unknown_category_is_rejected(tmp_path: Path) -> None:
    _create(tmp_path)
    with pytest.raises(ValueError):
        assistant_experts.save_layout(tmp_path, ["自选"], {"my-expert": {"category": "不存在"}})


def test_create_expert_with_unknown_category_is_rejected(tmp_path: Path) -> None:
    """归属到不存在的分类 → 专家会在界面上无处可寻, 直接拒绝, 且不留孤儿文件。"""
    with pytest.raises(ValueError):
        assistant_experts.create_expert(
            tmp_path, name="ghost", description="用途", content="正文", category="不存在",
        )

    assert assistant_experts.list_experts(tmp_path)["experts"] == []
    assert skill_loader.find(tmp_path, "ghost") is None


@pytest.mark.parametrize("reserved", ["全部", "未分类"])
def test_reserved_category_names_are_rejected(tmp_path: Path, reserved: str) -> None:
    """「全部」「未分类」是界面筛选的保留名, 分类不能叫这个。"""
    with pytest.raises(ValueError):
        assistant_experts.save_layout(tmp_path, [reserved], {})


def test_disabled_expert_is_hidden_from_usable_skills(tmp_path: Path) -> None:
    """停用的专家不进助手目录, 也读不到正文。"""
    _create(tmp_path)
    assistant_experts.save_layout(tmp_path, [], {"my-expert": {"category": "", "enabled": False}})

    assert assistant_experts.usable_skills(tmp_path) == []
    assert assistant_experts.is_enabled(tmp_path, "my-expert") is False


async def test_load_skill_tool_refuses_disabled_expert(tmp_path: Path) -> None:
    _create(tmp_path)
    assistant_experts.save_layout(tmp_path, [], {"my-expert": {"enabled": False}})
    ctx = assistant_tools.ToolContext.build(data_dir=tmp_path)

    payload = await assistant_tools.execute_assistant_tool("load_skill", {"name": "my-expert"}, ctx)

    assert payload["ok"] is False
    assert "停用" in payload["error"]


def test_corrupt_config_fails_loud(tmp_path: Path) -> None:
    """配置损坏要报错, 不能静默当空配置 (那会清掉用户的分类体系)。"""
    path = tmp_path / "assistant" / "experts.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ 不是 json", encoding="utf-8")

    with pytest.raises(ValueError):
        assistant_experts.list_experts(tmp_path)


# ── 导入导出 (zip 专家包) ──
def _pack(categories: list[str], experts: list[dict]) -> bytes:
    """造一个专家包 (zip): manifest.json + 每个专家一个 <name>/SKILL.md。"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        manifest = {
            "version": assistant_experts.EXPORT_VERSION,
            "categories": categories,
            "experts": [
                {
                    "name": item["name"],
                    "description": item.get("description", ""),
                    "category": item.get("category", ""),
                    "enabled": item.get("enabled", True),
                }
                for item in experts
            ],
        }
        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False))
        for item in experts:
            body = item.get("content", "")
            zf.writestr(
                f"{item['name']}/SKILL.md",
                f"---\nname: \"{item['name']}\"\n"
                f"description: \"{item.get('description', '')}\"\n---\n\n{body}\n",
            )
    return buf.getvalue()


def test_export_zip_contains_user_experts_only(tmp_path: Path, monkeypatch) -> None:
    """内置专家不导出 —— 它随程序分发, 导成用户副本反而会盖住内置版本。"""
    _builtin_root(tmp_path, monkeypatch)
    _create(tmp_path, category="自选")

    blob = assistant_experts.export_zip(tmp_path)

    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        names = set(zf.namelist())
        manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
        body = zf.read("my-expert/SKILL.md").decode("utf-8")
    assert manifest["version"] == assistant_experts.EXPORT_VERSION
    assert manifest["categories"] == ["自选"]
    assert [item["name"] for item in manifest["experts"]] == ["my-expert"]
    assert manifest["experts"][0]["category"] == "自选"
    assert "我的专家" in body
    assert "limit-up/SKILL.md" not in names


def test_export_zip_roundtrip_imports_back(tmp_path: Path) -> None:
    """导出的包自己导回来, 专家正文与分类归属都要还原。"""
    _create(tmp_path, category="自选")

    blob = assistant_experts.export_zip(tmp_path)
    other = tmp_path / "other"
    report = assistant_experts.import_zip(other, blob)

    assert report["imported"] == ["my-expert"]
    detail = skill_loader.read_skill(other, "my-expert")
    assert "我的专家" in detail["content"]
    assert assistant_experts.list_experts(other)["experts"][0]["category"] == "自选"


# ── Claude Code skill 包 (没有 manifest.json, 每个技能一个 <name>/SKILL.md) ──
def _claude_pack(skills: dict[str, str], extra: dict[str, str] | None = None) -> bytes:
    """造一个 Claude Code skill 包: <name>/SKILL.md (+ 附属文件)。"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, body in skills.items():
            zf.writestr(f"{name}/SKILL.md", body)
        for rel, text in (extra or {}).items():
            zf.writestr(rel, text)
    return buf.getvalue()


def test_import_zip_accepts_claude_code_pack(tmp_path: Path) -> None:
    """没有清单的 Claude Code 包也导得进 —— 名称/用途取自 frontmatter。"""
    pack = _claude_pack({
        "trend-reading": "---\nname: trend-reading\ndescription: 趋势判读流程\n---\n\n# 趋势\n\n正文。\n",
    })

    report = assistant_experts.import_zip(tmp_path, pack)

    assert report["imported"] == ["trend-reading"]
    detail = skill_loader.read_skill(tmp_path, "trend-reading")
    assert detail["description"] == "趋势判读流程"
    assert "正文" in detail["content"]


def test_import_zip_claude_code_pack_keeps_reference_files(tmp_path: Path) -> None:
    """附属文本文件 (references/) 一并带进来, 脚本与二进制不带。"""
    pack = _claude_pack(
        {"trend-reading": "---\nname: trend-reading\ndescription: 趋势判读\n---\n\n正文。\n"},
        {
            "trend-reading/references/notes.md": "参考材料",
            "trend-reading/scripts/run.py": "print('x')",
        },
    )

    assistant_experts.import_zip(tmp_path, pack)

    skill_dir = tmp_path / "assistant" / "skills" / "trend-reading"
    assert (skill_dir / "references" / "notes.md").read_text(encoding="utf-8") == "参考材料"
    assert not (skill_dir / "scripts").exists()


def test_import_zip_claude_code_pack_without_frontmatter(tmp_path: Path) -> None:
    """外部包常常没有 frontmatter —— 名字退到拼音, 用途从「定位」小节兜底。"""
    pack = _claude_pack({
        "打板回封": "# 打板/回封交易体系 Skill\n\n## 定位\n\n本Skill为超短打板选手提供涨停板分析框架。\n",
    })

    report = assistant_experts.import_zip(tmp_path, pack)

    assert report["imported"] == ["da-ban-hui-feng"]
    detail = skill_loader.read_skill(tmp_path, "da-ban-hui-feng")
    assert detail["description"] == "本Skill为超短打板选手提供涨停板分析框架。"


def test_import_zip_description_prefers_positioning_section(tmp_path: Path) -> None:
    """用途兜底: 优先「定位」段, 不被文档深处的「什么是」子节抢走。"""
    pack = _claude_pack({
        "demo": "# 演示框架\n\n## 定位\n\n这是一段定位说明。\n\n## 1.1 什么是演示\n\n这是定义。\n",
    })

    assistant_experts.import_zip(tmp_path, pack)

    assert skill_loader.read_skill(tmp_path, "demo")["description"] == "这是一段定位说明。"


def test_import_zip_description_drops_skill_md_noise(tmp_path: Path) -> None:
    """标题里的文件名残留 (「SKILL.md — 演示框架」) 不进用途说明。"""
    pack = _claude_pack({"demo": "# SKILL.md — 演示框架\n\n正文。\n"})

    assistant_experts.import_zip(tmp_path, pack)

    assert skill_loader.read_skill(tmp_path, "demo")["description"] == "演示框架"


def test_export_zip_uses_claude_slug_and_keeps_original_name(tmp_path: Path) -> None:
    """导出目录名用 Claude Code 的技能名规范, 原名留在清单里 —— 导回来还是原名。"""
    assistant_experts.create_expert(
        tmp_path, name="My.Expert", description="用途", content="# 正文", category="",
    )

    blob = assistant_experts.export_zip(tmp_path)

    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        names = zf.namelist()
        manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
        body = zf.read("my-expert/SKILL.md").decode("utf-8")
    assert "my-expert/SKILL.md" in names
    assert manifest["experts"][0]["name"] == "My.Expert"
    assert manifest["experts"][0]["dir"] == "my-expert"
    assert 'name: "my-expert"' in body

    other = tmp_path / "other"
    report = assistant_experts.import_zip(other, blob)
    assert report["imported"] == ["My.Expert"]


def test_export_zip_includes_reference_files(tmp_path: Path) -> None:
    """附属文件随包导出 —— 装进 Claude Code 还是完整的 skill。"""
    _create(tmp_path)
    skill_dir = tmp_path / "assistant" / "skills" / "my-expert"
    (skill_dir / "references").mkdir(parents=True)
    (skill_dir / "references" / "notes.md").write_text("参考材料", encoding="utf-8")

    blob = assistant_experts.export_zip(tmp_path)

    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        assert "my-expert/references/notes.md" in zf.namelist()


def test_import_zip_creates_experts_and_merges_categories(tmp_path: Path) -> None:
    """导入: 分类合并 (新增追加), 专家落盘并挂到对应分类。"""
    pack = _pack(["自选", "基本面"], [{
        "name": "pack-expert", "description": "包里的用途", "content": "# 正文",
        "category": "基本面", "enabled": False,
    }])

    report = assistant_experts.import_zip(tmp_path, pack)

    assert report["imported"] == ["pack-expert"]
    assert report["updated"] == []
    assert report["skipped"] == []
    assert report["categories_added"] == ["自选", "基本面"]

    layout = assistant_experts.list_experts(tmp_path)
    assert layout["categories"] == ["自选", "基本面"]
    item = layout["experts"][0]
    assert item["name"] == "pack-expert"
    assert item["category"] == "基本面"
    assert item["enabled"] is False
    assert skill_loader.find(tmp_path, "pack-expert") is not None


def test_import_zip_skips_existing_unless_overwrite(tmp_path: Path) -> None:
    """同名专家默认跳过 —— 不能拿别人的包覆盖本机正文。"""
    _create(tmp_path)
    pack = _pack([], [{"name": "my-expert", "description": "包里的用途", "content": "# 包里的正文"}])

    report = assistant_experts.import_zip(tmp_path, pack)

    assert report["skipped"] == ["my-expert"]
    assert report["imported"] == []
    assert "我的专家" in skill_loader.read_skill(tmp_path, "my-expert")["content"]


def test_import_zip_overwrites_when_allowed(tmp_path: Path) -> None:
    _create(tmp_path)
    pack = _pack([], [{"name": "my-expert", "description": "包里的用途", "content": "# 包里的正文"}])

    report = assistant_experts.import_zip(tmp_path, pack, overwrite=True)

    assert report["updated"] == ["my-expert"]
    assert report["skipped"] == []
    assert "包里的正文" in skill_loader.read_skill(tmp_path, "my-expert")["content"]


def test_import_zip_skips_builtin_expert(tmp_path: Path, monkeypatch) -> None:
    """内置专家正文只读 —— 包里带同名条目也不能顶掉它。"""
    _builtin_root(tmp_path, monkeypatch)
    pack = _pack([], [{"name": "limit-up", "description": "偷偷替换", "content": "# 假的"}])

    report = assistant_experts.import_zip(tmp_path, pack, overwrite=True)

    assert report["skipped"] == ["limit-up"]
    assert skill_loader.find(tmp_path, "limit-up").source == "builtin"
    assert "先看 get_market_overview" in skill_loader.read_skill(tmp_path, "limit-up")["content"]


def test_import_zip_rejects_invalid_entries(tmp_path: Path) -> None:
    """不合法的名字/缺用途的条目跳过, 不进目录 (外部文件不可信)。"""
    pack = _pack([], [
        {"name": "../evil", "description": "越界", "content": "正文"},
        {"name": "no-desc", "description": "", "content": "正文"},
        {"name": "ok-one", "description": "用途", "content": "正文"},
    ])

    report = assistant_experts.import_zip(tmp_path, pack)

    assert report["skipped"] == ["../evil", "no-desc"]
    assert report["imported"] == ["ok-one"]
    assert skill_loader.find(tmp_path, "../evil") is None


def test_import_zip_rejects_broken_archive(tmp_path: Path) -> None:
    """不是 zip 的文件 / 里面没有技能的 zip → 明确报错, 不当空包放行。"""
    with pytest.raises(ValueError, match="压缩包"):
        assistant_experts.import_zip(tmp_path, b"not a zip at all")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("readme.txt", "没有技能")
    with pytest.raises(ValueError, match=r"SKILL\.md"):
        assistant_experts.import_zip(tmp_path, buf.getvalue())


def test_import_zip_unknown_category_falls_back_to_uncategorized(tmp_path: Path) -> None:
    """包里的分类本地没有 → 专家落到未分类, 不能被丢掉。"""
    pack = _pack([], [{
        "name": "pack-expert", "description": "用途", "content": "正文",
        "category": "不存在的分类",
    }])

    assistant_experts.import_zip(tmp_path, pack)

    assert assistant_experts.list_experts(tmp_path)["experts"][0]["category"] == ""


# ── @ 引用 ──
def test_referenced_bodies_only_returns_enabled_experts(tmp_path: Path) -> None:
    """停用 / 不存在的专家不进 prompt。"""
    _create(tmp_path)
    assistant_experts.save_layout(tmp_path, [], {"my-expert": {"enabled": False}})

    assert assistant_experts.referenced_bodies(tmp_path, ["my-expert"]) == []
    assert assistant_experts.referenced_bodies(tmp_path, ["不存在"]) == []


def test_referenced_bodies_returns_content_and_dedupes(tmp_path: Path) -> None:
    _create(tmp_path)

    bodies = assistant_experts.referenced_bodies(tmp_path, ["my-expert", "my-expert"])

    assert len(bodies) == 1
    assert bodies[0]["name"] == "my-expert"
    assert "我的专家" in bodies[0]["content"]


class _StubStore:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir


class _StubRepo:
    def __init__(self, data_dir: Path) -> None:
        self.store = _StubStore(data_dir)


def _client(tmp_path: Path) -> TestClient:
    app = FastAPI()
    app.include_router(assistant_routes.build_router())
    app.state.repo = _StubRepo(tmp_path)
    return TestClient(app)


def test_experts_api_roundtrip(tmp_path: Path) -> None:
    client = _client(tmp_path)

    # 分类先建好, 专家才能挂上去
    assert client.put("/api/custom/assistant/experts/layout", json={
        "categories": ["自选"], "assignments": {},
    }).status_code == 200

    created = client.post("/api/custom/assistant/experts", json={
        "name": "my-expert", "description": "我的专家用途", "content": "正文", "category": "自选",
    })
    assert created.status_code == 200
    assert created.json()["experts"][0]["name"] == "my-expert"

    listed = client.get("/api/custom/assistant/experts").json()
    assert listed["experts"][0]["category"] == "自选"

    layout = client.put("/api/custom/assistant/experts/layout", json={
        "categories": ["自选", "基本面"],
        "assignments": {"my-expert": {"category": "基本面", "enabled": False}},
    })
    assert layout.status_code == 200
    assert layout.json()["experts"][0]["category"] == "基本面"
    assert layout.json()["experts"][0]["enabled"] is False

    removed = client.delete("/api/custom/assistant/experts/my-expert")
    assert removed.status_code == 200
    assert removed.json()["experts"] == []


def test_experts_api_rejects_bad_payload(tmp_path: Path) -> None:
    client = _client(tmp_path)

    resp = client.post("/api/custom/assistant/experts", json={
        "name": "../evil", "description": "用途", "content": "正文",
    })

    assert resp.status_code == 400


def test_experts_api_export_returns_attachment(tmp_path: Path) -> None:
    client = _client(tmp_path)
    client.post("/api/custom/assistant/experts", json={
        "name": "my-expert", "description": "用途", "content": "正文",
    })

    resp = client.get("/api/custom/assistant/experts/export")

    assert resp.status_code == 200
    assert "attachment" in resp.headers["content-disposition"]
    assert resp.headers["content-disposition"].endswith('.zip"')
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
    assert [item["name"] for item in manifest["experts"]] == ["my-expert"]


def test_experts_api_import_returns_report(tmp_path: Path) -> None:
    client = _client(tmp_path)
    pack = _pack(["自选"], [{
        "name": "pack-expert", "description": "用途", "content": "正文", "category": "自选",
    }])

    resp = client.post(
        "/api/custom/assistant/experts/import",
        files={"file": ("experts.zip", pack, "application/zip")},
        data={"overwrite": "false"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["report"]["imported"] == ["pack-expert"]
    assert body["categories"] == ["自选"]
    assert body["experts"][0]["name"] == "pack-expert"


def test_experts_api_import_rejects_broken_pack(tmp_path: Path) -> None:
    client = _client(tmp_path)

    resp = client.post(
        "/api/custom/assistant/experts/import",
        files={"file": ("experts.zip", b"not a zip", "application/zip")},
    )

    assert resp.status_code == 400
