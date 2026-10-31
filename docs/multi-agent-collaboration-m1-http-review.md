# M1 第五批：團隊草稿的正式 HTTP 邊界

日期：2026-10-31。狀態：**四個 draft-team HTTP 路由已實作及真 HTTP 驗證；M0/M1 與整體 AC 尚未完成，0/31**。本批首次修改原有 tracked WebApp／啟動器，原始單 Agent 行為仍以回歸驗證；未套用正式 migration、啟動 GPU／模型、變更 Docker 或 push／部署。

## 本批修改

- `collaboration/web_adapter.py`：GET/POST `/api/teams`、GET/PATCH `/api/teams/{team_id}`，呼叫既有 TeamService，不自建另一套狀態或授權。TeamService 在 transaction 內再次檢查 session／operation／resource，再查 request ledger。
- `web/app.py`：明確 opt-in 時掛接 adapter，開啟已有、版本及 checksum 相符的 DB；失敗不 ready、不自動 migration。startup／shutdown 關閉 adapter 連線，保留原單程序鎖及 legacy lifecycle。PATCH 只對 teams 路徑進入 mutation JSON／Origin／CSRF 流程，legacy PATCH 仍 405。domain error 使用安全 code／message，不將未知錯誤或 SQL 原文送出。
- audit 新增 canonical team ID／request ID，包含建立後的服務產生 ID；不記正文、query、cookie 或 headers。第一輪真 HTTP 測試抓到 create 只有 collection 路徑而缺新資源 ID，已修正，另以變異重現該缺陷。
- `web/__main__.py`：`--collaboration` 明確啟用，先唯讀檢查 schema，缺少或不符即拒絕並提示審查／備份／migration；預設原單 Agent 模式不建 collaboration schema。
- `collaboration/service.py`：讀取 DTO 時再次遮蔽 business config 的已知秘密，涵蓋寫入後才登記為秘密的字串；不改寫原始版本／歷史 hash。

四個路由均為真正持久化的設定操作，沒有使用 mock HTTP／AuthService／SQLite。此批不建立 TeamRun、不啟動 graph，不提供 metadata-only stop／continue 冒充真停止；未實作的團隊執行路由拒絕，不回假的 202。

## 對外合約

| 路由 | 輸入與結果 |
|---|---|
| GET `/api/teams` | `cursor` opaque UUID、`limit` 預設 50／最大 200；回 `items`／`next_cursor`。僅目前 admin workspace；未知／重複 query、超限／錯型拒絕 |
| POST `/api/teams` | 完整 name／goal／criteria／members、optional policy、request_id；回 201／server IDs／revision 0。2–4 成員、恰一 Orchestrator |
| GET `/api/teams/{id}` | 不接受 query；回 server-owned 設定、definition refs、revision／hash；外部 workspace／owner 403，授權範圍內不存在 404 |
| PATCH `/api/teams/{id}` | **提交完整設定**，另含 expected_revision、request_id；回 200。沿用現有領域 edit 合約，不做外部 read-merge，避免 sparse patch replay 隨新狀態改變；缺少必填設定 400 |

POST/PATCH 均必須有效 admin session、正確 Host／Origin、JSON Content-Type 與 CSRF；未知欄位（含 owner／workspace／role／command）拒絕。所有回應含 no-store／nosniff／既有 CSP。同 ID 同 body 回原結果，即使之後又編輯或重啟也不重做；不同 body 409，stale revision 409。GET 無 mutation。

新服務預設未啟用時，匿名仍 401；有效 admin 得 503，沒有繞過驗證的替代入口。啟用只提供本批已完成的 draft 路由，不能視為團隊 runtime 已可使用。

## 實際驗證及可信度

`tests/collaboration/test_team_http.py` 共 **14 項**：使用真 uvicorn/h11、只綁 127.0.0.1 的 ephemeral port、真 AuthService／SQLite。startup 完成 event 後才送 request，沒有 sleep 猜啟動；每個 fixture 關閉伺服器。runner_factory 只放「若被呼叫即失敗」的 sentinel，確保設定操作不啟動模型，不能用它宣稱真 graph／模型驗收。

覆蓋四路由匿名／nonadmin／有效 admin／operation denied／foreign workspace／logout／expired session、POST/PATCH Host／Origin／CSRF／Content-Type、unknown fields／malformed JSON／duplicate JSON keys／128 KiB、cursor、CAS、重複與異 body、三成員設定重啟、晚登記秘密的讀取遮蔽、audit、legacy draft 並存及 schema 缺少時 byte-for-byte 保留原 DB。另兩項 CLI subprocess 確認 flag help 與 missing schema 拒絕，未啟動服務。

| 執行 | 實際結果 | 原始證據 |
|---|---|---|
| 新 HTTP／CLI＋既有 WebApp＋草稿 service／coverage | 40 通過，28.989 秒 | `m1-team-http-covered-tests.log`、`m1-team-http-coverage.json` |
| 最終完整 Windows Python | **344 通過，115.524 秒，0 fail／skip** | `m1-team-http-final-full-python.log` |
| Node 既有 UI | **24 通過，0 fail／skip** | `m1-team-http-node.log` |
| 新 HTTP／CLI 打散順序 | **14 通過，seed 1041，0 fail／skip** | `m1-team-http-shuffled.json`、`.log` |
| 真三成員 HTTP probe | 1 個 round-trip case 通過，10 筆實際 HTTP 操作；server 已停止 | `m1-team-http-roundtrip-3members/` |
| HTTP 抽樣變異 | **8/8 killed，0 survivor** | `m1-team-http-mutations-final/` |
| 基礎合約／授權／交易變異重跑 | **9/9 killed** | `m1-team-http-foundation-mutations/` |

HTTP 變異涵蓋 outer admin、PATCH CSRF／Origin／body limit、create audit ID、operation permission、resource scope、ledger replay。每個 child 必須有完成 unittest marker；source drift／setup failure／空或 skipped suite 不能算 killed。首次 mutation baseline 因測試 CLI subprocess 在繼承 UTF-8 環境時用 cp950 解碼而失敗；已將輸出與解碼都明確設 UTF-8。該次失敗留在 `m1-team-http-mutations/baseline.log`，**不計任何分數**。最終 baseline／8 個 mutant 與完整 suite 皆重新執行。

branch coverage：adapter 行 **55/60（91.7%）**、分支 **20/24（83.3%）**；WebApp 行 **264/292（90.4%）**、分支 **113/134（84.3%）**；草稿 service **106/106、24/24**。CLI 確有 subprocess 功能測試，但這次 parent-only coverage 沒收子程序，不能宣稱 CLI 分支覆蓋。缺口包含 adapter constructor cleanup、過長 query、未支援 method 及部分既有 Web defensive branches。

依 unit-test-trust-review，這個 HTTP／設定範圍採**中等信任**：真 transport／auth／SQLite 反例與 8 個有意義的變異可重現；仍有上述分支及後續路由未覆蓋，沒有 CI coverage gate、沒有新增依賴／skip／exclude。不能把 344 全綠當成整體安全或 31 AC 完成。

## 啟用、資料與回復

請參閱 [Web 使用說明](web-workspace.md) 的團隊草稿 API 段落。既有正式資料庫 migration 仍須明確授權、先停服務及備份；這批沒有執行。`CollaborationStore.initialize_empty` 只適用新且不存在的 fixture／資料檔，不可拿來取代既有 DB。

啟用旗標不等於 migration；不準備 schema 就拒絕。回復到原單 Agent 啟動時省略旗標，保留團隊資料不刪表；若要還原 DB，先完整停服務，核對備份／目標，依正式審查步驟復原，不與新寫入合併。這批沒有新增 CI／部署、安裝、commit／push 或自動主機操作。

## 證據重現與剩餘工作

```powershell
.venv/Scripts/python.exe -m unittest tests.collaboration.test_team_http -q
.venv/Scripts/python.exe -m unittest discover -s tests -q
node --test tests/ui-state.test.mjs tests/ui-app.test.mjs
.venv/Scripts/python.exe -m scripts.collaboration.team_http_probe --output <new-empty-directory>
.venv/Scripts/python.exe scripts/collaboration/team_http_mutation_probe.py --output <another-new-empty-directory>
```

Windows 需要允許 loopback socket。probe 僅使用獨立 fixture，保存 request ID、team ID、revision、預期／實際 HTTP status、no-store／nosniff、audit.log、已關閉後的 SQLite；不保存密碼、session／CSRF 或 admin.json。DB 快照只用於證據查驗，不能當成正式服務 DB；重新 probe 會建立新的 fixture。

證據根目錄維持 `C:/Users/y4lin/.codex/visualizations/2026/10/29/01a18853-f3dc-7770-aac1-9c97284f6be4/`，最新索引為 `collaboration-m1-team-http-index.json`，綁定 tracked／untracked source、diff、文件與 evidence SHA-256。先前 checkpoint／各批 index 是歷史 tree，不改寫為本批。

**尚未驗證／實作**：所有其他團隊路由、UI／browser／mobile、live team start／stop／continue、legacy 反向活動鎖及完整跨模式交易、完整 approval／repair／candidate／required gates、Windows 人工證據、真 Linux 容器與真模型閉環。Docker 儲存搬移／映像拉取仍待確認，GPU pending。完整 AC 仍 **0/31**，不得宣稱可驗收。
