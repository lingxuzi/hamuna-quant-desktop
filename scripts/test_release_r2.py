"""release.yml 的 R2 上传步骤自检 —— 用桩 aws 跑真实脚本, 覆盖各配置组合。

跑法: python3 scripts/test_release_r2.py

为什么要有:
    那次失败是「桶名变量没配」→ 拼出 s3:///latest.json → AWS CLI 抛
    'Invalid bucket name ""' 正则错误, 完全指不到缺哪个配置项。
    修法 (凭据逐项点名 + 桶名自动发现) 写在 workflow 的 bash 里, 没有测试
    就只能靠真发一次版来验证 —— 太贵。

本自检从 release.yml 里**抠出真实的那段 bash**, 只把 aws 换成桩, 因此测的
就是发布流水线实际会跑的代码 (不会与 workflow 漂移)。

覆盖:
    1. 凭据齐全 + 桶名已配      → 上传
    2. 凭据齐全 + 未配桶名/单桶  → 自动发现桶名后上传
    3. 凭据齐全 + 未配桶名/多桶  → 报错并列出候选
    4. 凭据齐全 + 未配桶名/无桶  → 报错 (endpoint/权限问题)
    5. 缺凭据                   → 点名缺失的 secret
    6. 桶名含 S3 非法字符        → 上传前拦下
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "release.yml"

# 桩 aws: 替换真实的网络调用, 只保留控制流
STUB_AWS = """#!/usr/bin/env bash
case "$1 $2" in
  "s3 ls")
    [ -n "${FAKE_BUCKETS:-}" ] && printf '%s\\n' "$FAKE_BUCKETS"
    ;;
  "s3 cp")
    echo "UPLOAD_OK $3"
    ;;
  *)
    echo "unexpected aws $*" >&2
    exit 99
    ;;
esac
"""


def _step_script() -> str:
    """从 release.yml 抠出 R2 步骤的 run 块。"""
    import yaml

    wf = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    steps = wf["jobs"]["manifest"]["steps"]
    step = next(s for s in steps if "R2" in s["name"])
    # latest.json 由上一步生成, 本地没有 —— 去掉这道断言
    return step["run"].replace(
        'test -f latest.json || { echo "::error::latest.json 未生成"; exit 1; }', ":"
    )


def _run(script: str, env: dict, stub_dir: Path) -> tuple[int, str]:
    e = dict(os.environ)
    e.update(env)
    e["PATH"] = f"{stub_dir}{os.pathsep}{e['PATH']}"
    proc = subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, env=e, timeout=60
    )
    return proc.returncode, proc.stdout + proc.stderr


def main() -> int:
    script = _step_script()
    base = {
        "AWS_ACCESS_KEY_ID": "ak",
        "AWS_SECRET_ACCESS_KEY": "sk",
        "AWS_ENDPOINT_URL": "https://acct.r2.cloudflarestorage.com",
    }

    with tempfile.TemporaryDirectory() as tmp:
        stub_dir = Path(tmp)
        (stub_dir / "aws").write_text(STUB_AWS, encoding="utf-8")
        (stub_dir / "aws").chmod(0o755)

        # 1. 桶名已配 → 直接上传
        rc, out = _run(script, {**base, "R2_BUCKET": "my-bucket", "FAKE_BUCKETS": ""}, stub_dir)
        assert rc == 0, out
        assert "UPLOAD_OK" in out, out

        # 2. 未配桶名 + 唯一桶 → 自动发现
        rc, out = _run(script, {**base, "R2_BUCKET": "", "FAKE_BUCKETS": "only-bucket\n"}, stub_dir)
        assert rc == 0, out
        assert "自动发现桶名: only-bucket" in out, out
        assert "UPLOAD_OK" in out, out

        # 3. 未配桶名 + 多个桶 → 报错并列出候选
        rc, out = _run(script, {**base, "R2_BUCKET": "", "FAKE_BUCKETS": "b1\nb2\n"}, stub_dir)
        assert rc == 1, out
        assert "可见多个桶" in out and "b1" in out and "b2" in out, out

        # 4. 未配桶名 + 一个桶都列不出 → 报错指向 endpoint/权限
        rc, out = _run(script, {**base, "R2_BUCKET": "", "FAKE_BUCKETS": ""}, stub_dir)
        assert rc == 1, out
        assert "列不出任何桶" in out, out

        # 5. 缺凭据 → 点名缺失的 secret (按 secret 名报错, 不是 env 名)
        rc, out = _run(
            script,
            {**base, "AWS_ENDPOINT_URL": "", "R2_BUCKET": "b", "FAKE_BUCKETS": ""},
            stub_dir,
        )
        assert rc == 1, out
        assert "secret R2_ENDPOINT_URL" in out, out
        assert "AWS_ENDPOINT_URL" not in out.split("::error::")[1].split("——")[0], out

        # 6. 桶名含 S3 非法字符 → 上传前拦下
        for bad in ("MyBucket", "my_bucket", "my bucket"):
            rc, out = _run(script, {**base, "R2_BUCKET": bad, "FAKE_BUCKETS": ""}, stub_dir)
            assert rc == 1, out
            assert "桶名非法" in out, out
            assert "UPLOAD_OK" not in out, out

    print("release.yml R2 步骤自检通过: 6 种配置组合行为符合预期")
    return 0


if __name__ == "__main__":
    sys.exit(main())
