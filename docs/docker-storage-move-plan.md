# Docker Desktop 儲存移至 D 磁碟：待審查操作方案

> **2026-10-31：歷史方案，已被重新安裝方向取代。** 使用者表示已自行移除 Docker Desktop，後續須規劃重新安裝至 D 磁碟；不得執行本文件舊搬移步驟，也不再等待舊搬移窗口問題的答案。卸載殘留與舊資料是否存在尚未重新盤點。程式安裝位置與 Linux 資料位置必須分別確認；原定資料目標 `D:\DockerDesktop\wsl`，實際 installer／參數／備份回復方案仍待準備。詳見 [最新完整交接](multi-agent-collaboration-handoff-prompt.md) 第 2 節及 [授權紀錄](multi-agent-collaboration-user-requirements.md)。本次僅交接，沒有安裝、搬移、拉取或啟動 GPU；下文日期、版本及系統狀態均為歷史觀察。

日期：2026-10-30（Asia/Taipei）。狀態：**僅準備方案；未停止既有容器、未搬移資料、未拉取新映像**。

管理員已同意「先準備可審查的搬移步驟」。本文件不是搬移已完成的紀錄。多 Agent 實作繼續進行不依賴此環境的部分；GPU 模型維持 pending。

### 最新唯讀覆核（2026-10-31）

Desktop executable ProductVersion 為 4.41.2.191736；沙箱內外 desktop status 均無法取得狀態，沙箱外有三個 Desktop UI 程序、未列出 backend 程序。不能據此宣稱 engine ready、paused、容器停止或資料遺失；本次沒有啟動／解除暫停／停止。VHDX 仍為下列 C 路徑，29,391,585,280 bytes，最後寫入 2026-10-30 23:40:12；D 目標仍不存在。C 可用 48,777,248,768 bytes、D 943,690,248,192 bytes。這些是當次觀察，執行前重查。

已透過待答詢問請管理員明確核准停機窗口／正常停止原專案／離線備份／由管理員操作官方設定頁搬移及後續驗證恢復；**尚無回覆，不代表核准**。目前不啟動流程，也不拉取映像、使用 GPU 或套正式 migration。若核准，先重新診斷／盤點精確資源；無法證明健康及停止時停在診斷，不強制操作。

### 本次覆核更新（2026-10-30）

唯讀覆核時 `docker desktop status` 回報 **paused**，context 為 `desktop-linux`，Engine 版本仍為 28.1.1；`docker ps`／`docker ps -a` 沒有列出項目。因為 Desktop 暫停中，這不能作為原容器已刪除、已停止或資料遺失的證據。目前未解除暫停，原三個容器的狀態必須在執行窗口內重新盤點，不能沿用下方歷史狀態直接操作。

最新資料 VHDX 大小為 29,391,585,280 bytes（約 27.4 GiB）；C 可用 42,968,346,624 bytes（約 40.0 GiB），D 可用 944,769,146,880 bytes（約 879.9 GiB），兩者均 NTFS。`D:\DockerDesktop` 尚不存在。以下最初盤點數字保留作比較；執行時以重新查驗值為準。

核准範圍目前只有準備本文件。**實際解除暫停、停機、備份、搬移與恢復，以及兩個映像的拉取，都還沒有執行。**

## 實際盤點與明確路徑

Docker Desktop 4.41.2／Engine 28.1.1，Windows 11、WSL 2、Linux/amd64。engine 的 `/var/lib/docker` 位於 Docker 管理的 VHDX，不能用 `docker pull` 的參數指定 Windows 存放目錄。不是把 `docker save` 的 tar 放在 D 就能改變已載入映像的實際占用位置。

| 用途 | 完整 Windows 路徑 | 目前狀態 |
|---|---|---|
| 現有 Docker WSL 根目錄 | `C:\Users\y4lin\AppData\Local\Docker\wsl` | 已存在 |
| 映像／容器／volume 資料磁碟 | `C:\Users\y4lin\AppData\Local\Docker\wsl\disk\docker_data.vhdx` | 29,358,030,848 bytes，約 27.3 GiB |
| Docker 系統磁碟 | `C:\Users\y4lin\AppData\Local\Docker\wsl\main\ext4.vhdx` | 146,800,640 bytes，約 140 MiB |
| 提議新資料根目錄 | `D:\DockerDesktop\wsl` | 盤點時 `D:\DockerDesktop` 尚不存在 |
| 預計搬移後資料磁碟 | `D:\DockerDesktop\wsl\disk\docker_data.vhdx` | 須以實際設定與檔案查驗確認，不能預先宣稱已生效 |
| 搬移前離線備份 | `D:\DockerDesktop\backups\before-move-20261030\` | 尚未建立；目錄如已存在則改用新唯一名稱，不覆寫 |
| 搬移操作證據 | `docs/multi-agent-collaboration-acceptance.md` 的 D1/D2 紀錄＋本聊天專用 evidence 目錄 | 不在 Git 保存其他專案資料或敏感設定 |

盤點時 C 可用 51,098,046,464 bytes（約 47.6 GiB）；D 為 NTFS，可用約 944,770,682,880 bytes（約 880 GiB）。執行前重新檢查容量；備份＋移動後資料可能同時保留兩份約 27.5 GiB 的 VHDX，至少保留 70 GiB 可用空間才進行，不清理其他專案來騰空間。

目前三個執行中容器屬於既有 `mongo_job_operation_board` 專案，並非本次多 Agent 測試：frontend（ID 開頭 `cd09560bb7e8`）、backend（`93338d06c871`）、Mongo（`1869ae61c192`，healthy）。三者實查 restart policy 均為 `unless-stopped`；預計人工正常停止後，在 Desktop 重新開啟設定頁時仍保持停止，但仍須逐一查驗，不能只信任政策字串。移動會暫時中斷這些服務，必須先確認停機窗口。執行時重新解析完整 IDs 與來源，不能只靠本文件中的短 ID 停止程序。

## 建議操作順序與停機屏障

1. **取得對本方案、停機窗口及全域 Docker 儲存位置變更的明確確認。** 核准實作多 Agent 不等於允許停止其他專案容器。拉取映像仍未核准；位置生效後再請確認兩個確切 digest。
2. 在私有備份目錄保存必要盤點：目前完整 container IDs、原執行／health 狀態、restart policy、image IDs／digests、volume 名稱、Docker 版本及設定檔的 hash。不要匯出完整 `docker inspect` 的環境變數／憑證至一般日誌或本 repo。保存 Docker 設定檔複本只能放私有備份目錄並限制存取。
3. 先停止 frontend／backend 的新請求，再讓資料庫正常停止，對**重新確認的精確容器 IDs**執行有界 graceful stop。逐一確認 stopped；逾時或 unknown 停在此步，不自行 `--force`、重建、刪除或 `compose down -v`。
4. 使用此安裝版本已確認存在的 `docker desktop stop --timeout 60`，不加 `--force`。確認 engine 不再接受命令、Docker backend 已退出。若 VHDX 仍被占用，記錄原因並停止，不使用全域 `wsl --shutdown` 中斷其他工作，也不 unregister 發行版。
5. **完整停止後才備份。** 將上述兩個 VHDX 複製到 `D:\DockerDesktop\backups\before-move-20261030\disk\docker_data.vhdx` 與 `...\main\ext4.vhdx`，並備份相符的 Docker 設定檔。來源及目的均須解析為絕對路徑並驗證落在明確根目錄；拒絕 reparse／junction，拒絕覆寫既有備份。複製前後確認來源 size／mtime 未變，對來源與備份計算 SHA-256 並一致，保存 manifest。任何失敗不刪來源、不搬移。
6. 備份驗證成功後，重新開啟 Docker Desktop 進入設定頁，先確認既有容器沒有自行恢復執行。使用 **Docker Desktop 官方 Settings → Resources → Advanced → Disk image location** 選擇 `D:\DockerDesktop\wsl`，依該版本的 Move／Apply 流程搬移。現有可用工具不提供 Docker Desktop 原生設定頁控制；此步需管理員操作 UI。**不直接手改未查證的 settings key、不自行剪下正在掛載的 VHDX、不改 WSL 註冊或重裝 Docker。** 若 4.41.2 設定頁沒有這個選項或目標結構不同，先回報實際畫面／選項再決定，不改用另一套未核准搬移方法。Docker 重新開啟後可能再次寫入 VHDX；步驟 5 的備份是停機時點快照，不能事後宣稱仍與使用中的磁碟 hash 一致。
7. 設定套用後，確認 Linux engine 回來，再查實際 VHDX 路徑與大小、設定生效位置、image IDs／digests、原容器與 volume 清單。實際若有 `DockerDesktopWSL` 等額外子層，依查驗結果更新完整路徑，不能只沿用預計字串。C 可能仍保有小型程式／設定／日誌，不宣稱 Docker 對 C 零占用。
8. 依依賴順序恢復原本正在執行的 Mongo→backend→frontend；原本停止的容器保持停止。確認 Mongo healthy、其他服務／port 與盤點一致，並保留原 ID／volume。只有 engine ready 不代表應用或資料已驗證；若缺少可執行的資料完整性檢查，明確列出尚待管理員確認。
9. 成功後保留離線備份及 runtime socket 改名備份；**不自動刪除 C 原檔、備份或無關映像／cache**。另行確認後才處理回收。確認資料實際落在 D 後，才處理兩個已解析映像的拉取授權與 M0 真容器驗證。

## 回復方式

搬移前任何檢查失敗：不變更位置，保存診斷，依原位置重新啟動 Desktop 與原本活動的容器。

位置已變更但驗證失敗：先停止 Docker／容器，保留 D 的失敗現場與所有操作紀錄；優先透過相同官方設定頁改回原位置。若需用離線備份還原，必須在完全停止、精確來源／目標及設定版本已核對後，另提出具體還原步驟並確認覆寫對象；不能一面把 C 舊磁碟視為即時資料，一面繼續使用 D 產生新寫入。兩份磁碟的資料分歧不可用簡單檔案合併解決。

不執行 factory reset、Docker 更新／重裝、WSL unregister、全域 `docker system prune`，也不把主機搬移當作團隊 sandbox 或產品 migration 的驗收。

## 待確認的映像（仍未拉取）

| 用途 | 官方 Linux/amd64 immutable reference |
|---|---|
| Python 3.13 M0 runtime | `python@sha256:70bda1e795ded2a684ef9815955e05829b14ee5bb5c3eb2a940648e88f10fce9` |
| Node 22 M0 runtime | `node@sha256:9f0dde62d10e7ff7a310ecaebd66225f6b46c17e6cb778449a77b05d3541bea2` |

這兩個 digest 已經由 registry manifest 解析；不表示映像已在本機或已驗證工具版本。拉取後再次核對 image inspect 的 ID／RepoDigest／platform，記錄實際 Python／Node patch 版本，避免以浮動 tag 啟動。

## 依據

- [Docker Desktop WSL 2：預設資料位置與設定入口](https://docs.docker.com/desktop/features/wsl/)。
- [Docker Desktop 全 VM 備份：複製 VHDX 前須完全停止](https://docs.docker.com/desktop/settings-and-maintenance/backup-and-restore/)。
- [Docker Desktop stop CLI](https://docs.docker.com/reference/cli/docker/desktop/stop/)；本機 `docker desktop stop --help`／`start --help` 已確認有 timeout 選項。

本方案尚未執行；有副作用的每一步須在核准範圍內，unknown 結果先對帳，不重跑整套流程。

## 重要最新進展，User 直接自系統移除了 docker desktop
因此搬移不需進行，只要重新在指定位置安裝目標檔案與環境即可
