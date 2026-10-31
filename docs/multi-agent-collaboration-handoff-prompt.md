# 多 Agent 協作 v0.3：完整交接 prompt

交接日期：2026-10-31（Asia/Taipei）。可將本文件全文交給下一位工程師／AI 工程代理。本文是接續指令及現況索引，不是完成宣告，不取代原始規格。附錄保留使用者原始任務全文。

## 1. 你的任務與完成標準

請接續 `C:\Users\y4lin\gemini_repo\pi_deepagent` 的多 Agent 協作模式實作，完整達成 `docs/multi-agent-collaboration-spec.md` **v0.3**、原始使用者任務及下列最新指示。不得重新定義為較小目標，不得停在架構、UI 原型、mock、部分功能或測試全綠。最終必須 F1–F11 完整實作、AC-01–31 全部具足夠可重現通過證據，必要 Windows／Linux／真框架／真 Git／真 SQLite／真容器／真模型驗證及既有功能回歸全部完成。

原聊天的 goal 已依使用者要求 **paused**；本次只產出交接，沒有恢復產品實作。當使用者將本 prompt 交給你並要求接續時，依該次指令續作已核准計畫，不必重新要求整體計畫核准。若仍只是審閱／交接，不自行恢復工作。不要因完成一批就把整體目標標 complete；有工具時遵守當時 goal 狀態及完成／受阻審核規則。

## 2. 最新使用者指示：覆蓋舊 Docker 搬移假設

使用者最新原文：

> 請產出一份完整的交接 prompt 方便另一個工程師可以完整接續你的工作，並達成相同的最終目標
> 另外 Docker 的搬移因為本機電腦我個人操作移除了 Docker Desktop，因此遷移將改成重新安裝到新的目標位置
> GPU 的使用往後安排，仍然先做其他非依賴 GPU 之工作，直到必須的時候像我請求授權

解讀及執行界線：

- **Docker Desktop 已由使用者自行移除**，這是使用者提供的最新外部狀態，本次未探測卸載殘留。後續改成重新安裝至 D 磁碟目標，不再執行舊 Desktop 的「搬移」流程，也不再等待舊搬移窗口問題的答案。舊 pending 搬移詢問已被新方向取代，即使晚收到舊答案，也不能據以執行過時流程。
- 原定 Linux 資料目標是 `D:\DockerDesktop\wsl`。重新安裝時須分開規劃應用程式位置與實際 Linux image／container／volume 資料位置；例如程式目錄可另提 `D:\DockerDesktop\app`，**這是提案，不是已核准安裝參數或已存在路徑**。不能把 exe 裝在 D 或把 docker-save tar 放在 D，就宣稱 Docker VM 資料也在 D；不承諾對 C 零占用。
- 先唯讀盤點現在的 installer／CLI／WSL／Windows features／虛擬化／磁碟／舊資料殘留，再依實際準備使用的 Docker 官方版本文件，提出可審查的安裝位置、資料位置、權限、可能重啟、既有資料處置及回復方案。確認該 installer 真正支援的參數／入口，不能套用舊版或憑記憶手改 settings key。
- 使用者已同意「改走重新安裝」方向；**本輪只要求交接，沒有執行安裝**。後續依 AGENTS.md 及該次具體授權處理安裝、WSL／Windows 設定、重啟及資料匯入；不要重問已同意方向，但對尚未具體審查／授權的外部副作用一次清楚確認。沒有刪除殘留資料、重設／unregister WSL、prune、強制停機或重建其他專案的授權。
- 卸載不代表舊 VHDX、images、volumes、容器資料已不存在，也不代表必須匯回。保留殘留，先盤點，再讓管理員決定保留／備份／匯入；未核對不能刪除。不要用歷史 container IDs／restart policy 操作現在的系統。
- 兩個映像尚未拉取，精確 pull／build 授權仍未取得；重新安裝方向不自動包含它們。GPU、正式 repository DB migration、新依賴、部署／CI、commit／push 也沒有因此取得授權。
- **先完成所有可獨立推進的非 GPU 工作**。不要啟動 `scripts/start-local-model.ps1`、GPU 模型或對使用者私人 GPU 模型送推理；等真模型驗證成為必要依賴，且 CPU／容器／fixture／流程準備已具體可審查時，再一次說明資源、時段、命令、日誌及預算並請求 GPU 授權。不要反覆詢問 GPU，也不能取消最終真模型閉環。改用付費／遠端模型或下載另一模型不是自動授權的替代方案。

歷史 Docker 調查僅供找線索：卸載前曾為 Desktop 4.41.2.191736／Engine 28.1.1，最後 status 查詢失敗、有 UI 程序但未列 backend；**不是新安裝的版本要求或目前健康狀態**。歷史資料磁碟 `C:\Users\y4lin\AppData\Local\Docker\wsl\disk\docker_data.vhdx` 約 27.4 GiB，另有 `main\ext4.vhdx`；是否殘留必須重查。歷史 runtime 備份 `...\Docker\run.collaboration-backup-20261030` 也不能自動刪除。舊三個容器屬其他 `mongo_job_operation_board` 專案。C／D 空間數字全是舊觀察，重新盤點後才規劃。

尚未拉取的候選官方 Linux/amd64 refs（舊 registry 解析結果，不是本機可用證明）：

- `python@sha256:70bda1e795ded2a684ef9815955e05829b14ee5bb5c3eb2a940648e88f10fce9`
- `node@sha256:9f0dde62d10e7ff7a310ecaebd66225f6b46c17e6cb778449a77b05d3541bea2`

取得實際授權後仍核對 digest／platform／工具版本／實際 D 資料路徑，不改浮動 tag，不偷偷替換映像。

## 3. 開始前完整閱讀與工作規則

先 `git status --short`、核對檔案現況，再完整閱讀：

1. `AGENTS.md`、`docs/security-requirements.md`。
2. `docs/multi-agent-collaboration-spec.md` v0.3 全文及其引用文件：`ui-implementation-spec.md`、`web-workspace.md`、`core-contracts.md`、`a2a.md`、`local-llm.md`、`tool-output-review.md`。
3. `docs/multi-agent-collaboration-user-requirements.md`：原始任務及所有後續授權紀錄。附錄原任務不能取代後續最新指示。
4. `docs/multi-agent-collaboration-plan.md`：已核准 M0–M5、F1–F11、D1–D5、風險、替代方案與驗證。`docs/multi-agent-collaboration-acceptance.md`：全部 AC 映射。
5. 本交接，最近已完整索引的 `docs/multi-agent-collaboration-m1-criteria-review.md`，以及下述 repair-limit 尚未收尾批次的實際程式／測試／日誌。

兩個 checkpoint `multi-agent-collaboration-checkpoint.md`（policy-v5）及 `multi-agent-collaboration-checkpoint-20261031-work-wip.md` 都是**歷史 tree**，勿用內文當最新狀態。舊 review／index 同理，保留不覆寫。

遵守 Plan First：已核准範圍例行工作持續做，不逐步重問；若出現會影響架構／資料／安全／功能／依賴／修改範圍的矛盾或擴大，先提出具體差異、證據、方法與風險，取得必要同意後才做相依實作。不得自行挑容易解讀。先完成可審查結果才請最後操作授權。

先查已安裝框架的設定／公開 backend、middleware、checkpointer，再考慮最小相容層；不得因預設或 provider error 格式就替換核心。保留原生摘要／歷史／offload／工具配對及一次執行／取消；診斷日誌不等於修復。

Python 測試新增／修改後，使用 `unit-test-trust-review`（當時位置 `C:/Users/y4lin/.codex/skills/unit-test-trust-review/SKILL.md`），實際執行、分支覆蓋、有意義變異、反例及順序驗證。`explore-codebase` 也已使用；當時無 `.codegraph`，不為此初始化索引。請依接手時實際 skill catalog 讀取，不假設路徑永遠不變。

產品多 Agent 不等於 Codex 可自行 spawn sub-agents；此前沒有子 Agent／委派工作，遵守接手時適用的委派規則。

## 4. 工作樹：不能只交 Git commit

- Repo：`C:\Users\y4lin\gemini_repo\pi_deepagent`；PowerShell；branch `master`。
- HEAD：`6bd537a9fbf17c790de356cbe006b97ddf1b55fb`，本任務沒有 commit／push／reset／clean／改 index。
- 原始使用者唯一 untracked 檔是 spec，未改；SHA-256：`28cc0b0762499ab33209dde4ff2916a24013264de16c0cdbd3d7d59d1b59e4c1`。
- tracked 修改三檔：`docs/web-workspace.md`、`pi_deepagent/web/app.py`、`pi_deepagent/web/__main__.py`，來自草稿 HTTP 整合。
- `pi_deepagent/collaboration/`、`tests/collaboration/`、`scripts/collaboration/`、本任務計畫／矩陣／review／checkpoint 等多數都仍 **untracked**。`git diff` 不包含這些；`git diff` 很小不代表只有少量實作。
- 保留所有未提交內容，不 reset／clean／覆寫，也不為整理 context 刪檔。若換機／worktree，必須移交 tracked diff、全部相關 untracked、必要 evidence，逐檔核對 manifest；只 checkout HEAD 會遺失幾乎全部新功能。不要盲目打包 .env、admin.json、模型金鑰、真使用者 DB、home、舊 Docker VHDX 或無關資料。
- 最新產品／測試／probe source manifest 為 evidence 下 `m1-repair-limits-source-manifest.json`，**56 檔**；本次交接唯讀比對全部符合。文件整理會改 docs，不改該批產品來源。

## 5. 現況：已實作與已驗證的範圍

整體仍是 **M0 部分技術驗證＋M1 部分領域／後端**；不能宣稱完整多 Agent 模式已可用。**整項 AC 通過 0/31**。

| 位置 | 已有能力 | 不可誤稱完成的部分 |
|---|---|---|
| `collaboration/contracts.py`、`state.py` | immutable definitions／snapshots、嚴格欄位／路徑、各 lifecycle、revision／settlement 合約 | 合約不等於 service 已有完整執行／完成屏障 |
| `storage.py`、SQL 001–009 | 真 SQLite transaction、CAS／scope／ledger／events、明確 migration／checksum／backup | 正式 DB 未套用；沒有完整外部 journal |
| `access.py`、`service.py` | 真 admin session／operation／workspace、草稿 2–4 人／一 Orchestrator、定義版本、秘密遮蔽 | 不是 team live runner |
| `runs.py`、`budgets.py` | Run/Attempt、持久活動 fence、累計 model/message/wall、unknown、stop／continue metadata、歷史保留 | legacy 反向 fence、真 execution 排空、完整對帳及新 WorkAttempt continue 重建未完成 |
| `mailbox.py` | direct peer／broadcast、sequence、batch claim、MemberTurn、accepted/delivered/processed、unknown 不 replay | 沒有 live graph scheduler 或真模型成員修復 |
| `questions.py` | typed 一般 user question、原 member／WorkAttempt answer、期限／stale／logout、原子 outbox | 非程式驗收／manual validation、真 graph 待答恢復、拒絕後 planner 退出未完成 |
| `policies.py`、`criteria.py` | admin budget_extension、capability ceiling、responsibility_exception、criteria_change；immutable history／CAS／精確 scope／秘密 | validation_plan_change 仍無 validator；model 提案／workflow 等待恢復、實際 capability/revoke 未完成 |
| `work.py`、SQL 006／008 | WorkItem／DAG、server planning context、首次 queued WorkAttempt、根／多來源追溯、目標版本 refs | 不是實際派工；repair／continue attempts、failure／generation 未完成 |
| `repair_limits.py`、SQL 009 | 精確 root 總上限延長、immutable limits history／cache CAS、保留 used/reserved、root reservation 停止／續接屏障 | 沒有實際 repair quota reserve/settle、generation、程序對帳或修復執行 |
| `checkpoint.py`、`middleware.py` | 公開 SQLite saver、pending writes／versions／history、原生摘要／offload／工具配對／取消、per-graph 禁委派 | 真 graph＋可控模型；不是真 provider／GPU 或容器 backend 驗收 |
| `preflight.py` | 唯讀版本／engine／immutable image 檢查，缺 image fail closed | 不自動安裝／啟動／拉取；舊 Docker 外部狀態已過期 |
| `web_adapter.py`＋既有 WebApp／CLI | 四個 opt-in draft routes，真 HTTP／AuthService／SQLite／CSRF／Origin／CAS／replay／audit | 沒有新團隊 start/stop/continue、Work／approval／manual／artifact 全路由或團隊 UI |

四路由是 GET/POST `/api/teams`、GET/PATCH `/api/teams/{id}`。PATCH 提交完整設定＋expected_revision＋request_id；不是 sparse read/merge。`--collaboration` 只開已有正確 schema／checksum，預設不啟用、不自動 migration；缺 schema 拒絕。不要用 fixture init 取代正式 DB。

版本（當時安裝，不保證接手後未變）：Python 3.13.14、deepagents 0.7.10、langgraph 1.2.11、checkpoint 4.3.0、langchain 1.3.18、coverage 7.17.0、uvicorn 0.54.0、SQLite 3.53.1、Node 22.23.2。Docker 已卸載，不把舊版本當現在安裝。

## 6. 最新 repair-limit 批次：先收尾這一批

尚未完整交付的變更：

- 新 `repair_limits.py`、`009_repair_limit_versions.sql`、`test_repair_limits.py`、`repair_limit_mutation_probe.py`。
- 修改 `policies.py`，budget_extension 新增精確 `repair_root_id`；old/new 只能是 repair_attempts，不能混 model/message/wall，不能把 child ID 當新 root，關聯 work 必須真的繼承該 root。
- 核准只提高該 root total，**不改其他 roots、不改 PolicySpec 的新 root 預設、不歸零 used/reserved/wall，不 resume 或 reconcile unknown**。新 policy version 仍使舊 dispatch grant 失效。
- 修改 `runs.py`，root reserved>0 時禁止 finish_stopping／continue，保留 activity fence；不是已停止真容器的證明。
- `list_repair_limits` 是有 token／operation／run/root scope 的 domain read；尚無 HTTP route。
- 20 項新測試使用非零 seeded counters、真 SQLite／AuthService／多連線／v8 DB／backup／DDL fault；不是實際模型或修復計量。

已執行的最新證據：

| 執行 | 結果 | evidence 相對檔名 |
|---|---|---|
| 完整 Windows Python／branch coverage | **437 passed，242.152 秒，0 fail/skip** | `m1-repair-limits-full-python.log` |
| 相關 domain 子集 | **84 passed，46.215 秒** | `m1-repair-limits-covered-tests.log` |
| Node 回歸 | **24 passed** | `m1-repair-limits-node.log` |
| 新 suite shuffle | **20 passed，9.149 秒，seed 1046** | `m1-repair-limits-shuffled.json`／`.log` |
| root mutation | **10/10 killed**，完整 20 項／case | `m1-repair-limits-mutations/results.json` |
| typed mutation 重跑 | **10/10 killed**，完整 23 項／case | `m1-repair-limits-typed-mutations/results.json` |
| 真 SQLite round-trip | **8 項 domain checks passed** | `m1-repair-limits-roundtrip/record.json`、`tasks.sqlite`、`m1-repair-limits-probe.py` |
| coverage JSON | 有原始資料 | `m1-repair-limits-coverage.json` |

行／分支：repair_limits **33/35、16/18**；policies **325/336、140/152**；runs **255/268、70/86**。缺口包括 helper 非法 shape、limit cache/history 不一致等防守及既有 Run defensive 分支，不能宣稱全執行安全。root_cas mutant 由後續 history/cache 一致性防線再次攔下，錯誤語意被測試抓出；不要把每個 kill 說成實際重複派工。

第一輪 19 項有一個子案例用已不增加的 total 測 child ID，先被 increase guard 拒絕；已改用合法增加值，另加「另一個真 Run 的既有 root」案例成為 20 項。失敗原檔 `m1-repair-limits-first-tests.log` 保留，不計成功。

**尚未完成**：`docs/multi-agent-collaboration-m1-repair-limits-review.md` 與 `collaboration-m1-repair-limits-index.json` 都尚不存在；plan／AC 矩陣的最近完整批次仍指向 criteria 的 417。不要假裝已收尾，也不要把本交接 manifest 當成該批正式審核。先完成 trust review 說明、source／evidence 綁定、矩陣及計畫狀態整理，再依賴新批次。

原執行工具 session ID 已失效，但以上日誌有完整終態與 results.json，source 56 檔吻合。沒有已知需繼續 poll 的本次測試；不要沿用舊 PID 或盲目殺 Python／Docker。重新測試只在來源改變、結果缺件或新問題需要時執行，使用新輸出位置，保留失敗。

## 7. Evidence 的位置與效力

私有根目錄：

`C:/Users/y4lin/.codex/visualizations/2026/10/29/01a18853-f3dc-7770-aac1-9c97284f6be4/`

最近完整總索引是 `collaboration-m1-criteria-index.json`（417 的 tree）。其前有 baseline、M0、M1 foundation、runtime、questions、policies、team-http、work、typed-approvals 等 `collaboration-*-index.json`；各自綁定各自 source/hash，不能套用到新碼。

437 的 raw evidence／source manifest 如上已存在，正式 root index 尚待建立。本交接的 `collaboration-handoff-20261031-index.json` 只凍結目前 source／文件／交接狀態與證據完整性，**不是補做 Root 正式審核或把任何 AC 升為 pass**。

fixtures 的 tasks.sqlite、grants、IDs 都只適用當時暫存 workspace，不包含正式使用者授權，不可作 production DB 或重啟執行；模型／GPU／candidate／plan／environment hash 不適用時明確 null，不虛構。

## 8. 不可破壞的設計不變量

1. Windows Python 控制、Linux 容器執行隔離；single admin／single repo／2–4 members／一 Orchestrator；team 與 legacy single-agent 共用唯一頂層活動屏障。沒有 host fallback、自動 push/deploy 或跨重啟 replay。
2. Model／repo／peer／tool output／tests 都是不可信資料。服務掌握身分、能力、預算、驗證、發布與完成；所有外部 read/control/stream/download/A2A 驗登入、操作、父子資源，預設拒絕。logout 撤互動存取，預授權背景工作依規格續行；revoke 是另一個會停止不合規環境的協定。
3. 狀態轉移、immutable 終態、版本、唯一鍵、events／errors 先於 UI；UI 不推定完成。AgentDefinition／MemberSnapshot／Run／RunAttempt／Work／WorkAttempt／MemberTurn／Execution／Workspace／Container 不混用。
4. 交易／CAS／ledger／budget reservation／event 原子化；同 ID 同 body 回原結果，異 body 拒絕；auth 在 replay 之前。未知操作先用同 operation 對帳，不換 ID 重跑。Docker/Git/FS 外部副作用有 intent/result journal；確認 drain 前不放 lease。
5. `RunRepository` 仍 record-only；不可直接 expose 它的 stop/continue 為真正控制。必須加外部 container/execution leases/journals 及完整 completion barrier。舊 terminal attempts 不改，continue 只為未完成必要工作建新 attempts，保留成果／用量／root。
6. PlanningContext factory 只供 trusted coordinator。第一 WorkAttempt 封 initial plan；child/grandchild/multi-source 繼承所有 root/origins，不造較寬新 root，也不把追溯邊當阻塞 DAG 邊。來源 root/failure/generation 必須服務持久綁定，不能讓 model 選 root 洗額度。
7. repair generation/fingerprint/無進展/累積 quota 不得因換成員、拆工作、重建 candidate/lineage 或 admin extension 清空；multi-root 原子逐一保留，任一不足不派工；unknown 不 release。僅有 root tables／extension 不代表這些已實作。
8. 未發布候選修復分 checkout_base 與 publication_base：C+A+B→F，從 F 修 R，但 publication_base 仍 C；後續與新共同版本合併要保留 A+B+repair、完整來源／lineage／claim，不重複納入來源。發布 exact candidate/plan/policy/base CAS。
9. validation／regeneration service actor 仍用容器、同隊 lease、budget、stop/revoke，不在主機執行候選。乾淨 candidate 環境，不沿用成員 venv/cache；required checks／collection／protected assets／plan version，不以 exit 0／model 自報代替。
10. 改 assertion/skip/conftest/plugin/package scripts/runner/config/lock/deps 等 protected 資產要 review、admin 決議、新 plan、全 required 重驗，不能回填舊 pass。Windows required 用綁版本 admin_manual evidence／受保護附件，不能把 Agent 聊天當證據。
11. Criteria immutable history／refs 是身份，尚非 actual gate；capability support default empty，fixture 支援集合不可直接接正式 runtime；責任 prefix 不是 OS 安全邊界。新操作驗 current policy，只有既有精確 reservation/turn 的可信任結算可用受限 historical-policy 路徑，不能變成通用舊授權。
12. 使用者原 repo dirty/untracked 不變，服務端 Git 操作禁 hooks/filters/external diff/任意 repo config 執行，私有 Git metadata 不可寫共享到成員。交付預設 patch，無獨占窗口 apply 拒絕，不自行寫回原目錄。

## 9. 恢復後的具體工作順序

| 階段 | 下一步與退出條件 |
|---|---|
| 立即 | 核對本 prompt、最新指示、git status、56-source manifest／437 evidence；收尾 repair-limit review/index、plan/AC matrix，保留舊原始證據 |
| D1/D2 新安裝 | 不沿用舊搬移；唯讀調查卸載殘留／平台，完成可審查的新安裝及 D 資料路徑／備份／恢復方案，再依具體授權操作；精確 image pulls 另處理 |
| M0 | 真框架公開 backend/middleware/saver 差異；兩個真 Linux 隔離副本同名檔、所有 native/pi file tools 與命令同副本、停止／背景計量、網路／CPU/RAM/PID/disk/output、乾淨候選重建。Gate 未證實前不進相依 live M2/M3 |
| M1 非 GPU | 下一功能為 validation_plan_change 的明確目標／immutable plan／required/collection/protected/env 合約；另補非程式 typed acceptance、model proposals／等待恢復、完整 HTTP 授權／history、legacy 反向活動 fence。保持小批次可驗證，不能只繼續堆無驗證抽象 |
| M2 | dirty tracked＋選定 untracked／secret/path snapshot、私有 Git/volume、container backend/executor、capability grants、leases/journal／limits／revoke/drain、無 host fallback |
| M3 | member graphs/threads、FIFO／model與exec名額、完整模型計量、真問答恢復、supervisor／failure/root/generation／無進展與互等退出、known/unknown 對帳、stop/revoke、explicit continue 與 completion CAS |
| M4 | sources/claims/lineage、真 merge conflict／雙基準修復、required plan/collection/protected review、新環境重建、actual gates、Windows manual/uploads、publication CAS、patch/delivery／安全清理 journal |
| M5 | 正式 UI／reload／待答／錯誤／logout late response、375/768/desktop／keyboard／44px／mobile、全部 route matrix、單 Agent／CLI／A2A 回歸、操作/保留/migration/backup/recovery 文件及逐條 spec audit |
| GPU 必要關卡 | 先把非 GPU 場景／容器／fixture／資料及命令備妥；真模型閉環成必要依賴才請使用者授權，批准前保持 pending |

至少一條真的閉環必須是：實際缺陷讓首 candidate required tests 失敗 → 可信失敗證據 → Orchestrator 自主診斷／不同策略派工 → 成員直接修復 → 新 candidate 全 required pass → 受控交付。不得人肉轉貼錯誤、逐次派工確認、predetermined model、mock Docker/graph、刪測試或跳 gate 替代。

## 10. 重現命令與 Windows 工具注意

從 repo 根目錄，以既有 `.venv`，輸出另存新位置，不裝新依賴：

```powershell
git status --short
.venv/Scripts/python.exe -m unittest tests.collaboration.test_repair_limits -q
.venv/Scripts/python.exe -m unittest discover -s tests -q
node --test tests/ui-state.test.mjs tests/ui-app.test.mjs
.venv/Scripts/python.exe scripts/collaboration/repair_limit_mutation_probe.py --output <new-empty-directory>
.venv/Scripts/python.exe <evidence-root>/m1-repair-limits-probe.py --output <another-new-empty-directory>
```

full/graph/HTTP tests 在受限 sandbox 的 asyncio/socket 曾卡住，須有 loopback 權限；必要時依可用執行環境以 require_escalated 說明本機測試，不能用沙箱卡住取代測試結果或啟動 GPU/Docker。純同步 SQLite 可在 sandbox。

coverage 要 branch；設定 COVERAGE_FILE 到 evidence，不把暫存 .coverage 混進工作樹。所有 JSON／source／subprocess encoding 明確 UTF-8，Windows 預設 cp950 曾造成 evidence 索引與 CLI test 解碼問題。PowerShell 用 rg/rg --files／--glob，不假設 brace/glob 展開；不要把 JSON.stringify 當 shell quoting；不跨 shell 拼刪除／搬移命令。危險 Windows 路徑先核對絕對目標／workspace/root／reparse。

normal `git diff --check` 驗 tracked CRLF；整個 CRLF 檔對 NUL 且禁用 autocrlf 的 diff 會誤判。不要為達數字新增 omit／pragma／skip 或刪失敗測試。mutation source 非唯一、import/setup/teardown 失敗、空／skipped suite、baseline 紅或未完成 marker 不算 killed；檢查 primary/secondary guard 與實際 trace。

## 11. 最終交付與誠實驗收

逐一對 F1–F11、AC-01–31 開啟實際 code/evidence：每項記錄 source/tests、revision＋worktree manifest、candidate/plan/policy/env 等適用 hash、平台／版本／命令／預期／實際／證據位置／pass-fail-blocked。完整反例、競態、unknown／重啟、logout、protected gate、Windows manual、UI及舊功能都符合才把整項 AC 提為 pass。局部 SQLite／HTTP／可控 graph pass 不等於整項。

最後重新完整讀 spec，不只看表格或測試綠燈。交付實作摘要／檔案、31 項矩陣與索引、可重現設定啟動驗收、migration/backup/rollback、使用/啟停/恢復/保留/限制文件。若仍阻礙，分清已實作／已驗證／尚未驗證、具體外部條件；不宣稱完成、不刪目標。

## 附錄：原始使用者任務全文

以下全文保留原始要求；環境／授權以本文件第 2 節及最新使用者訊息優先。

請依照 docs/multi-agent-collaboration-spec.md v0.3，完整實作此 repository 的多 Agent 協作工作模式，直到所有規格要求具備可重現的驗收證據。不要停在架構草稿、UI 原型、部分功能或只有 mock 通過的程度。

一、工作依據與不可變更的邊界

開始前完整閱讀：
- AGENTS.md
- docs/multi-agent-collaboration-spec.md
- docs/security-requirements.md
- spec 引用的既有 UI、Web 與框架整合文件。

以目前 repository、安裝版本與實際執行環境為準，先檢查 git status，保留既有未提交修改。若文件彼此矛盾，先提出具體衝突，不自行選擇較容易實作的解讀。

必須保留 spec 已確認的目標與範圍，包括：
- Windows 11／Python 控制面，Linux 容器執行隔離。
- 單一 admin、單一 repository、團隊 2–4 位成員。
- 一次只有一個頂層團隊或既有單 Agent 工作活動。
- UI 設定職責、個別目標、共同目標與預授權。
- 成員直接溝通、獨立工作副本、受控整合與驗證。
- 原授權及預算內自主完成診斷、修復、重驗與交付。
- 不提供主機命令 fallback、自動 push／部署或跨重啟自動重跑。
- 登出撤銷互動存取，已核准背景工作依規格繼續。

不得以縮小功能、弱化驗收、取消隔離、增加人工轉送步驟或更換核心框架，來取得表面上的完成。

二、先建立實作計畫與追蹤表

先調查現有程式，再提出具體計畫，包含：
- 對需求與現況的理解。
- M0–M5 各階段修改模組、依賴及交付物。
- F1–F11、AC-01–31 對應的實作位置、測試與證據。
- D1–D5 的實際狀態、啟用條件及需取得的授權。
- 主要風險、回復方式與驗證方法。

依 AGENTS.md 取得必要的計畫確認後執行。已核准範圍內的例行工作持續完成，不必逐步重問；若需要擴大架構、安全、依賴或修改範圍，先提出差異與理由再取得同意。

安裝依賴、啟動或修改 Docker／WSL、套用 migration、部署、commit、push 等操作，依專案規則確認授權。遇到環境阻礙時，繼續完成不依賴該環境的工作，但不得把尚未執行的驗收標成通過。

三、依 M0–M5 完整實作

按照 spec 的交付順序推進，每階段完成對應檢查後再進入依賴它的階段。

M0 優先驗證最容易導致返工的假設：
- 實際 Deep Agents 公開 backend、middleware、checkpointer 能力。
- 原生摘要、歷史回查、工具配對與取消行為。
- 兩個真實 Linux 隔離環境操作同名檔案互不影響。
- 所有檔案工具與命令指向同一工作副本。
- 容器停止、背景程序計量、網路隔離與資源限制。
- 乾淨候選驗證及可重建環境。

先使用已安裝框架的設定與公開擴充點，不因預設行為不適合就替換核心實作。若實測與 spec 假設不同，提供最小重現、實際結果與建議修正，不靜默繞過問題。

四、實作時特別注意

1. 由服務端掌握狀態與權限。
模型只能提出工作、訊息及修復建議，不能自行決定身分、能力、預算、驗證結果或發布成功。所有對外入口都要驗證登入、操作權限與父子資源歸屬。

2. 先完成領域合約，再接 UI。
先定義狀態轉移、不可變物件、版本檢查、唯一鍵、事件及錯誤語意，避免 UI、排程器與資料庫各自維護不同真相。UI 顯示服務端狀態，不自行推定完成。

3. 將競態當成正常情境。
對派工、停止、發布、訊息接收、預算保留與完成結算使用明確的交易及 CAS。外部 Docker／Git／檔案操作使用 intent/result journal。重複請求回原結果；未知結果先對帳，不能換 request ID 重跑。

4. 不讓驗證流程繞過隔離。
驗證及再生成使用服務身分，但仍進容器、取得同一全隊 lease、消耗預算並服從停止與撤權。不得因為是可信任服務發起，就在主機執行候選程式。

5. 正確處理未發布候選的修復。
區分 checkout base 與 publication base，保留全部來源變更及 lineage。修復後不能遺失其他成員成果，也不能重複納入來源。修復 root 與額度由服務持久綁定，不能透過換成員、拆工作或重建候選重置。

6. 驗證不能只看 exit code。
required checks、受保護驗證資產、收集條件與計畫版本都要落實。測試、runner、設定及驗證依賴變更依規格審查。驗證使用乾淨候選環境，不沿用 Agent 修改過的 venv 或快取。

7. 保留完整生命週期。
Agent 定義、成員快照、TeamRun、RunAttempt、WorkItem、WorkAttempt、MemberTurn、Execution、Workspace 與 Container 不可混為同一物件。舊 attempt 終態不可改寫；容器移除不能造成歷史與證據遺失。

8. 避免一次完成過大的修改。
採可驗證的小批次，優先建立一條真實端到端流程，再依 spec 補齊所有分支。每批次檢查實際行為與差異，不在尚未驗證的抽象上堆疊大量功能。這是實作順序，不能用來縮小最終範圍。

五、測試與驗收要求

為 AC-01–31 逐項建立驗收紀錄，包含：
- 對應程式與測試位置。
- revision、candidate／plan hash 等適用識別。
- 平台、環境、操作或命令。
- 預期與實際結果。
- 證據位置及通過／失敗／受阻狀態。

必須驗證：
- 真實 SQLite、Git、框架 graph 與 Linux 容器。
- 未登入、無權限、有效 admin、登出及失效 session。
- 並行、重複事件、過期版本、停止、撤權、重啟及未知結果。
- 同檔衝突、候選修復、來源保留與發布 CAS。
- 修復上限、無進展退出及不能重置的累積預算。
- required 驗證資產被改動、人工 Windows 證據與附件授權。
- UI 重整、待答、錯誤回報、鍵盤與手機操作。
- 既有單 Agent、CLI、A2A 功能回歸。

至少完成 spec 要求的真實模型閉環：首次候選因實際缺陷測試失敗，Orchestrator 自主診斷派工，成員修復，新候選通過 required gates 並交付；原授權內不需要使用者轉貼錯誤、重新派工或逐次確認。

不能以 mock Docker、mock graph、模型自報成功、刪除失敗測試或跳過 required gate 代替驗收。Python 測試新增或修改後，依 unit-test-trust-review 檢查可信度。

六、完成標準與交付

持續實作、檢查與修正，直到：
- F1–F11 全部實作。
- AC-01–31 全部有足夠且可重現的通過證據。
- 必要真實框架、容器、模型及 Windows／Linux 驗證完成。
- 既有功能回歸通過。
- 沒有未處理的阻擋缺陷或規格矛盾。
- 使用、設定、啟動、停止、恢復、資料保留及限制文件完整。

最後交付：
- 實作摘要與主要修改位置。
- AC-01–31 驗收矩陣及證據索引。
- 可重現的設定、啟動與驗收步驟。
- migration、備份與回復說明。
- 已知限制及任何尚未驗證項目。

完成前重新以 spec 逐項核對實際程式與證據，不只依賴測試全綠或先前進度紀錄。若仍有阻礙，明確區分「已實作」「已驗證」「尚未驗證」，說明所需的具體外部條件；不得宣稱已達可驗收標準。
