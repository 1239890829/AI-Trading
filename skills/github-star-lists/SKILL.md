---
name: github-star-lists
description: 精确拉取用户 GitHub 的 Star 分组（Lists）及组内仓库。当用户要求"研读我 star 里某分组的仓库""我收藏的 X 类项目""GitHub 分组 star"时使用——REST 没有这个能力，必须走 GraphQL，且 UserListItems 是 union 类型，直接选字段会报 selectionMismatch。
agent_created: true
---

# GitHub Star 分组（Lists）精确查询

## 何时用

用户说"我 GitHub 上 star 分组为 X 的仓库""研读我收藏的 Y 类项目"。
不要按关键词去全量 star 里"猜哪些相关"——实测会漏也会误收（曾漏 3 个高星、误收 2 个）。

## 前置

`GITHUB_TOKEN`（读权限即可）。本机通常在 `~/.zshenv`，先 `source ~/.zshenv` 再取 `$GITHUB_TOKEN`。

## 步骤

### 1. 列出分组，拿到 list id

```bash
source ~/.zshenv
curl -s -X POST -H "Authorization: Bearer $GITHUB_TOKEN" -H "Content-Type: application/json" \
  -d '{"query":"query { viewer { login lists(first: 50) { nodes { id name description items { totalCount } } } } }"}' \
  https://api.github.com/graphql
```

返回的 `id` 形如 `UL_kwDOA1-Anc4Ah9q3`。

> REST 的 `/user/lists` 不存在（404），只有 GraphQL。

### 2. 拉分组内仓库

```bash
curl -s -X POST -H "Authorization: Bearer $GITHUB_TOKEN" -H "Content-Type: application/json" \
  -d '{"query":"query { node(id: \"UL_kwDOA1-Anc4Ah9q3\") { ... on UserList { name items(first: 50) { nodes { ... on Repository { nameWithOwner description stargazerCount primaryLanguage { name } url pushedAt } } } } } }"}' \
  https://api.github.com/graphql
```

## 坑（必踩一次）

**`UserListItems` 是 union 类型**，`items.nodes` 下不能直接选 `nameWithOwner` 等字段，否则报：

```
"Selections can't be made directly on unions (see selections on UserListItems)"
```

必须写成 `... on Repository { ... }`。

## 输出建议

拿到清单后，与已有分析文档/已知仓库做**差集比对**，明确列出"漏了哪些、误收了哪些"——
差集往往比清单本身更有价值（漏掉的高星项目常对应未被覆盖的能力面）。
