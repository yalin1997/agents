# M1 第一批：領域合約、交易與團隊草稿服務

日期：2026-10-30（Asia/Taipei）。狀態：**M1 部分實作與驗證，不代表 M1 完成；AC 通過仍為 0/31**。這批屬已核准計畫允許的獨立領域／SQLite 工作；M0 真容器 gate 仍待映像位置與拉取確認，GPU 驗收依管理員指示 pending。

## 實作與保留邊界

| 模組 | 已落實內容 | 尚未落實／不可推論 |
|---|---|---|
| `collaboration/contracts.py` | 嚴格 TeamSpec/MemberSpec/PolicySpec/BudgetLimits、不可變 canonical JSON、服務產生 AgentDefinition ID／版本、獨立 MemberSnapshot、可攜相對路徑字串驗證 | 路徑字串檢查不等於 OS mount／reparse 防護；政策設定不等於執行期能力強制 |
| `collaboration/state.py` | Team、TeamRun、RunAttempt、WorkItem、WorkAttempt、MemberTurn、Execution、ChangeSet 分離狀態；revision、不可改寫終態、明確 continue/restore/reconcile、完成與排空 facts | facts 是純合約，正式服務仍須在交易中從 DB 算出；journal ID 還須實際查驗，不能讓模型或 HTTP 提供 facts／成功狀態 |
| `collaboration/storage.py` | 真 SQLite BEGIN IMMEDIATE、授權先於 ledger 去重、同 ID 同 body 原結果／異 body 409、狀態＋事件＋ledger 同交易、cursor、實體備份與明確 migration | 尚無 run/member/work/lease/budget/mailbox 完整持久化；外部 Docker／Git journal 尚未接入 |
| `collaboration/migrations/001_foundation.sql` | 新增具命名空間的 schema metadata、teams、agent definitions、requests、events；FK、索引、不可變 triggers | 正式資料庫未套用；不改寫或重建舊 tasks／requests |
| `collaboration/service.py` | 使用真 AuthService 的團隊草稿建立／讀取／編輯／分頁；每次及交易內重驗 session、admin／operation／workspace；版本化定義與已知秘密遮蔽 | 尚未接 HTTP／UI、TeamRun 啟動、排程、成員溝通或執行器；未增加任何對外未保護路由 |

編輯草稿按既有位置更新 AgentDefinition 版本，保留舊版本；增減或重排草稿不改任何活動 MemberSnapshot。後續 UI 須顯示這個草稿語意，不能把名稱當身分或讓編輯草稿變相更新活動成員。

程式審查修正：定義新增不再使用 `INSERT OR IGNORE`，避免 ID／版本碰撞被默默當成功；明確回 409 並回滾。測試注入相同 UUID，實際 SQLite 確認原設定與定義未被重新綁定。CAS 被 SQLite trigger 抑制時，新增 definition／event／ledger 也全部回滾。

## 實際驗證

M1 的 `test_contracts.py`、`test_state.py`、`test_storage.py`、`test_team_service.py`、`test_migration_web_compat.py` 使用既有 unittest。M0＋M1 suite：**62 通過，11.842 秒，0 失敗／skip**。其中 M1 新增 39 項；沒有用 mock DB、mock auth 或 mock graph 代替核心行為。

- 資料合約：2–4 位、恰一位 Orchestrator；未知 owner／role／workspace／model／key／Docker flags 拒絕；nested input 與回傳值修改不改快照 hash；能力預設 false，網路與安裝獨立。
- 狀態：失敗 WorkAttempt 不被覆寫成成功，TeamRun continuation 與新 RunAttempt 語意分離；過期 revision、非法 wait 狀態、unknown 未對帳、queued 已 claim 的 stop、活動／未知資源排空及所有完成阻擋計數皆有反例。
- SQLite：兩個真連線同時提交同一 request，只產生一次效果／事件；不同 body 回 409；操作中途失敗及無法 JSON 保存時全部回滾。授權撤銷後不能以舊 request 查回原結果；外 workspace ledger 403。
- Service：未登入、非 admin、無操作權限、外 workspace、登出及到期 session 拒絕；有效 admin 可建立／編輯／重開讀回。兩個 DB 連線以 barrier 同時編輯同一 revision，只有一個成功且只有一個 update event。
- Migration：明確版本／checksum、保留既有 backup、不匹配 schema 不自動開啟。真舊 Web service 建立 DB／task，停止後在暫存 DB 加表，再用原 service 啟動，舊任務不變且仍可新建任務。從 migration 前 DB backup 還原後，原 task 可讀，備份後才建立的 task 不存在；沒有把這項 DB 測試說成 artifact/blob 的完整回復證據。
- Secrets：植入測試 secret，檢查 DTO 與實際 SQLite dump；設定／定義／ledger／event 無原文。request 保存 fingerprint，不保存原 body；一般例外不包含非法輸入值。

## unit-test-trust-review

判斷：**目前範圍具中高可信度**，足以支持後續領域整合；不支持協作產品發布。高風險授權與原子性有真 SQLite／session 及指定變異反例；仍缺完整 run 持久化、HTTP matrix、資源執行與故障注入證據。

| 模組 | 行覆蓋 | 分支覆蓋 |
|---|---|---|
| contracts | 217/227，約 95.6% | 76/84，約 90.5% |
| state | 119/120，約 99.2% | 39/40，97.5% |
| storage | 169/173，約 97.7% | 44/48，約 91.7% |
| service | 109/109，100% | 26/26，100% |

未覆蓋部分包括部分直接 Python constructor 的錯型／編碼錯誤防守、無效 facts 型別、migration statement 不完整及 backup integrity failure、錯誤 parent 的 ledger GET。不能以其他 guards 的 coverage 推論這些分支通過。

抽樣變異由 `scripts/collaboration/m1_mutation_probe.py` 在獨立子程序記憶體中修改單一分支，再載入同一批 38 個同步 M1 測試；原始 suite 先通過。**9/9 killed，無 survivor**，已檢查個別 log 中確實有測試 assertion／error 失敗，未把來源不匹配計成 killed：

| 變異 | 被測行為 | 結果 |
|---|---|---|
| 取消恰一位 Orchestrator | 成員數／協調者非法組合 | killed |
| 取消 capability boolean 型別 | 文字／數字不能冒充核准 | killed |
| 取消 JSON key 排序 | 同內容不同 key 順序 hash 必須相同 | killed |
| 取消 state revision 檢查 | 舊 revision 不得轉移 | killed |
| 取消完成結算計數屏障 | pending 訊息／工作／證據不允許 completed | killed |
| 取消 replay 前授權 | 撤權後不能讀舊結果 | killed |
| rollback 改 commit | 失敗不能留下 team／event／ledger | killed |
| 取消 service admin／operation guard | 有效非 admin session 仍拒絕 | killed |
| 取消保存前 redaction | DTO／真 SQLite dump 不得出現已知 secret | killed |

其他品質觀察：核心不靠 mock 呼叫斷言；單元測試有明確值與錯誤 code，不能只 assert truthy。只有 UUID 碰撞故障使用具體 return_value 注入；沒有 mock SQLite/AuthService。競態使用 Barrier，沒有 sleep 同步。均使用獨立 TemporaryDirectory，無 skip／xfail／coverage 排除，未新增依賴。同步測試逐檔可獨立執行，mutation baseline／合併 suite／後續完整回歸提供順序檢查；不宣稱全面隨機順序或全面 mutation testing。

## 重現與證據

```powershell
.venv/Scripts/python.exe -m unittest discover -s tests/collaboration -q
.venv/Scripts/python.exe -m unittest discover -s tests -q
.venv/Scripts/python.exe scripts/collaboration/m1_mutation_probe.py --output <new-evidence-directory>
```

Windows 真 graph／舊 Web service 測試需要允許 asyncio loopback。GPU、網路 provider、正式資料及 Docker 操作均不需要。

證據根目錄：`C:/Users/y4lin/.codex/visualizations/2026/10/29/01a18853-f3dc-7770-aac1-9c97284f6be4/`，本批檔案：

- `m1-verified-tests.log`：最終 62 項 suite。
- `m1-verified-report.json`：branch coverage 詳細結果。
- `m1-final-mutations/results.json` 與九個 case log：最終來源的變異抽查。
- `m1-full-python.log`：Windows 完整回歸 **250 通過、119.460 秒，0 失敗／skip**。新增 M1 在獨立逐檔、mutation baseline、collaboration suite 與完整 suite 中執行；未用真模型或 GPU。

上述資料須搭配本批 source/migration hash manifest。HEAD 本身未包含這些未提交新增檔案；尚無 TeamRun／candidate／plan hash，不能填假識別。
