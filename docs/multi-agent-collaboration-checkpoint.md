# 多 Agent 協作：目前 checkpoint

Checkpoint：`CP-20261031-policy-v5`；日期：2026-10-31（Asia/Taipei）。本文件是續作入口，保存目前可證實的狀態；歷史批次不再當成最新完成狀態。原始完整需求另存 [使用者工作要求與授權紀錄](multi-agent-collaboration-user-requirements.md)。

**交付前重新查詢，目標工具回報 `active`。完整目標保留，沒有完成；F1–F11 未全部完成，AC 完整通過仍為 0/31。** 整理開始時曾觀察為 paused，整理期間收到新的目標續行事件後已變 active；沒有自行呼叫工具改狀態。checkpoint 整理沒有執行新的產品功能、啟動模型／Docker 或套用正式 migration，後續可依本文件續作。

## 1. 續作時的最小閱讀順序

1. 本 checkpoint 及 [使用者完整工作要求](multi-agent-collaboration-user-requirements.md)，保留所有原始要求及後續授權。
2. [AGENTS.md](../AGENTS.md)、[規格 v0.3](multi-agent-collaboration-spec.md)、[security-requirements.md](security-requirements.md)。規格是完成標準，不能用本 checkpoint 的摘要取代逐條驗收。
3. [實作計畫](multi-agent-collaboration-plan.md) 的 D1–D5／M0–M5 及 [AC-01–31 矩陣](multi-agent-collaboration-acceptance.md)。
4. 只載入下一批會修改的模組、相關測試、適用 skill 與必要既有文件。UI／Web／framework 文件此前已完整讀過：`ui-implementation-spec.md`、`web-workspace.md`、`core-contracts.md`、`a2a.md`、`local-llm.md`、`tool-output-review.md`；跨入相關範圍時查回原文。

不必重新載入整段聊天、舊工具 discovery、舊測試逐行日誌或已結束 session。需要追溯才開歷史 review／evidence。檔案與外部實際狀態始終優先於本 checkpoint；恢復後先 `git status --short`，不要重跑未知結果的操作。

## 2. Repository 與未提交工作

- 根目錄：`C:\Users\y4lin\gemini_repo\pi_deepagent`；PowerShell；branch `master`；HEAD `6bd537a9fbf17c790de356cbe006b97ddf1b55fb`。
- 開始時使用者唯一未追蹤檔為 `docs/multi-agent-collaboration-spec.md`。原檔未改，SHA-256 為 `28cc0b0762499ab33209dde4ff2916a24013264de16c0cdbd3d7d59d1b59e4c1`。
- 目前 collaboration 程式、測試、scripts 及相關計畫／驗收／review 文件均為新增、未追蹤檔；沒有 commit、push、改 index，也沒有改動原有 tracked 產品程式。不要把 `git diff` 空白當成沒有實作；必須看 untracked 檔與 source manifest。
- 保留所有使用者及本次工作的未提交內容；不可 reset／clean／覆寫或為了清 context 刪檔。
- 最新 source／migration／evidence hash 索引為私有 evidence 根目錄的 `collaboration-m1-policies-index.json`，包含本 checkpoint 與需求檔 hash。歷史 index 對應的是各自當時的未提交 tree，不能套在最新原始碼。

## 3. 授權與環境邊界

| 項目 | 現況與不可誤讀的邊界 |
|---|---|
| 實作計畫 | 使用者明確同意按照計畫續行。已授權範圍的例行工作不再逐步問；改架構／安全／依賴／範圍先說明差異並取得同意 |
| GPU／本機模型 | 使用者有私人 GPU 使用需求，GPU 相關工作 pending。不得啟動 `scripts/start-local-model.ps1` 或模型。最終真模型閉環要求沒有取消 |
| 一般 Docker 操作 | 曾取得一般核准；不因此推定可以任意停止其他專案、搬移全域儲存或拉取未核准映像 |
| Docker 儲存搬移 | 使用者只同意「先準備可審查的搬移步驟」。[方案](docker-storage-move-plan.md) 已備妥；實際停機／備份／搬移未執行、仍待確認 |
| 映像 | Python／Node 兩個固定 digest 已解析但未拉取。先前拉取曾被自動審查拒絕，須確切授權。不可繞過或改用浮動 tag |
| 正式 migration | 未授權套用。已實作 SQL 001–005，僅測試暫存 DB。開啟 collaboration store 不自動升級 |
| 依賴／部署／Git | 未安裝或升級新依賴；正式部署、commit／push 未授權。產品第一版不提供自動 push／部署或 host-command fallback |

實際版本（checkpoint 唯讀核對）：Python 3.13.14；deepagents 0.7.10；langgraph 1.2.11；langgraph-checkpoint 4.3.0；langchain 1.3.18；coverage 7.17.0；uvicorn 0.54.0。先前 preflight Node 22.23.2、Docker Desktop 4.41.2／Engine 28.1.1。沒有更換核心框架。

Docker 曾因 stale runtime socket 啟動失敗。在當時授權及確認沒有 Docker 程序後，僅將 runtime `run` 目錄改名保留為 `C:\Users\y4lin\AppData\Local\Docker\run.collaboration-backup-20261030`，再啟動成功；沒有刪除映像／volume。後續 **2026-10-30 覆核 Desktop 為 paused**，沒有解除暫停；空白 `docker ps` 不代表容器消失。checkpoint 沒有重新探測或變動 Docker。

既有三個容器屬於其他 `mongo_job_operation_board` 專案，先前 IDs 開頭為 frontend `cd09560bb7e8`、backend `93338d06c871`、Mongo `1869ae61c192`，restart policy 均 `unless-stopped`。操作前必須重新查精確 IDs／狀態，不能據此短 ID 停止程序。

資料位置仍以最後查驗為：`C:\Users\y4lin\AppData\Local\Docker\wsl\disk\docker_data.vhdx`（約 27.4 GiB）、系統磁碟 `...\main\ext4.vhdx`。提議新根目錄 `D:\DockerDesktop\wsl`，離線備份 `D:\DockerDesktop\backups\before-move-20261030`；兩者尚未建立／搬移。最後 C 可用約 40 GiB、D 約 880 GiB，執行前需重查，不是當下保證。官方 UI 的 Disk image location 步驟需管理員操作；可用工具不提供原生 Desktop 設定頁控制，不手改未查證 settings key。

## 4. 已實作與已驗證的範圍

以下是內部領域及框架合約，不是已提供完整使用者功能。尚無新 HTTP／UI／團隊 live runner。

| 模組 | 已實作與相應測試 | 實際限制 |
|---|---|---|
| `contracts.py`、`state.py` | immutable 設定／定義／成員快照、嚴格欄位／路徑、各生命週期狀態／revision、settlement facts；`test_contracts.py`、`test_state.py` | 純合約不是執行權限或資料庫完成結算 |
| `storage.py`、`migrations/001…005` | SQLite BEGIN IMMEDIATE、scope、先授權再 replay、原結果／異 body 409、events、明確多版本 migration、checksum／備份；`test_storage.py`、`test_migration_web_compat.py` | 不在 open／import 時套正式 migration；沒有外部 intent/result journal |
| `access.py`、`service.py` | 共用真正 AuthService 的 admin／operation boundary，草稿設定、定義版本、秘密遮蔽、CAS；`test_team_service.py` | 無新 HTTP route，不能冒充逐路由驗收 |
| `runs.py`、`budgets.py` | run／attempt／snapshot 分層、持久單一 team 活動 fence、queued start/stop race、累積 used/reserved/wall、明確 continue、新 attempt／舊終態保留；`test_runs.py` | legacy worker 反向 fence 尚未接；UTC 累計不是完整 supervisor／硬 wall-time 限制；沒有實際程序停止 |
| `mailbox.py` | 直接 peer 訊息、固定廣播、每成員 sequence、批次 claim、唯一活動 MemberTurn、accepted/delivered/processed、unknown 不 replay；`test_mailbox.py` | claim／finish 只記錄狀態，不執行 graph；實際 runtime 結算待整合 |
| `questions.py` | typed user question、原 member／WorkAttempt 回答、分頁、expiry／stale／stop／session 防守、回答與 peer 訊息共用 sequence／turn；`test_questions.py` | 只有一般 user question；非程式驗收、manual validation 尚未實作；沒有真 graph 待答恢復／Orchestrator 拒絕處理 |
| `policies.py` | admin 精確核准 TeamRun `model_calls/messages/wall_seconds` 總額延長、immutable policy versions、CAS、expiry／deny／stale、歷史與分頁；`test_policies.py` | 只有上述 budget_extension；其他 ApprovalRequest 類型、repair-root extension、模型提出核准請求及真正撤權仍待實作 |
| `checkpoint.py`、`middleware.py` | 公開 BaseCheckpointSaver SQLite、thread／完整版本／pending writes、原生摘要／私有 offload／歷史回查、取消狀態、per-graph 停用委派及私有檔路徑拒絕；`test_checkpoint.py`、`test_framework.py` | 真 graph＋可控模型通過，不是實際 provider／GPU 模型或容器 backend 驗收；不得跨重啟重播未決工具 |
| `preflight.py` | 唯讀 runtime／engine／immutable image 檢查，缺 image fail closed；`test_preflight.py` | 不拉取、安裝、啟動、建置或用 GPU；兩個真 Linux 工作副本 gate 未完成 |

WorkItem／WorkAttempt／result-ref 的 schema、FK、活動 attempt 唯一鍵已存在；測試以可信任 fixture 填入，不能把這些表當成已完成 DAG／派工／整合／驗證服務。表格中的模組位於 `pi_deepagent/collaboration/`，測試位於 `tests/collaboration/`。

## 5. 最新政策批次的關鍵語意

此批已修改 `policies.py`、SQL `005_policy_versions.sql`、`runs.py`、`budgets.py`、`mailbox.py` 及相關測試／mutation probe；原有兩個直接 SQL 改 policy version 的測試改成經真實 admin 核准流程。

- 初始政策及後續版本保留不可變記錄。current policy cache 的更新須有對應新版本，版本必須 +1；MemberSnapshot／原 config／舊 attempt 不改。
- admin 請求精確保存舊／新總上限、policy version、run attempt、相關 work、reason、期限。接受請求與決議會更新 run revision；重複 request 回原結果，異 body 409。
- 核准只提高明確選取的三種 TeamRun 總額，不改能力、目標／criteria、used/reserved、不解除 unknown、不釋放資源，也不啟動／繼續 failed 或 interrupted run。
- 新操作仍只接受目前 policy 的 grant。**只有可信任 coordinator 的既有 budget settlement 與既有 turn finish** 可用 `allow_historical_policy=True`；仍須精確原 grant、active、目前 attempt、可查舊政策、原 reservation／turn fence。不接受模型傳該選項，不讓 revoked grant 生效，也不能任意結算 unknown。
- stopped／budget-exhausted／interrupted／continue 取消 pending approvals。deny／expired／superseded 都保存結果，不自動重提。真正 Orchestrator 事件消費與確定退出待 M3。
- 不支援的 capability、responsibility_exception、validation_plan_change、criteria_change 及 repair-root 額度目前拒絕，不宣稱已完成；完整最終範圍不變。

## 6. 已完成的驗證與證據

私有 evidence 根目錄：

`C:/Users/y4lin/.codex/visualizations/2026/10/29/01a18853-f3dc-7770-aac1-9c97284f6be4/`

| 批次 | 實際結果 | 主要索引／日誌 |
|---|---|---|
| 原始基準 | Python 188；Node 24 | `collaboration-baseline-index.json` |
| M0 | collaboration 23；完整 Python 211；6/6 checkpoint 抽樣變異 | `collaboration-m0-index.json`、M0 review |
| M1 基礎 | collaboration 62；完整 Python 250；9/9 變異 | `collaboration-m1-index.json`、M1 review |
| M1 run／mailbox | collaboration 100；完整 Python 288；10/10＋9/9 變異；seed 1030 的 37 項通過 | `collaboration-m1-runtime-index.json`、runtime review |
| M1 questions | collaboration 121；完整 Python 309；17/17＋9/9 變異；seed 1031 的 58 項通過 | `collaboration-m1-questions-index.json`、questions review |
| **最新 policy-v5** | **collaboration 142（26.831 秒）、完整 Python 330（69.588 秒）、26/26＋9/9 變異，0 fail／skip** | **`collaboration-m1-policies-index.json`；`m1-policies-tests.log`、`m1-policies-full-python.log`、`m1-policies-coverage.json`、`m1-policies-mutations/`、`m1-policies-foundation-mutations/`** |

最新 policy 新增 21 項測試：真 SQLite／AuthService、多連線 approve-vs-deny、同 request 去重、版本／父子資源、unknown／歷史消耗、舊操作結算、failed 延長不自動續跑、SQL 故障回滾、交易內 revision 變動 CAS，以及真 v4 schema／資料→v5 的備份保存。沒有 mock SQLite、mock graph 或 Docker 成功結果。

最新 coverage（line／branch 分開；沒有 exclude／omit／pragma）：policies **148/154（96.1%）／47/52（90.4%）**；budgets **77/77／28/28**；mailbox **156/158／62/64**；runs **231/245／58/76**。依 unit-test-trust-review，對這個內部持久化範圍採**中等信任**：抽樣變異全被抓出，但仍有 service-actor／防守分支、過長理由／找不到 approval、少數 CAS／不一致 cache 防守未覆蓋，不能推論真執行安全。

Mutation probe 只在子程序記憶體改單一分支，未改磁碟原始碼；必須有完成的 unittest marker，來源不唯一、import/setup 失敗、空 suite 或 skip 不能算 killed。部分 mutant 被真 SQL／CAS 防線先拒絕，並非每個都是單純 assertion failure。

**最新 policy suite 尚未另外執行 shuffled 79 項；不可填成通過。** 既有 58／37 項順序紀錄對應舊批次。最新 Node 24 為先前未改 UI 的回歸，不是真瀏覽器、手機／鍵盤或團隊 UI 驗收。

本 checkpoint 前的所有已知 test/mutation command handles 已回報終態成功，沒有需要續 poll 的工作。本 checkpoint 不枚舉或停止使用者其他程序；沒有子 Agent。raw logs 的預期 CLI／故障訊息不等於 suite 失敗，以最終 unittest 結果與 exit code 核對。

## 7. 全部仍須完成的工作

| 階段／功能 | 未完成內容及不可省略的證據 |
|---|---|
| M0／F3–F4 | 兩個真 Linux 副本同名檔隔離、全部 native/pi 檔案工具與命令同副本、背景程序計量／停止、網路／CPU/RAM/PID/磁碟／輸出限制、乾淨候選重建；approved images 與 Docker 條件仍待外部確認 |
| M1／F1–F2、F6、F10–F11 | 剩餘 typed approvals、policy／responsibility／criteria／plan 版本、repair-root 額度、非程式驗收、WorkItem／WorkAttempt 實際管理與 DAG、公開 HTTP 完整授權矩陣、legacy worker 共用頂層 fence；目前不對外提供新 routes |
| M2／F2–F4 | 原 repo dirty/untracked 快照、秘密／特殊檔政策、獨立 Git／volume、不可寫共用 metadata、container executor/backend／能力、資源 leases、intent/result journal、停止／撤權確認，無 host fallback |
| M3／F5、F6、F10 | 每 member 獨立 graph/thread、FIFO／model 與 execution 名額、原生摘要歷史、typed answers 真恢復、supervisor／repair root 累積額度、failure 去重／過期、無進展與互等退出、stop/revoke/drain、重啟只對帳不重播、完成 CAS 屏障 |
| M4／F7–F9 | changes／lineage／source claim、三方合併與同檔衝突、checkout_base vs publication_base 保留 A+B+repair、發布 CAS、service actors 仍進隔離、required plan／protected assets review／collection、新 plan 全重驗、Windows admin_manual／附件、patch／apply 拒絕／cleanup journal |
| M5／F1 及全驗收 | 真 UI 重載／待答／錯誤、375/768/desktop、keyboard／mobile、logout late response、31 項 AC 全部平台／反例、完整單 Agent／CLI／A2A 回歸、操作與設定／停止／恢復／資料保留／migration／備份回復文件 |
| 必要真模型閉環 | 真的缺陷使首 candidate required tests 失敗→服務證據→Orchestrator 自主診斷派工→成員直接修復→新 candidate 全 required pass／交付；無人工轉貼、逐次重派或假 pass。GPU pending 只延後執行，不能取消此要求 |

AC-01–31 的逐項映射與反例在 [驗收矩陣](multi-agent-collaboration-acceptance.md)；沒有任何整項可提升為 pass。F1–F11 與 D1–D5 的完整規劃仍保留於計畫。最終還需重新讀 spec 逐條查 code＋evidence；不能只看測試全綠。

特別不可遺失的規則：模型不能決定權限／預算／驗證／發布；驗證與再生成 service actor 仍須相同容器 lease／預算／停止規則；未知結果不得換 ID 重跑；修復 root 不能經換人、拆工作或重建 lineage 歸零；多來源候選修復不得遺失來源或重複納入；required gate 不以 exit code／模型文字替代；舊 attempt 終態及證據不可因容器移除而消失。

## 8. 恢復工作時的建議下一步

後續續作時（目前目標已 active）：

1. 重新核對 goal、git status、此 checkpoint 的 code/evidence hashes 與適用指示；保留全部未提交工作，不因中斷重跑已完成的測試／操作。
2. 如需要補 policy 批次的順序驗證，僅針對 run／mailbox／questions／policies 79 項執行並留下新唯一檔名；不得把此前未執行的案例標通過。若後續改碼，按影響範圍再測。
3. 依核准計畫續作 M1 剩餘領域與 HTTP 合約，小批次驗證；不要在 M0 容器假設未證實前進入依賴它的正式 M2/M3 live execution 整合。處理政策／能力時維持「新派工驗目前版本、既有結果用精確歷史身分結算」的分離，不把後者做成通用授權。
4. 外部條件需要時，對已可審查的 Docker 搬移方案取得實際執行／停機窗口與確切映像確認，再依官方流程操作。GPU 要等使用者解除 pending。新依賴、正式 migration、部署或 Git side effect 均維持原授權界線。

常用重現命令（repo 根目錄）：

```powershell
.venv/Scripts/python.exe -m unittest tests.collaboration.test_policies -q
.venv/Scripts/python.exe -m unittest discover -s tests/collaboration -q
.venv/Scripts/python.exe -m unittest discover -s tests -q
.venv/Scripts/python.exe scripts/collaboration/m1_runtime_mutation_probe.py --output <new-empty-evidence-path>
.venv/Scripts/python.exe scripts/collaboration/m1_mutation_probe.py --output <another-new-empty-evidence-path>
node --test tests/ui-state.test.mjs tests/ui-app.test.mjs
```

完整 graph／Web／asyncio suite 需要允許本機 loopback socket，受限沙箱內曾因 socket 建立卡住；必要時使用已授權的 `require_escalated` 執行測試，理由說明為本機測試，不啟動模型／Docker。同步 SQLite 子集可在沙箱執行。PowerShell 不展開 shell brace/glob 給 rg，使用 `rg --glob`；主機 Docker 路徑用已確認絕對路徑，沙箱 `%LOCALAPPDATA%` 會被重導。

## 9. Context 精簡規則

往後的工作摘要以本文件、使用者完整要求及最新 authoritative files 為主。已結束工具 handle、重複版本輸出、過時失敗嘗試、舊批次測試數、無關 discovery 結果及未採用的思考分支可從「工作記憶摘要」省略；原日誌與文件保留，不刪除證據或 user work。

本次可用工具未提供直接刪除／改寫模型已載入聊天歷史或強制 context compaction 的能力。這次建立的是可持久續作的精簡入口與完整需求存檔，沒有宣稱聊天歷史已被物理清空；自動 compaction／新工作摘要應保留本 checkpoint 路徑與最新 goal 狀態。
