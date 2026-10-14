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
  };

  class ApiError extends Error {
    constructor(status, code, message) { super(message); this.status = status; this.code = code; }
  }

  async function api(path, { method = 'GET', body, form } = {}) {
    const options = { method, headers: {} };
    if (form) options.body = form;
    else if (body !== undefined) { options.body = JSON.stringify(body); options.headers['Content-Type'] = 'application/json'; }
    let response;
    try { response = await fetch(`/insurance${path}`, options); }
    catch { throw new ApiError(0, 'NETWORK', 'تعذر الوصول إلى خادم بُعد.'); }
    if (response.status === 204) return null;
    const data = await response.json().catch(() => null);
    if (!response.ok) {
      const detail = data?.detail;
      if (detail?.error_code) throw new ApiError(response.status, detail.error_code, ERRORS[detail.error_code] || detail.message);
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
    return value == null || value === '' || Number.isNaN(n) ? '—' : `${n.toLocaleString('en-US')} ر.س`;
  };

  return { api, ApiError, catalog, pill, el, notify, run, date, money, REQUEST_STATUS, SUBMISSION_STATUS };
})();
