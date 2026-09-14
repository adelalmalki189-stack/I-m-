# Data Specification — V0.1

هذا هو المستند الذي طلبته المواصفة الأصلية صراحة كخطوة تالية ("نكتب Data Specification دقيقة: أسماء الجداول والحقول، نوع كل field، مصدره، طريقة حسابه، وتوقيت تحديثه"). يغطي كل الكيانات المذكورة في Part 2 بالإضافة إلى الكيانات الجديدة التي أضافتها `OPERATIONAL_SPEC_V0.1.md`.

القاعدة العامة: **PostgreSQL** للبيانات الدائمة، **Redis** للبيانات الحية/المؤقتة (وفق القسم 17 من المواصفة الأصلية). أي حقل بدون مصدر واضح لا يُقبل في هذا المستند.

---

## 1. `instruments` (Underlying)

| الحقل | النوع | Nullable | المصدر | ملاحظات |
|---|---|---|---|---|
| symbol (PK) | varchar(10) | لا | IBKR contract details | |
| name | varchar(200) | نعم | IBKR/Barchart | |
| exchange | varchar(20) | لا | IBKR | |
| sector | varchar(50) | نعم | IBKR/Barchart reference data | يُستخدم في `market_context.sector_regime` |
| industry | varchar(100) | نعم | نفس المصدر | |
| is_active | boolean | لا | Scanner filters (القسم 2 من Methodology) | false إذا فشل Initial Filters |
| created_at / updated_at | timestamptz | لا | النظام | |

**التخزين**: PostgreSQL (dimension table، تحديث نادر).

---

## 2. `underlying_quotes` (Live Tick)

| الحقل | النوع | المصدر | التحديث |
|---|---|---|---|
| symbol | varchar(10) | IBKR streaming | كل tick |
| timestamp | timestamptz | IBKR (exchange timestamp، ليس وقت الاستلام) | لحظي |
| bid / ask / last | numeric(12,4) | IBKR | لحظي |
| volume | bigint | IBKR (تراكمي لليوم) | لحظي |
| data_status | enum(VALID, STALE, INVALID) | محسوب (§2 من Operational Spec) | عند كل تحديث |
| source | varchar(20) | ثابت حسب Provider | |

**التخزين**: **Redis فقط** (آخر قيمة لكل رمز) — لا حاجة لتاريخ كل tick في Postgres؛ التاريخ المطلوب فعلياً هو الشموع (`candles_5m`).

---

## 3. `candles_5m`

| الحقل | النوع | المصدر | طريقة الحساب |
|---|---|---|---|
| symbol | varchar(10) | | |
| timestamp | timestamptz | بداية الفترة (مثلاً 09:30) | يُحدَّد عند إغلاق الفترة فقط، لا يُكتب سجل جزئي |
| open/high/low/close | numeric(12,4) | مُجمَّع من ticks IBKR ضمن الفترة | OHLC قياسي |
| volume | bigint | مجموع أحجام الـticks بالفترة | |
| vwap | numeric(12,4) | محسوب — §1 `CALCULATION_SPEC_V0.1.md` | تراكمي منذ بداية الجلسة |
| is_complete | boolean | النظام | **false أثناء التكوّن، true فقط بعد الإغلاق الفعلي** — الحقل الذي يفرض قاعدة "لا حساب على شمعة غير مكتملة" |

PK: `(symbol, timestamp)`. **التخزين**: PostgreSQL (تاريخي، يُقرأ كثيراً لحساب RVOL/EMA baseline).

---

## 4. `technical_features`

كيان منفصل عمداً عن `candles_5m` (المواصفة الأصلية أكّدت: "كل Feature يجب أن يكون قابلاً لإعادة الحساب" وليس فقط `signal=bullish` بدون سبب).

| الحقل | النوع | المصدر/الحساب |
|---|---|---|
| symbol, timestamp (PK, FK → candles_5m) | | |
| ema20, ema50 | numeric(12,4) | §2 Calculation Spec |
| ema20_slope | enum(POSITIVE, NEGATIVE, FLAT) | §2 |
| rvol | numeric(6,3) nullable | §3 — null إذا INVALID (warm-up) |
| atr14 | numeric(12,4) | §4 |
| pdh, pdl | numeric(12,4) | أعلى/أدنى سعر اليوم السابق (من `candles_5m` مجمّعة) |
| day_high, day_low | numeric(12,4) | تراكمي من بداية الجلسة الحالية |
| swing_high, swing_low | numeric(12,4) nullable | §5.1 — null إذا لم يُؤكَّد بعد |
| swing_confirmed_at | timestamptz nullable | لحظة التأكيد الفعلية (بتأخير k شمعة — §5.2) |
| market_structure | enum(BULLISH, BEARISH, NEUTRAL) | §5.3 |
| calc_version | varchar(10) | رقم إصدار `strategy_parameters` وقت الحساب — **إلزامي**، بدونه لا يمكن معرفة أي نسخة صيغة أنتجت هذا الرقم |

**التخزين**: PostgreSQL. Redis لآخر صف فقط لكل رمز (قراءة سريعة من Setup Engine).

---

## 5. `market_context`

| الحقل | النوع | المصدر |
|---|---|---|
| timestamp (PK) | timestamptz | |
| spy_price, qqq_price, vix_value | numeric | IBKR |
| spy_regime, qqq_regime, vix_regime | enum | محسوب من نفس منطق §1 Methodology (VWAP/EMA على SPY/QQQ نفسها) |
| sector_symbol, sector_regime | varchar / enum | ETF قطاعي مناظر (مثال: XLK للتكنولوجيا) |
| overall_market_regime | enum(SUPPORTIVE, NEUTRAL, RESTRICTIVE) | دمج القواعد أعلاه — يحتاج جدول تركيب صريح لاحقاً (مثال: SPY+QQQ Bullish و VIX Normal → SUPPORTIVE) |

**التخزين**: Redis (أحدث قيمة، يُقرأ من كل Signal) + PostgreSQL (أرشيف كل 5 دقائق ضمن `TradeSnapshot`).

---

## 6. `options_contracts`

| الحقل | النوع | المصدر |
|---|---|---|
| contract_id (PK) | varchar | IBKR conid أو مكافئه |
| symbol (underlying) | varchar(10) | |
| option_type | enum(CALL, PUT) | |
| strike | numeric(12,4) | IBKR contract details |
| expiration | date | IBKR |
| dte | int | محسوب: `expiration - current_trading_date` (عبر `trading_calendar`, ليس تقويم ميلادي بسيط) |
| stale_after_split | boolean | يُفعَّل عند اكتشاف corporate action (§4 Operational Spec) |

**التخزين**: PostgreSQL.

---

## 7. `option_quotes`

| الحقل | النوع | المصدر |
|---|---|---|
| contract_id, timestamp (PK) | | |
| bid, ask, last | numeric(12,4) | IBKR |
| spread_pct | numeric(6,3) | محسوب: `(ask-bid)/ask*100` |
| volume, open_interest | bigint | IBKR |
| iv | numeric(6,4) | IBKR/broker greeks feed |
| iv_rank | numeric(5,2) nullable | محسوب من `iv_history` (252 يوم تداول) — يتطلب سلسلة IV تاريخية منفصلة (`iv_history`، غير موجودة في المواصفة الأصلية، أُضيفت هنا) |
| delta, gamma, theta, vega | numeric(8,5) nullable | IBKR greeks (gamma تُخزَّن لكن لا تدخل V0.1 scoring — §7 من المواصفة الأصلية) |
| data_status | enum | §2 Operational Spec (10 ثوانٍ threshold) |

**التخزين**: Redis (أحدث قيمة لكل عقد نشط) + PostgreSQL (فقط عند لحظة `TradeSnapshot`، ليس كل tick — سيولة الخيارات لا تبرر تخزين تاريخي كامل في V0.1).

---

## 8. `market_events`

| الحقل | النوع | المصدر |
|---|---|---|
| event_id (PK) | uuid | |
| symbol | varchar(10) nullable | null = حدث على مستوى السوق كله (مثل FOMC) |
| event_type | enum(EARNINGS, CPI, FOMC, NFP, FED_SPEECH, ECONOMIC_DATA) | مصدر تقويم اقتصادي خارجي (يُحدَّد لاحقاً) |
| event_time | timestamptz | |
| severity | enum(LOW, MEDIUM, HIGH, EXTREME) | قواعد §21 Methodology (مثال: earnings خلال 24 ساعة → HIGH/EXTREME) |
| source | varchar(50) | |

**التخزين**: PostgreSQL. يُستهلك مباشرة في §3.2 (استثناء أيام RVOL) وفي Event Risk Gate.

---

## 9. `trading_calendar`

كما في `OPERATIONAL_SPEC_V0.1.md` §1 — `date (PK), session_type, market_open, market_close, source`.

---

## 10. `strategy_parameters`

| الحقل | النوع | ملاحظات |
|---|---|---|
| version (PK) | varchar(10) | مثال "v0.1.3" |
| parameters | jsonb | كل الثوابت في Calculation Spec (rvol_threshold, atr_multipliers, delta_range...) كـJSON واحد موصوف |
| effective_from | timestamptz | |
| notes | text | سبب التغيير (لتفادي "الاختبار عدّل نفسه" من Testing Spec §5) |

**قاعدة**: كل صف في `technical_features`, `signals`, `signal_snapshots` يحمل `calc_version` يشير لهذا الجدول — لا استثناء.

---

## 11. `signals`

| الحقل | النوع | المصدر |
|---|---|---|
| signal_id (PK) | uuid | |
| timestamp | timestamptz | لحظة التوليد |
| symbol, direction | | |
| setup_score, contract_score, final_score | numeric(5,2) | محسوبة — §9 Calculation Spec + §22 Methodology |
| status | enum(WAIT, READY, ENTRY, INVALIDATED, EXPIRED) | Timing Engine |
| entry_zone_low, entry_zone_high | numeric(12,4) | §7 Calculation Spec |
| invalidation | numeric(12,4) | §8.1 |
| target | numeric(12,4) | §8.2 |
| risk_reward | numeric(5,2) | §8.3 |
| event_risk | enum | |
| calc_version | varchar(10) FK | |

---

## 12. `signal_snapshots` (TradeSnapshot)

نسخة كاملة مجمَّدة وقت القرار — **كل** حقل هنا هو نسخة (copy)، وليس مرجع (foreign key) يتغيّر مع الزمن، لأن الهدف تحديداً هو تجميد اللحظة:

| الحقل | النوع |
|---|---|
| signal_id (FK) | |
| underlying_price, vwap, ema20, ema50, rvol | numeric (منسوخة، ليست مرجعاً) |
| spy_state, qqq_state, vix_value, sector_state | (منسوخة من market_context) |
| contract_id, strike, expiration, dte | |
| bid, ask, last, volume, oi, iv, delta | (منسوخة من option_quotes) |
| calc_version | |

---

## 13. `risk_plans`

| الحقل | النوع | الحساب |
|---|---|---|
| signal_id (FK) | | |
| account_size, risk_percent | | إعداد المستخدم |
| max_risk | numeric | `account_size * risk_percent` |
| entry_price, stop_price, target_price | numeric | من `signals` |
| risk_per_contract | numeric | `(entry_price - stop_price) * 100` |
| recommended_quantity | int | `floor(max_risk / risk_per_contract)`، **0 إذا الناتج < 1 → NO TRADE** (اختبار وحدة إلزامي — Testing Spec §3) |
| max_loss, potential_profit, r_multiple | numeric | مشتقة |

---

## 14. `portfolio_risk_config`

كما في `OPERATIONAL_SPEC_V0.1.md` §5 — سجل واحد (أو منسوخ حسب المستخدم لاحقاً) بالحقول: `max_concurrent_signals, max_concurrent_per_sector, daily_max_loss_pct, max_total_deployed_risk_pct, enforce_pdt_limit`.

---

## 15. `trades` (Trade Journal)

كما في المواصفة الأصلية القسم 13 حرفياً: `trade_id, signal_id, user_entry_time, user_entry_price, quantity, user_exit_time, user_exit_price, realized_pnl, max_profit, max_loss, exit_reason`. يُدخَل يدوياً من المستخدم عبر الواجهة (V0.1 بدون تنفيذ آلي).

---

## 16. `outcomes`

| الحقل | النوع | الحساب |
|---|---|---|
| signal_id (FK) | | |
| offset_minutes | int | 5, 10, 15, 30, 60 (صف منفصل لكل نقطة زمنية) |
| underlying_price_at_offset | numeric | من `candles_5m` |
| option_price_at_offset | numeric nullable | من `option_quotes` إن توفرت |
| mfe, mae | numeric | §14 Methodology |

---

## 17. `notification_log`

كما في `OPERATIONAL_SPEC_V0.1.md` §6.

---

## 18. `iv_history` (كيان جديد — متطلب اكتُشف أثناء §9.5 Calculation Spec)

| الحقل | النوع | ملاحظة |
|---|---|---|
| symbol, date (PK) | | |
| atm_iv | numeric(6,4) | متوسط IV لأقرب عقد ATM بأقرب تاريخ انتهاء سيولته كافية |
| source | varchar(20) | |

بدون هذا الجدول، لا يمكن حساب `iv_rank` في §9.5 من Calculation Spec — كان مفقوداً تماماً من تصميم المواصفة الأصلية.

---

## 19. سياسة الاحتفاظ بالبيانات (Retention) — غير مذكورة أصلاً

| الجدول | مدة الاحتفاظ المقترحة V0.1 |
|---|---|
| `underlying_quotes` (Redis) | لا يُحفظ فعلياً — لحظي فقط |
| `candles_5m` | سنتان (كافية لـRVOL baseline ولإعادة تشغيل Backtest Plan) |
| `option_quotes` (Postgres، snapshots فقط) | تُحفظ دائماً (مرتبطة بـTradeSnapshot، لا تُحذف) |
| `signals` / `signal_snapshots` / `trades` / `outcomes` | تُحفظ دائماً (هي جوهر قيمة المشروع طويل المدى) |
| `notification_log` | 90 يوماً (تدقيق تشغيلي فقط) |

---

## 20. ملخص المصادر (Source Traceability)

كل حقل أعلاه يقع في واحدة من ثلاث فئات، ويجب أن يحمل هذا التصنيف صراحة في الكود لاحقاً:

```
RAW        → منسوخ مباشرة من Provider بدون تعديل (bid, ask, volume...)
DERIVED    → محسوب من RAW بصيغة موثقة في CALCULATION_SPEC_V0.1.md (vwap, rvol, atr14...)
CONFIG     → مدخل من المستخدم أو ثابت نظام (risk_percent, calc_version...)
```

هذا التصنيف هو ما يمنع لاحقاً الخلط بين "بيانات حقيقية" و"نتيجة حساب" و"إعداد يدوي" عند تدقيق أي Signal قديم.
