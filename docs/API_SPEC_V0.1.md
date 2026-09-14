# API Specification — V0.1

الخطوة الثانية التي حددتها المواصفة الأصلية بعد Data Specification. يفترض **FastAPI** كما رشّح القسم 18 من المواصفة الأصلية. مستخدم واحد (single-user) في V0.1 — انظر `OPERATIONAL_SPEC_V0.1.md` §7.

مبدأ التصميم: الواجهة (Frontend) **لا تحسب شيئاً بنفسها** — كل رقم معروض جاء جاهزاً من الـBackend، اتساقاً مع مبدأ فصل Data/Logic/Decision/Explanation (§20 من المواصفة الأصلية).

---

## 0. المصادقة (Auth)

```
POST /auth/login          → { token }     (single-user، توكن طويل الأمد محلي)
```
كل الطلبات التالية تحمل `Authorization: Bearer <token>`. لا نظام أدوار (roles) في V0.1 — مستخدم واحد فقط، موثّق كقرار صريح لا نقص.

---

## 1. Market Context

```
GET /market/context
```
Response:
```json
{
  "timestamp": "...",
  "spy": {"price": 512.3, "regime": "BULLISH"},
  "qqq": {"price": 438.1, "regime": "BULLISH"},
  "vix": {"value": 14.2, "regime": "NORMAL"},
  "overall_market_regime": "SUPPORTIVE"
}
```
يُغذّي شريط "MARKET" أعلى الـDashboard (§19 من المواصفة الأصلية).

---

## 2. Scanner / Watchlist

```
GET /scanner/candidates?limit=50
```
قائمة الرموز التي اجتازت Initial Filters (§2 Methodology) قبل حتى محاولة توليد Signal — مفيدة لعرض "ماذا يراقب النظام الآن" بشفافية.

```
GET  /scanner/config
PUT  /scanner/config      { min_price, min_avg_volume, universe_symbols[] }
```

---

## 3. Signals (الجوهر)

```
GET /signals?status=ENTRY,READY&limit=20
```
القائمة المرتبة (§23 Methodology — Ranking)، كل عنصر:
```json
{
  "signal_id": "...",
  "symbol": "NVDA",
  "direction": "CALL",
  "final_score": 93,
  "status": "ENTRY",
  "risk_reward": 2.3
}
```

```
GET /signals/{signal_id}
```
التفاصيل الكاملة — تطابق تماماً "النموذج النهائي" في §24 من المواصفة الأصلية (Setup, Underlying price, Entry zone, Invalidation, Target, R:R, Contract المقترح, Risk, Market Context, Event Risk, Status).

```
GET /signals/{signal_id}/why
```
**"WHY THIS TRADE?"** (§19 الأصلية) — تفصيل كل شرط اجتازه/لم يجتزه Signal، بالقيم الخام:
```json
{
  "conditions": [
    {"rule": "price_above_vwap", "value": true},
    {"rule": "rvol", "value": 1.72, "threshold": 1.30, "passed": true},
    {"rule": "structure_break", "value": true},
    {"rule": "spread_pct", "value": 2.1, "threshold": 5.0, "passed": true}
  ],
  "hard_gates": {
    "market": "PASS", "setup": "PASS", "timing": "PASS",
    "liquidity": "PASS", "risk": "PASS", "rr": "PASS",
    "event_risk": "PASS", "data_quality": "PASS"
  },
  "calc_version": "v0.1.3"
}
```
هذا الـendpoint هو التطبيق المباشر لمبدأ "قابل للتدقيق" — بدون أي AI، فقط بيانات Logic الخام.

```
GET /signals/{signal_id}/snapshot
```
يُعيد `TradeSnapshot` كاملة (§8 Data Spec) — للمراجعة اللاحقة "هل كانت التوصية جيدة بالبيانات المتاحة وقتها؟".

```
GET /signals/{signal_id}/contracts
```
كل العقود المرشَّحة مع الـScore (وليس فقط العقد الفائز) — يطابق مثال §15 الأصلية (Contract A/B/C).

```
GET /signals/{signal_id}/outcome
```
بيانات MFE/MAE عند +5/10/15/30/60 دقيقة (§14 الأصلية)، تُملأ تدريجياً بعد التوليد.

---

## 4. Risk

```
GET /risk/config
PUT /risk/config     { account_size, risk_percent }
```
```
GET /risk/portfolio-status
```
```json
{
  "open_signals_count": 2,
  "max_concurrent_signals": 3,
  "total_deployed_risk_pct": 3.1,
  "daily_realized_pnl": -45.0,
  "daily_max_loss_hit": false
}
```
يُغذّي أي بانر تحذيري في الواجهة (Circuit Breaker اليومي — `OPERATIONAL_SPEC_V0.1.md` §5).

---

## 5. Trade Journal

```
POST /trades           { signal_id, entry_time, entry_price, quantity }
PUT  /trades/{trade_id} { exit_time, exit_price, exit_reason }
GET  /trades?from=...&to=...
GET  /trades/{trade_id}
```
تسجيل يدوي بالكامل — النظام لا يفترض أبداً أن المستخدم نفّذ الصفقة فعلياً (V0.1 = Decision Support، ليس Auto Trading).

---

## 6. Events

```
GET /events/upcoming?symbol=NVDA
```
تقويم `market_events` القادمة (Earnings/CPI/FOMC...) لعرض تحذير "⚠️ Event Risk" الاستباقي في الواجهة قبل ظهور أي Signal حتى.

---

## 7. Strategy Parameters (شفافية النسخ)

```
GET /strategy-parameters              # النسخة الحالية النشطة
GET /strategy-parameters/{version}    # نسخة تاريخية محددة، لمقارنة Signal قديم بمعاييره وقتها
```
Read-only عبر API في V0.1 — التعديل يتم عبر ملف/migration مباشر وليس عبر الواجهة، لتفادي تعديل عرضي أثناء التداول الحي.

---

## 8. System Health

```
GET /system/status
```
```json
{
  "data_feed_status": "OK",           
  "degraded_mode": false,
  "last_scan_cycle_ms": 4200,
  "last_scan_at": "..."
}
```
يعكس مباشرة `OPERATIONAL_SPEC_V0.1.md` §3 (Circuit Breaker) — الواجهة تعرض بانر أحمر فوراً إذا `degraded_mode = true`.

---

## 9. Real-Time Updates (WebSocket)

```
WS /ws/live-updates
```
بما أن حالة `ENTRY` قد تكون قصيرة العمر، الاعتماد على HTTP polling وحده غير كافٍ للتوقيت الحرج. رسائل push:

```json
{"type": "SIGNAL_STATUS_CHANGE", "signal_id": "...", "new_status": "ENTRY"}
{"type": "MARKET_CONTEXT_UPDATE", "overall_market_regime": "RESTRICTIVE"}
{"type": "SYSTEM_DEGRADED", "degraded_mode": true}
```
هذا القناة نفسها التي تُشغّل التنبيه الفوري (`NotificationLog` — Operational Spec §6) في طبقة الواجهة (صوت/Push في المتصفح)؛ قنوات SMS/Webhook خارجية (إن أُضيفت لاحقاً) تشترك بنفس الحدث من جهة الـBackend.

---

## 10. أخطاء موحّدة (Error Shape)

كل خطأ يُعاد بشكل موحّد لتسهيل التعامل من الواجهة:
```json
{"error_code": "DATA_STALE", "message": "...", "details": {...}}
```
أكواد أساسية متوقعة: `DATA_STALE`, `DATA_INVALID`, `HARD_GATE_FAILED`, `PORTFOLIO_LIMIT_REACHED`, `MARKET_CLOSED`.

---

## ملاحظة ختامية

هذا الإصدار من API Specification يغطي فقط V0.1 Scope المحدد صراحة في المواصفة الأصلية (§22: لا Auto Trading، لا Multi-leg، لا Portfolio Optimization) — لا توجد endpoints للتنفيذ الآلي أو استراتيجيات متعددة الأرجل عمداً، تماشياً مع القرار الأصلي بعدم توسيع النطاق قبل إثبات V0.1.
