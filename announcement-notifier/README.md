# Announcement Notifier

獨立小工具：定期檢查一個公告頁面，發現新公告時透過 **LINE** 與 **Email** 發送通知。
與本 repo 其他的 ETF 交易系統完全獨立，不共用任何程式碼。

## 運作方式

1. `src/scraper.py` 抓取公告頁面：先嘗試常見的 RSS/Atom feed 路徑，找不到則
   用 CSS selector 解析 HTML（先用內建的常見 selector 清單，找不到再用
   `config.json` 裡自訂的 `list_selector`）。
2. `src/state_store.py` 把已通知過的公告（以網址或標題+日期為 key）存成
   `data/seen_announcements.json`，下次執行時比對差異找出「新公告」。
   - **第一次執行**只會記錄目前的公告當作基準，不會發通知（避免把所有舊公告
     當成新公告轟炸一次）。
3. 找到新公告後，分別呼叫 `src/line_notifier.py`（LINE Messaging API）與
   `src/email_notifier.py`（SMTP）發送通知。
4. `.github/workflows/announcement-notify.yml` 用 GitHub Actions 排程執行，
   並把更新後的 `data/seen_announcements.json` commit 回 repo 當作持久化狀態。

## 設定公告來源

編輯 `config.json`：

```json
{
  "source": {
    "name": "顯示在通知裡的來源名稱",
    "list_url": "公告列表頁網址",
    "base_url": "用來把相對連結轉成絕對網址的網域",
    "list_selector": null
  },
  "state_file": "data/seen_announcements.json"
}
```

`list_selector` 留 `null` 時會自動嘗試常見版面（`table tr td a`、
`ul.list li a` 等）。如果抓不到正確的公告，用瀏覽器「檢視原始碼」找到
公告列表的 HTML 結構，把對應的 CSS selector 填進 `list_selector`。

## 必要的環境變數 / GitHub Secrets

| 變數 | 說明 |
|---|---|
| `LINE_CHANNEL_ACCESS_TOKEN` | LINE Messaging API 的長期 channel access token |
| `LINE_TARGET_IDS` | 要推送的 LINE user/group ID，逗號分隔 |
| `SMTP_HOST` | SMTP 伺服器（預設 `smtp.gmail.com`） |
| `SMTP_PORT` | SMTP 連接埠（預設 `587`） |
| `SMTP_USER` | 寄件信箱 |
| `SMTP_PASS` | 信箱密碼 / app password |
| `ANNOUNCE_EMAIL_TO` | 收件信箱，逗號分隔（留空則寄給 `SMTP_USER` 自己） |

任何一組憑證沒設定時，對應的通知器會跳過發送並寫 log（不會噴錯）。

### 取得 LINE Messaging API 憑證

LINE Notify 已於 2025-03-31 停止服務，必須改用 Messaging API：

1. 到 [LINE Developers Console](https://developers.line.biz/console/) 建立一個
   Provider，再建立一個 **Messaging API** channel（這會自動建立一個官方帳號）。
2. 在 channel 的「Messaging API」分頁取得 **Channel access token (long-lived)**。
3. 用手機加官方帳號好友，傳一則訊息給它；暫時設定 webhook URL（例如
   [webhook.site](https://webhook.site) 或自己寫的小型 endpoint）來取得該則訊息
   payload 裡的 `source.userId`，這就是 `LINE_TARGET_IDS` 要填的值。
   （要推送到群組則改用該群組事件裡的 `groupId`。）

## 本機執行

```bash
cd announcement-notifier
pip install -r requirements.txt
python -m src.main
```

## 執行測試

```bash
cd announcement-notifier
python -m pytest tests/ -v
```
