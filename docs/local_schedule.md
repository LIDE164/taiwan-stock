# 本機主排程與 GitHub 備援

使用現有 Windows 工作排程器，不新增付費主機或排程服務。GitHub 原排程保留作為電腦關機、斷網時的備援；但 GitHub 可能延遲，因此無法保證電腦關機時仍準時收到盤中通知。既有 Firebase 等服務用量與電腦電費仍依原來方式計算。

## 前提與安全限制

- 電腦在工作日相關時段開機、連網，Windows 時區為 `Taipei Standard Time`。
- 使用目前使用者、一般權限、**保持登入**。鎖定畫面不等於登出；此方案不儲存 Windows 密碼，不提供登出後執行。電腦關機、睡眠或離線時不會工作；腳本不改全機電源設定，也不自動喚醒。使用預設電池限制，筆電請接電源。
- 已安裝專案所需 Python 3.14 與依賴，現有 `.streamlit/secrets.toml` 已具備 Firebase、Telegram 設定。金鑰不放入任務參數、不提交、不顯示。
- 任務直接使用安裝時解析出的 **Python 完整路徑**，工作目錄固定為本專案。不依賴工作排程器的 PATH，也不自動 `git pull`。修改或搬移程式／Python 後，應重新預覽及安裝。
- 本機和 GitHub 共用原 Firestore 掃描／投遞鎖與收件紀錄。不要以強制重掃或強制重送取代正常備援；送達不明須先核對，不能盲目再發。

## 先預覽，再註冊

在專案根目錄執行。預覽及安裝都先執行 `local_schedule.py --check`，只讀檢查環境與設定；不會掃描、寫入雲端或發 Telegram。

```powershell
powershell.exe -NoProfile -ExecutionPolicy RemoteSigned -File .\scripts\install_local_schedule.ps1
```

若需要指定 Python，加入 `-PythonPath 'C:\完整路徑\python.exe'`，亦可指定已安裝的 `py` 啟動器；安裝時會解析成真正的 Python 執行檔。不要把金鑰加入此命令。

確認預覽路徑、使用者及時段無誤後：

```powershell
powershell.exe -NoProfile -ExecutionPolicy RemoteSigned -File .\scripts\install_local_schedule.ps1 -Install
```

`RemoteSigned` 僅套用本次 PowerShell 程序，不修改系統永久執行原則；組織的群組原則仍有效。若被組織規則阻擋，停止並請管理者確認，不停用防護。

安裝只註冊，不立即啟動任務；預設不安裝。以下兩個名稱若已存在，只有「同一專案路徑＋同一使用者」的既有受管任務可更新，其他任務會被拒絕覆寫。兩個名稱均先檢查再註冊；若 Windows 在第二個註冊時失敗，修正原因後重跑即可安全更新已註冊的第一個。

| 任務 | 台北時間，週一至週五 | 上限 |
| --- | --- | --- |
| `TaiwanStock-DailyScan` | 08:05、15:17、16:17、22:17 | 40 分鐘 |
| `TaiwanStock-PredictionPrices` | 09:05／09:20／09:35；10–13 時各 :05／:20；13:35／13:50／14:05／14:20 | 10 分鐘 |

同一任務正在執行時忽略新觸發，不排隊堆積。休市日由專案的已驗證日曆略過；備援成功後不再重複發同一時段。缺少或延遲的公開行情會如實標示，不冒充即時／收盤價格。

行情通知使用 12 分鐘的共用寄送鎖（其他研究通知仍為 30 分鐘），讓未開始傳送就逾時的工作能由下一個 15 分鐘備援接手。若已記錄傳送意圖但無法確認收件，鎖到期也不會自動重送，須先核對實際收件。

GitHub 的手動補跑預設也不強制重掃。只有明確選取 `force_rescan` 才重算已完成日期；正常備援請維持 `false`，不要用它處理寄送狀態不明。

08:05 是有截止時間的盤前補掃：前晚電腦未開但早上已可用時，只補上一個已驗證、已完成的交易日；已完成且寄送齊全則唯讀略過。電腦須在 **08:05 前開機、登入並連網**，不另設開機即補跑。沿用 <08:30 才可開始、09:00 前才能寫入的限制，以及原始生成時間與延遲標籤；Python／啟動器內部上限讓這次工作最晚約 08:43 結束，不把隔日盤中資料補成昨晚榜單。

## 執行方式與檢查

任務以隱藏 PowerShell 啟動器呼叫：

```text
<absolute-python.exe> -X utf8 local_schedule.py --job scan --run
<absolute-python.exe> -X utf8 local_schedule.py --job prices --run
```

任務不使用 `FORCE_SCAN` 或 `--resend-telegram`。Python runner 的內部截止、PowerShell 的 38／8 分鐘子程序樹截止，以及 Windows 任務的 40／10 分鐘上限分層保護，不留背景 Python 繼續跑過 Firestore 掃描鎖的有效期限。PowerShell 只在自己啟動的子程序超時時終止該子程序樹。

不啟用錯過排程後自動補跑；當下已過時段時不能用現在價格補造早前通知。程式仍依台北當下時間核對安全掃描窗口、發布截止、預測名單適用日與收件紀錄。

檢查「工作排程器」中的最後執行時間／結果，以及 `logs/local_schedule.jsonl` 的固定欄位摘要；日誌不保留原始子程序輸出或金鑰。也可使用只讀命令：

```powershell
Get-ScheduledTask -TaskName 'TaiwanStock-DailyScan','TaiwanStock-PredictionPrices' | Get-ScheduledTaskInfo
```

不要為了驗證而按「執行」或手動加 `--run`：那些會在合法時段正式掃描／發送。環境檢查用 `python local_schedule.py --check`，正式驗證前須先確認日期與寄送紀錄。

若電腦關機，GitHub 備援仍可能執行；若當日所有排程都錯過交易時段，保留缺失紀錄，不捏造補發。程式測試通过、任務註冊成功、任務確實啟動、Telegram 確認收件是四件不同的事。

Windows 任務行為參考：[Microsoft ScheduledTasks 設定文件](https://learn.microsoft.com/en-us/powershell/module/scheduledtasks/new-scheduledtasksettingsset?view=windowsserver2025-ps)。
