# 賽會防護員媒合平台（MVP 測試版）

全台、全運動項目的賽會防護員與主辦方雙邊媒合平台。這一版用 **Streamlit** 快速做出完整功能，有兩個目的：一是確認功能流程是否完整，二是開始蒐集真實用戶資料。

核心邏輯放在 `core/`，跟介面分開。之後要換成開發書建議的 React / Next.js + FastAPI 時，配對引擎、資料模型與 LINE 串接可以直接沿用。

---

## 功能對照開發書

| 開發書章節 | 本版實作 |
|---|---|
| 4.1 防護員檔案 | LINE 登入並綁定 userId；履歷全欄位（LINE ID 必填）；學校下拉可搜尋；畢業年份動態算年資；接案縣市可「全台（全選）」；**所有證照都要填證號**：AT、PT、EMT-1、EMT-2、CPR+AED，有填證號才算具備該證照（證號只給管理員核對） |
| 4.2 發案模組 | 賽會基本欄位；**多時段**（連續日期／每週固定／自訂，產生後可在表格逐筆修改）；證照、資歷、學校條件；高風險運動自動警告並預設勾選 EMT；**即時顯示符合人數** |
| 4.3 配對與通知 | AND 交集配對，只配對審核通過且未封鎖者；LINE Flex Message 一對一推播；高風險加註紅色警告 |
| 4.4 挑選與結案 | **依時段應徵與錄用**：防護員勾選可出席時段（可修改，已錄用的鎖定）；發案方可「依應徵者」一次錄用多個時段，或「依時段挑人」；時段總覽顯示每個時段錄用幾人、幾人可出席；所有時段錄滿才自動結案；應徵者能來的時段都被補滿時自動標為未錄取；應徵時間戳記；三層權重排序；錄用前去識別化（王O明，經歷中的電話也會遮蔽）；錄用後解鎖個資並 LINE 通知雙方；額滿自動關閉；未錄取者點舊連結顯示「本賽事已招募額滿」 |
| 5 營運防弊 | 後台證照審核（核對證號、**同一證號被多個帳號使用會自動標示**、通過或退件）；檢舉查證；黑名單（無法登入、不再收到通知） |
| 新增：我想發問 | 防護員可在應徵前對案件提問，發案方回覆；雙方都會收到 LINE 通知；發案方可選擇公開問答（匿名）給其他防護員看；問答中的電話、Email、LINE ID 自動遮蔽；每人每案最多 3 則待回覆問題 |
| 6 驗收標準 | `tests/` 自動化驗收測試；後台「配對驗證」頁 |
| **新增：防護紀錄表（付費功能）** | 依《防護紀錄表》原型的 3 步驟表單：選手資料 → 部位（四肢自動展開左／右／雙側）與受傷類型 → 處置與耗材計數器，手機上「儲存並發送」固定在底部；從 LINE（LIFF）開啟免登入；送出後主辦方 LINE 即時收到，「建議轉診／傷退」置頂標示；紀錄查詢、耗材統計、CSV 匯出；主辦方可在「我的案件」查看與下載該場紀錄；記錄每筆填寫秒數（目標 30–45 秒） |
| **新增：付費開通（功能試驗）** | 付費牆、7 天免費試用（每人一次）、月／年方案、模擬結帳（成功／失敗／取消）、續約從到期日接續、取消自動續訂；後台「付費功能試驗」頁有轉換漏斗、模擬營收、手動開通／停用、付款紀錄、紀錄使用統計；**不會真的扣款** |
| 資料蒐集（本階段目標） | 數據總覽與轉換漏斗（含提問數、平均回覆時間）、意見回饋頁、行為紀錄、全資料 CSV 匯出、個資告知與同意 |

**額外功能**：每個時段可以要多位防護員、提前結束招募、取消案件、賽後評價（排序第二權重會用到）。

---

## 專案結構

```
app.py                 進入點：LINE 回呼、依身分組合選單
core/
  config.py            讀取 secrets / 環境變數
  constants.py         縣市、運動項目、學校、證照等下拉選項（改這裡就好）
  validators.py        LINE ID、證號格式；縣市全選；時段產生與檢查
  fmt.py               時段摘要與明細的顯示格式
  db.py                資料表（SQLite / PostgreSQL 共用）
  matching.py          配對引擎與應徵者排序
  services.py          發案、應徵、提問與回覆、錄用、結案、審核等流程
  records.py           防護紀錄表：驗證、儲存、通知主辦方、耗材統計
  billing.py           付費方案、試用、結帳（金流供應商介面 + 模擬金流）
  line_api.py          LINE Flex Message 推播與通知紀錄
  auth.py              LINE Login（OAuth）、測試登入、管理員登入
  privacy.py           去識別化
  seed.py              測試資料（含驗收情境）
  ui.py                樣式與元件
views/                 各頁面（common / trainer / requester / records / admin）
liff/index.html        LIFF 啟動頁（放 GitHub Pages），在 LINE 裡取得身分後轉進平台
tests/                 驗收測試與介面冒煙測試
Dockerfile             自架伺服器用
.github/workflows/     每次 push 自動跑測試
```

---

## 一、本機試跑（5 分鐘）

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
cp .streamlit/secrets.toml.example .streamlit/secrets.toml   # 至少改 ADMIN_PASSWORD
streamlit run app.py
```

打開 http://localhost:8501 後：

1. 按「建立測試資料」。
2. 選一個測試帳號登入，就可以體驗發案方或防護員的流程。
3. 從側邊欄「管理員登入」輸入 `ADMIN_PASSWORD`，進入後台。

執行測試：

```bash
pytest -q
```

---

## 二、上線部署

### 1. 推上 GitHub

```bash
git init && git add . && git commit -m "MVP"
git branch -M main
git remote add origin https://github.com/<你的帳號>/<repo>.git
git push -u origin main
```

> `.streamlit/secrets.toml` 已經列在 `.gitignore`，不會被上傳。**任何金鑰都不要寫進程式碼。**

> **從舊版升級？** 程式啟動時會自動補上新增的欄位與資料表（時段、證號、提問等），不需要刪資料庫。
> - 舊案件沒有時段明細，啟動時會依原本的日期區間自動補上時段；舊的應徵視為「全部時段都能出席」，已錄用的視為「全部時段已錄用」。
> - 舊防護員若有勾選證照卻沒有證號，登入「我的履歷」會看到補填提示；在補填前仍照舊參與配對，儲存時沒有證號的證照就會移除。
> - 舊版上傳的證照圖片資料表 `license_images` 已不再使用，可以手動刪除。

### 2. 建立資料庫（Supabase，免費）

**這一步很重要。** Streamlit Community Cloud 每次重新部署或重啟都會清空本機檔案，SQLite 裡的用戶資料會全部消失。

1. 到 [supabase.com](https://supabase.com) 建立專案，區域選 Tokyo 或 Singapore。
2. 進入 Project Settings → Database → Connection string，選 **Session pooler**，複製連線字串。
   Streamlit Cloud 不支援 IPv6，不要用 Direct connection。
3. 把連線字串填進 `DATABASE_URL`。資料表會在第一次啟動時自動建立。

### 3. 部署到 Streamlit Community Cloud（免費）

1. 到 [share.streamlit.io](https://share.streamlit.io)，用 GitHub 登入 → New app → 選 repo，主程式填 `app.py`。
2. 在 Advanced settings → Secrets 貼上 `secrets.toml.example` 的內容並填好。
3. 部署完成後會拿到網址，例如 `https://xxx.streamlit.app`，把它填進 `APP_BASE_URL`。

> 免費方案的 App 一段時間沒人用會進入休眠，下一位訪客要等它喚醒。正式營運建議改用 Dockerfile 部署到 GCP Cloud Run 或任何 VPS：
> ```bash
> docker build -t trainer-match . && docker run -p 8501:8501 --env-file .env trainer-match
> ```

### 4. 設定 LINE（[LINE Developers Console](https://developers.line.biz/console/)）

> ⚠ **LINE Login channel 和 Messaging API channel 必須建在同一個 Provider 底下。** 不同 Provider 拿到的 userId 不同，推播會失敗。

**A. Messaging API channel（官方帳號，負責推播）**

1. 在 Provider 底下建立 Messaging API channel。這會同時建立一個 LINE 官方帳號。
2. 到 Messaging API 分頁，發行 Channel access token (long-lived)，填進 `LINE_MESSAGING_ACCESS_TOKEN`。
3. 在 LINE Official Account Manager 關閉「自動回應訊息」，避免干擾用戶。

**B. LINE Login channel（負責登入）**

1. 在同一個 Provider 底下建立 LINE Login channel，App type 勾選 Web app。
2. 把 Basic settings 的 Channel ID 和 Channel secret 分別填進 `LINE_LOGIN_CHANNEL_ID` 和 `LINE_LOGIN_CHANNEL_SECRET`。
3. 到 LINE Login 分頁的 Callback URL，填入 `APP_BASE_URL` 加上結尾 `/`，例如 `https://xxx.streamlit.app/`。
4. 到 Basic settings 的 **Linked LINE Official Account**，選擇 A 建立的官方帳號。這樣用戶登入時會被邀請加好友。沒加好友的人收不到推播。
5. 把 channel 從 Developing 切換成 **Published**，否則只有你自己的帳號能登入。

**C. 驗證**

用自己的 LINE 登入，並建立一份防護員履歷。到後台把自己審核通過後，你的 LINE 應該會收到「審核通過」通知。之後用另一個測試帳號發一個符合你條件的案件，確認收到 Flex 卡片。

**推播額度**：LINE 官方帳號的免費方案每月推播則數有限。每通知一位防護員算一則，錄用時通知雙方算兩則。後台「數據總覽」可以看到實際發送數量，接近上限時要升級方案。

### 5. 開放真實用戶前的檢查清單

- [ ] `DATABASE_URL` 已改成 PostgreSQL
- [ ] `ADMIN_PASSWORD` 設成長密碼，`STATE_SECRET` 設成隨機字串
- [ ] LINE Login channel 已 Published，並已連結官方帳號
- [ ] 「隱私權聲明」頁已請法律顧問審閱（目前是範本）
- [ ] 後台「資料匯出」頁按「清除測試資料」
- [ ] `DEMO_MODE = false`，關掉首頁的測試登入

---

### 6. 防護紀錄表：串接 LINE LIFF

Streamlit 無法直接執行 LINE 的 LIFF SDK，所以多一個很小的「啟動頁」：
在 LINE 裡打開 → 啟動頁取得防護員的 LINE 身分 → 帶著一次性的 ID token 轉進平台 → 平台向 LINE 驗證後直接登入、打開紀錄表。防護員全程不用按登入。

1. **放上啟動頁（GitHub Pages，免費）**
   1. 打開 `liff/index.html`，改最上面兩行：`LIFF_ID`（第 3 步拿到後再回來填）、`APP_URL`（你的平台網址）。
   2. GitHub repo → **Settings → Pages** → Source 選 **Deploy from a branch**，Branch 選 `main`、資料夾 `/ (root)`。
   3. 啟動頁網址會是 `https://你的帳號.github.io/at_match/trainer-match/liff/`。
   > 免費的 GitHub Pages 需要 **公開（public）的 repo**。如果主程式的 repo 是私人的，可以另外開一個公開 repo，只放這一個 `index.html`。程式裡沒有任何金鑰，公開沒有安全問題。
2. **確認 repo 沒有放金鑰**：`.streamlit/secrets.toml` 已列在 `.gitignore`。
3. **建立 LIFF**：LINE Developers → 你的 **LINE Login channel**（跟登入用的同一個）→ **LIFF** 分頁 → Add：
   - Size：**Full**
   - Endpoint URL：第 1 步的啟動頁網址
   - Scopes：勾選 **openid** 與 **profile**
   - Add friend option：On (Aggressive)
   建好後複製 **LIFF ID**，填回 `liff/index.html`，並在 Streamlit Secrets 加上 `LIFF_ID = "…"`。
4. **放進 LINE 選單**：LINE Official Account Manager → **圖文選單** → 新增一個按鈕，動作選「連結」，網址填 `https://liff.line.me/你的LIFF_ID?page=record`。防護員在官方帳號點一下就能開始記錄。
5. 錄用通知訊息裡也會自動附上該場賽事的紀錄表連結（有設定 `LIFF_ID` 時走 LIFF）。

**測試**：用手機 LINE 點圖文選單 → 應該直接看到「📋 防護紀錄表」，不用登入。若出現「LINE 身分驗證失敗」，通常是 LIFF 建在不同的 channel，或 `LINE_LOGIN_CHANNEL_ID` 不是那個 channel。

### 7. 付費功能（目前為功能試驗）

- 目前 `BILLING_MODE = "mock"`：付款頁是模擬的，可以選「模擬付款成功／失敗／取消」，**不會扣款，也不會收集信用卡資料**。
- 價格、試用天數在 `core/constants.py` 的 `PLANS`、`TRIAL_DAYS`，可以直接改來測試不同價格。
- 後台「付費功能試驗」頁：看付費牆 → 試用 → 結帳 → 付款的轉換率、模擬營收；也可以手動幫種子用戶開通。
- 防護員到期後仍可查詢與匯出自己的舊紀錄，只是不能新增。

**之後要真的收費時**（還需要的工作）：
1. 在 `core/billing.py` 新增金流供應商類別（綠界 ECPay、藍新 NewebPay 或 LINE Pay），實作建立訂單與驗證付款結果。
2. 金流公司付款完成後會用 POST 通知你的伺服器（webhook），**Streamlit 無法接收這種通知**，需要另外一個小型後端，例如 Supabase Edge Function 或 Cloud Run，收到後呼叫 `billing.settle()` 的同等邏輯寫入資料庫。
3. 開立電子發票、訂定退款政策與服務條款（數位內容與訂閱服務的消費者保護規定），建議諮詢會計師與律師。

### 8. 常見部署問題

| 錯誤訊息 | 原因與修正 |
|---|---|
| `could not translate host name "db.xxxx.supabase.co"` | 用了 Supabase 的 Direct connection（只支援 IPv6）。改用 **Connect → Session pooler** 的連線字串，主機會是 `aws-0-…pooler.supabase.com` |
| `Tenant or user not found` | Session pooler 的帳號要寫成 `postgres.專案代號`，不是只有 `postgres` |
| `could not translate host name "xxxx@aws-0-…"` | 密碼裡有 `@`。新版程式會自動處理密碼中的特殊符號，更新程式即可；也可以重設成只有英數字的密碼 |
| `password authentication failed` | 密碼錯誤，請到 Supabase → Database Settings 確認或重設 |
| 連線逾時 | Supabase 免費專案一週沒使用會自動暫停，到 Supabase 按 Restore |

資料庫連不上時，網站會顯示「系統暫時無法使用」與上面對應的中文修正說明，不會把密碼顯示出來。修改 Secrets 後記得 Reboot app。

## 三、驗收方式（對應開發書第 6 點）

**自動測試**：執行 `pytest -q`，包含以下項目。

- **配對精準度**：發布「限台體大 + AT 證 + 台中」的案件，13 位種子防護員中只有陳冠宇、林雅婷收到通知。其餘都是刻意設計的干擾組：沒 AT、學校不同、不接台中、未審核、被退件、黑名單。
- **年資條件**：「X 年以上」與「X 年以內」兩種篩選。
- **高風險運動**：自動標記，並且只配對到有 EMT 的人。
- **排序權重**：有放鳥紀錄者排最後，有好評者排在無評價者前面。
- **隱私**：錄用前的資料完全不含電話、Email 與完整姓名，經歷欄裡寫的電話也會被遮蔽。
- **錄用流程**：解鎖個資、額滿自動結案、通知雙方、之後無法再應徵。
- **多時段**：「每個週六日、連續兩週」產生 4 個時段；時段自動排序；結束要晚於開始、同一天不可重疊、不可選過去日期；摘要文字依「單一／連續／每週」自動切換；LINE 卡片列出各時段；最後一個時段還沒過的賽事仍會出現在職缺列表。
- **依時段錄用**：只勾部分時段應徵、依時段錄用、加錄時段、時段錄滿後不能再錄、全部錄滿自動結案、能來的時段被補滿自動未錄取、修改可出席時段（已錄用的不能取消）、取消後重新應徵、舊案件與舊應徵自動補上時段。
- **證號**：五種證照都以證號決定資格，且證號永遠不會出現在發案方看到的資料裡。
- **我想發問**：提問與回覆、LINE 通知、公開或私下可見、聯絡方式遮蔽、提問上限、結案後不能提問。
- **全選縣市、LINE ID 必填**：格式檢查。
- **自動升級**：舊資料庫啟動時會自動補上新欄位。
- **付費開通**：未開通不能新增紀錄、試用每人一次、模擬付款成功／失敗／取消、重複的付款通知不會重複延長、續約從到期日接續、到期與停用後失去權限但保留舊紀錄。
- **防護紀錄表**：只能記錄自己被錄用的案件、四肢部位必須選左右、各步驟必填檢查、通知主辦方並標示轉診／傷退、耗材統計、刪除權限。
- **LIFF**：ID token 向 LINE 驗證、過期 token 被拒、登入前後都能帶著「打開紀錄表」的目的地。

**手動驗收**：在後台「案件與通知紀錄 → 配對驗證」選一個案件，可以直接比對「當下收到通知的人」和「用目前資料重新配對的結果」。

**隱私說明**：Streamlit 是伺服器端渲染，瀏覽器只會收到畫面上顯示的內容。錄用前，發案方的頁面只會拿到 `core/privacy.py` 中 `public_trainer_view()` 白名單裡的欄位，電話等資料根本不會送出伺服器。這可以用瀏覽器開發者工具的 WebSocket 封包確認。

---

## 四、已知限制與下一步

- **登入狀態**：重新整理頁面需要重新登入。LINE 的內建瀏覽器重新登入只需要點一下。正式版可以改用 cookie 或 JWT。
- **證號核對**：目前由管理員手動到官方系統查詢。若之後取得官方查詢 API，可以在 `core/validators.py` 加上自動核對。
- **新增證照種類**：在 `core/constants.py` 的 `LICENSE_FIELDS` 加一行，並在 `core/db.py` 加對應欄位即可，表單、審核、匯出會自動跟上。
- **時段衝突提醒**：目前防護員可以同時應徵兩個時間重疊的案件。下一步可以在應徵或錄用時，提醒「這個時段你已經被其他案件錄用」。
- **配對查詢**：縣市以 JSON 陣列儲存，篩選在 Python 端完成。幾千位防護員內速度都沒問題，再大可以改用 PostgreSQL 的 `jsonb` 索引。
- **長期優化**：回饋頁蒐集的「最想要的功能」排名，可以當作下一階段的開發優先順序。
