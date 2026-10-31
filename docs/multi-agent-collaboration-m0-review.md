# M0 第一批：框架／checkpoint 實作與測試可信度審核

日期：2026-10-30（Asia/Taipei）。範圍：`pi_deepagent/collaboration/checkpoint.py`、`middleware.py`、`preflight.py` 及 `tests/collaboration/`。狀態：**此批部分技術合約已實證；M0 整體尚未完成，AC 仍為 0/31**。

## 已實作與已驗證

- 真 SQLite 的 BaseCheckpointSaver adapter，具 thread 綁定、namespace、完整 checkpoint／channel versions／pending writes、原生寫入排序與去重語意、父鏈、歷史 filter／before／limit、同步／非同步介面。依公開 BaseCheckpointSaver 保留 delta ancestor 重建，沒有自行替代框架摘要或工具配對。
- 新資料庫僅在明確 `initialize=True` 且空 DB 時建立 schema；既有非相符 DB 拒絕初始化，不在 import／開啟舊 Web DB 時偷偷套 migration。checkpoint ID／channel version 相同內容可重送，不同結果拒絕；真多連線 SQLite 保留全部寫入。
- 真實 Deep Agents graph 的已完成工具結果可經 saver 重開讀回，下一正常 turn 不重跑原工具；取消保留待執行 node 與輸入，沒有偽造完成。本測試僅驗 saver 合約；正式服務重啟仍必須由 service 建新 attempt／thread，不能把 saver 可讀回當成已授權自動 resume。
- 公開 per-graph middleware 隱藏 `task` 與非核准 async 委派／host `pi_bash`，偽造 `task` 呼叫被拒絕；另一個普通單 Agent graph 仍有原 `task`，未修改全域 profile。
- CompositeBackend 的 `/.framework/` 私有路由與 `artifacts_root` 保存原生摘要及大型工具結果；正常 file write 不可覆寫該路由。包含相對／雙 slash／`..`／backslash 反例。真原生摘要產生，完整歷史及 tool result 可回讀，普通工作檔仍可寫；原生 callback 觀察到 summary 呼叫，可供後續預算入口計量。
- 使用既有 runtime adapter 的單次 probe 證明內部摘要沒有混入可見 final output，工具只執行一次。私有 backend 重建後仍可讀保存內容；這不是容器重建驗收。
- 唯讀 preflight 實際連接已就緒 Linux engine，保存版本／資源與 blocked reason；無 image 時保持 `eligible_for_sandbox_probe=false`，不安裝／啟動／拉取，不宣稱容器行為已驗證。子程序使用明確 argv，剔除模型 credentials 環境，輸出只選擇必要欄位。

## 本批發現與處置

最初 graph probe 誤用 `ReadResult.content`；實際安裝版公開結果為 `ReadResult.file_data['content']`，已依實際合約修正測試，未替換框架。delta 測試最初 fixture 的 channel_versions 沒有對應 delta channel，亦已修正成實際版本合約；Windows SQLite 測試用 `contextlib.closing` 確保關閉連線，避免把 transaction context 誤當 connection close。

Docker engine 啟動問題的日誌確認為既有 stale AF_UNIX `dockerInference` socket。確認 Desktop／backend 均退出後，將精確 `C:\Users\y4lin\AppData\Local\Docker\run` 改名備份，再啟動既有 Desktop，Linux engine 恢復。未安裝 WSL／改版本／factory reset／刪除映像或 volume。[官方 tracker 同類回報](https://github.com/docker/desktop-feedback/issues/554)提供背景，**本機成功依 engine 實測結果，不以 issue 回報當作修復證據**。

## unit-test-trust-review 結論

對目前 checkpoint 核心合約為**高信心的有限範圍驗證**；對整體協作功能仍不足以驗收。middleware 的 graph 覆蓋良好，但只有 checkpoint 做了變異抽查；preflight 的真容器／image、異常資源分支及 CLI 分支尚有缺口。

| 指標 | 實測結果 |
|---|---|
| 本批測試 | 23 通過，0 失敗、0 skip；coverage run 8.027 秒 |
| Windows 全回歸 | 211 通過，118.096 秒，含原 188 與新增 23；不使用真 provider／GPU |
| checkpoint 行覆蓋 | 158/158＝100% |
| checkpoint 分支覆蓋 | 52/52＝100% |
| middleware 行覆蓋 | 36/36＝100% |
| middleware 分支覆蓋 | 8/8＝100% |
| preflight 行覆蓋 | 85/108，約 78.7% |
| preflight 分支覆蓋 | 24/30＝80% |
| checkpoint 人工變異抽查 | 6/6 killed；未修改來源檔，僅子程序記憶體編譯 |

同一批最終 M0 suite 亦在獨立執行、全 suite 與之後獨立重跑三種順序中通過；最後一次 23 項為 7.426 秒。只做一次全 repo 回歸，不以大量重跑取代缺失的容器證據。

品質檢查：

- A（assertions）：驗證實際歷史／tool call ID／內容、排序、不可變結果、原 DB 未更動、各 thread scope 與 final output。未以 mock 被呼叫作核心成功標準；移除了循環 fake model index 的弱斷言。
- B（mock）：SQLite／graph／native middleware 真實執行；只有 provider 用可控模型，preflight parser 的失敗分支注入簡單 ProbeResult。這些 parser unit test 不計真 Docker 驗收。
- C（behavior）：覆蓋空／失效 schema、foreign thread、namespace、歷史 filter/pagination、同 ID 不同 metadata、部分 channel snapshot、immutable channel version、pending error 更新與正常寫入去重、12 次多連線寫入及 cancellation。
- D（isolation）：每測試使用獨立 TemporaryDirectory；並行以 ThreadPoolExecutor／asyncio.Event，不靠 sleep。未依賴測試 admin、正式 service DB、repo 中秘密或 GPU。新增 suite 可獨立執行並納入全 suite。Windows asyncio socket 必須在允許 loopback 的環境，沙箱阻擋不算測試失敗後改 skip。
- E（maintainability）：全套使用既有 unittest 與已安裝 coverage，未加依賴／改 lockfile；無 skip/xfail、coverage omit 或 `pragma: no cover`。實際 code path 的最低機制測試與真 graph 行為測試分開。

變異抽查明細（每項執行相同 checkpoint tests）：

| 變異 | 必須被抓到的行為 | 結果 |
|---|---|---|
| 取消 thread guard | foreign read/write/history/delete 不能成功 | killed |
| 取消 checkpoint ID 不可變 guard | 同 ID 改 metadata 必須拒絕 | killed |
| 取消 channel version 不可變 guard | 同版本覆寫內容必須拒絕並 rollback | killed |
| 普通 pending write 改為 REPLACE | 重送不能改寫已確認工具結果 | killed |
| task_path 排序反轉 | native pending writes 順序必須保持 | killed |
| 取消既有 schema guard | 不可默默接受／改動單 Agent DB | killed |

未發現 survivor。這是 6 個指定高風險分支的抽查，不是完整 mutation testing。由實作者執行本審核，未宣稱獨立 reviewer 已放行。source/schema／policy 整合、真正容器停止、forced process crash、runtime 資源硬限制、migration／restore、HTTP 授權及真模型閉環仍需後續驗證；coverage 不能代替它們。

## 可重現指令與證據

從 repo 根目錄、使用現有 Python；Windows 環境需允許 asyncio loopback：

```powershell
.venv/Scripts/python.exe -m unittest discover -s tests/collaboration -q
.venv/Scripts/python.exe -m unittest discover -s tests -q
.venv/Scripts/python.exe -m pi_deepagent.collaboration.preflight --output <new-evidence-file.json>
.venv/Scripts/python.exe scripts/collaboration/mutation_probe.py --output <new-evidence-directory>
```

preflight 尚未指定已核准映像時回 blocked／非零，這是預期行為；不是新增 tests 全綠就准許啟動 team。既有輸出檔／目錄拒絕覆寫，重跑須新 evidence ID。

原始證據目錄：`C:/Users/y4lin/.codex/visualizations/2026/10/29/01a18853-f3dc-7770-aac1-9c97284f6be4/`：

- `collaboration-m0-tests-2.log`、`collaboration-m0-coverage-2.json`：23 項及分支覆蓋。
- `collaboration-m0-tests-repeat.log`：全 suite 後 23 項獨立重跑。
- `collaboration-m0-full-python.log`：211 項全回歸。
- `collaboration-m0-framework.log`：初次讀取結果合約不符的失敗；`collaboration-m0-framework-2.log`：修正後三項 graph probe 通過，後續 sync probe 收在上述全 suite。
- `collaboration-m0-preflight-engine.json`：真 Linux engine、資源、映像仍待核准，未使用 GPU。
- `collaboration-m0-mutations/results.json` 與六項個別 log：變異位置、內容、結果。

後續必須保存具 source hash 的可攜 evidence index；上述單機絕對目錄不是最終 AC 交付替代品。D 磁碟搬移未執行，見 [待審查方案](docker-storage-move-plan.md)。
