"""Every downloadable template must be read back cleanly by the system's own readers."""

from __future__ import annotations

import io

import pytest

from app.insurance import file_templates, networks, sheets


@pytest.mark.parametrize("template", file_templates.TEMPLATES, ids=lambda t: t.key)
def test_template_round_trips(template):
    buffer = io.BytesIO()
    file_templates.build(template).save(buffer)
    content = buffer.getvalue()
    if template.profile is networks.PROVIDERS:
        parsed = networks.parse(content)
        assert len(parsed.rows) == len(template.rows) and not parsed.issues
        return
    sheet = sheets.read_workbook(content)
    assert sheet.sheet_name == "البيانات"  # not the instructions sheet
    mapping = sheets.auto_mapping(template.profile, sheet.headers)
    assert [k for k in template.fields if mapping.get(k) is None] == []
    result = sheets.analyze(template.profile, sheet, sheets.Options(column_mapping=mapping))
    assert len(result.rows) == len(template.rows)
    assert [i for i in result.issues if i["level"] == "error"] == []


def test_short_list_beside_a_notes_sheet_picks_the_list():
    from openpyxl import Workbook

    wb = Workbook()
    wb.active.append(["الاسم", "رقم الهوية", "الجنس"])
    wb.active.append(["أحمد", "1012345678", "ذكر"])
    notes = wb.create_sheet("ملاحظات")
    for i in range(20):
        notes.append([f"ملاحظة {i}"])
    buffer = io.BytesIO()
    wb.save(buffer)
    assert len(sheets.read_workbook(buffer.getvalue()).rows) == 1
