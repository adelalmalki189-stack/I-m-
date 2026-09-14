# Implementation Plan — V0.1

خطة تنفيذ مرحلية لبناء المشروع فعلياً، بالاعتماد على المستندات الست الجاهزة:
`CALCULATION_SPEC` · `DATA_SPEC` · `OPERATIONAL_SPEC` · `TESTING_SPEC` · `API_SPEC` · `BACKTEST_PLAN`.

المبدأ: كل مرحلة تُبنى فوق سابقتها وتُختبر قبل الانتقال للتالية — لا قفز مباشر لتوصيات حية بأموال حقيقية. الترتيب يتبع الرسم في §22-23 من المواصفة الأصلية (Real Data → Feature Engine → ... → Trade Proposal).

---

## المرحلة 0 — الأساس (Foundation)

**الهدف**: بنية المشروع + قاعدة البيانات + طبقة الاتصال بالبيانات، بدون أي منطق تداول بعد.

- إعداد Modular Monolith (FastAPI) بهيكلة modules منفصلة تطابق أسماء المحركات في `API_SPEC_V0.1.md`.
- إنشاء كل الجداول في `DATA_SPEC_V0.1.md` عبر migrations (Alembic).
- تطبيق `DataProvider` interface + `IBKRDataProvider` (اتصال Paper account أولاً وليس Live) + `FakeDataProvider` (اختبارات فقط، §2 `TESTING_SPEC_V0.1.md`).
- تفعيل فحص CI "No Mock Data in Production" (نفس القسم) من أول يوم — أسهل تطبيقه من البداية من إضافته لاحقاً.
- تعبئة `trading_calendar` من مصدر تقويم NYSE.

**معيار الإنجاز**: النظام يستقبل شموع 5 دقائق حقيقية من IBKR Paper ويخزّنها في `candles_5m` بشكل صحيح، مع اختبارات وحدة خضراء على DataProvider.

---

## المرحلة 1 — Feature Engine

**الهدف**: كل الصيغ في `CALCULATION_SPEC_V0.1.md` §1-5 (VWAP, EMA/Slope, RVOL, ATR, Swing/Market Structure).

- تُبنى كدوال نقية (pure functions) قابلة للاستدعاء من Live ومن Replay Mode لاحقاً (شرط `BACKTEST_PLAN_V0.1.md` §2).
- كل صيغة تُطوَّر مع اختبار الوحدة المقابل لها في `TESTING_SPEC_V0.1.md` §3 في نفس الـPR — لا صيغة بدون اختبار.
- الانتباه الخاص لـ RVOL warm-up وSwing confirmation lag — هذه أكثر نقطتين عرضة لأخطاء look-ahead صامتة.

**معيار الإنجاز**: `technical_features` تُملأ صحيحة لعينة تاريخية معروفة يدوياً (يُتحقق حسابياً لا بصرياً فقط) + Golden Scenario الأولى (§5 Testing Spec) تمر.

---

## المرحلة 2 — Setup Engine + Timing Engine

**الهدف**: تحويل الـFeatures إلى CALL/PUT Candidate ثم حالة WAIT/READY/ENTRY/INVALIDATED.

- Breakout (§6)، Entry Zone/Max Extension (§7)، Invalidation (§8.1) من Calculation Spec.
- State machine للـTiming Engine بأربع الحالات فقط، مع اختبارات الانتقال الحدّية (§3 Testing Spec — إغلاق مساوٍ تماماً للمستوى، إلخ).
- **بدون** ربط بالخيارات بعد — الخرج في هذه المرحلة هو Signal على مستوى Underlying فقط.

**معيار الإنجاز**: `signals` تُنشأ بحالات صحيحة على بيانات Replay تاريخية، وMarket Context (SPY/QQQ/VIX regime) يُحسب ويُدمج كشرط سياقي.

---

## المرحلة 3 — Options Chain + Contract Engine

**الهدف**: ربط كل Signal بعقود فعلية وتقييمها.

- استيعاب `options_contracts` / `option_quotes` من IBKR (Delta/IV/Greeks).
- Hard Gates: Liquidity (spread <=5%)، OI/Volume (§12-13 Methodology)، Delta/DTE range (§10-11).
- دوال تطبيع الدرجات (§9 Calculation Spec) لكل عامل، بما فيها `iv_rank` — يتطلب هذا تشغيل تجميع `iv_history` يومياً بالتوازي (اعتماد جديد اكتُشف في Data Spec §18، يجب أن يبدأ التجميع من هذه المرحلة حتى تتوفر 252 يوماً لاحقاً).

**معيار الإنجاز**: `GET /signals/{id}/contracts` يُعيد عقوداً مرتبة فعلياً مطابقة لمثال §15 من المواصفة الأصلية (Contract A/B/C).

---

## المرحلة 4 — Risk Engine + Scoring + Hard Gate النهائي

**الهدف**: تحويل الـSignal + Contract المختار إلى توصية كاملة قابلة للعرض.

- `RiskPlan` (§13 Data Spec) بما فيها حافة `recommended_quantity = 0 → NO TRADE`.
- Final Opportunity Score (§22 Methodology) بأوزانه ودوال التطبيع.
- Hard Gate النهائي (§24 Methodology) — تجميع كل الـgates السابقة (Market/Setup/Timing/Liquidity/Risk/RR/EventRisk/DataQuality) في قرار واحد.
- Portfolio-level risk (`portfolio_risk_config` — §5 Operational Spec): max concurrent, daily loss limit.

**معيار الإنجاز**: النموذج الكامل في §24 من المواصفة الأصلية (NVDA CALL Score 93/100...) يظهر فعلياً من طرف لطرف على بيانات Replay.

---

## المرحلة 5 — API + Dashboard الأساسي

**الهدف**: كل endpoints في `API_SPEC_V0.1.md`، وواجهة تعرض §19 من المواصفة الأصلية (Market bar, Top Opportunities, Why This Trade).

- WebSocket للتحديثات اللحظية (حالة ENTRY تحديداً).
- شاشة `/system/status` تعكس Degraded Mode فوراً عند محاكاة انقطاع (Circuit Breaker — Operational Spec §3).
- Trade Journal (تسجيل يدوي) — بدون أي تنفيذ آلي.

**معيار الإنجاز**: مستخدم يفتح Dashboard، يرى فرصة، يضغط "WHY THIS TRADE؟" ويرى كل الأسباب الخام، ويسجل صفقة يدوياً بعد تنفيذها في IBKR.

---

## المرحلة 6 — Alerting + Outcome Tracking

- `NotificationLog` + قناة تنبيه فعلية (Push/Sound كحد أدنى — §6 Operational Spec) عند دخول حالة ENTRY.
- Outcome Tracker: يسجّل MFE/MAE عند +5/10/15/30/60 دقيقة تلقائياً لكل Signal (§14 Methodology).

**معيار الإنجاز**: تنبيه يصل فعلياً (اختبار end-to-end وليس سجل قاعدة بيانات فقط)، وبيانات outcome تتراكم تلقائياً بدون تدخل يدوي.

---

## المرحلة 7 — Rule Sanity Check (Backtest خفيف)

تُنفَّذ خطة `BACKTEST_PLAN_V0.1.md` كاملة الآن، **بعد** اكتمال كل المحركات أعلاه (لأنها تعيد استخدام نفس الكود حرفياً بوضع Replay).

**معيار الإنجاز**: قائمة التحقق اليدوية في §8 من `TESTING_SPEC_V0.1.md` بالكامل ✅ — لا انتقال لما بعدها بدون كل بند.

---

## المرحلة 8 — Paper Trading

- تشغيل حي على حساب Paper فقط لفترة تراكم بيانات كافية (مقترح: 4-6 أسابيع تداول فعلي) قبل أي نقاش عن Live.
- مراقبة يومية لـ `system/status` وسجل `outcomes` — أي انحراف حاد عن توقعات Rule Sanity Check يُوقف الإضافة لـLive ويُعاد فحص Calculation Spec.

---

## جدول التبعيات (Dependency Summary)

```
المرحلة 0 (Foundation)
      │
      ▼
المرحلة 1 (Feature Engine) ──────────────┐
      │                                   │
      ▼                                   │ (iv_history تبدأ بالتوازي
المرحلة 2 (Setup + Timing)                │  من المرحلة 3 فصاعداً)
      │                                   │
      ▼                                   │
المرحلة 3 (Options + Contract) ◄──────────┘
      │
      ▼
المرحلة 4 (Risk + Scoring + Hard Gate)
      │
      ▼
المرحلة 5 (API + Dashboard)
      │
      ▼
المرحلة 6 (Alerting + Outcome)
      │
      ▼
المرحلة 7 (Rule Sanity Check) ◄── يتطلب كل ما سبق مكتملاً
      │
      ▼
المرحلة 8 (Paper Trading)
```

**قاعدة صارمة تحكم كل الخطة**: لا مرحلة تُعتبر "منتهية" بدون اختبارات الوحدة الخاصة بها من `TESTING_SPEC_V0.1.md` خضراء — الانتقال المبكر بين المراحل بدون اختبار هو بالضبط النمط الذي أدى لمشاكل مشاريع سابقة (مذكور في §22 من المواصفة الأصلية كسبب لتحديد النطاق بصرامة).
