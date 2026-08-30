"""守护 .env.example 与 config.py 不漂移。

为什么值得一个测试：配置项散落在 config.py，使用方看的是 .env.example。
新加配置忘记同步是高频事故——本文件就是在这类审计中发现的
（缺 alert/review/news 三组共 11 项，且"写接口鉴权"整段重复了两次）。
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG = REPO_ROOT / "backend" / "app" / "core" / "config.py"
ENV_EXAMPLE = REPO_ROOT / ".env.example"

# 非用户可配（代码常量或派生属性），不需要出现在 .env.example
INTERNAL = {"app_name", "version"}

_FIELD = re.compile(r"^    ([a-z_]+): ", re.M)
_KEY = re.compile(r"^#?\s*ASHARE_([A-Z0-9_]+)=", re.M)


def _config_fields() -> set[str]:
    src = CONFIG.read_text(encoding="utf-8")
    # 只取 Settings 类体内的类型注解字段；@property 是 def，不会命中
    body = src.split("class Settings", 1)[1]
    return {m for m in _FIELD.findall(body) if m not in INTERNAL}


def _env_keys() -> list[str]:
    return _KEY.findall(ENV_EXAMPLE.read_text(encoding="utf-8"))


def test_env_example_covers_every_setting():
    fields = _config_fields()
    documented = {k.lower() for k in _env_keys()}
    missing = sorted(fields - documented)
    assert not missing, (
        f".env.example 缺少配置项：{missing}\n"
        "（在 config.py 加了设置就要同步 .env.example，敏感项可加 # 注释掉）"
    )


def test_env_example_has_no_duplicate_keys():
    keys = _env_keys()
    dupes = sorted({k for k in keys if keys.count(k) > 1})
    assert not dupes, f".env.example 存在重复配置项：{dupes}"


def test_env_example_has_no_dead_keys():
    """.env.example 里的 ASHARE_* 必须真在 config.py 中，否则是过期占位符。"""
    fields = _config_fields()
    dead = sorted({k.lower() for k in _env_keys()} - fields)
    assert not dead, (
        f".env.example 里的配置项在 config.py 中已不存在：{dead}\n"
        "（删字段时同步清理示例，否则部署方会配了一堆无效环境变量）"
    )
