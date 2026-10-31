# 多 Agent 協作 v0.3：實作計畫與決策紀錄

原始計畫日期：2026-10-30（Asia/Taipei）。2026-10-31 最新入口為 [完整交接 prompt](multi-agent-collaboration-handoff-prompt.md)，原 goal 已依使用者要求 **paused**，本次僅整理交接。計畫仍已核准，完整要求及授權保存在 [使用者需求](multi-agent-collaboration-user-requirements.md)；兩份舊 checkpoint 保留為歷史，不代表目前工作樹或狀態。接手者收到明確續作指示後，持續既有已核准範圍；整體 M0／M1 未退出，完整 AC **0/31**。

最新未收尾批次為 repair root 上限延長／migration 009：原始完整 Python **437 passed（242.152 秒）**、Node **24**、20 項打散、root 10/10＋typed 10/10 變異及真 SQLite fixture 證據已存在；56 檔 source manifest 本次比對符合。**該批正式 trust-review 文件及 `collaboration-m1-repair-limits-index.json` 尚未建立**，先收尾審核／索引／逐項矩陣再依賴該批；詳細位置與限制見交接。以下 HTTP／Work／typed／criteria 的數字為各批歷史 tree，最近完整審核索引仍為 criteria 的 417，不是最新 raw test 數字。

Docker Desktop 已由使用者自行移除（本次未探測殘留），舊搬移問題及流程已被「重新安裝至 D 目標」方向取代。後續分開規劃程式及 Linux 資料位置，先盤點、準備可審查安裝／備份／回復方案，依具體授權執行；本次不安裝。GPU 往後安排，先完成獨立非 GPU 工作，必要關卡備妥時才請求授權；真模型最終驗收要求不變。

工作依據：[v0.3 規格](multi-agent-collaboration-spec.md)、[專案指示](../AGENTS.md)、[身分與權限](security-requirements.md)。[逐項驗收追蹤](multi-agent-collaboration-acceptance.md)與本文件一起維護。下列新模組、測試與指令交付物均為規劃，存在與否不能代替驗收。

checkpoint 後最新增量：[團隊草稿 HTTP 審核](multi-agent-collaboration-m1-http-review.md)。四個 opt-in 草稿路由已通過真 HTTP／AuthService／SQLite 驗證；完整 Python 344、Node 24 通過，整體仍 0/31 AC。原 checkpoint／index 保留其當時 tree，最新程式與證據以新批次索引為準。

最新增量：[WorkItem／DAG 審核](multi-agent-collaboration-m1-work-review.md)。checkpoint 中的 `work.py`／migration 006／`runs.py` WIP 已補齊本批領域測試與 trust review；最後完整 Python **371**、Node **24**、27 項打散、11/11 變異通過，真 SQLite 領域紀錄／快照保留。這仍不是 live Work 執行、完整 repair quota／continue 對帳或任何整項 AC 完成；M0／M1 尚未全部退出。HTTP 的 344 是其歷史 tree 結果，不是最新數字。

目前最新：[typed approvals 審核](multi-agent-collaboration-m1-typed-approvals-review.md)。新增 capability ceiling／精確 responsibility_exception validator；007 保留資料重建 collaboration 三表以擴充 CHECK，001–006 不改、舊 tasks 不重寫、正式套用未授權。完整 Python **394**、Node **24**、23 項打散、10/10 變異通過，真 SQLite 證據保留。其他 approval／實際隔離／撤權／恢復仍待完成，完整 AC 0/31。

最新增量：[criteria_change 審核](multi-agent-collaboration-m1-criteria-review.md)。008 additive 目標版本、admin 精確 replacement、原條件／輸入保留、新 WorkAttempt refs 已驗；最終 Python **417**、Node **24**、23 項打散、10/10＋10/10 變異通過，真 SQLite probe 保存。先前 394 為歷史 tree；criteria refs 不是 actual gate，驗證計畫／supervisor／HTTP/UI 等仍待實作，完整 AC 0/31。

## 1. 調查結果與需求理解

初始 revision：`6bd537a9fbf17c790de356cbe006b97ddf1b55fb`，分支 `master`。調查開始時只有未追蹤的 `docs/multi-agent-collaboration-spec.md`；不加入 index、不覆寫、不刪除它。後續批次記錄實際工作樹 diff hash；有未提交修改時不能只用 HEAD 證明執行版本。

已閱讀 AGENTS.md、v0.3 全文、security-requirements.md、ui-implementation-spec.md、web-workspace.md，以及相關的 core-contracts.md、a2a.md、local-llm.md、tool-output-review.md。已追查 factory、local model middleware、Web runner/service/app/auth 與既有 graph／工具／HTTP 測試。無 `.codegraph/` 索引，採檔案與符號搜尋；不建立索引。

| 現有位置 | 觀察 | 實作影響 |
|---|---|---|
| `pi_deepagent/agent.py` | 呼叫公開 `create_deep_agent`；factory 自行建立 host backend，未提供 checkpointer 入口 | 新增有明確參數的整合入口，保留原呼叫相容；團隊 factory 不附加 host 工具 |
| `pi_deepagent/web/runner.py` | `_HistoryGraph` 以 user／assistant 文字重建歷史 | 保留單 Agent 路徑；團隊使用獨立 graph、thread、完整 checkpoint |
| `pi_deepagent/web/service.py` | SQLite task payload、request ledger、單一 `_worker`／`_runner`、程序鎖 | 新增團隊儲存與排程；單 Agent 和團隊透過同一 DB 頂層活動 lease 互斥 |
| `pi_deepagent/web/app.py` | 授權先於 private dispatch；只接受 GET／POST，POST 僅 JSON | 明確加入團隊 PATCH 與單一 evidence multipart 分支，共用認證／CSRF／Origin；保留舊路由合約 |
| `pi_deepagent/web/auth.py` | admin、可撤銷 session、已知秘密遮蔽 | 沿用；新增父子資源與操作檢查、所有新路由授權矩陣 |
| `pi_deepagent/local_model.py`、`tool_output.py` | 原生摘要、compact、offload 與保存失敗即停止的相容層 | 保留功能；團隊 offload 路由與候選資料分離，摘要模型呼叫也計量 |
| CLI／獨立 A2A | 獨立入口，Web 未代理；有既有回歸測試 | 保留原本功能與界線，不把團隊成員接到未驗證 A2A |

部署維持 Windows Python 控制面、Linux 容器執行，單一 admin／repo，2–4 位成員且恰一位 Orchestrator，單一活動頂層工作。UI 設定角色、目標、責任及預授權；成員直接溝通與獨立副本，服務整合、驗證、結算及發布。所有修復消耗原 run／repair root 預算，保留失敗與來源。登出只撤銷互動存取；停止、撤權、重啟各有獨立語意。無 host command fallback、自動 push／部署或跨重啟重跑。

尚未發現須變更產品範圍的文件矛盾。舊 UI／Web 文件中的 JSON-only、無 diff／待答是既有單 Agent 邊界；v0.3 明確為團隊新增 PATCH、multipart、diff 與 typed requests。實作需限定新增分支並更新說明，不能因此放寬所有舊入口。WSL 無一般使用者發行版不等於證明 Docker 必須安裝 Ubuntu；先檢查既有 Docker Desktop backend，再提出必要環境變更。

## 2. 環境、基準與 D1–D5

唯讀 preflight 得到：Python 3.13.14、Deep Agents 0.7.10、LangGraph 1.2.11、langgraph-checkpoint 4.3.0、LangChain 1.3.18、uvicorn 0.54.0、Node 22.23.2。Docker client 28.1.1，context 為 `desktop-linux`，Server=null，Linux engine named pipe 不存在；沙箱外結果相同。WSL 清單回報未安裝發行版；沙箱內會 E_ACCESSDENIED。現有本機模型 `openai:qwen3.8-27b` 設定存在，沙箱外 health 連線被拒（WinError 10061），尚未啟動。

Windows 實體 RAM 34,103,640,064 bytes、查詢時可用 20,132,556,800 bytes，32 logical CPUs；工作所在磁碟查詢時剩餘 16,087,281,664 bytes。這些是當次觀察，不是啟動時保證；Linux engine 的實際 RAM／CPU／磁碟仍待 preflight。

| ID | 實際狀態 | 方法、啟用條件與授權 |
|---|---|---|
| D1 | **使用者已自行移除 Desktop；真 Linux gate 未驗收** | 改為重新安裝至 D 目標。先唯讀盤點 installer／CLI／WSL／磁碟及殘留資料，提出版本相符、分開程式與資料位置的安裝／備份／回復方案；依具體授權執行。卸載前 engine 就緒／paused／runtime backup／其他專案三容器皆只屬歷史，不能用來操作現在系統；不得自動刪除殘留、重設 WSL 或恢復其他專案 |
| D2 | **兩個 digest 已解析，尚未拉取；重裝後實際資料位置待驗** | 原定 Linux 資料目標 `D:\DockerDesktop\wsl`；程式目錄須另規劃，不能僅憑 exe／tar 在 D 就宣稱 images／volumes 在 D。舊 [搬移方案](docker-storage-move-plan.md) 已失效。先確認新安裝實際資料位置／容量及確切映像授權，再 pull／build；自動審查先前拒絕未獲確切授權的拉取。之後以真容器驗證 EnvironmentSpec／限制，不以浮動 tag 執行 |
| D3 | **checkpoint／graph 公開合約部分實證通過；容器 backend 待驗** | 真 SQLite saver、pending writes／排序／版本、原生摘要／私有 offload／回查、取消保留未決狀態、per-graph 隱藏並拒絕 task 已有測試。詳 [M0 審核](multi-agent-collaboration-m0-review.md)。尚未證明真容器 backend、全隊預算硬限制或跨服務重啟的完整調度，不標 M0 完成 |
| D4 | **計畫已核准，採 patch 交付** | 無可靠外部獨占窗口證據，apply 回結構化拒絕；不自動寫回。Windows required check 使用版本綁定的 admin_manual 證據，保留人工來源標示。開放寫回需另提獨占／備份／journal 計畫 |
| D5 | **計畫已核准；正式 migration 未授權套用** | 標準庫 SQLite、新增命名空間資料表及版本化 migration；不重寫既有 tasks。M0 saver 僅允許明確初始化空 DB，拒絕改動既有單 Agent DB。先審查正式 SQL、備份還原及暫存舊 DB 相容測試，再請管理員授權正式套用 |

D2 提議的初始執行政策：模型並行 1、全隊容器執行並行 2（涵蓋 member／integration／validation），每容器 1 CPU、1 GiB RAM、128 PIDs、128 MiB tmpfs，唯讀 root、非 root、drop capabilities、no-new-privileges、離線、不開 port。每 workspace 容量監測門檻 1 GiB、每 run 管理資料門檻 4 GiB、宿主可用空間低於 8 GiB 拒絕啟動／新配置；此為**容量監測與終止門檻，非硬磁碟 quota**，必須實測耗盡與監測失敗。映像建置前重新計算可用容量；不足時提出位置／容量方案，不刪除既有資料騰空間。

其他預設：wall time 60 分鐘、turn 10 分鐘、命令 120 秒、模型呼叫 100、訊息 200、無進展交付 10、peer wait 5 分鐘；自動修復最多 3 次，相同 fingerprint 兩次修復仍無進展退出。輸出提議上限每 Execution 1 MiB；附件依 spec 10 MiB。計畫核准不等於已取得 image digest、啟用無上限執行或延長 runtime budget。

基準回歸（Windows，未修改產品程式）：完整 Python 188 項通過，44.506 秒；Node 24 項通過。Python 必須在允許 loopback socket 的環境執行；最初沙箱程序 handle 已消失且無完成紀錄，未算通過，以後續完成的沙箱外執行為準。這些是舊功能基準，不是新 AC 的通過證據。

## 3. 先完成的領域合約

新增 `pi_deepagent/collaboration/`，不重寫既有 runtime loop，也不引入 Agent Protocol server／通用工作流引擎。具體拆檔可依最小完整批次調整，對外與持久化合約不隨 UI 改變：

- `contracts.py`：definition／snapshot／TeamRun／RunAttempt／WorkItem／WorkAttempt／MemberTurn／Execution／Workspace／Container 分離。明確狀態轉移、immutable 終態、UTC 時間、canonical hash、typed requests、結構化錯誤與嚴格欄位驗證。
- `storage.py`、`migrations/`：SQLite foreign keys、unique keys、revision CAS、request ledger、單調 events、outbox、budget reservations、activity／model／execution leases、intent/result journal；同一狀態變更的 ledger／event／預留額度在同交易。
- `policy.py`、`service.py`：服務綁定 actor、run／attempt／member／workspace／policy，模型只提業務參數；admin／owner／workspace／父子 membership 先驗證再查去重結果。capability／責任例外／budget extension／plan／criteria 核准均版本化。
- `mailbox.py`：確定收件清單、member 序號、accepted／delivered／processed、原子批次 claim、回覆與 typed question、去重及完成屏障。
- `workspace.py`：dirty tracked＋明確選取 untracked 快照、二次驗證 manifest、秘密與跨平台路徑政策、私有 Git 版本、獨立 Linux volume，禁止共用 Git metadata 可寫掛載。
- `execution.py`：服務設定的 Docker argv、精確 ownership labels、lease／journal、命令結果、背景程序計量、資源監測、確認停止後才釋放名額；缺 engine／限制即 fail closed。
- `backend.py`、`checkpoint.py`、`runtime.py`：同副本 Sandbox backend＋私有 Composite 路由、全套 SQLite saver、獨立 graph/thread、所有模型呼叫預算入口、保留原生摘要及工具配對、per-graph 禁委派 middleware。
- `scheduler.py`、`supervisor.py`：FIFO、同成員串行、DAG／動態互等、事件驅動 Orchestrator、持久 failure/root/generation、修復計畫驗證、累積預算、停止排空及重啟對帳。
- `integration.py`：不可變 changes／candidate、publication_base 與 checkout_base、來源集合／claim／lineage、三方合併、受控再生成、發布 CAS；主機 Git 禁 hooks／filters／external diff／任意 repo config 執行。
- `validation.py`、`artifacts.py`：固定 plan/check/protected rules、乾淨容器、可信程序／collection 結果解析、新 plan 全 required 重驗、manual-validation、附件與 patch、受登記路徑限制的清理 journal。
- `web_adapter.py`＋既有 `web/app.py`、`web/service.py`、`web/__main__.py`：HTTP／startup／shutdown 整合、共享頂層 lease、正式 migration 明確啟用；UI 待領域與服務端路徑可驗證後接入。

核心交易不變量：

1. 同 ID 同 body 回原結果；不同 body 回 409。unknown 必須對帳同一 operation，不換 ID 重跑。
2. 派工 claim 與停止／撤權／預算保留／完成共享版本屏障；新訊息先接受就必須處理，先完成就拒絕 mutation。
3. 資源未確認排空不釋放 lease；停止不回復副作用。重啟只恢復已知結果，不 resume 未決工具 checkpoint。
4. 修復 root／failure 關係由服務建立並跨來源重提、重建 lineage、換成員、子工作保留；multi-root 原子逐一預留，任一不足不得派工。
5. 多來源未發布候選修復保留完整來源與雙基準；共同版本推進後重合併、重驗。發布須 exact candidate／plan／policy／base CAS。
6. required gate、protected 資產、collection 條件與人工驗收不由模型決定。integration／validation actor 仍用容器、同隊 lease、預算及停止／撤權規則。

## 4. M0–M5 依賴、交付與退出條件

| 階段 | 小批次與修改位置 | 依賴與交付物 | 進入下一相依階段前的證據 |
|---|---|---|---|
| M0 技術驗證 | `scripts/collaboration/` preflight／framework／sandbox probes、最小 `backend.py`／`checkpoint.py` spike、測試資料 fixture；不接 UI | 計畫核准；容器部分另需 D1/D2。保存版本／合約、真實 graph transcript、環境設定與可重建步驟 | 真實 graph 摘要／歷史／offload／工具配對／取消；兩個 Linux 環境同名檔隔離、工具與命令同副本、背景程序計量／停止／網路／limits、乾淨候選重建。失敗提供最小重現；不把 spike 當正式功能 |
| M1 領域與政策 | contracts→storage/migrations→policy/service/mailbox→HTTP 嚴格合約與授權 | M0 的框架／持久化合約確認；新增 schema、狀態轉移表、typed decisions、授權矩陣、race／ledger 測試 | 真實 SQLite 交易／CAS／競態、原子 budget reservations、定義與快照／attempt 不可變、郵件去重、舊 DB 相容；AC-03/04/12/19/20/25 對應後端分支 |
| M2 工作區與執行器 | workspace→execution→policy tools/backend→factory 注入點 | M0 容器與掛載驗證通過、D1/D2 可用；快照／volume／container／journal／配額實作 | AC-05–09 真 Git／Linux 容器通過；host sentinel 原樣、同副本、越界拒絕、停止未確認持有 lease；無 host fallback |
| M3 排程與恢復 | checkpoint/runtime→scheduler→supervisor→reconciliation | M1/M2；先以真 graph 及可控模型做一條 turn／訊息／命令，再補取消、重啟、修復分支 | AC-10/11/18/22–25/29–31 的相關排程／故障案例；摘要回查與工具不重跑；舊通知不喚醒新 attempt |
| M4 整合與驗證 | changes/integration→validation/protected review→失敗回送／修復→manual/artifacts/cleanup | M3 可控制執行；固定 EnvironmentSpec／validation plan；先完成真實 defect→failure→repair→new candidate→publish 一條流程 | AC-13–18/21–24/26–30：來源保留、conflict／claim／發布 CAS、乾淨 required gates、multi-root 預算、unknown 對帳、人工 Windows 證據；至少一次真實設定模型閉環 |
| M5 UI 與完整驗收 | 原生 static HTML/CSS/JS、state.mjs、web adapter、設定與操作文件、驗收 runner／索引 | 領域與 HTTP 已可執行；使用現有輪詢與 admin session；無前端重寫／新串流協定 | AC-01–31 全部具可重現證據；375/768/桌面、keyboard、重載／待答／late response；完整 Python/Node、CLI/A2A 回歸、測試可信度審核、最終 spec→code→evidence 逐項稽核 |

環境受阻時，可完成不依賴受阻假設的領域合約、SQL 審查、純邏輯／真 SQLite／Git 測試與文件。M0 容器 gate 保持受阻；不得進入依賴它的正式執行整合，或宣稱 M0/M2 已完成。這個分流不縮減最後要求。

每一批先定位現有目標／用途，補具意義的失敗回歸、修改最小完整行為，執行窄檢查與 diff review，再更新追蹤表。通過後才堆疊相依能力。Python 測試新增或修改後依 unit-test-trust-review 審核；UI 實作時讀取 ui-ux-pro-max；實際瀏覽器驗收時使用可用的瀏覽器工具與隔離測試 admin。

## 5. F1–F11 實作追蹤

| 功能 | 主要實作位置（規劃） | 測試／證據主線 | 現況 |
|---|---|---|---|
| F1 UI | `web/static/`、`collaboration/web_adapter.py` | AC-01/02/16/28，真瀏覽器／JS 狀態測試 | M1 草稿 service 及四個 HTTP 路由已有；團隊 UI／run 啟動未實作 |
| F2 能力 | `policies.py`、`service.py`、`execution.py` | AC-03/04/23/29，HTTP／DB／容器拒絕與撤權 | M1 immutable policy versions 與 admin 的三種 TeamRun 總預算延長已驗；其餘核准類型、執行期能力／撤權未實作 |
| F3 副本 | `workspace.py`、`backend.py` | AC-05/06/08/26，真 Git／檔案／容器 | 未實作 |
| F4 容器 | `execution.py`、`scripts/collaboration/` | AC-07–09/29，真 Linux engine 故障與限制 | M0 preflight 已實作；執行器與容器 gate 未完成 |
| F5 上下文／排程 | `runtime.py`、`checkpoint.py`、`scheduler.py`、`supervisor.py` | AC-10/11/21–25/29–31，真 graph／預算／repair | M0 checkpoint／工具邊界部分已實作；完整 F5 未完成 |
| F6 訊息 | `mailbox.py`、`questions.py`、`service.py` | AC-12/23/24/31，持久 mailbox／定向交接 | M1 mailbox 與 typed user questions／原 WorkAttempt 回答／停止失效的 SQLite 分支已驗；graph、HTTP 與 UI 未整合 |
| F7 整合 | `integration.py`、`workspace.py` | AC-13/14/22/26，真 Git／來源 claim／CAS | 未實作 |
| F8 驗證 | `validation.py`、`execution.py` | AC-15/16/21/24/27–29，乾淨 gate／review／Windows manual | 未實作 |
| F9 交付 | `artifacts.py`、`service.py` | AC-17/28/30，patch／membership／cleanup journal | 未實作 |
| F10 狀態／恢復 | `contracts.py`、`storage.py`、`scheduler.py`、`service.py` | AC-18/23/25/30/31，競態與 crash/restart | M1 run／attempt／grant／預算／mailbox 交易與歷史已驗；外部資源排空、對帳及完整 runtime 未整合 |
| F11 HTTP／稽核 | `web_adapter.py`、`storage.py`、既有 `web/app.py/auth.py` | AC-19/20/28，逐路由／分頁／秘密植入／migration | M1 draft HTTP／授權／CSRF／分頁／稽核四路由已驗；其餘 HTTP、附件及完整 runtime 稽核未整合 |

## 6. 驗證方法與證據保存

測試以既有 unittest／Node surface 延伸，整合測試使用實體暫存 SQLite、Git repo、真實 graph；容器 suite 明確要求 Linux engine 與核准 digest。mock 僅補故障分支，不算系統層通過。必要環境不存在時驗收 runner 回受阻且整體 gate 非通過，不用 skip 美化成 green。

預定交付 `scripts/collaboration/` 內可重現的 preflight、驗收與真模型 scenario 入口，以及 Linux runtime 建置來源；其確切命令於對應實作批次固定並寫入 acceptance。每次執行保存 execution record、原始遮蔽日誌、檔案 hash、候選及 plan/environment ID；建立可攜式 evidence index，避免只指向聊天或會被清掉的 tempdir。DB 與候選 fixture 複本保留必要追溯材料，不保存憑證／私有推理。

Windows 全回歸指令：

```powershell
.venv/Scripts/python.exe -m unittest discover -s tests -q
node --test tests/ui-state.test.mjs tests/ui-app.test.mjs
```

Linux 命令與真模型閉環必須另有完整紀錄：第一候選的實際缺陷、服務失敗事件、Orchestrator 的可驗證 repair plan、成員直接往來、新內容、全 required checks、新候選發布、完整 ledger 與無人工重派工證據。不能以 predetermined model 代替此項；可控模型 graph suite 分開列。

最終重新完整閱讀 v0.3，逐項對照 F、AC 及各節必須／不得；逐一開啟證據並確認它對應最終 tree／candidate／plan。所有 AC 通過、平台／真模型證據齊全、無阻擋缺陷才可宣稱可驗收。

## 7. 風險、備份與回復

| 風險 | 降低方式與驗證 | 回復／停止邊界 |
|---|---|---|
| 公開 graph 擴充點不能區分 framework write 與一般 tool write | M0 真 graph 驗證 Composite 私有路由、middleware、所有讀寫／偽造 task call；先設定再擴充 | 不改 site-packages／全域 profile；失敗時提交最小重現與新計畫，不能換核心 loop |
| engine 不可用、停止 unknown、磁碟不足 | fail closed、journal／labels 對帳、儲存／CPU/RAM/PID/網路與 engine failure 實測 | 僅停止／清理本服務登記資源；未確認保留 lease，禁止新執行 |
| SQLite 競態或 migration 損傷舊資料 | 真 DB 多連線／barrier 競態、唯一鍵／CAS、舊版本 DB fixture、schema 與備份雜湊 | 正式 migration 前停服務並確認無 active/unknown；SQLite backup API＋blob manifest，驗完整性；另授權套用 |
| 多來源修復遺失成果／重複來源 | 雙基準、immutable lineage／claims、root 永續關係；實測 A+B 失敗修復與另一候選搶先發布 | 保留原 proposals／candidates；共同版本回復也新建版本；不 reset 使用者 repo |
| gate 被削弱／Agent 宣告成功 | protected manifest 含新增路徑規則、admin review→新 plan→全 required 重驗、可信收集解析 | 未核准不發布；保留原失敗與原 plan，人工拒絕形成確定退出 |
| 模型無法自主修復或只循環 | 用實際缺陷＋真模型閉環驗證；fingerprint／無進展／累積 budget 程式限制 | 必要工作 failed 阻止 completed；保留阻礙報告，不縮減測試／目標 |
| 跨平台或外部編輯器競態 | Windows required 人工版本證據；拒 symlink/reparse/collision；原 repo sentinel／index/HEAD 對照 | 無可靠外部獨占只交付 patch；任何後續 apply 另提計畫 |
| 新入口洩密／登出遲到回應 | 全路由權限與 membership、秘密植入、multipart 限額／nosniff、session generation | 清除私有 UI、失效 token 拒絕；已授權工作依政策繼續 |

正式回復採停服務、對帳本服務執行資源、保存現有失敗現場與 DB/blob 備份，再恢復經驗證相配的一組備份；不得把只還原 DB 當成已回復所有檔案／外部副作用。新增表保留而停用團隊 adapter 的舊版相容性需實測；不預設 destructive down migration。未提交程式可依人工審查的檔案級差異回復，不使用 blanket reset/clean。

## 8. 本次請求確認的範圍

1. 核准本計畫後可持續執行 M0–M5 的程式、測試、文件與隔離測試資料修改，依既定 gate 及授權推進；不逐步重問已核准例行工作。
2. 可另外核准啟動**既有** Docker Desktop（`C:/Program Files/Docker/Docker/Docker Desktop.exe`）及既有 `scripts/start-local-model.ps1`，供 preflight／真模型驗收；後者會使用 GPU 並寫既有 `D:/LocalLLM` 日誌。這不包含安裝／升級 WSL、拉取／建置映像、新依賴安裝、正式 migration、部署、commit 或 push。
3. 映像／Linux 依賴的確切來源、digest、安裝及磁碟成本，於 M0 準備可審查內容後另請核准；正式 migration 也於備份／還原證據齊備後另請核准。若需要擴張架構、安全或範圍，先報差異。

確認紀錄（2026-10-30）：管理員明確回覆「我同意你按照計畫續行」。追加回覆：「先把依賴 GPU 的功能 pending 我暫時有私人使用需求，除此之外的使用如 docker 操作核准」。因此可執行本計畫的程式／測試與所需 Docker 操作；不啟動 GPU 模型。Docker 映像／容器操作保存實際來源、digest、限制與證據；若需要 WSL 安裝／系統設定／重開機或 host 新依賴，另列確切差異。正式 migration、部署、commit、push 仍未授權。GPU pending 不代表 AC-21 可免驗，也不縮減最終交付標準。
