// الشريط الجانبي الموحد لبُعد — قائمة واحدة لكل صفحات فريق العمل.
// يُستدعى مباشرة بعد <aside class="side"></aside> فيُرسم قبل ظهور الصفحة (بدون وميض).
(function () {
  const sections = [
    ['مساحة العمل', [
      ['index.html', '◫', 'نظرة عامة'],
      ['requests.html', '⌕', 'طلبات الأسعار'],
      ['underwriting.html', '✎', 'الاكتتاب'],
      ['comparison.html', '⇄', 'مقارنة العروض'],
      ['clients.html', '♙', 'العملاء'],
      ['policies.html', '▤', 'الوثائق والتجديدات'],
      ['claims.html', '◉', 'المطالبات'],
      ['collections.html', '↗', 'التحصيلات والعمولات'],
    ]],
    ['الشبكة والإدارة', [
      ['insurers.html', '⌂', 'شركات التأمين'],
      ['reports.html', '▥', 'التقارير'],
      ['settings.html', '⚙', 'الإعدادات'],
      ['portal.html', '◎', 'بوابة العميل'],
      ['templates.html', '⬇', 'نماذج الملفات'],
    ]],
  ];

  const side = document.currentScript && document.currentScript.previousElementSibling;
  if (!side || !side.classList.contains('side')) return;
  const current = location.pathname.split('/').pop() || 'index.html';

  const nav = sections.map(([label, links]) =>
    `<div class="nav-label">${label}</div>` + links.map(([href, icon, text]) =>
      `<a href="${href}"${href === current ? ' class="active" aria-current="page"' : ''}><span class="nav-icon">${icon}</span>${text}</a>`
    ).join('')
  ).join('');

  side.innerHTML =
    '<a class="brand" href="index.html"><div class="brand-icon">BUD</div><div><div class="brand-name">بُعد</div><div class="brand-sub">وساطة تأمينية رقمية</div></div></a>' +
    `<nav class="nav" aria-label="القائمة الرئيسية">${nav}</nav>` +
    '<div class="user"><div class="avatar" id="user-avatar">…</div><div><span id="user-name"></span><small id="user-role"></small></div>' +
    '<button id="logout" class="logout" type="button">تسجيل الخروج</button></div>';

  // Staff pages need a session: no session → login page, then back here.
  const toLogin = () => location.replace(`login.html?next=${encodeURIComponent(current + location.search)}`);
  window.BUD_ME = fetch('/auth/me').then(r => {
    if (r.status === 401) { toLogin(); return new Promise(() => {}); }
    return r.ok ? r.json() : null;
  }).then(me => {
    if (!me) return null;
    side.querySelector('#user-name').textContent = me.full_name;
    side.querySelector('#user-role').textContent = me.role_label;
    side.querySelector('#user-avatar').textContent = me.full_name.trim().split(/\s+/).map(w => w[0]).slice(0, 2).join('');
    document.body.dataset.role = me.role;
    return me;
  });
  side.querySelector('#logout').addEventListener('click', async () => {
    await fetch('/auth/logout', { method: 'POST' });
    location.replace('login.html');
  });
})();
