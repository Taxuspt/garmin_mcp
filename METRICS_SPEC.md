# Metrics Spec — for Eric's `~/Running/seasons/2026H2/metrics/weekly-log.md` workflow

> **目的**：列出 weekly_review.py 需要的 Garmin 資料、現有工具狀態、要修/要新建的工具規格、驗證指令。
> **另一個 Claude session 從這份檔案開始接手**：cd 到本 repo、跑 §8 驗證指令、然後動工 §3-§5。
> **背景**：用戶（Eric）的 2026 H2 賽季訓練追蹤系統（FORMOSA 40K 11/21 + 半馬 1:45 目標）。完整 design 在 `~/Running/seasons/2026H2/metrics/DESIGN.md`（這個 session 之後會寫）、HANDOFF 在 `~/Running/seasons/2026H2/metrics/HANDOFF.md`。
> **PR #173 狀態**：cycling=null crash 已修並開 PR 給 upstream。本檔處理的是 PR #173 out-of-scope 段落標的「第二 bug」。

---

## 1. 指標 → endpoint 對應表

| # | 指標 | 現有 MCP 工具 | 狀態 | 動作 |
|---|---|---|---|---|
| 1 | HRV 7d 均 | `get_hrv_trend` | ✅ work | — |
| 2 | RHR 7d 均 | `get_user_summary` → `lastSevenDaysAvgRestingHeartRate` | ✅ work | — |
| 3 | Sleep 時數 7d 均 | `get_sleep_summary` × 7 days | ✅ 但 N+1 | §5 可選優化 |
| 4 | Sleep 分數 7d 均 | `get_sleep_summary` × 7 days | ✅ 但 N+1 | §5 可選優化 |
| 5a | CTL（dailyTrainingLoadChronic） | `get_training_load_trend` | ⚠️ **broken** | §3 修補 |
| 5b | ATL（dailyTrainingLoadAcute） | `get_training_load_trend` | ⚠️ broken | §3 修補 |
| 5c | ACWR + acwrStatus | `get_training_load_trend` | ⚠️ broken | §3 修補 |
| 6 | VO2max | `get_vo2max_trend` 或 `get_training_load_trend` | ✅ work（前者更穩） | — |
| 7 | Endurance Score | `get_endurance_score` | ✅ work | — |
| 8 | Hill Score（strength + endurance） | `get_hill_score` | ✅ work | — |
| 9 | 週量 + 累積爬升 | `get_activities_by_date` | ✅ work | — |
| **10** | **Load Focus（Aerobic Low/High/Anaerobic 三類分布 + feedback phrase）** | **無** | ❌ 缺工具 | §4 **新建** |
| **11** | **Training Status（PRODUCTIVE / MAINTAINING / OVERREACHING enum）** | 應隨 §3 修補回來 | ⚠️ 跟 #5 同 bug | §3 修補 |

**主觀指標**（ITBS / 睡眠主觀 / 體感）由 weekly_review.py 互動 prompt 處理、不靠 Garmin。

---

## 2. Bug 根因（簡述）

`get_training_load_trend`（`src/garmin_mcp/training.py:709-785`）對 `data.get("mostRecentTrainingStatus", {}).get("latestTrainingStatusData", {})` 直接 `.get("acuteTrainingLoadDTO", {})`——但 **`latestTrainingStatusData` 不是直接含 DTO，而是 dict keyed by device ID**：

```python
# 實際結構（2026-06-25 sample）
data["mostRecentTrainingStatus"]["latestTrainingStatusData"]
# == {"3620729386": {...device-specific status data...}}
```

於是 `.get("acuteTrainingLoadDTO", {})` 拿空 dict、所有 `.get("dailyTrainingLoadAcute")` 等 return None、沒進 `entry`、最後 trend list 只剩 vo2_max（從 sibling `mostRecentVO2Max` 拿、那個沒受影響）。

**附加 bug**：device data 內**已無 `trainingStatusDTO`** key。現有代碼 line 764-767 寫：
```python
ts = status_data.get("trainingStatusDTO", {})
ts_label = ts.get("trainingStatusCyclingFeedbackPhrase") or ts.get("trainingStatusFeedbackPhrase")
```
都會拿 None。新版 device data 直接平鋪：`trainingStatus`（int enum）、`trainingStatusFeedbackPhrase`（str）、`fitnessTrend`（int）、`fitnessTrendSport`（str）。

---

## 3. 修補 `get_training_load_trend`

### 3.1 修補 pattern

把 status_data 取得改為「iterate device-keyed dict、取主要 device」：

```python
# 取代 line 743-746
ltsd = data.get("mostRecentTrainingStatus", {}).get("latestTrainingStatusData") or {}
# device-keyed dict; pick primary training device, else first
status_data = None
for dev_data in ltsd.values():
    if isinstance(dev_data, dict):
        if dev_data.get("primaryTrainingDevice"):
            status_data = dev_data
            break
        if status_data is None:
            status_data = dev_data  # fallback to first device
if status_data is None:
    status_data = {}
```

把 line 764-767（過時 trainingStatusDTO）替換成：

```python
# device data 直接平鋪這幾個 key、不再有 trainingStatusDTO 包裝
ts_status_int = status_data.get("trainingStatus")  # int enum
ts_phrase = status_data.get("trainingStatusFeedbackPhrase")
if ts_phrase:
    entry["training_status"] = ts_phrase
if ts_status_int is not None:
    entry["training_status_code"] = ts_status_int
fitness_trend = status_data.get("fitnessTrend")
if fitness_trend is not None:
    entry["fitness_trend"] = fitness_trend  # int: 1=down, 2=stable, 3=up (推測 enum)
```

### 3.2 修補後 raw response 預期

跑 `mcp__garmin__get_training_load_trend("2026-04-27", "2026-06-25")` 應回：

```json
{
  "start_date": "2026-04-27",
  "end_date": "2026-06-25",
  "days_with_data": 60,
  "trend": [
    {
      "date": "2026-06-25",
      "atl": 230.0,
      "ctl": 219.0,
      "tsb": -11.0,
      "acwr": 1.0,
      "acwr_status": "OPTIMAL",
      "training_status": "MAINTAINING_2",
      "training_status_code": 4,
      "fitness_trend": 2,
      "vo2_max": 52.0
    }
    // ...每日一個 entry
  ]
}
```

**關鍵欄位來自 garminconnect `get_training_status(date)` 後 device value 內**：
- `acuteTrainingLoadDTO.dailyTrainingLoadAcute` → atl
- `acuteTrainingLoadDTO.dailyTrainingLoadChronic` → ctl
- `acuteTrainingLoadDTO.dailyAcuteChronicWorkloadRatio` → acwr
- `acuteTrainingLoadDTO.acwrStatus` → "OPTIMAL" / "LOW" / "HIGH"（待補完整 enum）
- `trainingStatus` → int 1-8
- `trainingStatusFeedbackPhrase` → str

### 3.3 Training Status enum 對照（推測、需驗證）

Garmin 文獻：
```
1 = NO_STATUS / RECOVERY
2 = PRODUCTIVE
3 = PEAKING
4 = MAINTAINING
5 = DETRAINING
6 = UNPRODUCTIVE
7 = OVERREACHING
8 = NO_STATUS（不同階段）
```

實測 sample：`trainingStatus: 4 + trainingStatusFeedbackPhrase: "MAINTAINING_2"` 對得起來。

### 3.4 注意事項

- `acuteTrainingLoadDTO` 內也含 `acwrPercent`（int）、`maxTrainingLoadChronic` / `minTrainingLoadChronic`（load tunnel）。**建議也回傳這幾個欄位**——對 PMC 圖很有用（畫 chronic load tunnel）。
- `weeklyTrainingLoad`、`loadTunnelMin`、`loadTunnelMax`、`loadLevelTrend` **常為 None**——`.get()` 保護要做、但**不要當錯誤**、就是 device 沒這天的數據。
- 60 天範圍實測沒問題（30 天那條限制是 HRV trend 才有、training_load_trend 本身上限 90 天）。

---

## 4. 新工具 `get_training_load_balance`（Load Focus 真身）

### 4.1 為什麼

現有 MCP 完全沒這個工具。但 Garmin 後台「訓練狀態 > 負荷焦點」（你 6/26 sample 顯示「高強度有氧不足」）就是這個 endpoint。對應 Eric 設計裡的指標 #10「Load Focus 狀態」——監控訓練組合是否平衡（低有氧 / 高有氧 / 無氧三類佔比）。

### 4.2 底層調用

```python
ts = garmin_client.get_training_status(date_str)
mtlb = ts.get("mostRecentTrainingLoadBalance", {})
metrics_map = mtlb.get("metricsTrainingLoadBalanceDTOMap", {})  # device-keyed
# pick primary device same way as §3.1
```

device value 完整欄位（2026-06-25 sample）：
```json
{
  "calendarDate": "2026-06-25",
  "deviceId": 3620729386,
  "monthlyLoadAerobicLow": 497.48456,
  "monthlyLoadAerobicHigh": 212.39876,
  "monthlyLoadAnaerobic": 0.0,
  "monthlyLoadAerobicLowTargetMin": 200,
  "monthlyLoadAerobicLowTargetMax": 463,
  "monthlyLoadAerobicHighTargetMin": 281,
  "monthlyLoadAerobicHighTargetMax": 543,
  "monthlyLoadAnaerobicTargetMin": 0,
  "monthlyLoadAnaerobicTargetMax": 262,
  "trainingBalanceFeedbackPhrase": "AEROBIC_HIGH_SHORTAGE",
  "primaryTrainingDevice": true
}
```

### 4.3 建議 MCP 工具 spec

```python
@app.tool()
async def get_training_load_balance(date: str) -> str:
    """Get Garmin's Load Focus — distribution of monthly training load across
    Aerobic Low / Aerobic High / Anaerobic, plus the system's feedback phrase
    (AEROBIC_HIGH_SHORTAGE, BALANCED, ANAEROBIC_SHORTAGE, etc.).

    Use this to assess whether the athlete's training mix is balanced or
    deficient in a particular intensity band. Drives the "Load Focus" metric
    in Eric's weekly review.

    Args:
        date: Date in YYYY-MM-DD format
    """
```

回傳建議：
```json
{
  "date": "2026-06-25",
  "feedback": "AEROBIC_HIGH_SHORTAGE",
  "aerobic_low": {
    "load": 497.5,
    "target_min": 200,
    "target_max": 463,
    "status": "above"  // above/within/below — 自己算
  },
  "aerobic_high": {
    "load": 212.4,
    "target_min": 281,
    "target_max": 543,
    "status": "below"
  },
  "anaerobic": {
    "load": 0.0,
    "target_min": 0,
    "target_max": 262,
    "status": "within"
  }
}
```

### 4.4 feedbackPhrase enum（已知）

從 Eric 圖中與 sample：
- `AEROBIC_HIGH_SHORTAGE` — 高強度有氧不足
- `AEROBIC_LOW_SHORTAGE` — 低強度有氧不足
- `ANAEROBIC_SHORTAGE` — 無氧不足
- `BALANCED` — 平衡

待補：其他 phrase 例如 `OVERREACHING` / `LOW_VARIETY` 等。

### 4.5 trend 版本（可選）

如果 weekly_review.py 想看 Load Focus 隨時間變化，可加 `get_training_load_balance_trend(start_date, end_date)`——對範圍每天調用一次 `get_training_status` 並抽 mtlb。實作 pattern 跟 `get_training_load_trend` 一樣（iterate 日期、抽 device data）。**MVP 建議先做單日版、有需求再做 trend**。

---

## 5. 可選優化：`get_sleep_trend`

### 5.1 為什麼

weekly_review.py 要算 Sleep 時數 / 分數的 **7 天滾動均**。現在只能跑 7 次 `get_sleep_summary(date)`——N+1 query、慢且 token 浪費（雖然 summary 本身只 350 bytes、但 7 次 call overhead 不小）。

### 5.2 實作

garminconnect 沒原生 trend method、要在 garmin_mcp 層自己 loop：

```python
@app.tool()
async def get_sleep_trend(start_date: str, end_date: str) -> str:
    """Get sleep duration and score trend over a date range.

    Returns daily sleep hours and Garmin sleep score, plus the 7-day rolling
    averages. Use this for tracking baseline sleep patterns instead of N
    individual get_sleep_summary calls.

    Recommended range: 7-30 days. Maximum: 60 days.
    """
```

回傳 schema：
```json
{
  "start_date": "...",
  "end_date": "...",
  "trend": [
    {"date": "2026-06-25", "sleep_hours": 7.7, "sleep_score": 90, "sleep_hours_7d_avg": 7.1, "sleep_score_7d_avg": 84.5},
    ...
  ]
}
```

### 5.3 優先級

**Low**——可以等 weekly_review.py 寫到 sleep 那段、感覺真的太慢再加。MVP 階段用 7 次 single-day call 也行。

---

## 6. PR 策略

| 工具 | 上游 PR 適合度 | 理由 |
|---|---|---|
| §3 `get_training_load_trend` 修補 | ✅ **強烈建議 PR** | 純 bug fix、所有用戶受惠、PR #173 已標記 |
| §4 `get_training_load_balance` 新工具 | ✅ 建議 PR | 通用功能、不是 Eric-specific |
| §5 `get_sleep_trend` | 🟡 可選 | 是優化、不是 bug、可單獨 PR |

**fork-only 不發 PR 的東西**：本檔本身（這是 Eric 專案的 spec）、`METRICS_SPEC.md` 不該進 upstream。

---

## 7. 完整 raw response 快照（給 fork session 參考）

### 7.1 `garmin_client.get_training_status("2026-06-25")` top-level

```
keys: ['userId', 'mostRecentVO2Max', 'mostRecentTrainingLoadBalance', 'mostRecentTrainingStatus', 'heatAltitudeAcclimationDTO']
```

- `mostRecentTrainingStatus.latestTrainingStatusData = {device_id: device_data}` ← §3 處理
- `mostRecentTrainingLoadBalance.metricsTrainingLoadBalanceDTOMap = {device_id: device_data}` ← §4 處理
- `heatAltitudeAcclimationDTO = None`（純跑者常見、可能用戶有越南／東南亞訓練才會有值——HANDOFF caveat #3 標的就是這類 explicit null）

### 7.2 Device data 內（status_data）

```
keys: ['calendarDate', 'sinceDate', 'weeklyTrainingLoad', 'trainingStatus', 'timestamp', 'deviceId',
       'loadTunnelMin', 'loadTunnelMax', 'loadLevelTrend', 'sport', 'subSport', 'fitnessTrendSport',
       'fitnessTrend', 'trainingStatusFeedbackPhrase', 'trainingPaused', 'acuteTrainingLoadDTO',
       'primaryTrainingDevice']

acuteTrainingLoadDTO 完整內容（2026-06-25）：
{
  "acwrPercent": 42,
  "acwrStatus": "OPTIMAL",
  "acwrStatusFeedback": "FEEDBACK_2",
  "dailyTrainingLoadAcute": 230,
  "maxTrainingLoadChronic": 328.5,
  "minTrainingLoadChronic": 175.2,
  "dailyTrainingLoadChronic": 219,
  "dailyAcuteChronicWorkloadRatio": 1.0
}
```

### 7.3 Hill Score DTO（list 元素、`get_hill_score("2026-04-27", "2026-06-25")`）

```json
{
  "userProfilePK": 96947785,
  "deviceId": 3620729386,
  "calendarDate": "2026-06-25",
  "strengthScore": 20,
  "enduranceScore": 35,
  "hillScoreClassificationId": 2,
  "overallScore": 44,
  "hillScoreFeedbackPhraseId": 42,
  "vo2Max": null,
  "vo2MaxPreciseValue": null,
  "primaryTrainingDevice": true
}
```

### 7.4 Endurance Score 結構

```
top-level keys: ['userProfilePK', 'startDate', 'endDate', 'avg', 'max', 'groupMap', 'enduranceScoreDTO']
avg: 6058 / max: 6193

groupMap: dict keyed by week-start date (週一)
  "2026-04-24": {
    "groupAverage": 6138,
    "groupMax": 6164,
    "enduranceContributorDTOList": [
      {"activityTypeId": null, "group": 6, "contribution": 8.05},
      {"activityTypeId": null, "group": 0, "contribution": 84.53},
      ...
    ]
  }

enduranceScoreDTO（latest snapshot）含 classification 區間（superior/elite 門檻）、contributors。
```

---

## 8. 驗證指令（fork session 動工前先跑這幾個）

```bash
cd ~/projects/garmin_mcp

# 1. 確認當前 branch + uv 環境
git branch --show-current  # 預期：fix/training-status-null-cycling 或新 feature branch
uv run python -c "from garminconnect import Garmin; print('ok')"

# 2. 確認 token 還有效
uv run python -c "
from garminconnect import Garmin
g = Garmin()
g.login('/Users/chenyucheng/.garminconnect')
print('username:', g.full_name)
"

# 3. Reproduce bug — 修補前應該只回 vo2_max、沒 CTL/ATL
uv run python -c "
from garminconnect import Garmin
import json
g = Garmin()
g.login('/Users/chenyucheng/.garminconnect')
data = g.get_training_status('2026-06-25')
ltsd = data['mostRecentTrainingStatus']['latestTrainingStatusData']
print('latestTrainingStatusData type:', type(ltsd).__name__)
print('keys (should be device IDs):', list(ltsd.keys()))
dev = list(ltsd.values())[0]
print('device data has acuteTrainingLoadDTO:', 'acuteTrainingLoadDTO' in dev)
print('atl:', dev['acuteTrainingLoadDTO']['dailyTrainingLoadAcute'])
print('ctl:', dev['acuteTrainingLoadDTO']['dailyTrainingLoadChronic'])
print('acwr:', dev['acuteTrainingLoadDTO']['dailyAcuteChronicWorkloadRatio'])
"

# 4. 修補後驗證 — MCP tool 應該回 CTL/ATL/ACWR
# 透過 ~/.claude.json 已指向本 fork、重啟 Claude Code session 後在主對話跑：
#   /mcp__garmin__get_training_load_trend start_date=2026-06-19 end_date=2026-06-25
# 預期回 7 個 entry、每個含 atl/ctl/acwr/training_status

# 5. 新工具 get_training_load_balance 驗證
uv run python -c "
from garminconnect import Garmin
import json
g = Garmin()
g.login('/Users/chenyucheng/.garminconnect')
data = g.get_training_status('2026-06-25')
mtlb = data['mostRecentTrainingLoadBalance']['metricsTrainingLoadBalanceDTOMap']
dev = list(mtlb.values())[0]
print(json.dumps(dev, indent=2))
print('feedback:', dev['trainingBalanceFeedbackPhrase'])
"

# 6. 跑 garmin_mcp 現有測試套件（確認沒 regression）
uv run pytest tests/ -x --tb=short
```

---

## 9. 跟 PR #173 的關係

- PR #173 修了 `get_training_status` 對 `mostRecentVO2Max.cycling: null` 的 crash（純跑者場景）
- PR #173 的 out-of-scope 段標了「device-iteration 缺失 + 過時 trainingStatusDTO key」——**那就是本檔 §3 處理的 bug**
- 建議：**先把 §3 修補做完、開 PR #174 give upstream**。新工具 §4（Load Focus）可以同 PR 或拆開、看 maintainer 喜好
- branch 命名建議：`fix/training-load-trend-device-keyed-data` 或 `feat/training-load-balance`（拆兩個 branch 可以分別 PR）

---

## 10. 已知 caveat（重要）

1. **HANDOFF caveat #3 還是要記得**：Garmin sub-section 對「沒做該運動的人」回 explicit `null`、不是 missing key。`.get(key, {})` 會回 None 而不是 default、crash 下一個 `.get()`。寫法：`.get(key) or {}`。修補 code 時請套用。
2. **VO2max 對 Eric 慣性高估 ~10 VDOT 點**（顯示 52、實際 PB-VDOT 42）。修補後 trend 工具看趨勢、別讓人看絕對值。可以在 docstring 加一行 caveat 提醒。
3. **device-keyed dict 設計可能延伸到其他 endpoint**——之後若發現別的 endpoint 也有類似結構，套用同 §3.1 pattern。

---

## 11. 完成後通知 Eric

修補 + 新工具完成後，請：
1. 在這份檔案最底下加一行 changelog：「YYYY-MM-DD: 完成 §3 修補 + §4 新工具」
2. 重啟 Claude Code session 讓 ~/.claude.json 重載 MCP（or 直接 reload garmin server）
3. Eric 的主 thread 用 `mcp__garmin__get_training_load_trend` 驗證能拿到 CTL/ATL/ACWR
4. 然後就能繼續 `~/Running/seasons/2026H2/metrics/` 的 design + weekly_review.py 工作

---

_本 spec 由 Claude session（2026-06-26）為 Eric 的 metrics 追蹤系統設計階段產出。_

---

## Changelog

- **2026-06-26**：完成 §3 修補（`fix/training-load-trend-device-keyed` branch、commit `e418e17`）+ §4 新工具 `get_training_load_balance`（commit `a2f0aba`）。`~/.claude.json` GARMIN_ENABLED_TOOLS 已加入 `get_training_load_balance`、重啟 Claude Code session 後即可在主對話用。Unit + integration 測試 385/385 過。
