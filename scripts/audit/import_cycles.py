#!/usr/bin/env python3
"""Static import-cycle candidates; runtime import success still needs a real probe.

Extracts SCC/path analysis from the retired project-local tool. Class bodies run
at import time; function bodies are deferred. Conditional imports are included
conservatively. Dynamic imports and implicit package-initializer edges are not
resolved. Exit 1 means a load-time candidate, 2 an incomplete/invalid scan;
deferred-only cycles are informational, never proof of a startup failure.
"""
from __future__ import annotations

import argparse
import ast
from collections import deque
import os
from pathlib import Path
import sys


def project_files(root: Path) -> list[Path]:
    files = []
    for name in ("app", "tests", "scripts"):
        start = root / name
        if not start.is_dir() or start.is_symlink():
            continue
        for directory, dirs, names in os.walk(start, followlinks=False):
            base = Path(directory)
            dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d != "__pycache__"
                             and not (base / d).is_symlink())
            files.extend(base / n for n in names if n.endswith(".py") and not (base / n).is_symlink())
    return sorted(files)


def module_name(path: Path, root: Path) -> str:
    parts = list(path.relative_to(root).with_suffix("").parts)
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


def build_graph(root: Path) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    files = {module_name(p, root): p for p in project_files(root)}
    eager, deferred = ({m: set() for m in files} for _ in range(2))
    for module, path in files.items():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                targets = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                base = module.split(".") if path.name == "__init__.py" else module.split(".")[:-1]
                if node.level:
                    if node.level > len(base):
                        raise ValueError(f"relative import escapes package: {path}:{node.lineno}")
                    base = base[:len(base) - node.level + 1]
                    resolved = ".".join(base + (node.module.split(".") if node.module else []))
                else:
                    resolved = node.module or ""
                targets = [resolved, *(f"{resolved}.{a.name}" for a in node.names if a.name != "*")]
            else:
                continue
            parent, lazy = node, False
            while parent in parents:
                parent = parents[parent]
                if isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                    lazy = True
                    break
            graph = deferred if lazy else eager
            graph[module].update(t for t in targets if t in files)
        deferred[module] -= eager[module]
    return eager, deferred


def strongly_connected(graph: dict[str, set[str]]) -> list[list[str]]:
    """Tarjan SCC, including self edges; deterministic order for comparisons."""
    indices, low, stack, active, result = {}, {}, [], set(), []
    def visit(v):
        indices[v] = low[v] = len(indices)
        stack.append(v)
        active.add(v)
        for w in sorted(graph[v]):
            if w not in indices:
                visit(w)
                low[v] = min(low[v], low[w])
            elif w in active:
                low[v] = min(low[v], indices[w])
        if low[v] == indices[v]:
            component = []
            while True:
                w = stack.pop()
                active.remove(w)
                component.append(w)
                if w == v:
                    break
            if len(component) > 1 or v in graph[v]:
                result.append(sorted(component))
    for v in sorted(graph):
        if v not in indices:
            visit(v)
    return result


def shortest_cycle(graph: dict[str, set[str]], component: list[str]) -> list[str]:
    members, best = set(component), []
    for start in sorted(component):
        queue, seen = deque([[start]]), {start}
        while queue:
            path = queue.popleft()
            for target in sorted(graph[path[-1]] & members):
                if target == start:
                    candidate = path + [start]
                    if not best or len(candidate) < len(best):
                        best = candidate
                    queue.clear()
                    break
                if target not in seen:
                    seen.add(target)
                    queue.append(path + [target])
    return best


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("backend", type=Path)
    args = parser.parse_args(argv)
    root = args.backend.resolve()
    if not (root / "app").is_dir() or (root / "app").is_symlink():
        parser.error("backend must contain a real app directory")
    try:
        eager, deferred = build_graph(root)
        cycles = strongly_connected(eager)
        for label, graph in (("load-time candidates", eager), ("deferred-only candidates", deferred)):
            found = strongly_connected(graph)
            print(f"{label}: {len(found)}; modules={len(graph)}")
            for component in found:
                print("  " + " -> ".join(shortest_cycle(graph, component)))
    except (OSError, UnicodeError, SyntaxError, ValueError, RecursionError) as exc:
        print(f"incomplete scan: {exc}", file=sys.stderr)
        return 2
    return int(bool(cycles))


if __name__ == "__main__":
    raise SystemExit(main())
