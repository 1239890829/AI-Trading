"""Parquet 快照的容错读取。

为什么要单独一个模块：读取逻辑散在 screener_service 与 market 两处，
都是 `sorted(glob)[-1]` 取最新文件直接读——**最新那份损坏就整个 502**。
实测 2026-08-30 的快照目录里有 7 个损坏文件（完整大小但 thrift 反序列化失败），
只要最新那份落在其中，选股器与情绪端点就全挂，且错误信息还误报成"TDX 源不可用"。

策略（沿用本项目"显式降级"纪律）：
- 从最新往回逐个尝试，跳过读不了的损坏文件
- 记录跳过了哪些、最终用了哪份，调用方据此标注数据时效
- 全都读不了才抛错，且错误必须说清是**本地快照**问题
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

# 最多回退尝试几份。写盘每 5 分钟一次，回退 6 份 ≈ 覆盖半小时。
MAX_ATTEMPTS = 6


@dataclass
class SnapshotRead:
    """读取结果。df 为 None 表示全部不可读。"""

    df: object | None = None  # polars.DataFrame
    path: Path | None = None
    skipped: list[str] = field(default_factory=list)
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.df is not None


def write_parquet_atomic(df: object, path: Path) -> None:
    """原子写 parquet：临时文件 + os.replace。

    直接 write_parquet(目标路径) 时，进程被 kill（重启/部署/崩溃）会留下一个
    **大小正常但内容损坏**的文件，后续任何读取方都会炸。
    临时文件用 .tmp 后缀，不会被 glob("*.parquet") 扫到。
    """
    import os
    import tempfile

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    os.close(fd)
    tmp_path = Path(tmp_name)
    try:
        df.write_parquet(tmp_path)  # type: ignore[attr-defined]
        os.replace(tmp_path, path)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise


def read_parquet_safe(
    path: Path,
    *,
    columns: list[str] | None = None,
) -> tuple[object | None, str | None]:
    """读 parquet，损坏时返回 (None, 错误说明) 而不是让异常炸穿调用方。

    适用于"没有这份数据也能继续跑"的场景（如量比基线、分钟缓存）。
    缺了就不能算的（如全市场快照）请用 read_latest_*，它们会显式报错。
    """
    import polars as pl

    path = Path(path)
    if not path.exists():
        return None, f"文件不存在：{path}"
    try:
        return (pl.read_parquet(path, columns=columns) if columns else pl.read_parquet(path)), None
    except Exception as exc:  # noqa: BLE001 - 损坏文件的异常类型由底层库决定
        log.warning("parquet 不可读，按缺失处理：%s | %s", path, exc)
        return None, f"{type(exc).__name__}: {exc}"


def read_latest_in_dir(
    day_dir: Path,
    *,
    columns: list[str] | None = None,
    max_attempts: int = MAX_ATTEMPTS,
) -> SnapshotRead:
    """读某个日期目录里最新一份**可读**的快照。"""
    import polars as pl

    files = sorted(Path(day_dir).glob("*.parquet"), reverse=True)
    if not files:
        return SnapshotRead(error=f"目录无快照文件：{day_dir}")

    skipped: list[str] = []
    last_err: str | None = None
    for f in files[:max_attempts]:
        try:
            df = pl.read_parquet(f, columns=columns) if columns else pl.read_parquet(f)
        except Exception as exc:  # noqa: BLE001 - 损坏文件的异常类型由底层库决定
            skipped.append(str(f))
            last_err = f"{f.name}: {type(exc).__name__}: {exc}"
            log.warning("快照文件不可读，跳过：%s", last_err)
            continue
        if skipped:
            log.warning("已跳过 %d 个损坏快照，最终使用 %s", len(skipped), f)
        return SnapshotRead(df=df, path=f, skipped=skipped)

    return SnapshotRead(
        skipped=skipped,
        error=f"{day_dir} 内连续 {len(skipped)} 份快照均不可读（最后错误 {last_err}）",
    )


def read_latest_snapshot(
    parquet_dir: Path,
    *,
    columns: list[str] | None = None,
    max_attempts: int = MAX_ATTEMPTS,
) -> SnapshotRead:
    """读最新一份**可读**的全市场快照（跨日期目录回退）。"""
    base = Path(parquet_dir) / "snapshots"
    if not base.exists():
        return SnapshotRead(error=f"快照目录不存在：{base}")

    all_skipped: list[str] = []
    last: SnapshotRead | None = None
    for day_dir in sorted((p for p in base.iterdir() if p.is_dir()), reverse=True):
        read = read_latest_in_dir(day_dir, columns=columns, max_attempts=max_attempts)
        if read.ok:
            return SnapshotRead(
                df=read.df, path=read.path, skipped=[*all_skipped, *read.skipped]
            )
        all_skipped.extend(read.skipped)
        last = read

    if all_skipped:
        return SnapshotRead(
            skipped=all_skipped,
            error=f"连续 {len(all_skipped)} 份快照均不可读（最后错误 {last.error if last else None}）",
        )
    return SnapshotRead(error="暂无任何快照文件")
