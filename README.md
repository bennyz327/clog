# CreatorLog (`clog`)

一個以「創作者主檔」為核心的本地追蹤工具，適合用來記錄創作者的名稱、網址、平台帳號 ID、貼文 metadata、提醒與整理作業紀錄。

工具目標是：

- 跨平台使用，Windows / Linux 都能跑
- 資料全部落在本地 SQLite
- 冷啟動快，CLI 操作短
- 所有資訊都圍繞同一個創作者主檔
- 支援全局搜尋，不需要先選資料類型

## 核心概念

`clog` 不是把「作者名稱」、「網址」、「貼文」、「整理記錄」拆成彼此獨立的主體，而是把它們都當成附加在同一個創作者主檔上的事實。

這代表：

- 名稱不是唯一鍵，不同創作者可以撞名
- 網址也不是唯一辨識來源，平台搬家、換帳號、被 ban 都要保留歷史
- 真正穩定的定位優先使用：
  - `#creator_id`
  - `platform:platform_id`
  - 已知 URL
- 名稱主要用來搜尋與顯示，不足以唯一定位時會拒絕寫入

## 執行模式

`clog` 有兩種入口：

- 無參數：進入互動式 TUI
- 有命令或查詢字串：走 CLI

範例：

```bash
clog
clog a "creator name" https://x.com/example
clog s example
clog example
```

## 主要檔案

原始碼模式：

- `clog.py`：入口點（thin wrapper）
- `src/`：主要程式碼
  - `constants.py`：例外類別、常數
  - `config.py`：app_dir、logging、設定檔讀寫
  - `utils.py`：字串/URL/解析工具函式
  - `db.py`：SQLite schema 與所有查詢操作
  - `gallery.py`：gallery-dl 整合
  - `worker.py`：背景 metadata worker
  - `service.py`：業務邏輯（add_* / set_* 記錄）
  - `render.py`：資料 → 顯示字串
  - `tui.py`：Textual TUI
  - `cli.py`：CLI 命令、dispatch、main
- `clog.json`：設定檔
- `clog.sqlite`：SQLite 資料庫
- `clog.log`：執行紀錄與錯誤 log

打包模式（產物仍為單一 exe）：

- `clog.exe` 或 Linux `clog`
- `clog.json`
- `clog.sqlite`
- `clog.log`

## 快速開始

初始化：

```bash
clog init
```

新增創作者：

```bash
clog a erovirus https://www.patreon.com/cw/erovirus
```

查看主檔：

```bash
clog v erovirus
clog v #1
```

搜尋：

```bash
clog s ero
clog ero
```

設定提醒：

```bash
clog r #1 30d
clog r #1 2026-05-01
```

新增整理記錄：

```bash
clog c #1 "整理 2026-04 贊助包" --tag pack --path D:\\archive\\creator
```

## 常用 CLI 命令

| 功能 | 命令 |
|---|---|
| 初始化 | `clog i` / `clog init` |
| 新增創作者 | `clog a NAME [URL]` |
| 新增名稱事實 | `clog n TARGET NAME` |
| 新增網址事實 | `clog u TARGET URL` |
| 記錄貼文 | `clog p URL [TARGET]` |
| 設提醒 | `clog r TARGET WHEN` |
| 查看到期提醒 | `clog d` |
| 近期清單 | `clog ls` / `clog l` |
| 新增工作紀錄 | `clog c TARGET MESSAGE` |
| 全局搜尋 | `clog s QUERY` |
| 查看主檔 | `clog v TARGET` |

## TARGET 定位規則

大多數寫入命令都需要 `TARGET`。支援格式：

```text
#12
https://platform/profile
platform:platform_id
精確或模糊名稱（前提是最後只命中一個創作者）
```

如果同名或模糊結果命中多個創作者，工具不會猜，會要求你改用：

- `#id`
- 已知 URL
- `platform:platform_id`

## 名稱、網址、平台 ID 的處理方式

### 名稱

`clog n` 用來追加名稱事實，不區分「別名」、「改名」、「新帳號名」為不同命令。可用參數補上下文：

```bash
clog n #1 "A1" -p fanbox --pid 123
clog n #1 "A2" --from "A" -r rename
```

### 網址

`clog u` 用來追加網址事實，也不區分「新平台」、「搬家」、「被 ban 後新帳號」為不同命令：

```bash
clog u #1 https://fanbox.cc/@example -p fanbox
clog u #1 https://new.example --from https://old.example -r moved
```

### 平台帳號 ID

若平台有被 `gallery-dl` 支援，工具會盡量從 metadata 裡取出作者平台 ID，寫入 `platform_accounts`，後續就能用：

```bash
clog v pixiv:123456
clog u pixiv:123456 https://www.pixiv.net/users/123456
```

## 貼文記錄

`clog p URL [TARGET]` 會優先用 `gallery-dl` 做 metadata-only 擷取，盡量記錄：

- 平台
- 作者名稱
- 作者平台 ID
- 貼文 ID
- 標題
- 文字內容
- 發布時間
- raw metadata JSON

範例：

```bash
clog p https://www.patreon.com/posts/123456 #1
```

行為規則：

- 若 metadata 足夠定位創作者，會自動掛到正確主檔
- 若 metadata 不足，但你有提供 `TARGET`，仍可建立最小貼文紀錄
- 若 metadata 指向別的創作者，工具會拒絕寫入，不自動合併

## 互動式 TUI

無參數執行：

```bash
clog
```

TUI 介面包含：

- 上方搜尋列
- 左側到期提醒與近期創作者
- 中間搜尋 / creators / posts / work 分頁
- 右側詳細資訊
- modal 表單寫入 creator / name / url / post / reminder / work

預設快捷鍵：

- `/`：回到搜尋框
- `Ctrl+A`：新增創作者
- `Ctrl+N`：新增名稱
- `Ctrl+U`：新增網址
- `Ctrl+P`：記錄貼文
- `Ctrl+R`：設定提醒
- `Ctrl+W`：新增工作紀錄
- `[` / `]`：切換近期列表頁數
- `F5`：重新整理

## 搜尋設計

全局搜尋建立在 SQLite FTS5 之上，但輸出會以「創作者主檔」分組，而不是把底層 row 全部攤平。

搜尋來源包含：

- creator 主檔
- 名稱歷史
- URL 歷史
- 平台帳號
- 貼文 metadata
- 提醒
- worklog

因此搜尋舊名、舊網址、平台 ID、貼文標題、整理記錄訊息，都有機會回到同一個創作者主檔。

## 資料架構

主要 SQLite 表：

- `creators`：創作者主檔
- `creator_names`：名稱事實與名稱歷史
- `creator_urls`：網址事實與網址歷史
- `platform_accounts`：平台帳號 ID 對應
- `posts`：貼文 metadata
- `reminders`：提醒
- `worklogs`：整理 / 作業紀錄
- `metadata_tasks`：背景 metadata 補全任務
- `search_fts`：全局搜尋索引

## 背景 metadata 補全

當你新增 URL 或名稱時，只要有機會從網址中取得 metadata，工具會在背景啟動一次性 worker，自動補：

- 平台
- 平台作者 ID
- 作者名稱
- 作者頁 URL

這樣可以在不拖慢高頻 CLI 操作的前提下，逐步把資料補完整。

## 設計與實作架構

- `clog.py`：入口點（thin wrapper，三行）
- `src/`：模組化主程式碼，各模組職責單一
- `clog.spec`：PyInstaller onefile 打包設定

實作原則：

- 原始碼分模組維護，打包產物為單一 exe
- CLI 與 TUI 共用同一套資料處理邏輯（service / db 層）
- 資料追加與歷史保留優先，不做破壞性覆蓋
- 本地優先，不依賴外部服務

## Log 與排錯

程式會在執行目錄生成 `clog.log`，用於記錄：

- 啟動 / 關閉
- TUI action 開始與結束
- 使用者錯誤
- 未捕捉例外
- 資料庫開啟 / 關閉

SQLite 使用 WAL 模式，程式正常關閉時會做 checkpoint，避免長期殘留 `-wal` / `-shm`。

如果 TUI 或 metadata 流程出問題，先看：

- `clog.log`
- `clog.sqlite`
- 同目錄下是否有 `clog.sqlite-wal` / `clog.sqlite-shm`

## 原始碼執行

需要 Python 3.12+。

若要使用 TUI，需安裝 `textual`。
若要完整貼文 metadata 與平台 ID 補全，需安裝或打包 `gallery-dl`。

範例：

```bash
python clog.py init
python clog.py a example https://x.com/example
python clog.py
```

## 打包

Windows onefile 打包：

```bash
python -m PyInstaller .\clog.spec --noconfirm
```

輸出在：

```text
dist/clog.exe
```

Linux 版需要在 Linux 環境自行打包，不能直接用 Windows 的 PyInstaller 輸出交叉產生 Linux binary。
