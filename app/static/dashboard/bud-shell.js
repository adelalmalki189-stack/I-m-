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
    '<div class="user"><div class="avatar">عم</div><div>عمر المنصوري<small>مدير العمليات</small></div></div>';
})();
