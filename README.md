# CreatorLog (`clog`)

[English README](README.en.md)

`clog` 是一個本地端的創作者整理工具，用來把你追蹤的創作者、作者頁網址、貼文、提醒與工作紀錄集中在同一個地方管理。

它適合想把資料留在自己電腦上的人使用。預設資料會存成本地 SQLite 檔案，不需要把內容交給雲端服務。

## 可以做什麼

- 建立創作者主檔，集中管理同一位創作者的資料
- 保存作者頁網址與平台帳號，減少重複查找
- 記錄貼文連結，並在支援的平台上自動補齊部分 metadata
- 設定提醒，追蹤之後要回看的創作者
- 新增工作紀錄，保存備忘、觀察或整理筆記
- 用搜尋快速找出相關創作者、網址、貼文與筆記

## 快速開始

`clog` 目前提供 GUI 與 CLI 兩種使用方式：

- `clog`：桌面程式，適合日常整理
- `clog-cli`：命令列工具，適合腳本、自動化或快速輸入

建議一般使用者直接從 GitHub Releases 下載已打包版本：

1. 前往 [Releases](https://github.com/bennyz327/clog/releases)
2. 下載適合你的平台壓縮檔：`windows-x64` 或 `linux-x64`
3. 解壓縮後啟動 `clog`（Windows 會是 `clog.exe`）
4. 第一次啟動後，程式會在目錄下建立自己的本地資料檔

如果你只想用圖形介面，可以直接開 `clog`；CLI 是選用的，不必先學會才能開始使用。

## 常見用法

### GUI

GUI 適合把 `clog` 當成日常整理工具來用。典型流程通常是：

1. 新增創作者
2. 補上作者頁網址或平台帳號
3. 記錄想保存的貼文
4. 視需要加上提醒或工作紀錄
5. 之後用搜尋快速回到同一位創作者

### CLI

CLI 適合偏好終端機、想快速輸入，或要整合進自己的流程時使用。以下是常見例子：

```bash
clog-cli init
clog-cli add "Creator Name" https://x.com/example
clog-cli name "#1" "Old Alias"
clog-cli url "#1" https://www.pixiv.net/users/123456
clog-cli post https://x.com/example/status/123
clog-cli remind "#1" 7d --note "check updates"
clog-cli work "#1" "Commission status noted."
clog-cli search example
clog-cli show "#1"
```

主要命令如下：

| 功能 | 命令 |
|---|---|
| 初始化 | `clog-cli init` |
| 新增創作者 | `clog-cli add NAME [URL]` |
| 新增名稱 | `clog-cli name TARGET NAME [CONTEXT]` |
| 新增作者頁網址 | `clog-cli url TARGET URL` |
| 記錄貼文 | `clog-cli post URL [TARGET]` |
| 設提醒 | `clog-cli remind TARGET WHEN` |
| 查看提醒 | `clog-cli due` |
| 近期清單 | `clog-cli ls` |
| 新增工作紀錄 | `clog-cli work TARGET CONTENT` |
| 搜尋 | `clog-cli search QUERY` |
| 查看主檔 | `clog-cli show TARGET` |

短別名仍可使用，但公開文件以完整命令為主，較容易閱讀與記憶。

## 給開發者

如果你想直接從原始碼執行：

```bash
python -m pip install -r requirements.txt
python clog_cli.py init
python clog_gui.py
```

CLI 開發入口也可以直接這樣使用：

```bash
python clog_cli.py add "Creator Name" https://x.com/example
python clog_cli.py search example
```

目前相依套件主要包含：

- Python 3.13
- PySide6
- gallery-dl
- PyInstaller（打包時需要）

## 更多技術細節

- 資料模型與 ER 說明：[`docs/ER.md`](docs/ER.md)
- 發布流程：GitHub Actions 會產出 Windows 與 Linux 的 release build

## License

本專案採用 [MIT License](LICENSE)。
