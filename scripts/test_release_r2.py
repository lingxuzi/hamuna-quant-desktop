"""release.yml 的 R2 上传步骤自检 —— 用桩 aws/curl 跑真实脚本, 覆盖各配置组合。

跑法: python3 scripts/test_release_r2.py

为什么要有:
    那次失败是「桶名变量没配」→ 拼出 s3:///latest.json → AWS CLI 抛
    'Invalid bucket name ""' 正则错误, 完全指不到缺哪个配置项。
    修法 (凭据逐项点名 + 桶名四级来源 + endpoint 归一 + path-style + CORS 自检)
    写在 workflow 的 bash 里, 没有测试就只能靠真发一次版来验证 —— 太贵。

本自检从 release.yml 里**抠出真实的那段 bash**, 只把 aws / curl 换成桩, 因此
测的就是发布流水线实际会跑的代码 (不会与 workflow 漂移)。

覆盖:
    1. 桶名已配                     → 上传 (path-style + 账户级端点)
    2. endpoint 是桶级写法           → 从路径取桶名后上传
    3. 更新基址是 r2.dev 域名        → 反推桶名后上传 (桶名带/不带 pub- 两种)
    4. 反推的桶名都不存在            → 上传报 NoSuchBucket
    5. ListBuckets 单桶/多桶/无桶     → 自动发现 / 报错 / 报错并露出真实 stderr
    6. 缺凭据                        → 点名缺失的 secret
    7. 桶名含 S3 非法字符 / endpoint 非法 → 上传前拦下
    8. 上传后公网自检                 → 缺 CORS 头则报错并给出修复用的 JSON
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
# (s3 ls 的输出形态照抄真实 CLI: 桶名以 TAB 分隔, 挤在一行)
STUB_AWS = """#!/usr/bin/env bash
case "$1 $2" in
  "s3 ls")
    # 桶存在性探测: aws s3 ls s3://<bucket>/ --max-items 1
    for a in "$@"; do
      case "$a" in
        s3://*)
          b="${a#s3://}"; b="${b%%/*}"
          if [ -z "${FAKE_EXISTING_BUCKETS:-}" ]; then exit 0; fi
          case ",${FAKE_EXISTING_BUCKETS}," in
            *",$b,"*) exit 0 ;;
          esac
          echo "An error occurred (NoSuchBucket) when calling the ListObjectsV2 operation: The specified bucket does not exist" >&2
          exit 1 ;;
      esac
    done
    # ListBuckets 自动发现
    if [ -n "${FAKE_LS_ERR:-}" ]; then echo "$FAKE_LS_ERR" >&2; exit 1; fi
    [ -n "${FAKE_BUCKETS:-}" ] && printf '%s\\n' "$FAKE_BUCKETS"
    ;;
  "s3 cp")
    for a in "$@"; do
      case "$a" in
        s3://*)
          b="${a#s3://}"; b="${b%%/*}"
          if [ -n "${FAKE_EXISTING_BUCKETS:-}" ]; then
            case ",${FAKE_EXISTING_BUCKETS}," in
              *",$b,"*) ;;
              *)
                echo "An error occurred (NoSuchBucket) when calling the PutObject operation: The specified bucket does not exist" >&2
                exit 1 ;;
            esac
          fi ;;
      esac
    done
    echo "UPLOAD_OK $*"
    # 回显 path-style 配置, 供测试断言请求 URL 的形态
    [ -n "${AWS_CONFIG_FILE:-}" ] && cat "$AWS_CONFIG_FILE"
    ;;
  *)
    echo "unexpected aws $*" >&2
    exit 99
    ;;
esac
"""

# 桩 curl: 模拟公网读取 (客户端就是从这个地址读清单)
STUB_CURL = """#!/usr/bin/env bash
out=""; url=""; prev=""
for a in "$@"; do
  [ "$prev" = "-D" ] && out="$a"
  case "$a" in http://*|https://*) url="$a" ;; esac
  prev="$a"
done
code="${FAKE_HTTP_CODE:-200}"
{
  printf 'HTTP/1.1 %s\\r\\n' "$code"
  [ "${FAKE_CORS:-0}" = "1" ] && printf 'access-control-allow-origin: *\\r\\n'
  printf 'content-type: application/json\\r\\n\\r\\n'
} > "${out:-/dev/stdout}"
echo "CURL_URL $url" >&2
case "$code" in
  2*|3*) exit 0 ;;
  *) exit 22 ;;
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
        "UPDATE_BASE_URL": "https://pub-mybucket.r2.dev",
        "FAKE_CORS": "1",
    }

    with tempfile.TemporaryDirectory() as tmp:
        stub_dir = Path(tmp)
        for name, body in (("aws", STUB_AWS), ("curl", STUB_CURL)):
            p = stub_dir / name
            p.write_text(body, encoding="utf-8")
            p.chmod(0o755)

        # 1. 桶名已配 → 直接上传 (账户级端点 + path-style)
        rc, out = _run(script, {**base, "R2_BUCKET": "my-bucket"}, stub_dir)
        assert rc == 0, out
        assert "UPLOAD_OK" in out, out
        assert "s3://my-bucket/latest.json" in out, out
        assert "--endpoint-url https://acct.r2.cloudflarestorage.com" in out, out
        assert "addressing_style = path" in out, out
        # 公网自检打的正是更新基址
        assert "CURL_URL https://pub-mybucket.r2.dev/latest.json" in out, out

        # 2. endpoint 是桶级写法 → 从路径取桶名, 端点归一到账户级
        rc, out = _run(
            script,
            {
                **base,
                "AWS_ENDPOINT_URL": "https://acct.r2.cloudflarestorage.com/path-bucket",
                "R2_BUCKET": "",
            },
            stub_dir,
        )
        assert rc == 0, out
        assert "从 R2 endpoint 路径取到桶名: path-bucket" in out, out
        assert "s3://path-bucket/latest.json" in out, out
        assert "--endpoint-url https://acct.r2.cloudflarestorage.com " in out, out

        # 3. 更新基址是 r2.dev 域名 → 反推桶名 (桶名不带 pub- 前缀)
        rc, out = _run(
            script,
            {**base, "R2_BUCKET": "", "FAKE_EXISTING_BUCKETS": "mybucket"},
            stub_dir,
        )
        assert rc == 0, out
        assert "反推出桶名: mybucket" in out, out
        assert "s3://mybucket/latest.json" in out, out

        # 4. 桶名本身带 pub- 前缀 → 第一个候选就该命中
        rc, out = _run(
            script,
            {**base, "R2_BUCKET": "", "FAKE_EXISTING_BUCKETS": "pub-mybucket"},
            stub_dir,
        )
        assert rc == 0, out
        assert "反推出桶名: pub-mybucket" in out, out
        assert "s3://pub-mybucket/latest.json" in out, out

        # 5. 反推出的桶名都不存在 → 上传报 NoSuchBucket
        rc, out = _run(
            script,
            {**base, "R2_BUCKET": "", "FAKE_EXISTING_BUCKETS": "other-bucket"},
            stub_dir,
        )
        assert rc == 1, out
        assert "NoSuchBucket" in out, out
        assert "UPLOAD_OK" not in out, out

        # 6. 定不出桶名 → ListBuckets 自动发现 (单桶)
        rc, out = _run(
            script,
            {
                **base,
                "UPDATE_BASE_URL": "https://updates.example.com",
                "R2_BUCKET": "",
                "FAKE_BUCKETS": "only-bucket",
            },
            stub_dir,
        )
        assert rc == 0, out
        assert "自动发现桶名: only-bucket" in out, out
        assert "UPLOAD_OK" in out, out

        # 7. ListBuckets 可见多个桶 → 报错并列出候选 (真实 CLI 用 TAB 分隔, 挤一行)
        rc, out = _run(
            script,
            {
                **base,
                "UPDATE_BASE_URL": "https://updates.example.com",
                "R2_BUCKET": "",
                "FAKE_BUCKETS": "b1\tb2",
            },
            stub_dir,
        )
        assert rc == 1, out
        assert "可见多个桶" in out and "b1" in out and "b2" in out, out
        assert "UPLOAD_OK" not in out, out

        # 8. 一个桶都列不出 → 报错, 且露出 aws 的真实报错
        rc, out = _run(
            script,
            {
                **base,
                "UPDATE_BASE_URL": "https://updates.example.com",
                "R2_BUCKET": "",
                "FAKE_BUCKETS": "",
                "FAKE_LS_ERR": "AccessDenied: not authorized",
            },
            stub_dir,
        )
        assert rc == 1, out
        assert "列不出任何桶" in out, out
        assert "AccessDenied: not authorized" in out, out
        assert "HQUANT_R2_BUCKET" in out, out

        # 9. 缺凭据 → 点名缺失的 secret (按 secret 名报错, 不是 env 名)
        rc, out = _run(
            script,
            {**base, "AWS_ENDPOINT_URL": "", "R2_BUCKET": "b"},
            stub_dir,
        )
        assert rc == 1, out
        assert "secret R2_ENDPOINT_URL" in out, out
        assert "AWS_ENDPOINT_URL" not in out.split("::error::")[1].split("——")[0], out

        # 10. 桶名含 S3 非法字符 → 上传前拦下
        for bad in ("MyBucket", "my_bucket", "my bucket"):
            rc, out = _run(script, {**base, "R2_BUCKET": bad}, stub_dir)
            assert rc == 1, out
            assert "桶名非法" in out, out
            assert "UPLOAD_OK" not in out, out

        # 11. endpoint 不是 http(s) → 上传前拦下
        rc, out = _run(
            script,
            {**base, "AWS_ENDPOINT_URL": "acct.r2.cloudflarestorage.com", "R2_BUCKET": "b"},
            stub_dir,
        )
        assert rc == 1, out
        assert "endpoint 非法" in out, out
        assert "UPLOAD_OK" not in out, out

        # 12. 上传成功但桶没开 CORS → 报错并给出修复用的 JSON
        rc, out = _run(script, {**base, "R2_BUCKET": "my-bucket", "FAKE_CORS": "0"}, stub_dir)
        assert rc == 1, out
        assert "UPLOAD_OK" in out, out  # 清单确实传上去了
        assert "没开 CORS" in out, out
        assert "AllowedOrigins" in out, out

        # 13. 上传成功但公网读不到 (桶不公开 / r2.dev 没启用) → 报错
        rc, out = _run(
            script,
            {**base, "R2_BUCKET": "my-bucket", "FAKE_HTTP_CODE": "404"},
            stub_dir,
        )
        assert rc == 1, out
        assert "公网地址读不到清单" in out, out

    print("release.yml R2 步骤自检通过: 13 种配置组合行为符合预期")
    return 0


if __name__ == "__main__":
    sys.exit(main())
