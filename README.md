# 美股市寬與動能監察（免費版）

每個星期一至五香港時間中午 12 點，自動計算上一個美股交易日的市寬、動力股和期權觀察名單，
生成網頁放在 GitHub Pages。全部用免費數據（Yahoo Finance + Nasdaq 股票篩選器）。

## 內容

- **市寬**：升／跌 4% 股票數、% 高於 20／50／200 日線、SPY／QQQ 距 50 日 EMA（ATR 倍數）、領導股指數 MLI
- **4% 動量脈搏**：近 20 日柱狀圖
- **動力股分析**：季度升幅 ≥ 20% 的股票，按行業的佔比、滲透率、超額
- **期權觀察名單**：均線距離、OI 牆、IV、RV20、VRP20、財報事件

## 設定（一次性）

1. 在 GitHub 開一個新 repository（Public 才可以免費用 Pages）。
2. 把這個資料夾全部檔案上載，包括 `.github/workflows/daily.yml`。
3. Settings → Actions → General → Workflow permissions，揀 **Read and write permissions**。
4. Actions 分頁 → 「每日美股市寬總結」→ **Run workflow**，手動跑第一次（約 10–20 分鐘）。
5. Settings → Pages → Source 揀 **Deploy from a branch**，Branch 揀 `main`、資料夾揀 `/docs`。
6. 之後網址會係 `https://你的帳號.github.io/repo名稱/`。

## 修改

`market_dashboard.py` 頂部「設定」一段可以改門檻同觀察名單（`WATCHLIST`）。
本機測試版面：`python market_dashboard.py --demo`（用假數據，輸出 `docs/demo.html`）。

## 限制

- Yahoo 數據非官方，偶爾會限速或缺數據；行業分類由 Nasdaq 行業字串粗略歸類。
- IV 百分位要自己累積：每日存一次 IV，20 日後開始顯示，一年後才是真正的一年百分位。
- GitHub 排程有時會延遲十幾分鐘至一小時。
