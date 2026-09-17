"""源码守卫的进程内解析复用；调用方只读 AST，不得修改节点。

每次仍读取完整文件，内容或文件名变化均重新解析；不缓存目录清单与判定结果。
"""
import ast
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=512)
def _parse_source(source: str, filename: str) -> ast.Module:
    return ast.parse(source, filename=filename)


def read_source_ast(path: Path) -> ast.Module:
    return _parse_source(path.read_text(encoding="utf-8"), str(path))
