#!/bin/bash
# 安全删除（唯一允许的删除方式）：移入项目回收站，可原路找回。禁止 rm。
#
# 背景（2026-09-10 建立，同日复核更正）：系统 `trash` 改名并非"无效"——
# 文件确实进入了 ~/.Trash；是**本进程读不到**（macOS TCC 拦截，ls 报
# "Operation not permitted" 且静默返回空），当时据此误判为"假进废纸篓"。
# 教训：受限导致的假阴性不能当事实（同 shell grep 假阴性）。
# 仍统一用「项目内回收站」的真正理由：**可验证、可 grep 追溯、不依赖 TCC 授权**，
# 且删除原因与时间留痕（MANIFEST.md）。
#
# 用法：
#   scripts/safe-trash.sh <路径...> [--reason "删除原因"]
#   scripts/safe-trash.sh --list                    # 查看回收站
#   scripts/safe-trash.sh --restore <条目名>         # 找回
#
# 纪律：任何删除动作（含 git rm）之前，先过本脚本；git 跟踪文件另受 git 历史保护。

set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DAY="$(date +%Y-%m-%d)"
TRASH="$ROOT/.workbuddy/trash/$DAY"
MANIFEST="$ROOT/.workbuddy/trash/MANIFEST.md"
STAMP="$(date +%H%M%S)"

mkdir -p "$TRASH"

if [[ "${1:-}" == "--list" ]]; then
  echo "回收站：$ROOT/.workbuddy/trash/"
  find "$ROOT/.workbuddy/trash" -mindepth 2 -maxdepth 5 -type f -not -name "MANIFEST.md" | sed "s|$ROOT/.workbuddy/trash/||" | sort
  exit 0
fi

if [[ "${1:-}" == "--restore" ]]; then
  src="$ROOT/.workbuddy/trash/$2"
  [[ -e "$src" ]] || { echo "❌ 找不到：$2"; exit 1; }
  rel="${2#*/}"     # 剥离 YYYY-MM-DD/ 前缀，回到原相对路径
  dest="$ROOT/$rel"
  mkdir -p "$(dirname "$dest")"
  mv "$src" "$dest"
  echo "✅ 已找回：$dest"
  exit 0
fi

REASON="未说明"
ARGS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --reason) REASON="$2"; shift 2;;
    *) ARGS+=("$1"); shift;;
  esac
done

[[ ${#ARGS[@]} -eq 0 ]] && { echo "用法: $0 <路径...> [--reason \"原因\"]"; exit 1; }

echo "🗑️  移入项目回收站（可找回）："
for p in "${ARGS[@]}"; do
  [[ -e "$p" ]] || { echo "   ⏭  跳过（不存在）: $p"; continue; }
  # 项目内文件保留相对路径；项目外文件用 basename
  if [[ "$(cd "$(dirname "$p")" && pwd)" == "$ROOT"* ]]; then
    rel="${p#"$ROOT"/}"
  else
    rel="external/$(basename "$p")"
  fi
  dest="$TRASH/$rel"
  if [[ -e "$dest" ]]; then dest="$TRASH/$rel.$STAMP"; fi
  mkdir -p "$(dirname "$dest")"
  mv "$p" "$dest"
  echo "   ✅ $p"
  echo "      → .workbuddy/trash/$DAY/$rel"
  printf '| %s %s | %s | %s |\n' "$DAY" "$STAMP" "$rel" "$REASON" >> "$MANIFEST"
done
echo
echo "↩️  找回：scripts/safe-trash.sh --restore \"$DAY/<路径>\""
echo "📋 清单：cat .workbuddy/trash/MANIFEST.md"
