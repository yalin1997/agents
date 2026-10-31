# 多 Agent 協作：目前 checkpoint

Checkpoint：`CP-20261031-work-wip`；日期：2026-10-31（Asia/Taipei）。這是目前續作入口，取代 [policy-v5 checkpoint](multi-agent-collaboration-checkpoint.md) 的「目前狀態」角色；舊檔及舊 evidence 不改寫。使用者原始完整要求、後續授權與限制保存於 [使用者要求](multi-agent-collaboration-user-requirements.md)。

**目標未完成，F1–F11 尚未全部實作，完整 AC 通過仍為 0/31。** 本次工具查詢 goal 為 `active`，objective 為「繼續完成指定任務」、無 token budget；沒有將 checkpoint 解讀為取消、完成或暫停目標。這一輪依使用者要求先整理紀錄，不繼續產品實作、不啟動模型／Docker、不套用正式 migration、不 commit。

## 1. 續作閱讀順序與資訊優先級

1. 本檔、[使用者完整要求與授權](multi-agent-collaboration-user-requirements.md)。
2. [AGENTS.md](../AGENTS.md)、[spec v0.3](multi-agent-collaboration-spec.md)、[security-requirements.md](security-requirements.md)。摘要不能取代最終逐條核對原規格。
3. [核准計畫](multi-agent-collaboration-plan.md)：D1–D5、M0–M5、F1–F11；[AC-01–31 矩陣](multi-agent-collaboration-acceptance.md)。
4. [最新已驗證批次：HTTP review](multi-agent-collaboration-m1-http-review.md)，以及下一批相關 source／tests。過去已完整閱讀 `ui-implementation-spec.md`、`web-workspace.md`、`core-contracts.md`、`a2a.md`、`local-llm.md`、`tool-output-review.md`；進入相關範圍時回查原文。

以原始規格、當時適用指示、實際檔案及外部狀態為準。發現會影響架構／功能／安全／資料／修改範圍的矛盾，提出具體衝突，停止依賴該決策的實作；不自行挑較容易的解讀。已核准範圍的例行工作持續完成，不逐步重問。

舊逐行日誌、已完成的工具 handle、重複環境輸出、未採用方案不用載入工作摘要。需要追溯時再讀歷史 review／index；不刪除原證據或未提交內容。

## 2. 工作樹與本 checkpoint 的識別

- Repository：`C:\Users\y4lin\gemini_repo\pi_deepagent`；PowerShell；branch `master`；HEAD `6bd537a9fbf17c790de356cbe006b97ddf1b55fb`。
- 原始使用者唯一未追蹤檔 `docs/multi-agent-collaboration-spec.md` 保留不改。SHA-256：`28cc0b0762499ab33209dde4ff2916a24013264de16c0cdbd3d7d59d1b59e4c1`。
- 目前 tracked 修改三個：`docs/web-workspace.md`、`pi_deepagent/web/app.py`、`pi_deepagent/web/__main__.py`。這些為已完成 HTTP 批次的修改；原始版本 hash 及 diff 保存在該批索引。
- collaboration 程式／migrations／測試／scripts、計畫／驗收／review／checkpoint 文件仍為 untracked。不能只看 `git diff` 就判斷沒有實作。沒有 commit、push、reset、clean 或改 index。
- HTTP 批次之後另有 **尚未完成驗證的三個產品變更**：新增 `work.py`、新增 `migrations/006_work_planning.sql`、修改 `runs.py`。不要把上批 344 項結果套到目前 tree。
- 本次證據索引：evidence 根目錄的 `collaboration-checkpoint-20261031-work-wip-index.json`；保存目前 source／tests／scripts／docs hashes、git status、tracked diff、語法檢查及舊 evidence 完整性核對。本檔不自稱測試報告。

## 3. 原始目標與不可減少的範圍

Windows 11／Python 控制面、Linux 容器隔離；單一 admin、單一 repository、2–4 成員且恰一 Orchestrator；同時間只有一個頂層團隊或既有單 Agent 工作。UI 設定個別職責／目標、共同目標及預授權；成員直接溝通、獨立副本、受控整合與驗證。原授權及預算內自主完成診斷、修復、重驗與交付，不增加人工轉貼錯誤或逐次重派步驟。

不得提供主機 command fallback、自動 push／部署或跨重啟自動重跑。登出撤銷互動存取，已核准背景工作依規格繼續。不得縮小功能、取消隔離、換核心框架、刪除失敗測試或跳 required gate 取得表面完成。

不可遺失的實作規則：

- 服務掌握 actor／能力／預算／狀態／驗證及發布結果；模型只提案。所有外部讀取、執行、控制、stream、下載、A2A 均驗證登入、操作及父子資源，預設拒絕。
- 先完成領域合約、不可變物件、版本、唯一鍵、事件及錯誤，再接 UI；UI 顯示服務端真相。
- 派工／停止／發布／訊息／預算／結算有交易及 CAS；外部 Docker／Git／FS 有 intent/result journal。重複請求回原結果，unknown 先對帳，不換 ID 重跑。
- 驗證及再生成即使由 service actor 發起，仍進容器、用同一全隊 lease、消耗預算並服從停止／撤權；不得在主機執行候選程式。
- checkout base 與 publication base 分離；未發布多來源候選的修復保留完整來源、lineage、claim，不能遺失或重複來源。root／累積額度由服務持久綁定，不能換人／拆工作／重建候選歸零。
- required checks、protected assets、收集條件及 plan version 必須落實；修改測試／runner／設定／驗證依賴走規格審查。乾淨候選驗證不得沿用成員修改的 venv／cache，不能只看 exit code。
- 定義、快照、TeamRun、RunAttempt、WorkItem、WorkAttempt、MemberTurn、Execution、Workspace、Container 分離；舊 attempt 終態不可改，刪容器不刪歷史與證據。
- 先查已安裝框架設定／公開擴充點，再考慮最小相容層；替換核心前須證據及重新核准。保留原生摘要、歷史、工具配對／一次執行、取消語意，不以消除報錯冒充修復。

完整完成條件仍為 F1–F11、AC-01–31 全部有足夠可重現通過證據，真框架／Git／SQLite／Linux／Windows／模型、既有單 Agent／CLI／A2A 回歸完成，無阻擋缺陷或矛盾，操作／設定／保留／migration／備份回復文件完整。最終重新逐條讀 spec，比對最終 code 與 evidence，不能只看測試綠燈。

## 4. 授權、D1–D5 與外部環境

| 項目 | 目前可依據的授權與條件 |
|---|---|
| 計畫 | 使用者已明確同意續行。已授權範圍小批次不重問；擴大架構／安全／依賴／範圍先提差異與理由並取得同意 |
| GPU／模型 | **pending**，使用者有私人 GPU 需求。不得啟動 `scripts/start-local-model.ps1` 或 GPU／模型；最終真模型閉環要求未取消 |
| D1 Docker | 一般 Docker 操作曾核准。曾成功啟動 engine，後續最後觀察為 paused；本次未探測／解除暫停。不可推定現在 ready 或任意停止其他專案 |
| D2 映像／儲存 | 兩個 immutable digest 已解析，未拉取。先前 pull 被自動審查拒絕，要求確切授權；使用者轉而要求 D 磁碟位置及「先準備可審查的搬移步驟」。實際搬移／停機／備份／pull 仍待確認 |
| D3 框架 | 已安裝公開 checkpointer／middleware、真 graph 可控模型部分實證。容器 backend、完整預算／supervisor／重啟對帳尚未證明，不宣稱 M0 完成 |
| D4 寫回 | 核准計畫採 patch 交付；無可靠獨占窗口時 apply 結構化拒絕。若要啟用寫回，另提獨占／備份／journal 計畫。Windows required 證據為版本綁定 admin_manual |
| D5 migration | additive SQLite 計畫已核准；**正式資料庫套用未授權**。001–005 已在暫存 DB 驗證；006 為未完成批次。open／import 不自動升級。新依賴、部署／CI、commit／push 依規則另確認 |

Docker 搬移的可審查文件：[搬移方案](docker-storage-move-plan.md)。最後確認資料為 `C:\Users\y4lin\AppData\Local\Docker\wsl\disk\docker_data.vhdx`（約 27.4 GiB），另有 `main\ext4.vhdx`。提議根目錄 `D:\DockerDesktop\wsl`，離線備份 `D:\DockerDesktop\backups\before-move-20261030`，尚未建立／搬移。最後 C 可用約 40 GiB、D 約 880 GiB，執行前重查。官方 Disk image location UI 需管理員操作，目前工具沒有 native Desktop 控制，不私改未查證 settings key。

曾處理 stale runtime socket：確認無 Docker 程序後，僅保留改名 `C:\Users\y4lin\AppData\Local\Docker\run.collaboration-backup-20261030`，啟動成功，沒有刪 image／volume。三個既有容器屬其他 `mongo_job_operation_board` 專案，先前 frontend `cd09560bb7e8`、backend `93338d06c871`、Mongo `1869ae61c192`、均 unless-stopped。這些是歷史短 ID，不能據以操作；須重新盤點精確 ID／狀態。paused 下空白列表不代表容器消失。

未拉取的官方 Linux/amd64 映像：

- `python@sha256:70bda1e795ded2a684ef9815955e05829b14ee5bb5c3eb2a940648e88f10fce9`
- `node@sha256:9f0dde62d10e7ff7a310ecaebd66225f6b46c17e6cb778449a77b05d3541bea2`

最後安裝版本：Python 3.13.14、deepagents 0.7.10、langgraph 1.2.11、checkpoint 4.3.0、langchain 1.3.18、coverage 7.17.0、uvicorn 0.54.0、Node 22.23.2、Docker Desktop 4.41.2／Engine 28.1.1，engine 曾回報 Linux/amd64、16,634,740,736 bytes RAM／32 CPUs。以上不是本次重新執行環境驗收；必要時唯讀確認。

## 5. 已實作／已驗證的產品範圍

所有模組在 `pi_deepagent/collaboration/`；相關 Python tests 在 `tests/collaboration/`。表內「已驗證」指各批當時 tree，未推論到本次 WIP tree。

| 範圍 | 實作及證據 | 尚未具備的能力 |
|---|---|---|
| 合約與儲存 | contracts／state；immutable 定義／快照、strict fields、生命周期、revision；storage＋001–005，真 SQLite transaction／CAS／scope／ledger／event／checksum、備份 migration／舊 DB 相容 | 完整外部 journal、所有領域物件及執行結算未完成 |
| 草稿設定 | access／service，真 AuthService admin／operation／resource、定義版本、2–4 成員／一 Orchestrator、目標／職責／預授權、秘密遮蔽 | 真團隊啟動及 UI 未實作 |
| Run／budget | runs／budgets，獨立 run／attempt、活動 fence、queued start-stop race、累積 used/reserved/wall、明確 continue、舊 attempt 保留、unknown 阻擋 | internal record-only；沒有停止實際程序、legacy 反向 fence、硬 wall supervisor 或完整 drain |
| 直接訊息 | mailbox，固定收件人、peer／broadcast、sequence、原子 claim、MemberTurn、accepted/delivered/processed、unknown 不 replay | 沒有 live graph scheduler／真模型 peer 修復 |
| 問答 | questions，服務綁原 member／WorkAttempt、expiry、admin answer／decline、原子 outbox、停止／重啟 stale 防守 | 只有一般 user_question；非程式／manual、真 graph 待答恢復未完成 |
| 政策核准 | policies＋005，精確 budget_extension，只延長 TeamRun model_calls／messages／wall_seconds 總額，immutable 版本、CAS、deny／expired／superseded 保存 | 其他四類 approval、repair-root extension、模型提案／真正撤權未完成 |
| 框架 | checkpoint／middleware，公開 SQLite saver、pending writes／versions／history、原生摘要／offload／回查、取消、per-graph 禁委派／私有路徑 | 真 graph 使用可控模型，不能充當 provider／GPU／容器模型驗收；不跨重啟重播工具 |
| preflight | 唯讀版本／engine／固定 image，缺 image fail closed | 不拉取／建置／啟動；兩個 Linux gate 未完成 |
| 真 HTTP | web_adapter＋既有 WebApp／CLI opt-in，GET/POST `/api/teams`、GET/PATCH `/api/teams/{id}`；14 項真 Uvicorn／h11／AuthService／SQLite tests，實際三成員 probe | 只有 draft 設定；沒有其他團隊路由／run graph／UI；不能把 metadata-only API 當成真開始或停止 |

HTTP 已驗證四路由逐一匿名／nonadmin／有效 admin／operation／foreign workspace／logout／expired session、Host／Origin／JSON／CSRF／128 KiB、嚴格欄位、cursor、CAS、replay、重啟、秘密及 audit。PATCH 提交完整設定＋expected_revision＋request_id，避免 sparse merge 不穩定 replay。預設不啟用，匿名仍 401、有效 admin 503；`--collaboration` 要求已有精確 schema／checksum，缺少就拒絕，不自動 migration。startup／shutdown 沿用 legacy lock／lifecycle，無模型呼叫。

policy 的重要邊界：新派工只接受 current policy grant；只有可信任 coordinator 對「既有精確 reservation settlement／turn finish」可接受 historical policy，仍須 active grant、目前 attempt、可查歷史。這不是通用授權，不能釋放 unknown、改目標／能力、重置 used/reserved 或自動續跑。後續整合不得放寬。

## 6. 正在進行但未驗證完成的 Work 批次

最後一次大 patch 已確實寫入；本次唯讀 AST parse 成功，檔案未截斷。**AST 成功只證明 Python 語法可解析，沒有 import、SQL 完整驗證或 runtime 測試。** 沒有新增 `test_work.py`，沒有此批 coverage／mutation／全回歸證據。

| 檔案 | 已寫入內容與狀態 |
|---|---|
| `migrations/006_work_planning.sql` | plan gate、repair_roots／work_roots、DAG dependencies、server-issued planning contexts、origins；v5 backfill；首 WorkAttempt 封閉 initial plan、root identity／used 不倒退、mapping／origins／goal immutability、保留未來受控 purge 條件。中途曾跑 storage 子集，之後又修改，不算目前版本通過 |
| `runs.py` | prepare 新 Run 時建立 initial plan gate；其餘 work lifecycle 整合尚未做。當前 SHA-256 `4a2c7b90895df787e0ca462438cbe54ccd32ade3abda7a5b26f54315bb9d5eb1` |
| `work.py` | PlanningContext／WorkRepository；Orchestrator 綁定、dispatch／wall／unknown guard；初始／child context factory、strict create、服務決定 roots／required、DAG Kahn 檢查、set_dependencies CAS、首次 queued WorkAttempt／inputs snapshot。當前 SHA-256 `96244ecc2876cd9da55e075713e10b9e43d70b2f62c4c1741d04c3ba42cd51f0` |

設計意圖：模型不傳 root／required／parent／actor 身分；初始規劃在第一個 WorkAttempt 後封閉，不能新建 root 洗掉額度。child context 由服務根據可信任 source work 綁定全部 roots／origins，多來源取 union；一般分解尚不是 failure repair generation。prepare_initial_attempt 對有任何舊 attempt 的 work 拒絕，必須走未來 repair／continue protocol，不能換成員重置。

**此批還沒有** repair reservation／generation／failure 指紋／無進展退出、多 root 額度原子扣帳、scheduler、實際執行或新的 HTTP。root counter table 存在不代表 AC-22 完成。服務 factory 只能由可信任 coordinator 使用，不可把可任意指定 source 的 factory 直接暴露為 model tool；M3 須從持久 failure／candidate 推導修復 scope。

續作須先 review 而不是直接堆功能：

1. `prepare_initial_attempt` 對 Work UPDATE 的 CAS rowcount 尚未明確檢查；檢查交易／trigger 故障下是否符合合約。
2. Work DTO 的讀取遮蔽未補齊「寫入之後才註冊的秘密」案例。
3. `stop`／`finish_stopping`／`mark_interrupted`／`continue_run` 尚未接新 work lifecycle；不可只因 budget／turn 排空就宣稱 work／外部 execution 排空。任何調整須保留舊 attempt 終態、unknown／fence，未接容器不可假稱已停止。
4. 檢查 initial context revoke 後 ledger replay 的授權先後、current grant／policy／attempt、child scope 失效與跨 run，以及 006 backfill／SQL trigger 和原測試 fixtures 相容性。
5. `RunFixture.activate` 取 UUID 排序第一個成員，不保證是 Orchestrator；新測試須從 snapshot 明確挑 `orchestrator is True` 再 bind，不能靠 fixture 碰巧通過。

## 7. 已完成驗證與保留的證據

私有 evidence 根目錄：

`C:/Users/y4lin/.codex/visualizations/2026/10/29/01a18853-f3dc-7770-aac1-9c97284f6be4/`

最新完整驗證來源為 `collaboration-m1-team-http-index.json`，不是本次 WIP tree。HTTP review 保存命令／版本／coverage／限制；各歷史 index 分別對應各自 source hashes。

| HTTP 批次執行 | 實際結果與證據 |
|---|---|
| 最終完整 Windows Python | **344 passed，115.524 秒，0 fail／skip**；`m1-team-http-final-full-python.log` |
| 既有 Node UI | **24 passed**；`m1-team-http-node.log`，不是真 browser／mobile 驗收 |
| HTTP＋既有 WebApp＋draft service coverage | **40 passed，28.989 秒**；`m1-team-http-covered-tests.log`、`m1-team-http-coverage.json` |
| HTTP 順序打散 | **14 passed，seed 1041**；`m1-team-http-shuffled.json`／`.log` |
| 真三成員 probe | 10 筆 HTTP 操作；`m1-team-http-roundtrip-3members/`，server 已停止，關閉後 DB／audit／record 保留 |
| 變異抽查 | HTTP **8/8 killed**：`m1-team-http-mutations-final/`；foundation **9/9 killed**：`m1-team-http-foundation-mutations/` |

三成員證據 team ID `6ff8db25-840e-4ea9-af80-3023f554f1d0`、revision 1。其 SQLite 是 fixture 證據，不是正式 DB，沒有 executed TeamRun／candidate／plan／environment hash，不能拿來宣稱交付。真 transport 測試的 runner sentinel 只保證設定操作不叫模型，不是真模型證據。

HTTP coverage 行／分支：adapter 55/60、20/24；WebApp 264/292、113/134；draft service 106/106、24/24。CLI subprocess 有功能測試，parent coverage 未收子程序，不能宣稱 CLI 分支覆蓋。依 unit-test-trust-review 對這個範圍中等信任，尚有 defensive branches／其他路由／整體整合缺口。

HTTP 初輪 audit 缺 create canonical ID，已修復並有 mutant 捕捉；初次 mutation baseline 曾因 CLI 子程序 UTF-8/cp950 解碼失敗，原失敗 `m1-team-http-mutations/baseline.log` 保留且不計分，修正後最終 baseline／8 mutants／全 suite 已重跑。新 Python 測試要再次作 trust review：真持久化與反例、必要 concurrency、branch coverage、抽樣 mutation、順序／重跑；不能以 import/setup 失敗、空 suite 或 skip 算 killed。

舊批次只需保留索引，無須每次載入數百行日誌：`collaboration-baseline-index.json`、`collaboration-m0-index.json`、`collaboration-m1-index.json`、`collaboration-m1-runtime-index.json`、`collaboration-m1-questions-index.json`、`collaboration-m1-policies-index.json`。policy 的 79 項 shuffled **未執行**，不可因 HTTP shuffled 已跑而改填通過。

## 8. M0–M5 剩餘範圍與下一批順序

| 階段 | 全部仍需完成的主要範圍 |
|---|---|
| M0 | 兩個真 Linux 環境同名檔互不影響；全部 native／pi file tools 與命令同副本；停止／背景程序計量、network／CPU／RAM／PID／disk／output limits、乾淨候選與可重建環境。Docker 及 image 條件未完成 |
| M1 | 本批 WorkItem／WorkAttempt／DAG 完整驗證；其餘 typed approvals、repair-root extension、criteria／responsibility／plan 版本、非程式／manual、完整 HTTP 授權、legacy 反向頂層 fence |
| M2 | 真 Git dirty＋選取 untracked 快照、秘密／特殊檔／跨平台路徑、獨立 volume／metadata、container backend/execution、leases／journal／資源限制／停止撤權，無 host fallback |
| M3 | member 獨立 graph/thread、FIFO／model 與 execution 名額、native context 計量、真 typed answer 恢復、supervisor／failure／repair-root／generation／多 root 累積額度、無進展及互等退出、stop/revoke/drain、restart 只對帳、完成 CAS |
| M4 | immutable changes／sources／lineage／claim、真三方 Git／同檔衝突、多來源未發布修復雙基準、publication CAS、required gates／protected assets review／collection、新 plan 全重驗、service actor 容器、Windows manual／附件、patch／受控 apply 拒絕、cleanup journal |
| M5 | 真 UI 重整／待答／錯誤／登出 late response、375/768/desktop／keyboard／mobile、31 AC 所有反例／平台、既有單 Agent／CLI／A2A 回歸、使用／設定／啟停／恢復／資料保留及回復文件 |

優先下一步：

1. `git status`、核對本索引及原 spec hash、讀本批 source；保留三個 WIP 變更。先窄測 storage／run 相容，再補 Work 測試，修正上節未決問題。
2. 真 SQLite 測試初始 create/replay／嚴格欄位／遮蔽、Orchestrator／偽 context／grant／policy／attempt、DAG 自環／間接循環／跨 run／依賴未完成、競態 CAS、首次 attempt immutable snapshot／重複／舊 attempt 拒絕、初始封閉跨 continue、child/grandchild/multi-root lineage、計數不可倒退、stop/wall/unknown、transaction fault rollback、真 v5→v6 備份 backfill。不要拿手動 fixture 完成狀態當真 graph 成功。
3. 按狀態合約接 work stop／interrupt／explicit continue，保留舊終態及 unknown，不能提供未接 execution 的對外假 stop；完成此批 trust review、coverage／有意義 mutation、回歸、review 及新 index 後再依賴它。
4. M0 依賴受阻期間繼續已核准的獨立 M1 工作；**不要在真容器假設未驗證時進入依賴它的 M2/M3 live execution**。需要 Docker 外部條件時再請實際執行／精確映像授權；GPU 維持 pending。

必要真模型閉環仍待執行：真正缺陷使首 candidate required test 失敗 → 可信服務失敗證據 → Orchestrator 自主診斷派工 → 成員直接修復 → 新 candidate 全 required gates 通過並交付。不得用 predetermined model、mock Docker／graph、模型自報、刪測試、跳 gate、使用者轉貼／逐次派工替代。

AC 每項紀錄必含 source/test、revision／candidate／plan 等適用 hash、平台環境、命令操作、預期／實際、evidence 路徑及 pass/fail/blocked。矩陣仍全部未整項通過；部分分支測過不能提升整項。最終交付摘要、主要檔案、31 項矩陣／索引、可重現設定啟動驗收、migration／備份回復、已知限制及尚未驗證條件。

## 9. 續作工具與 context 精簡規則

使用 `.venv/Scripts/python.exe`，不安裝依賴。相關重現命令（在 repo 根目錄執行，輸出另存新檔，不覆寫舊證據）：

```powershell
.venv/Scripts/python.exe -m unittest tests.collaboration.test_storage tests.collaboration.test_runs -q
.venv/Scripts/python.exe -m unittest tests.collaboration.test_team_http -q
.venv/Scripts/python.exe -m unittest discover -s tests/collaboration -q
.venv/Scripts/python.exe -m unittest discover -s tests -q
node --test tests/ui-state.test.mjs tests/ui-app.test.mjs
.venv/Scripts/python.exe -m scripts.collaboration.team_http_probe --output <new-empty-directory>
.venv/Scripts/python.exe scripts/collaboration/team_http_mutation_probe.py --output <another-new-empty-directory>
```

受限沙箱的 asyncio/socket 曾卡住；真 graph／HTTP／完整 suite 需要 loopback 時，以 `require_escalated` 說明本機測試，仍不啟動 GPU／Docker。同步 SQLite 子集可在沙箱。子程序 UTF-8 輸出與解碼須明確一致。PowerShell 用 `rg --glob`，不假設 shell 展開 brace/glob；Windows 危險操作先核對絕對目標，不跨 shell 組字串刪／搬。normal tracked `git diff --check` 保留 CRLF 行為，不能對整個 CRLF 檔用禁用 autocrlf 的 NUL diff 誤判。網路查證技術能力只用官方／primary sources。

沒有子 Agent；本任務是產品多 Agent，不代表允許 Codex spawn。須遵守當時委派規則。已使用 explore-codebase／unit-test-trust-review；無 `.codegraph`。修改 Python tests 後必須使用後者，不能把舊批 review 當新批完成。

本次沒有待 poll 的新長時間工作，沒有盤點／停止使用者其他程序。恢復時檢查必要實際狀態，不重跑未知副作用。工具 goal 狀態以重新查詢為準，不能擅自將 checkpoint 設成 paused／complete；只有使用者明確要求暫停才設 paused。

本次可用工具沒有直接刪除已載入聊天歷史或強制 context compaction 的 API。整理成果是持久檔案、hash 索引與精簡續作入口；沒有宣稱歷史已被物理清空。下一份工作摘要只需保留本檔路徑、完整要求檔、原 spec、未完成範圍、WIP 三檔、授權邊界、最新有效證據與立即下一步；其餘按需查回，原始資料不刪。
