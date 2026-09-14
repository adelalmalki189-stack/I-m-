# Testing & CI Specification — V0.1

يغطي هذا المستند فجوة "لا توجد استراتيجية اختبار شاملة" من المراجعة الأصلية. الهدف: منع أي تعديل مستقبلي على صيغة حسابية (`CALCULATION_SPEC_V0.1.md`) من كسر سلوك النظام بصمت.

---

## 1. هرم الاختبار

```
                ▲
               /│\        Manual QA (قبل تفعيل Paper Trading)
              /─┼─\
             / Golden \    Regression على سيناريوهات تاريخية ثابتة
            /  Scenarios \
           /───────────────\
          /   Integration    \   DataProvider + Engines معاً (fake IBKR)
         /─────────────────────\
        /       Unit Tests       \  كل صيغة/دالة منفردة
       /───────────────────────────\
```

القاعدة: كل صيغة في `CALCULATION_SPEC_V0.1.md` لها اختبار وحدة (unit test) قبل أن تُعتبر منتهية — الصيغة بدون اختبار = غير مكتملة.

---

## 2. فرض قاعدة "No Mock Data in Production" برمجياً

القاعدة موجودة في المواصفة الأصلية كنص، لكن يجب فرضها آلياً لا الاعتماد على الانضباط فقط:

```
DataProvider (interface)
    ├── IBKRDataProvider       (production)
    ├── BarchartDataProvider   (production)
    └── FakeDataProvider       (tests only)

قيد إلزامي:
    - FakeDataProvider يعيش في مجلد tests/ فقط (لا يُستورد من كود الإنتاج).
    - فحص CI ثابت (static check / lint rule) يفشل الـ build إذا استوردت
      أي وحدة تحت src/engines/** أو src/production/** الرمز FakeDataProvider
      أو أي بيانات hard-coded كأسعار/عقود.
    - متغير بيئة ENVIRONMENT=production يمنع تحميل أي DataProvider غير
      مُسجَّل صراحة في allow-list الإنتاج، حتى لو استُورد بالخطأ.
```

---

## 3. مصفوفة اختبارات الوحدة (Unit Tests) لكل محرك

### Feature Engine
| الدالة | حالات يجب اختبارها |
|---|---|
| VWAP | إعادة التصفير عند بداية الجلسة، شمعة واحدة، عدة أيام متتالية بدون تسرّب بين الجلسات |
| EMA20/50 + Slope | مرحلة seed (SMA) الصحيحة، استمرارية عبر منتصف الليل بدون قفزة، عتبة slope_threshold تُصنّف FLAT بشكل صحيح |
| RVOL | Warm-up < N يوم → INVALID، يوم half-day مستثنى من baseline، يوم earnings مستثنى، baseline=0 → INVALID وليس Infinity |
| ATR14 | القيمة الأولى (seed)، فجوة سعرية كبيرة بين شمعتين (gap) تُحسب صح ضمن True Range |
| Swing High/Low | لا يُؤكَّد Swing إلا بعد k شمعة لاحقة، حافة بداية التاريخ (لا بيانات كافية) لا تُنتج Swing وهمي |
| Market Structure | انتقال Bullish→Neutral→Bearish يتبع نفس تسلسل Swings المؤكدة فقط |

### Setup Engine
| الحالة | المتوقع |
|---|---|
| كل شروط CALL محققة بالضبط على الحد الأدنى (RVOL=1.30 تماماً) | CALL Candidate |
| كسر أقل من 0.10×ATR14 | لا Breakout (WAIT) |
| السعر تجاوز max_extension | WAIT وليس ENTRY حتى لو باقي الشروط ممتازة |
| أي مُدخل بحالة STALE/INVALID | NO TRADE فوراً بغض النظر عن باقي الشروط |

### Timing Engine
| الانتقال | الشرط |
|---|---|
| WAIT → READY | تحقق أغلب الشروط عدا confirmation candle |
| READY → ENTRY | كل الـ Hard Gates ناجحة معاً في نفس اللحظة |
| ENTRY → INVALIDATED | إغلاق شمعة تحت مستوى الكسر (CALL) — اختبار حافة: إغلاق مساوٍ تماماً للمستوى |
| لا رجوع من INVALIDATED إلى ENTRY لنفس Signal | يجب توليد Signal جديد منفصل |

### Contract Engine / Scoring
| الحالة | المتوقع |
|---|---|
| Spread = 0%, 3%, 5%, 6% | Score = 100, 40, 0, ورفض Hard Gate عند >5% |
| Delta عند حدود النطاق بالضبط (0.40, 0.70) وخارجها (0.39) | تطابق دوال lerp في القسم 9 من Calculation Spec، ورفض خارج 0.40-0.70 |
| OI منخفض جداً (1) مقابل مرتفع (50,000) | Volume/OI score لا يتجاوز 100 ولا يكون سالباً (log10 edge cases) |

### Risk Engine
| الحالة | المتوقع |
|---|---|
| account_size صغير جداً بحيث risk_per_contract > max_risk | recommended_quantity = 0 → NO TRADE، ليس تقريب لأسفل لعقد واحد بالخطأ |
| فتح صفقات متعددة تتجاوز max_concurrent_signals | الصفقة الجديدة تُعرض كـ Blocked |
| daily_max_loss_pct محقق تماماً | إيقاف كل ENTRY الجديد لبقية الجلسة |

---

## 4. اختبارات التكامل (Integration)

تُشغَّل ضد `FakeDataProvider` يحاكي:
- انقطاع اتصال مفاجئ ثم استرجاع (يتحقق أن Reconnection + Circuit Breaker من `OPERATIONAL_SPEC_V0.1.md` يعملان).
- تأخر بيانات عمداً لتجاوز عتبة staleness (يتحقق من رفض الإشارة).
- محاكاة تجاوز rate limit من IBKR (يتحقق من تقسيم الاشتراكات على دفعات دون رمي استثناء غير معالَج).

---

## 5. Golden Scenarios (Regression على بيانات تاريخية ثابتة)

مجموعة صغيرة (10-15) من مقاطع بيانات تاريخية حقيقية مُجمَّدة (fixtures)، كل واحدة بنتيجة متوقعة مثبَّتة (expected Signal/Score/Status). تُشغَّل في كل Pull Request:

```
fixtures/golden/
    nvda_call_breakout_2025-xx-xx.json   → expected: CALL, Score ~93, ENTRY
    choppy_market_no_signal_2025-xx-xx.json → expected: NO TRADE
    ...
```

أي PR يُغيّر النتيجة المتوقعة لأي fixture **يجب** أن يوضّح صراحة في وصف الـPR سبب التغيير (تعديل مقصود في `strategy_parameters` مثلاً) — وليس مجرد "الاختبار عدّل نفسه ليمر".

---

## 6. خط أنابيب CI (CI Pipeline)

```
على كل Pull Request:
  1. Lint + type check
  2. Unit tests (كل المحركات)
  3. Integration tests (FakeDataProvider)
  4. Golden Scenario regression tests
  5. فحص "No Mock Data" الثابت (القسم 2)
  → فشل أي بند = حظر الدمج (merge blocked)

Nightly (ليس على كل PR لأنها أبطأ):
  6. تشغيل كامل لخطة BACKTEST_PLAN_V0.1.md على نافذة تاريخية أوسع
     (فحص تشخيصي فقط، لا يحظر الدمج، لكن يُنبّه إذا انحرفت التوزيعات
      بشكل حاد عن الفحص السابق)
```

---

## 7. ميزانية الأداء (Performance Budget) للـ Scanner

غير مذكورة أصلاً في المواصفة الأصلية، وضرورية لأن مسح عدة رموز مع سلاسل خيارات كاملة كل 5 دقائق قد يكون مكلفاً:

```
target_universe_size      = 200 رمز كحد أقصى في V0.1 (قابل للتوسيع لاحقاً)
max_full_scan_cycle_time  = 90 ثانية  (يجب أن تنتهي دورة الفحص الكاملة
                              قبل إغلاق الشمعة 5 دقائق التالية بوقت كافٍ)
alert_if_cycle_time_exceeds = 120 ثانية → تحذير تشغيلي (ليس بالضرورة NO TRADE)
```

يُختبر هذا عبر Load test بسيط بمحاكاة 200 رمز في `FakeDataProvider`.

---

## 8. قائمة تحقق يدوية قبل تفعيل Paper Trading

- [ ] كل الصيغ في `CALCULATION_SPEC_V0.1.md` لها اختبار وحدة ناجح.
- [ ] Golden Scenarios كلها خضراء.
- [ ] Rule Sanity Check (`BACKTEST_PLAN_V0.1.md`) لا يُظهر أي كارثة منطقية.
- [ ] Circuit Breaker (فقدان بيانات) و Daily Loss Limit تم اختبارهما يدوياً بمحاكاة حقيقية وليس فقط بالكود.
- [ ] التنبيه الفوري (Alerting) يصل فعلياً للمستخدم (اختبار end-to-end وليس فقط سجل قاعدة البيانات).
- [ ] لوحة Dashboard تعرض Data Quality/Degraded Mode بوضوح عند محاكاة انقطاع.
