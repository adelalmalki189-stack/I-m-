# Calculation Specification — V0.1

هذا المستند يحوّل القواعد الوصفية في `Options Trading System Spec V0.1` إلى صيغ حسابية دقيقة (pseudocode)، بحيث لا يُترك مجال للافتراض عند البرمجة. كل صيغة هنا **قابلة للتعديل لاحقاً** عبر `strategy_parameters` (انظر القسم 9)، لكن يجب أن تبدأ بقيمة افتراضية محددة صراحة — لا قيم ضمنية.

القاعدة الذهبية في كل هذا المستند:

> **لا يُحسب أي مؤشر على شمعة لم تُغلق بعد.** كل الحسابات تعتمد فقط على شموع 5 دقائق مكتملة (closed candles) وبيانات معروفة حتى تلك اللحظة. أي مخالفة لهذا = Look-Ahead Bias.

---

## 0. تعريفات الجلسة (Session Definitions)

- الجلسة الأساسية (Regular Session): 09:30–16:00 (بتوقيت شرق أمريكا ET).
- الشموع 5 دقائق تُرقَّم بدءاً من 09:30 (الشمعة #1 = 09:30–09:35).
- بيانات ما قبل/بعد الجلسة (pre-market/after-hours) **لا تدخل** في حساب VWAP أو baseline الخاص بـRVOL افتراضياً — تُستخدم فقط لسياق الفجوة (gap context)، قابل للتغيير عبر إعداد `include_extended_hours`.
- تعريف "يوم تداول صالح" يعتمد على `trading_calendar` (انظر `OPERATIONAL_SPEC_V0.1.md`) — الأيام نصف المفتوحة (half days) تُستثنى من baseline الخاص بـRVOL افتراضياً لأنها تشوّه المتوسط.

---

## 1. VWAP

VWAP يُعاد تصفيره كل جلسة (session-anchored)، لا يُحسب تراكمياً عبر الأيام.

```
typical_price_i = (high_i + low_i + close_i) / 3
cum_pv_i  = cum_pv_{i-1}  + typical_price_i * volume_i
cum_vol_i = cum_vol_{i-1} + volume_i
VWAP_i    = cum_pv_i / cum_vol_i
```

حيث `i=1` هي أول شمعة مكتملة في الجلسة (`cum_pv_0 = 0`, `cum_vol_0 = 0`).

---

## 2. EMA20 / EMA50 و الميل (Slope)

EMA تُحسب على إغلاقات الشموع 5 دقائق، **مستمرة عبر الأيام** (لا تُصفَّر يومياً) لتفادي القفزات الصناعية عند فتح كل جلسة. عند أول ظهور لرمز جديد في النظام، تُستخدم SMA كبذرة أولية:

```
alpha = 2 / (N + 1)                     # N = 20 أو 50

# Seed (أول N شمعة فقط):
EMA_N = SMA(close_1 .. close_N)

# بعدها:
EMA_i = alpha * close_i + (1 - alpha) * EMA_{i-1}
```

**الميل (Slope):**

```
ema20_slope_i = EMA20_i - EMA20_{i-3}        # نافذة 15 دقيقة (3 شموع)
slope_threshold = 0.05% * close_i             # قابل للضبط

if ema20_slope_i >  slope_threshold  → POSITIVE
if ema20_slope_i < -slope_threshold  → NEGATIVE
else                                  → FLAT
```

وجود `slope_threshold` ضروري لمنع اعتبار أي تذبذب صغير جداً "ميلاً" فعلياً.

---

## 3. RVOL بدون Look-Ahead

هذه أخطر نقطة في كامل المواصفة الأصلية لأنها معرّضة جداً لتسرّب معلومات من المستقبل إذا لم تُحسب بعناية.

### 3.1 الفكرة

نقارن حجم التداول في نافذة 5 دقائق الحالية بمتوسط الحجم في **نفس نافذة الوقت من اليوم** (time-of-day bucket) عبر عدد أيام سابقة.

### 3.2 الخوارزمية

```
bucket(t) = time-of-day slot لهذه الشمعة، مثلاً "09:30-09:35"
N = 20   # عدد الأيام التاريخية المستخدمة (قابل للضبط)

valid_days = آخر N يوم تداول سابق (قبل اليوم الحالي فقط)
             مستثنى منها:
               - الأيام نصف المفتوحة (half-day)
               - أي يوم يوجد فيه MarketEvent بخطورة HIGH/EXTREME لنفس الرمز
                 (مثل يوم الأرباح نفسه)
               - أي يوم بياناته data_status != VALID

baseline_volume(bucket) = mean( volume(bucket, day) for day in valid_days )

RVOL_i = current_bucket_volume_i / baseline_volume(bucket_i)
```

### 3.3 قواعد Warm-up و الحالات الحدّية

```
if count(valid_days) < N:
    RVOL = INVALID           # لا قيمة، وليس صفراً أو واحداً
    → أي Hard Gate يعتمد على RVOL يفشل تلقائياً (NO TRADE)

if baseline_volume(bucket) == 0:
    RVOL = INVALID           # تفادي القسمة على صفر
```

هذا يعني: الأسهم المطروحة حديثاً (IPO) أو التي توقفت طويلاً لا تدخل ضمن صفقات RVOL-dependent حتى تتوفر لها N يوم صالح من التاريخ.

---

## 4. ATR (أداة مساعدة مطلوبة لعدة حسابات لاحقة)

غير مذكورة صراحة في V0.1 الأصلية، لكنها ضرورية لتحويل "المسافات" (Entry Zone, Invalidation) من أرقام تحكمية إلى أرقام نسبية لتقلب كل سهم.

```
TR_i  = max(high_i - low_i, |high_i - close_{i-1}|, |low_i - close_{i-1}|)
ATR14_i = EMA(TR, period=14)     # على شموع 5 دقائق
```

---

## 5. Swing High / Swing Low و Market Structure

### 5.1 تعريف Swing (Fractal-based)

بمعامل `k` (افتراضي k=2):

```
Swing High عند الشمعة i  إذا:
    high_i > high_j  لكل j في [i-k, i-1]
    AND
    high_i > high_j  لكل j في [i+1, i+k]

Swing Low: نفس المنطق على low بدلاً من high.
```

### 5.2 التأخير الإلزامي في التأكيد (Confirmation Lag)

بما أن Swing يحتاج معرفة k شمعات *لاحقة* لتأكيده، فهو **لا يمكن تأكيده فورياً في الوقت الحقيقي** — هذا تريد-أوف معروف ويجب توثيقه صراحة بدل تجاهله:

```
عند الشمعة الحالية i، آخر Swing High/Low "مؤكد" هو دائماً بتأخير k شمعة
(أي حالة Market Structure المعروضة هي "كما كانت قبل k*5 دقيقة"، وليست لحظية 100%)
```

### 5.3 حالة Market Structure

```
احتفظ بسلسلة آخر Swing Highs المؤكدة وآخر Swing Lows المؤكدة.

Bullish Structure  = آخر Swing High > الذي قبله (Higher High)
                     AND آخر Swing Low > الذي قبله (Higher Low)

Bearish Structure  = Lower Low + Lower High (نفس المنطق معكوساً)

غير ذلك → Neutral / Mixed structure
```

### 5.4 Structure Break

```
CALL break: close_i (شمعة 5 دقائق مكتملة) > آخر Swing High مؤكد
PUT  break: close_i < آخر Swing Low مؤكد
```

---

## 6. تعريف Breakout

### 6.1 تحديد مستوى المقاومة/الدعم

أولوية اختيار المستوى (أقرب مستوى منطقي للسعر الحالي من بين):

```
1. آخر Swing High/Low مؤكد
2. Previous Day High/Low (PDH/PDL)
3. Opening Range High/Low (أول 15-30 دقيقة من الجلسة)
```

يُختار الأقرب للسعر ضمن مسافة معقولة (مثلاً <= 1.5 × ATR14)، وإلا يُستخدم Swing الافتراضي.

### 6.2 شرط التأكيد

```
breakout_confirmed =
       close_i > resistance_level          (وليس مجرد فتيل/wick)
   AND الشمعة i مكتملة بالكامل
   AND RVOL_i >= rvol_breakout_threshold    (افتراضي 1.30)
```

### 6.3 حد أدنى لمسافة الكسر (تفادي الكسر الهامشي/الضجيج)

```
breakout_distance = close_i - resistance_level

require: breakout_distance >= 0.10 * ATR14_i
```

كسر أقل من هذا الحد لا يُعتبر Breakout حقيقياً — يبقى الحالة WAIT.

---

## 7. Entry Zone و الحد الأقصى للامتداد (Avoid Chasing)

بدل نسبة ثابتة، نستخدم ATR14 لتتكيف تلقائياً مع تقلب كل سهم:

```
entry_zone_low   = resistance_level
entry_zone_high  = resistance_level + 0.5 * ATR14
max_extension    = resistance_level + 1.0 * ATR14

if current_price <= entry_zone_high:
    → ENTRY-eligible (كامل الجودة)
elif entry_zone_high < current_price <= max_extension:
    → ENTRY-eligible لكن مع "Extension Penalty" في Setup Score
else:  # current_price > max_extension
    → WAIT   (بالضبط كما نص المخطط الأصلي)
```

القيم 0.5 / 1.0 × ATR هي V0.1 initial parameters قابلة للضبط.

---

## 8. Invalidation / Stop / Target / R:R

### 8.1 Invalidation Level (على مستوى السهم Underlying)

```
CALL invalidation = min( resistance_level - 0.25 * ATR14,
                          آخر Swing Low مؤكد )

PUT  invalidation = max( support_level + 0.25 * ATR14,
                          آخر Swing High مؤكد )
```

هذا يربط الـStop بالبنية الفعلية للسعر بدل رقم تحكمي، تماشياً مع القسم 17 من المواصفة الأصلية ("underlying invalidation" وليس عشوائي).

### 8.2 Risk / Target على مستوى Underlying

```
Risk_underlying   = entry_price - invalidation_level          (CALL)
Target_underlying = entry_price + 2 * Risk_underlying          (2R, تبسيط V0.1)
```

### 8.3 تحويل التأثير إلى Premium العقد (تقريب باستخدام Delta)

> **قيد معروف يجب توثيقه**: هذا تقريب خطي باستخدام delta لحظة الإشارة فقط، **يتجاهل theta decay وتغيّر gamma**. لذلك دقته تنخفض كثيراً مع 0DTE أو العقود قريبة الانتهاء — وهذا يبرر إضافياً شروط 0DTE الأكثر صرامة في القسم 11 من المواصفة الأصلية. تحسين هذا (إعادة تسعير عبر Black-Scholes بدل delta ثابت) هو أول مرشح لتحسين V0.2.

```
estimated_premium_change ≈ delta * underlying_price_change

Stop_premium   ≈ current_premium - |delta| * Risk_underlying
Target_premium ≈ current_premium + |delta| * (Target_underlying - entry_price)

R:R = (Target_premium - current_premium) / (current_premium - Stop_premium)

require: R:R >= 2.0   → وإلا NO TRADE (كما في القسم 18 الأصلي)
```

---

## 9. دوال تطبيع الدرجات (Score Normalization Functions)

المواصفة الأصلية أعطت أوزاناً (Liquidity 25%, Spread 20%...) لكن بدون دالة تحوّل كل قيمة خام (raw) إلى نقاط 0-100. فيما يلي دوال ابتدائية concrete، مع الإشارة إلى أنها ستُعاد معايرتها بعد جمع بيانات فعلية (Outcome Tracking):

### 9.1 Spread Score

```
spread_score = clamp( 100 - (spread_pct / 5.0) * 100, 0, 100 )
# spread = 0%  → 100
# spread = 3%  → 40
# spread = 5%  → 0   (Hard Gate يرفضه أصلاً قبل الوصول هنا)
```

### 9.2 Delta Score (تفضيل 0.50–0.65 كما في القسم 10)

```
d = |delta|

if 0.50 <= d <= 0.65:        delta_score = 100
elif 0.40 <= d < 0.50:       delta_score = lerp(d, 0.40→70, 0.50→100)
elif 0.65 < d <= 0.70:       delta_score = lerp(d, 0.65→100, 0.70→70)
else:                        delta_score = 0    # خارج الـHard Gate أصلاً
```

### 9.3 DTE Score (تفضيل منتصف المدى 7–45، أولوية 14–21)

```
if 14 <= DTE <= 21:          dte_score = 100
elif 7 <= DTE < 14:          dte_score = lerp(DTE, 7→70, 14→100)
elif 21 < DTE <= 45:         dte_score = lerp(DTE, 21→100, 45→70)
else:                        dte_score = 0     # خارج الـHard Gate أصلاً
```

### 9.4 Volume/OI Score (مقياس لوغاريتمي لأن التفاوت واسع جداً)

```
OI_target = 5000   # قابل للضبط لاحقاً حسب توزيع البيانات الفعلية
volume_oi_score = clamp( 100 * log10(max(OI,1)) / log10(OI_target), 0, 100 )
```

### 9.5 IV Score (نسبي وليس مطلق)

القيمة المطلقة لـIV لا تعني شيئاً بمعزل عن تاريخ الرمز نفسه. نحتاج **IV Rank/Percentile** لكل Underlying (يتطلب الاحتفاظ بتاريخ IV — هذا متطلب بيانات إضافي غير موجود في المواصفة الأصلية، يُضاف إلى `Underlying` الميزات المحسوبة):

```
IV_rank = percentile_rank(current_IV, trailing_252_day_IV_history)

if IV_rank <= 50:      iv_score = 100
elif IV_rank <= 80:    iv_score = lerp(IV_rank, 50→100, 80→60)
else:                  iv_score = lerp(IV_rank, 80→60, 100→20)   # عقوبة لا رفض كامل
```

### 9.6 المبدأ العام لبقية العوامل

كل عامل يدخل في Contract Score أو Final Opportunity Score (Setup Quality, Market Context, Momentum, R:R, Event Risk...) يجب أن يمرّ بنفس المعالجة: **دالة lerp/clamp صريحة تُحوّل القيمة الخام إلى 0-100**، وليس تقديراً حدسياً وقت التنفيذ. الجداول أعلاه أمثلة كافية للنمط؛ الباقي يُشتق بنفس الطريقة عند كتابة Data Specification، وتُراجَع القيم الحدّية (thresholds) بعد أول دفعة من Outcome Tracking الحقيقية بدل تثبيتها نهائياً الآن.

---

## 10. ملاحظة حول Versioning

كل صيغة/threshold في هذا المستند يجب أن تُخزَّن كسجل في `strategy_parameters` مع رقم إصدار (`version`)، ويُربط كل `Signal` و`TradeSnapshot` بالإصدار الذي استُخدم وقت توليده. بدون هذا الربط، لا يمكن لاحقاً معرفة هل تحسّن/تراجع الأداء بسبب تعديل هذه الصيغ أم لأسباب أخرى.
