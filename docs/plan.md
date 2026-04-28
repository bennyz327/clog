# CreatorLog v2 設計摘要

本檔是目前已實作的 v2 基線摘要，不再描述舊的 fact-splitting schema。

## 核心方向

- creator 是穩定主檔
- profile 是唯一外部身份
- URL 掛在 profile
- alias 可綁 creator，也可綁特定 profile
- post 必須掛在既有 profile

## 命令語意

| 命令 | 語意 |
|---|---|
| `clog a NAME [URL]` | 建立 creator；若附 URL，走作者頁 attach pipeline |
| `clog n TARGET NAME [CONTEXT]` | 建 generic alias、既有 profile rename，或透過作者頁 URL 建 profile 後補 alias |
| `clog u TARGET URL [--note]` | 只處理作者頁 URL；成功則 attach 到 resolved/unresolved profile |
| `clog p URL [TARGET]` | 只處理 post URL；metadata 必須命中既有 profile |

## `add name`

- CLI:
  - `clog n TARGET NAME`
  - `clog n TARGET NAME PLATFORM`
  - `clog n TARGET NAME AUTHOR_URL`
- TUI:
  - 先輸入名稱
  - 再選 `Generic Alias / Existing Profile / Via Author URL`
  - 不提供自由輸入平台或平台 ID

## `add url`

- 只收 `url` 必填、`note` 可選
- 不給使用者手填 platform / platform_id
- 流程：
  1. canonicalize URL
  2. 用 gallery-dl extractor 類型分成 `profile-like / post-like / unknown`
  3. `post-like` 直接拒絕，導去 `add post`
  4. metadata 成功：resolve/upsert profile，再 attach URL
  5. metadata 失敗：仍建立 unresolved profile + profile_url

## `add post`

- 一定跑 post metadata
- 必須命中既有 `(platform, platform_id)` profile
- `TARGET` 只做驗證，不做 fallback
- 不補建 profile / profile_url / alias

## Worker

- 背景 worker 只處理 `profile_enrichment_jobs`
- 只做 profile 補全與衝突標記
- 不做 post side effects

## Schema

目前主要表：

- `creators`
- `creator_profiles`
- `profile_urls`
- `creator_aliases`
- `posts`
- `reminders`
- `worklogs`
- `profile_enrichment_jobs`
- `search_fts`

`worklogs` 只保留 `id, creator_id, content, created_at, updated_at`。`content` 保存 raw markdown source，不再拆 tags / paths / urls / metadata JSON。

完整欄位定義見 `docs/ER.md`。
