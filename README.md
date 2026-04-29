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

打包後產出兩支 exe：

- `clog.exe`：原生 GUI（PySide6）。雙擊啟動。
- `clog-cli.exe`：純 CLI / 自動化腳本用。

兩支共用同一個 `clog.json` 與 `clog.sqlite`（依 `clog.json` 的 `db_path` 決定）；GUI 跑背景 enrichment 時會自動 spawn 同目錄的 `clog-cli.exe __meta_worker`，不會把 GUI 自己當 worker。

範例 CLI 用法：

```bash
clog-cli a "creator name" https://x.com/example
clog-cli n #1 "old alias"
clog-cli u #1 https://www.pixiv.net/users/123456
clog-cli p https://x.com/example/status/123
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

## Worklogs

- `worklogs` 只綁 `creator_id`
- 內容只存單一 `content` 欄位
- `content` 保存 raw markdown source，現在先當純文字顯示
- CLI 不再提供 tags / paths / urls / metadata JSON 的獨立輸入欄位

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
# CLI 開發入口
python clog_cli.py init
python clog_cli.py a example https://x.com/example

# GUI 開發入口
python clog_gui.py
```

打包：

```bash
pyinstaller clog_cli.spec     # → dist/clog-cli.exe（onefile, 純 CLI 無 PySide6, ~16 MB）
pyinstaller clog.spec         # → dist/clog/clog.exe + dist/clog/_internal/（onedir, GUI 含 PySide6）
```

GUI 採 **onedir** 而非 onefile，原因是 onefile 每次啟動都要把 ~250 MB 解壓到 `%TEMP%`，實測啟動約 18–20 秒；onedir 直接讀檔，啟動降到 0.6 秒。發布時整個 `dist/clog/` 資料夾要一起送給使用者。

主要原始碼位於 `src/`，套件結構：

```
src/
├── core/          # 共用業務邏輯（CLI + GUI 都用）
│   ├── constants.py
│   ├── config.py / utils.py / render.py / gallery.py / service.py
│   ├── enrichment.py    # profile metadata 補全：CLI 走 SubprocessDriver、GUI 走 InProcessRunner
│   ├── pubsub.py / jobs.py
│   └── controller.py    # GUI 用單例：read/write dispatch + DB lock + pubsub + JobPool
├── db/            # SQLite schema + 全部 query function（單檔，re-export）
├── cli/           # CLI 介面層
│   ├── main.py / dispatch.py / commands.py / printers.py / tty.py
└── gui/           # PySide6 介面層
    ├── app.py / main_window.py / pubsub_bridge.py
    ├── themes/{light,dark}.qss
    └── dialogs/{add_creator,add_name,add_url,add_post,add_work,set_reminder}.py
```
