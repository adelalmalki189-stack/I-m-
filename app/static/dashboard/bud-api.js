// أدوات مشتركة لصفحات بُعد: الاتصال بالخادم، تسميات الحالات، الكتالوج، والتنبيهات.
const BUD = (() => {
  const REQUEST_STATUS = {
    new: ['جديد', 'blue'],
    needs_info: ['نواقص بيانات', ''],
    under_review: ['قيد الاكتتاب', ''],
    ready_for_quote: ['بانتظار عروض الشركات', 'blue'],
    quote_selected: ['عرض مختار', 'blue'],
    policy_issued: ['وثيقة صادرة', 'dark'],
    closed: ['مغلق بدون بيع', ''],
  };
  const SUBMISSION_STATUS = {
    draft: ['مسودة', ''],
    pending_approval: ['بانتظار الاعتماد', ''],
    approved: ['معتمد للإرسال', 'blue'],
    sent: ['أُرسل', 'dark'],
    failed: ['فشل الإرسال', 'red'],
    follow_up_required: ['يحتاج متابعة', 'red'],
    rejected: ['مرفوض', 'red'],
    closed: ['مغلق', ''],
  };
  const ERRORS = {
    EMAIL_DELIVERY_DISABLED: 'البريد غير مفعّل على الخادم. فعّل إعدادات SMTP أو استخدم «تسجيل كمُرسل».',
    INVALID_STATUS_TRANSITION: 'هذا الانتقال غير مسموح من الحالة الحالية.',
    SUBMISSION_NOT_APPROVED: 'اعتمد الرسالة أولًا.',
    SUBMISSION_LOCKED: 'الرسالة أُرسلت ولا يمكن تعديلها.',
    REQUEST_NOT_OPEN: 'الطلب مغلق.',
    UNKNOWN_TEMPLATE_VARIABLES: 'القالب يحتوي متغيرات غير معروفة.',
    INSURER_EXISTS: 'يوجد شركة مسجلة بنفس البريد.',
    FORBIDDEN: 'صلاحيتك لا تسمح بهذا الإجراء.',
    EMPLOYEE_EXISTS: 'يوجد موظف مسجل بنفس البريد.',
    CANNOT_DEMOTE_SELF: 'لا يمكنك إيقاف حسابك أو تغيير دورك بنفسك.',
    TOO_MANY_REQUESTS: 'طلبات كثيرة من نفس الجهاز. حاول بعد قليل.',
    UPLOAD_NOT_ALLOWED: 'انتهت صلاحية رابط رفع المستندات.',
    FILE_TOO_LARGE: 'حجم الملف أكبر من المسموح.',
    UNSUPPORTED_FILE_TYPE: 'نقبل ملفات PDF أو JPG أو PNG فقط.',
    CHANNEL_NOT_CONFIGURED: 'بيانات مزوّد الرسائل غير موجودة على الخادم. أضفها أولًا ثم فعّل الوضع الفعلي.',
    AMOUNT_REQUIRED: 'أدخل المبلغ لهذه المرحلة.',
    COMMISSION_ALREADY_RECEIVED: 'العمولة مسجلة كمستلمة مسبقًا.',
    NO_COMMISSION: 'العرض المختار لا يحتوي نسبة عمولة.',
    CUSTOMER_EXISTS: 'يوجد عميل بنفس البريد.',
    DOCUMENT_FILE_MISSING: 'ملف المستند غير موجود على الخادم.',
    INCIDENT_IN_FUTURE: 'تاريخ الحادث لا يمكن أن يكون في المستقبل.',
    INCIDENT_OUTSIDE_COVERAGE: 'تاريخ الحادث خارج مدة الوثيقة.',
    INSTALLMENT_NOT_DUE: 'هذا القسط ليس مستحقًا (مدفوع أو ملغى).',
    INSURER_INACTIVE: 'إحدى الشركات المختارة موقوفة.',
    INVALID_CREDENTIALS: 'البريد أو كلمة المرور غير صحيحة.',
    INVALID_FILE: 'الملف غير صالح.',
    INVALID_INSTALLMENTS: 'عدد الأقساط غير مسموح.',
    INVALID_PARAM: 'قيمة التوقيت خارج الحدود المسموحة.',
    UNKNOWN_PARAM: 'إعداد غير معروف لهذه القاعدة.',
    INVALID_PERIOD: 'تاريخ البداية بعد تاريخ النهاية.',
    INVALID_PREMIUM: 'العرض المختار لا يحتوي قسطًا صحيحًا.',
    NOT_RETRYABLE: 'يمكن إعادة إرسال الرسائل الفاشلة فقط.',
    NO_MESSAGE: 'هذه القاعدة مهمة داخلية ولا ترسل رسالة.',
    NO_SELECTED_QUOTE: 'اختر عرضًا أولًا.',
    POLICY_CANCELLED: 'الوثيقة ملغاة.',
    RENEWAL_EXISTS: 'يوجد طلب تجديد مفتوح لهذه الوثيقة.',
    QUOTE_NOT_FOR_REQUEST: 'العرض لا يخص هذا الطلب.',
    QUOTES_NOT_FOUND: 'لا توجد عروض لهذا الطلب.',
    TOO_MANY_ATTEMPTS: 'محاولات كثيرة. حاول بعد ربع ساعة.',
    TOO_MANY_DOCUMENTS: 'وصلت للحد الأقصى من المستندات.',
    UNDERWRITER_ID_REQUIRED: 'اختر المكتتب.',
  };

  class ApiError extends Error {
    constructor(status, code, message) { super(message); this.status = status; this.code = code; }
  }

  async function api(path, { method = 'GET', body, form, headers = {} } = {}) {
    const options = { method, headers: { ...headers } };
    if (form) options.body = form;
    else if (body !== undefined) { options.body = JSON.stringify(body); options.headers['Content-Type'] = 'application/json'; }
    let response;
    try { response = await fetch(`/insurance${path}`, options); }
    catch { throw new ApiError(0, 'NETWORK', 'تعذر الوصول إلى خادم بُعد.'); }
    if (response.status === 204) return null;
    const data = await response.json().catch(() => null);
    if (response.status === 401 && !location.pathname.endsWith('/portal.html')) {
      location.replace(`login.html?next=${encodeURIComponent(location.pathname.split('/').pop() + location.search)}`);
      return new Promise(() => {});
    }
    if (!response.ok) {
      const detail = data?.detail;
      if (detail?.error_code) {
        const fallback = /_NOT_FOUND$/.test(detail.error_code) ? 'العنصر المطلوب غير موجود (ربما حُذف).' : detail.message;
        throw new ApiError(response.status, detail.error_code, ERRORS[detail.error_code] || fallback);
      }
      if (Array.isArray(detail)) throw new ApiError(response.status, 'VALIDATION', detail.map(d => d.msg.replace(/^Value error, /, '')).join('، '));
      throw new ApiError(response.status, 'HTTP', `خطأ من الخادم (${response.status})`);
    }
    return data;
  }

  let catalogPromise;
  function catalog() {
    catalogPromise ||= api('/catalog').then(({ lines }) => {
      const byKey = Object.fromEntries(lines.map(line => [line.key, line]));
      return {
        lines,
        line: key => byKey[key],
        lineLabel: key => byKey[key]?.label || key,
        productLabel: (lineKey, productKey) => byKey[lineKey]?.products.find(p => p.key === productKey)?.label || productKey,
        fieldLabel: (lineKey, fieldKey) => byKey[lineKey]?.fields.find(f => f.key === fieldKey)?.label || fieldKey,
      };
    });
    return catalogPromise;
  }

  function pill(map, status) {
    const [label, tone] = map[status] || [status, ''];
    const el = document.createElement('span');
    el.className = `pill ${tone}`.trim();
    el.textContent = label;
    return el;
  }

  function el(tag, attrs = {}, ...children) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs)) {
      if (value == null || value === false) continue;
      if (key === 'class') node.className = value;
      else if (key.startsWith('on')) node.addEventListener(key.slice(2), value);
      else if (key === 'text') node.textContent = value;
      else node.setAttribute(key, value === true ? '' : value);
    }
    node.append(...children.flat().filter(c => c != null && c !== false));
    return node;
  }

  let toastTimer;
  function notify(text) {
    let toast = document.getElementById('toast');
    if (!toast) { toast = el('div', { id: 'toast', class: 'toast', role: 'status', 'aria-live': 'polite' }); document.body.append(toast); }
    toast.textContent = text;
    toast.classList.add('show');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => toast.classList.remove('show'), 3200);
  }

  // Run an async action from a button: disable it meanwhile and show errors as a toast.
  // On failure the returned promise never settles, so a caller's `.then(...)` follow-up
  // (re-render, navigation) is skipped without an unhandled rejection.
  async function run(button, action, success) {
    if (button) button.disabled = true;
    try { const result = await action(); if (success) notify(success); return result; }
    catch (error) { notify(error.message || 'حدث خطأ غير متوقع'); return new Promise(() => {}); }
    finally { if (button) button.disabled = false; }
  }

  const dateFmt = new Intl.DateTimeFormat('ar-SA-u-ca-gregory-nu-latn', { dateStyle: 'medium', timeStyle: 'short' });
  const date = value => (value ? dateFmt.format(new Date(value)) : '—');
  const money = value => {
    const n = Number(String(value ?? '').replace(/,/g, ''));
    return value == null || value === '' || Number.isNaN(n) ? '—' : `${n.toLocaleString('en-US', { maximumFractionDigits: 2 })} ر.س`;
  };

  return { api, ApiError, catalog, pill, el, notify, run, date, money, REQUEST_STATUS, SUBMISSION_STATUS };
})();
