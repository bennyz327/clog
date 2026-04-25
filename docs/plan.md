# CreatorLog 跨平台 CLI 工具計畫

## 摘要

建立 Windows/Linux 可用的單檔 CLI 工具 clog。核心概念是「創作者主檔」：名稱、網址、平台帳號 ID、貼文 metadata、提醒、整理紀錄都附加到同
一個創作者主檔。

名稱與網址都不是唯一識別。真正可靠的定位優先使用 creator_id 或 platform + platform_id；名稱只當搜尋線索。平台 ID 偵測與貼文歸屬由工具在
合適時機自動處理，不提供「補連貼文」或「補跑平台 ID」這類手動維護命令。

## CLI 設計

所有命令都有短寫。若第一個參數不是命令，預設執行全局搜尋，例如 clog 作者名 等同 clog s 作者名。

| 用途 | 完整命令 | 簡寫 | 範例 |
|---|---|---|---|
| 初始化 | clog init | clog i | clog i |
| 新增創作者主檔 | clog add NAME [URL] | clog a NAME [URL] | clog a "作者名" https://x.com/user |
| 追加名稱事實 | clog name TARGET NAME | clog n TARGET NAME | clog n "#12" "A1" -p fanbox --pid 123 |
| 追加網址事實 | clog url TARGET URL | clog u TARGET URL | clog u "#12" URL -r moved --from OLD_URL |
| 記錄貼文 | clog post URL [TARGET] | clog p URL [TARGET] | clog p https://site/post/123 "#12" |
| 設定提醒 | clog remind TARGET WHEN | clog r TARGET WHEN | clog r "#12" 30d |
| 查看到期提醒 | clog due | clog d | clog d |
| 新增整理紀錄 | clog work TARGET MESSAGE | clog c TARGET MESSAGE | clog c "#12" "整理 2026-04 贊助包" |
| 全局搜尋 | clog search QUERY | clog s QUERY | clog s 作者名 |
| 查看主檔 | clog show TARGET | clog v TARGET | clog v "#12" |

TARGET 可接受 #id、已記錄 URL、platform:platform_id、名稱。寫入命令必須解析到唯一創作者；若同名或模糊命中多個主檔，工具只列候選並停止，
要求改用 #id、URL 或 -p/--pid。

## 自動平台 ID 與貼文歸屬

clog p URL [TARGET] 一定會執行 gallery-dl metadata-only。流程固定如下：

1. 從 metadata 擷取平台、作者平台 ID、作者顯示名稱、作者頁 URL、貼文 ID、貼文時間、標題/文字與 raw JSON。
2. 若 platform + platform_id 已存在，自動歸到該創作者。
3. 若使用者提供 TARGET，且 metadata 不與既有主檔衝突，將貼文歸到該創作者，並補寫 platform_accounts。
4. 若沒有 TARGET，但作者頁 URL 或名稱只命中唯一創作者，歸到該創作者並補寫平台 ID。
5. 若無法唯一判斷或偵測到 platform + platform_id 已綁到其他主檔，停止寫入並列出候選，不建立未歸檔貼文。

clog a NAME [URL]、clog n ... -u URL、clog u TARGET URL 若帶 URL，會建立內部 metadata 任務。一般命令先快速完成寫入，再由同一執行檔啟動
一次性背景 worker 嘗試解析 gallery-dl metadata，補上 platform_accounts。這是內部機制，不暴露成使用者命令，也不需要手動補跑。

## 資料與行為

SQLite 儲存 creators、creator_names、creator_urls、platform_accounts、posts、reminders、worklogs、metadata_tasks、search_fts。

clog n 是唯一名稱追加入口；跨平台不同名、同平台改名、同平台新帳號都用它處理。可選參數包含
-p/--platform、--pid、-u/--url、--from、-r/--reason、--note。



## 測試計畫

測試兩個創作者使用同一名稱時，所有寫入命令必須拒絕用名稱直接寫入並列候選。

測試用 #id、URL、platform:platform_id、-p PLATFORM --pid ID 能在同名情況下精確定位。

測試 clog p 能從 gallery-dl metadata 自動取得平台作者 ID，並正確歸屬到既有主檔。

測試 clog a/u/n 帶 URL 後會自動排程平台 ID 偵測，且背景 worker 成功後後續命令可用平台 ID 定位。

測試同一 platform + platform_id 嘗試綁到不同主檔時會停止並提示衝突。

## 假設

第一版只做 CLI，不做 TUI 或 Web UI。

gallery-dl 只用於 metadata 與平台 ID 偵測，預設不下載媒體。

不提供硬刪除；改名、搬家、停用、被 ban 都以追加事實與狀態標記保存歷史。
