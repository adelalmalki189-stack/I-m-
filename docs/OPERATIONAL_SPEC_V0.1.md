# Operational Specification — V0.1

هذا المستند يغطي الفجوات التشغيلية التي لم تُذكر إطلاقاً في المواصفة الأصلية (Part 2)، وهي ضرورية لأن النظام يعمل ضد بيانات حية أثناء ساعات تداول فعلية وليس في بيئة اختبار ثابتة.

---

## 1. Trading Calendar

جدول دائم في PostgreSQL:

```
TradingCalendar
----------------
date
session_type      # FULL | HALF | CLOSED
market_open       # NULL إذا CLOSED
market_close       # NULL إذا CLOSED
source             # مثلاً NYSE calendar feed
```

- يُحدَّث دورياً (مثلاً شهرياً) من مصدر موثوق لتقويم NYSE.
- **كل** حساب زمني في النظام (RVOL baseline، بداية/نهاية الجلسة، End-of-session rule) يجب أن يستعلم هذا الجدول بدل افتراض "كل الأيام متماثلة".
- الأيام `HALF` تُستثنى افتراضياً من baseline الخاص بـRVOL (انظر `CALCULATION_SPEC_V0.1.md` §3.2) لأنها تشوّه المتوسط.
- الأيام `CLOSED` (عطل رسمية) لا يعمل فيها الـScanner إطلاقاً.

---

## 2. Data Staleness Thresholds

المواصفة الأصلية ذكرت `data_status = STALE` كمفهوم لكن بدون عتبات زمنية. القيم الابتدائية:

```
Underlying Quote (bid/ask/last):   STALE إذا لم تُحدَّث خلال 5 ثوانٍ أثناء ساعات التداول
Option Quote (bid/ask):            STALE إذا لم تُحدَّث خلال 10 ثوانٍ (سيولة أقل من الأسهم)
5m Candle:                          STALE إذا لم تصل خلال 15 ثانية بعد وقت إغلاقها المتوقع
MarketContext (SPY/QQQ/VIX):        STALE إذا لم تُحدَّث خلال 10 ثوانٍ
```

قاعدة صارمة: **أي Signal يعتمد على مُدخل STALE أو INVALID يفشل تلقائياً في Data Quality Gate → NO TRADE**، بغض النظر عن قوة بقية الشروط.

---

## 3. اتصال IBKR: إعادة الاتصال وحدود المعدل (Rate Limits)

- **Reconnection**: عند انقطاع الاتصال، إعادة محاولة بـ exponential backoff (مثلاً 2s, 4s, 8s, 16s, 30s سقف)، مع إعادة الاشتراك التلقائي في كل الرموز التي كانت نشطة قبل الانقطاع.
- **Pacing / Rate Limits**: IBKR TWS API تفرض حدوداً معروفة (مثال: ~50 رسالة/ثانية، وحد أقصى للاشتراكات المتزامنة في بيانات الأسهم/الخيارات في آن واحد). يجب على `Scanner`:
  - تقسيم قائمة الرموز إلى دفعات (batches) تدور ضمن نافذة زمنية (rotating subscription window) بدل الاشتراك بكل شيء دفعة واحدة.
  - عدم تجاوز حد الاشتراكات المتزامنة الموثق من IBKR لحساب المستخدم (يختلف حسب نوع الاشتراك/الحساب).
- **Circuit Breaker**: إذا انقطعت بيانات مصدر رئيسي (IBKR) لأكثر من عتبة زمنية (مثلاً 60 ثانية) أثناء ساعات التداول:
  ```
  System → DEGRADED MODE
  - إيقاف توليد Signals جديدة
  - عرض تحذير واضح في Dashboard: "⚠️ Data Feed Degraded"
  - الصفقات المفتوحة تبقى معروضة (آخر بيانات معروفة) لكن بدون قرارات جديدة تلقائية
  ```

---

## 4. Corporate Actions

- الانقسامات (splits) وتوزيعات الأرباح غير المعتادة تؤثر مباشرة على strikes الخيارات والتاريخ السعري.
- عند اكتشاف split (عبر تغذية corporate actions أو ملاحظة قفزة سعرية غير منطقية):
  1. تعديل السلاسل التاريخية للسعر/الحجم بأثر رجعي (adjusted).
  2. وضع علامة على كل `OptionContract` القديم للرمز كـ `stale_after_split = true`.
  3. **تعليق توليد إشارات جديدة على هذا الرمز ليوم تداول واحد على الأقل** بعد الحدث حتى تُتحقق سلامة البيانات الجديدة.

---

## 5. مخاطر على مستوى المحفظة (Portfolio-Level Risk)

`RiskPlan` في المواصفة الأصلية يحسب المخاطرة **لكل صفقة منفردة فقط**. نضيف طبقة على مستوى الحساب:

```
PortfolioRiskConfig
----------------
max_concurrent_signals          # افتراضي 3
max_concurrent_per_sector       # افتراضي 2  (لتخفيف correlation risk)
daily_max_loss_pct              # افتراضي 3% من الحساب
max_total_deployed_risk_pct     # افتراضي 5% من الحساب (مجموع كل risk_per_contract × quantity المفتوحة)
```

قواعد الفرض:

```
IF open_signals_count >= max_concurrent_signals:
    → New signals تُعرض لكن بحالة "Blocked: Max Concurrent Trades"

IF realized_pnl_today <= -(account_size * daily_max_loss_pct):
    → إيقاف توليد أي ENTRY جديد لبقية الجلسة (Circuit Breaker يومي)
    → عرض بانر واضح بالسبب

IF sum(open_risk) + new_trade_risk > account_size * max_total_deployed_risk_pct:
    → NO TRADE لهذه الفرصة تحديداً حتى تُغلق صفقة أخرى
```

---

## 6. التنبيه الفوري (Alerting)

بما أن التنفيذ يدوي وحالة `ENTRY` قد تكون قصيرة العمر، الاعتماد فقط على فتح الـDashboard غير كافٍ.

```
NotificationLog
----------------
notification_id
signal_id
channel          # PUSH | SOUND | WEBHOOK | SMS (V0.1: PUSH + SOUND كحد أدنى)
sent_at
delivery_status  # SENT | FAILED
```

قاعدة: تنبيه يُرسَل **فقط** عند انتقال Signal إلى حالة `ENTRY` (وليس عند WAIT/READY لتفادي إغراق المستخدم بإشعارات). عند `INVALIDATED` بعد أن كانت `ENTRY`، يُرسل تنبيه ثانٍ بأولوية أقل.

---

## 7. الأمن (Security)

- مفاتيح/بيانات اعتماد IBKR والمصادر الأخرى تُحفظ في متغيرات بيئة أو secret manager (مثل Vault/AWS Secrets Manager) — **ليس** في قاعدة البيانات ولا في الكود.
- الاتصال بين الخدمات الداخلية (إن وُجد أكثر من عملية) عبر TLS.
- V0.1 نظام مستخدم واحد (single-user) — يُوثَّق هذا صراحة كقرار، مع تأجيل أي نظام صلاحيات متعدد المستخدمين حقيقي (roles/permissions) إلى ما بعد V0.1.
- لا حاجة لتخزين بيانات شخصية حساسة (PII) في V0.1 غير إعدادات حجم الحساب ونسبة المخاطرة.

---

## 8. حدود الحساب في القيود التنظيمية (Regulatory Awareness)

- قاعدة **Pattern Day Trader (PDT)**: الحسابات الأمريكية أقل من $25,000 محدودة بعدد الصفقات اليومية خلال 5 أيام تداول متتالية. هذا يجب أن يدخل كقيد إضافي اختياري في `PortfolioRiskConfig` (`enforce_pdt_limit: bool`) عندما يكون حجم الحساب أقل من الحد.
- النظام يُصدر "توصيات قرار" وليس تنفيذاً آلياً — يُفضَّل تثبيت هذا كملاحظة واضحة في الواجهة (ليس نصيحة استثمارية، القرار والتنفيذ يدويان بالكامل من المستخدم).
