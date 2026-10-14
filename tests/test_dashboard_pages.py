"""Every BUD dashboard page is served and shows its core sections."""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app


def test_dashboard_pages_load():
    with TestClient(app) as client:
        response = client.get("/dashboard/")
        requests_response = client.get("/dashboard/requests.html")
        comparison_response = client.get("/dashboard/comparison.html")
        clients_response = client.get("/dashboard/clients.html")
        policies_response = client.get("/dashboard/policies.html")
        claims_response = client.get("/dashboard/claims.html")
        collections_response = client.get("/dashboard/collections.html")
        insurers_response = client.get("/dashboard/insurers.html")
        reports_response = client.get("/dashboard/reports.html")
        settings_response = client.get("/dashboard/settings.html")
        portal_response = client.get("/dashboard/portal.html")
        underwriting_response = client.get("/dashboard/underwriting.html")
        login_response = client.get("/dashboard/login.html")
        track_response = client.get("/dashboard/track.html")
    assert response.status_code == 200
    assert "BUD | بُعد للوساطة التأمينية" in response.text
    assert "رحلة طلبات التأمين" in response.text
    assert requests_response.status_code == 200
    assert "صندوق الطلبات الرقمية" in requests_response.text
    assert "bud-api.js" in requests_response.text
    assert comparison_response.status_code == 200
    assert "مقارنة التغطيات" in comparison_response.text
    assert "إصدار الوثيقة" in comparison_response.text
    assert clients_response.status_code == 200
    assert "ملفات العملاء" in clients_response.text
    assert "بيانات التواصل" in clients_response.text
    assert policies_response.status_code == 200
    assert "الوثائق والتجديدات" in policies_response.text
    assert "بدء تجديد" in policies_response.text
    assert claims_response.status_code == 200
    assert "متابعة المطالبات" in claims_response.text
    assert "تسجيل مطالبة جديدة" in claims_response.text
    assert collections_response.status_code == 200
    assert "التحصيلات والتسويات" in collections_response.text
    assert "تسويات عمولات الشركات" in collections_response.text
    assert insurers_response.status_code == 200
    assert "شبكة شركات التأمين" in insurers_response.text
    assert "بريد طلبات التسعير" in insurers_response.text
    assert reports_response.status_code == 200
    assert "التقارير والتحليلات" in reports_response.text
    assert "الأقساط المكتتبة شهريًا" in reports_response.text
    assert settings_response.status_code == 200
    assert "الإعدادات والصلاحيات" in settings_response.text
    assert "سجل التدقيق" in settings_response.text
    assert portal_response.status_code == 200
    assert "اطلب تأمينك من بُعد" in portal_response.text
    assert "إرسال الطلب" in portal_response.text
    assert underwriting_response.status_code == 200
    assert "قوالب الرسائل" in underwriting_response.text
    assert login_response.status_code == 200
    assert "دخول فريق العمل" in login_response.text
    assert track_response.status_code == 200
    assert "تابع طلبك" in track_response.text
