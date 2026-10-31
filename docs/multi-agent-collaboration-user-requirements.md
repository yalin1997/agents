# 使用者完整工作要求與授權紀錄

此文件保存本次工作對話中使用者的工作要求，供 context 精簡後續作。不是新的範圍核准，也不取代 AGENTS.md 或規格。原始任務全文如下；後續授權與限制列於文末。

## 原始任務全文

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

## 後續指示及授權邊界

以下依本次 session 對話順序記錄，不推定尚未取得的同意：

1. 使用者：「我同意你按照計畫續行」。實作計畫已核准；核准內容在 [實作計畫](multi-agent-collaboration-plan.md)。已授權範圍的例行工作不必逐步重問。
2. 對啟動 Docker／本機模型的詢問，使用者回答：「先把依賴 GPU 的功能 pending 我暫時有私人使用需求，除此之外的使用如 docker 操作核准」。因此不得啟動 GPU 或 `scripts/start-local-model.ps1`；GPU 需求暫緩不等於取消真模型最終驗收。
3. 曾提議拉取兩個官方 Linux/amd64 固定映像。自動審查拒絕先前拉取，要求確切映像授權；使用者未核准拉取，而是問：「先回報拉取映像你要存放在哪裡? C 磁碟空間較少，是否可放於 D 磁碟並明確標記存放路徑?」
4. 對移動 Docker 儲存根目錄的詢問，使用者回答：「同意，先準備可審查的搬移步驟」。目前只完成 [Docker 搬移方案](docker-storage-move-plan.md)；未取得實際停機／備份／搬移及拉取兩個映像的明確確認。不得把「先準備」當成可以執行搬移。
5. 兩個尚未拉取的確切映像是 `python@sha256:70bda1e795ded2a684ef9815955e05829b14ee5bb5c3eb2a940648e88f10fce9`、`node@sha256:9f0dde62d10e7ff7a310ecaebd66225f6b46c17e6cb778449a77b05d3541bea2`。原 registry manifest 已解析，不能因此宣稱映像已在本機或容器測試已通過。
6. 新依賴、正式 DB migration、部署、commit／push 仍依 AGENTS.md 取得授權。測試暫存 DB 的 migration 不等於正式套用。沒有自動 push／部署授權。
7. 先前人工要求：「先將此設為目前的 checkpoint 建立資料彙整並整理 context 中殘留已不需要的資訊，保留住所有user 要求的工作與目標細節以及達成繼續目標所需資訊」。該次結果保存在 [policy-v5 checkpoint](multi-agent-collaboration-checkpoint.md)。當時整理開始曾觀察 paused，期間收到續行事件，交付前為 active；沒有自行改 goal 狀態或標完成。
8. 本次再次要求以上 checkpoint／context 整理。最新入口為 [CP-20261031-work-wip](multi-agent-collaboration-checkpoint-20261031-work-wip.md)：保留完整目標、授權、HTTP 已驗證版本、其後未驗證 WorkItem／DAG／006 變更及續作條件。最新工具查詢 active；本次不繼續產品修改、不變更 Docker／GPU、不套正式 migration，不將 checkpoint 解讀為暫停或完成。既有聊天歷史無直接刪除／強制 compaction API，以持久精簡入口續作，原始證據不刪。
9. checkpoint 後收到 active goal 續行事件，繼續既有核准範圍。後續 [WorkItem／DAG 批次](multi-agent-collaboration-m1-work-review.md) 更新目前實作／證據，原 checkpoint 保留歷史狀態；沒有新增 Docker／GPU／正式 migration／commit／push 授權，也沒有縮小最終範圍。

後續增量見 [typed approvals 審核](multi-agent-collaboration-m1-typed-approvals-review.md)。2026-10-31 助理另提出 Docker 停機／備份／管理員操作設定頁搬移的待答詢問；**這是待答問題，不是使用者已核准的要求**。未收到明確答覆前不執行、不推定 GPU／映像／正式 migration／Git side effect 授權；原完整目標不變。

再後續的 [criteria_change 批次](multi-agent-collaboration-m1-criteria-review.md) 延續同一核准 M1 範圍；不增加任何外部操作授權。Docker 問題仍待答，GPU pending，原始 F1–F11／AC-01–31 與真模型閉環要求完整保留。

## 不可遺失的專案指示

### 2026-10-31 最新暫停及交接指示（覆蓋舊外部環境假設）

使用者先要求：「暫停先與我回報目前完成項目，進行中項目，下一個項目是什麼? 有沒有什麼困難或需要我協助的地方」。原 goal 已設為 **paused**，未標完成；後續只整理狀態與交接，不恢復產品工作。

使用者最新原文：

> 請產出一份完整的交接 prompt 方便另一個工程師可以完整接續你的工作，並達成相同的最終目標
> 另外 Docker 的搬移因為本機電腦我個人操作移除了 Docker Desktop，因此遷移將改成重新安裝到新的目標位置
> GPU 的使用往後安排，仍然先做其他非依賴 GPU 之工作，直到必須的時候像我請求授權

最新完整交接位於 [handoff prompt](multi-agent-collaboration-handoff-prompt.md)，保留原始任務全文、已實作／已驗證／未驗證、來源與證據、下一步及授權。最新 repair-limit 批次 raw evidence 為 437 Python／24 Node；正式該批 review／index 尚未建立，完整 AC 仍 0/31，不能用交接代替驗收。

Docker 卸載為使用者提供的最新狀態，本次未探測殘留。舊搬移方案與窗口待答問題已被重新安裝方向取代，勿等待或沿用；保留舊文件為歷史。原資料目標 `D:\DockerDesktop\wsl`，重新安裝須區分程式目錄與實際 Linux images／containers／volumes 位置；先盤點及提出版本相符的安裝／備份／回復方案，依具體授權執行。本次未安裝，沒有刪除殘留、prune／reset／unregister WSL、任意恢復其他專案資料、正式 migration 或 pull/build 的新增授權。

GPU 使用延後：先做所有能獨立前進的非 GPU 工作；必要關卡與資源／命令／時段／證據計畫備妥時才請求 GPU 授權。不要啟動本機模型、向私人 GPU 模型推理、私換付費／遠端模型或取消最終真模型閉環。整體 F1–F11／AC-01–31 目標與原計畫核准不變；本聊天維持 paused，接手者依明確續作指示繼續。

完整指示仍以 [AGENTS.md](../AGENTS.md)、[身分與權限](security-requirements.md) 及相關開發者／工具指示為準。關鍵包含：Plan First、保留未提交修改、先查框架內建設定及公開擴充點、服務端預設拒絕、admin 單一登入、先完成可審查內容才請最終授權、Python 測試使用 unit-test-trust-review、不將診斷日誌冒充修復。

本任務是在產品中實作多 Agent 模式，不自動等於允許 Codex 自行 spawn sub-agents；目前沒有 spawn 子 Agent，續作須遵守當時適用的委派規則。

使用者另提供的全域工作偏好亦保留：以明確限制內的成果為目標，先查相關 context 再按證據擴大調查；只在有實質範圍／行為／風險歧義時詢問，其他採合理判斷；指示矛盾須提出。採最小完整修改、遵循既有抽象／命名／慣例，不作無關重構或清理，生成檔透過來源流程更新。驗證依影響比例，行為改動有自然測試面時加 focused regression，先窄後廣，最後 review diff 並區分未驗證。破壞／難回復操作及新依賴、正式 migration、部署／CI、commit／push／外部副作用依授權確認。交付簡潔說明結果、驗證及實質剩餘限制。這些偏好受當時更高優先級及更具體指示約束。
