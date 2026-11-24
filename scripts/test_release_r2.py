"""release.yml 的 R2 上传步骤自检 —— 用桩 aws/curl 跑真实脚本, 覆盖各配置组合。

跑法: python3 scripts/test_release_r2.py

为什么要有:
    那次失败是「桶名变量没配」→ 拼出 s3:///latest.json → AWS CLI 抛
    'Invalid bucket name ""' 正则错误, 完全指不到缺哪个配置项; 后来又出过
    「反推桶名猜错」和「只传了清单没传安装包」两次红。
    修法 (凭据逐项点名 + 桶名来源 + endpoint 归一 + path-style + 安装包与清单
    一起发 + 公网自检) 写在 workflow 的 bash 里, 没有测试就只能靠真发一次版
    来验证 —— 太贵。

本自检从 release.yml 里**抠出真实的那段 bash**, 只把 aws / curl 换成桩, 因此
测的就是发布流水线实际会跑的代码 (不会与 workflow 漂移)。

覆盖:
    1. 桶名已配                     → 上传安装包 + 清单 (path-style + 账户级端点)
    2. endpoint 是桶级写法           → 从路径取桶名后上传
    3. 更新基址是 pub-<HASH>.r2.dev  → **不**拿它当桶名 (那个哈希与桶名无关)
    4. ListBuckets 单桶/多桶/无桶     → 自动发现 / 报错 / 报错并露出真实 stderr
    5. 缺凭据                        → 点名缺失的 secret
    6. 桶名含 S3 非法字符 / endpoint 非法 → 上传前拦下
    7. 上传后公网自检                 → 缺 CORS 头则报错并给出修复用的 JSON
    8. 清单里的地址不指向更新基址     → 报错
    9. 清单里的安装包地址下不动       → 报错
"""
from __future__ import annotations

import json
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
    if [ -n "${FAKE_LS_ERR:-}" ]; then echo "$FAKE_LS_ERR" >&2; exit 1; fi
    [ -n "${FAKE_BUCKETS:-}" ] && printf '%s\\n' "$FAKE_BUCKETS"
    ;;
  "s3 cp")
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

# 桩 curl: 模拟公网读取 (客户端就是从这些地址读清单和安装包)
STUB_CURL = """#!/usr/bin/env bash
# 头文件与正文分别落到 -D / -o 指定的位置 (两者可能同时出现:
# -D hdr -o /dev/null 表示「头写到 hdr, 正文丢掉」)
hdrout=""; bodyout=""; url=""; fmt=""
for a in "$@"; do
  [ "$prev" = "-D" ] && hdrout="$a"
  [ "$prev" = "-o" ] && bodyout="$a"
  [ "$prev" = "-w" ] && fmt="$a"
  case "$a" in http://*|https://*) url="$a" ;; esac
  prev="$a"
done
echo "CURL_URL $url" >&2
# FAKE_ASSET_CODE: 只作用于安装包 (非 latest.json), 用来模拟安装包没传上去
code="${FAKE_HTTP_CODE:-200}"
case "$url" in
  */latest.json) ;;
  *) [ -n "${FAKE_ASSET_CODE:-}" ] && code="$FAKE_ASSET_CODE" ;;
esac
[ -n "$hdrout" ] && {
  printf 'HTTP/1.1 %s\\r\\n' "$code"
  [ "${FAKE_CORS:-0}" = "1" ] && printf 'access-control-allow-origin: *\\r\\n'
  printf 'content-type: application/json\\r\\n\\r\\n'
} > "$hdrout"
[ -n "$bodyout" ] && [ "$bodyout" != /dev/null ] && printf '{}' > "$bodyout"
# -w '%{http_code}': 真 curl 会把状态码打到 stdout, 这里照做
case "$fmt" in *http_code*) printf '%s' "$code" ;; esac
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
    # latest.json 由前一步生成, 这里由 _fixture 造 —— 去掉这道断言
    return step["run"].replace(
        'test -f latest.json || { echo "::error::latest.json 未生成"; exit 1; }', ":"
    )


def _fixture(work: Path, *, version: str = "0.3.31", base: str, url_override: str = "") -> None:
    """造出上一步的产物: assets/ 下的安装包 + 同目录的 latest.json。"""
    (work / "assets").mkdir(parents=True, exist_ok=True)
    exe = f"HQuant-Setup-x64-{version}.exe"
    (work / "assets" / exe).write_bytes(b"stub-installer")
    manifest = {
        "version": version,
        "tag": f"v{version}",
        "channels": {
            "windows": {
                "name": exe,
                "url": url_override or f"{base}/{exe}",
                "size": 14,
                "sha256": "0" * 64,
            }
        },
    }
    (work / "latest.json").write_text(json.dumps(manifest), encoding="utf-8")


def _run(script: str, env: dict, stub_dir: Path, work: Path) -> tuple[int, str]:
    e = dict(os.environ)
    e.update(env)
    e["PATH"] = f"{stub_dir}{os.pathsep}{e['PATH']}"
    proc = subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, env=e, cwd=work, timeout=60
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

    def run(env: dict, *, url_override: str = "") -> tuple[int, str]:
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / "work"
            work.mkdir()
            stub_dir = Path(tmp) / "bin"
            stub_dir.mkdir()
            for name, body in (("aws", STUB_AWS), ("curl", STUB_CURL)):
                p = stub_dir / name
                p.write_text(body, encoding="utf-8")
                p.chmod(0o755)
            _fixture(work, base=env.get("UPDATE_BASE_URL", ""), url_override=url_override)
            return _run(script, env, stub_dir, work)

    # 1. 桶名已配 → 上传安装包 + 清单 (账户级端点 + path-style)
    rc, out = run({**base, "R2_BUCKET": "my-bucket"})
    assert rc == 0, out
    assert "UPLOAD_OK" in out, out
    assert "s3 cp assets/HQuant-Setup-x64-0.3.31.exe s3://my-bucket/HQuant-Setup-x64-0.3.31.exe" in out, out
    assert "s3 cp latest.json s3://my-bucket/latest.json" in out, out
    # 安装包先于清单 —— 顺序反了会出现「清单已更新、安装包还没到」的空窗
    assert out.index("UPLOAD_OK s3 cp assets/") < out.index("UPLOAD_OK s3 cp latest.json") , out
    assert "--endpoint-url https://acct.r2.cloudflarestorage.com" in out, out
    assert "addressing_style = path" in out, out
    # 公网自检打的正是更新基址
    assert "CURL_URL https://pub-mybucket.r2.dev/latest.json" in out, out
    assert "安装包可下载: https://pub-mybucket.r2.dev/HQuant-Setup-x64-0.3.31.exe" in out, out

    # 2. endpoint 是桶级写法 → 从路径取桶名, 端点归一到账户级
    rc, out = run({**base, "AWS_ENDPOINT_URL": "https://acct.r2.cloudflarestorage.com/path-bucket", "R2_BUCKET": ""})
    assert rc == 0, out
    assert "从 R2 endpoint 路径取到桶名: path-bucket" in out, out
    assert "s3://path-bucket/latest.json" in out, out
    assert "--endpoint-url https://acct.r2.cloudflarestorage.com " in out, out

    # 3. 回归: 那次真发版的失败 —— 更新基址是 pub-<HASH>.r2.dev,
    #    <HASH> 与桶名无关, 绝不能拿它 (或去掉 pub- 后的它) 当桶名。
    #    定不出桶名就该走 ListBuckets, 而不是猜一个去撞 NoSuchBucket。
    rc, out = run(
        {
            **base,
            "UPDATE_BASE_URL": "https://pub-ca588f64114640eba1c795d23d960d13.r2.dev",
            "R2_BUCKET": "",
            "FAKE_BUCKETS": "real-bucket",
        }
    )
    assert rc == 0, out
    # 那个哈希只允许出现在更新基址 URL 里, 不能出现在任何 s3:// 桶名位置
    assert "s3://ca588f64114640eba1c795d23d960d13" not in out, out
    assert "s3://pub-ca588f64114640eba1c795d23d960d13" not in out, out
    assert "自动发现桶名: real-bucket" in out, out
    assert "s3://real-bucket/latest.json" in out, out

    # 4. 定不出桶名 → ListBuckets 自动发现 (单桶)
    rc, out = run(
        {
            **base,
            "UPDATE_BASE_URL": "https://updates.example.com",
            "R2_BUCKET": "",
            "FAKE_BUCKETS": "only-bucket",
        }
    )
    assert rc == 0, out
    assert "自动发现桶名: only-bucket" in out, out
    assert "UPLOAD_OK" in out, out

    # 5. ListBuckets 可见多个桶 → 报错并列出候选 (真实 CLI 用 TAB 分隔, 挤一行)
    rc, out = run(
        {
            **base,
            "UPDATE_BASE_URL": "https://updates.example.com",
            "R2_BUCKET": "",
            "FAKE_BUCKETS": "b1\tb2",
        }
    )
    assert rc == 1, out
    assert "可见多个桶" in out and "b1" in out and "b2" in out, out
    assert "UPLOAD_OK" not in out, out

    # 6. 一个桶都列不出 → 报错, 且露出 aws 的真实报错
    rc, out = run(
        {
            **base,
            "UPDATE_BASE_URL": "https://updates.example.com",
            "R2_BUCKET": "",
            "FAKE_BUCKETS": "",
            "FAKE_LS_ERR": "AccessDenied: not authorized",
        }
    )
    assert rc == 1, out
    assert "列不出任何桶" in out, out
    assert "AccessDenied: not authorized" in out, out
    assert "HQUANT_R2_BUCKET" in out, out

    # 7. 缺凭据 → 点名缺失的 secret (按 secret 名报错, 不是 env 名)
    rc, out = run({**base, "AWS_ENDPOINT_URL": "", "R2_BUCKET": "b"})
    assert rc == 1, out
    assert "secret R2_ENDPOINT_URL" in out, out
    assert "AWS_ENDPOINT_URL" not in out.split("::error::")[1].split("——")[0], out

    # 8. 桶名含 S3 非法字符 → 上传前拦下
    for bad in ("MyBucket", "my_bucket", "my bucket"):
        rc, out = run({**base, "R2_BUCKET": bad})
        assert rc == 1, out
        assert "桶名非法" in out, out
        assert "UPLOAD_OK" not in out, out

    # 9. endpoint 不是 http(s) → 上传前拦下
    rc, out = run({**base, "AWS_ENDPOINT_URL": "acct.r2.cloudflarestorage.com", "R2_BUCKET": "b"})
    assert rc == 1, out
    assert "endpoint 非法" in out, out
    assert "UPLOAD_OK" not in out, out

    # 10. 上传成功但桶没开 CORS → 报错并给出修复用的 JSON
    rc, out = run({**base, "R2_BUCKET": "my-bucket", "FAKE_CORS": "0"})
    assert rc == 1, out
    assert "UPLOAD_OK" in out, out  # 清单确实传上去了
    assert "没开 CORS" in out, out
    assert "AllowedOrigins" in out, out

    # 11. 上传成功但公网读不到 (桶不公开 / r2.dev 没启用) → 报错
    rc, out = run({**base, "R2_BUCKET": "my-bucket", "FAKE_HTTP_CODE": "404"})
    assert rc == 1, out
    assert "公网地址读不到清单" in out, out

    # 12. 清单里的下载地址没指向更新基址 (上一版就是这样: 指向 GitHub) → 报错
    rc, out = run(
        {**base, "R2_BUCKET": "my-bucket"},
        url_override="https://github.com/x/y/releases/download/v0.3.31/HQuant-Setup-x64-0.3.31.exe",
    )
    assert rc == 1, out
    assert "没指向更新基址" in out, out

    # 13. 清单里写了安装包, 但 R2 上没有 (没传上去) → 报错
    rc, out = run({**base, "R2_BUCKET": "my-bucket", "FAKE_ASSET_CODE": "404"})
    assert rc == 1, out
    assert "安装包地址不可用" in out, out

    print("release.yml R2 步骤自检通过: 13 种配置组合行为符合预期")
    return 0


if __name__ == "__main__":
    sys.exit(main())
