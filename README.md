# CreatorLog (`clog`)

`clog` 是一個以「創作者主檔」為核心的本地 SQLite 追蹤工具，用來整理：

- 創作者主檔
- 平台帳號 / 作者頁 URL
- 名稱歷史
- 貼文 metadata
- 提醒
- 工作紀錄

目前資料模型已切到 profile-centric identity model：外部身份的單一真相來源是 `creator_profiles`。

## 核心概念

`clog` 把 identity 分成三層：

- `creators`
  - 穩定主檔
  - `primary_name` 只是主標籤，不代表平台當前顯示名稱
- `creator_profiles`
  - 系統已知的平台帳號
  - 唯一身份鍵是 `(platform, platform_id)`
- `profile_urls`
  - 掛在 profile 下的作者頁 URL

名稱歷史用 `creator_aliases` 表示：

- `profile_id = NULL`：generic alias
- `profile_id != NULL`：某個 profile 的名稱歷史

貼文則必須掛在既有 profile 上，不能只掛 creator。

## 執行模式

- `clog`：啟動 TUI
- `clog ...`：走 CLI

範例：

```bash
clog
clog a "creator name" https://x.com/example
clog n #1 "old alias"
clog u #1 https://www.pixiv.net/users/123456
clog p https://x.com/example/status/123
```

## 常用命令

| 功能 | 命令 |
|---|---|
| 初始化 | `clog i` / `clog init` |
| 新增創作者 | `clog a NAME [URL] [--note TEXT]` |
| 新增名稱 | `clog n TARGET NAME [CONTEXT]` |
| 新增作者頁 URL | `clog u TARGET URL [--note TEXT]` |
| 記錄貼文 | `clog p URL [TARGET] [--note TEXT]` |
| 設提醒 | `clog r TARGET WHEN` |
| 查看提醒 | `clog d` |
| 近期清單 | `clog ls` |
| 新增工作紀錄 | `clog c TARGET CONTENT` |
| 搜尋 | `clog s QUERY` / `clog QUERY` |
| 查看主檔 | `clog v TARGET` |

## TARGET 解析

大多數命令的 `TARGET` 支援：

```text
#12
https://platform/profile
platform:platform_id
名稱（前提是只命中一位 creator）
```

如果同名或模糊命中多位 creator，程式會拒絕並列出候選，要求改用 `#id` / URL / `platform:platform_id`。

## `add name`

CLI 形式：

```bash
clog n TARGET NAME
clog n TARGET NAME pixiv
clog n TARGET NAME https://site.example/creator-page
```

語意：

- 沒有 `CONTEXT`
  - 建立 generic alias
- `CONTEXT` 是平台字串
  - 在該 creator 底下找既有 profile
  - 剛好一筆時，建立 profile-bound rename
  - 0 筆或多筆都會拒絕
- `CONTEXT` 是作者頁 URL
  - 走 `add url` 的 profile attach pipeline
  - 成功後把 alias 綁到該 profile

TUI 形式：

- 只能在目前選中的 creator 上操作
- 使用者先輸入名稱
- 再從「Generic Alias / 既有 profile / Via Author URL」三種路徑中選一條
- 不提供自由輸入 platform / pid 欄位

## `add url`

CLI 形式：

```bash
clog u TARGET URL [--note TEXT]
```

規則：

- 只接受作者頁 URL，不接受使用者手填 platform / platform_id
- 先 canonicalize，再用 gallery-dl extractor 類型判斷：
  - `post-like`：拒絕，改用 `clog p`
  - `profile-like` / `unknown`：允許
- metadata 成功時：
  - resolve 或 upsert resolved profile
  - attach `profile_urls`
- metadata 失敗或不足時：
  - 仍建立 unresolved profile + profile_url

## `add post`

CLI 形式：

```bash
clog p URL [TARGET] [--note TEXT]
```

規則：

- 只接受 post URL
- 一定會跑 gallery-dl metadata
- post metadata 必須命中既有 `(platform, platform_id)` profile
- 沒有既有 profile 就拒絕，提示先用 `add url` 或 `add name via URL`
- `TARGET` 只拿來驗證命中的 profile 是否屬於該 creator，不能作 fallback
- `add post` 不會補建或更新 profile / profile_url / alias

## TUI

TUI 目前已配合 profile-centric schema 更新：

- creator detail 會顯示 aliases、profiles、profile URLs、recent posts、recent work
- `Add Name` 改成兩段式流程
- `Add URL` 只保留 URL 與 note
- `Record Post` 不再提供「只靠 TARGET 強掛 creator」的舊語意
- 搜尋、recent tables、detail panel 都以 `profile` 為主要外部身份顯示單位

## Worklogs

- `worklogs` 只綁 `creator_id`
- 內容只存單一 `content` 欄位
- `content` 保存 raw markdown source，現在先當純文字顯示
- CLI/TUI 不再提供 tags / paths / urls / metadata JSON 的獨立輸入欄位

快捷鍵：

- `/`：回到搜尋框
- `Ctrl+A`：新增 creator
- `Ctrl+N`：新增名稱
- `Ctrl+U`：新增作者頁 URL
- `Ctrl+P`：記錄貼文
- `Ctrl+R`：設定提醒
- `Ctrl+W`：新增工作紀錄
- `[` / `]`：切換 recent list 頁數
- `F5`：重新整理

## 搜尋

搜尋建立在 SQLite FTS5 上，輸出以 creator 分組。索引來源包含：

- creator
- alias
- profile
- profile_url
- post
- reminder
- work

## 資料表

主要表如下：

- `creators`
- `creator_profiles`
- `profile_urls`
- `creator_aliases`
- `posts`
- `reminders`
- `worklogs`
- `profile_enrichment_jobs`
- `search_fts`

完整 ER 與欄位說明見 [docs/ER.md](docs/ER.md)。

## 開發備註

- 這版 schema 是 fresh baseline，不支援 migration
- 若本機舊 dev DB 仍是舊格式，程式會直接報不相容，要求刪掉重建
- metadata worker 只負責 profile 補全，不負責 post 歸屬以外的 side effect

## 原始碼執行

```bash
python clog.py init
python clog.py a example https://x.com/example
python clog.py
```

主要原始碼位於 `src/`：

- `db.py`：schema 與查詢
- `service.py`：命令業務邏輯
- `gallery.py`：gallery-dl 整合
- `worker.py`：profile enrichment worker
- `render.py`：detail rendering
- `cli.py`：CLI dispatch
- `tui.py`：Textual TUI
