"""v0.041 — CSV отчёт владельца, отсутствие приватных полей и формул."""
import csv
import io
import os
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import UUID

from fastapi.testclient import TestClient

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"api"))
os.environ.setdefault("DATABASE_URL","postgresql://unused")
os.environ.setdefault("SESSION_SIGNING_KEY","a"*64)
from app import main
from app.placement_report import build_campaign_report
from app.report_export import render_campaign_csv, _safe_cell

CAMPAIGN=UUID("00000000-0000-4000-8000-000000000941")
OWNER=UUID("00000000-0000-4000-8000-000000000942")
NOW=datetime(2026,11,9,18,tzinfo=timezone.utc)

def campaign():
    return dict(
        id=CAMPAIGN,
        channel_title="=HYPERLINK(\"https://example.com\",\"bad\")",
        channel_url="https://t.me/pushok_test",
        tariff_name_snapshot="1 день",
        duration_seconds_snapshot=86400,
        price_kopecks_snapshot=30000,
        currency_snapshot="RUB",
        moderation_status="approved",
        payment_status="verified",
        placement_status="completed",
        requested_start_at=NOW,
        scheduled_start_at=NOW,
        scheduled_end_at=NOW,
        payment_secret="DO_NOT_EXPORT",
    )

def report():
    return build_campaign_report(
        campaign(),
        {"visible_count":2,"not_visible_count":1,"private_screenshot_count":1,
         "latest_result":"not_visible","latest_observed_at":NOW,
         "operator_note":"PRIVATE_OPERATOR_NOTE","storage_key":"PRIVATE_FILE"},
        [{"event_type":"campaign_submitted","created_at":NOW},
         {"event_type":"placement_evidence_uploaded","created_at":NOW},
         {"event_type":"placement_completed_manually","created_at":NOW}],
        generated_at=NOW,
    )

class ExportTests(unittest.TestCase):
    def setUp(self):
        main.app.dependency_overrides.clear()
        self.client=TestClient(main.app)
        self.path=f"/api/v1/campaigns/{CAMPAIGN}/report.csv"
    def tearDown(self):
        main.app.dependency_overrides.clear()
        self.client.close()
    def login(self):
        main.app.dependency_overrides[main.get_current_user]=lambda:{"id":OWNER,"role":"advertiser"}

    def test_protects_spreadsheet_formula_cells(self):
        for payload in ("=1+1","+SUM(1,2)","-20","@SUM(1,2)"," \t=cmd",
                        "\r=cmd","\n=cmd"):
            with self.subTest(value=payload):
                self.assertTrue(_safe_cell(payload).startswith("'"))
        self.assertEqual(_safe_cell("Обычный канал"),"Обычный канал")

    def test_csv_safe_content_and_utf8_bom(self):
        content=render_campaign_csv(report())
        self.assertTrue(content.startswith(bytes([239,187,191])))
        text=content.decode("utf-8-sig")
        rows=list(csv.reader(io.StringIO(text),delimiter=";"))
        self.assertEqual(rows[0],["Раздел","Поле","Значение"])
        lookup={(section,field):value for section,field,value in rows}
        self.assertEqual(lookup[("Тариф","Цена, копейки")],"30000")
        self.assertTrue(lookup[("Канал","Название")].startswith("'=HYPERLINK"))
        self.assertEqual(lookup[("Проверка","Реальные показы подтверждены")],"нет")
        self.assertEqual(lookup[("Проверка","Количество показов")],"нет данных")
        self.assertEqual(len([x for x in rows if x[0]=="История"]),2)
        for secret in ("DO_NOT_EXPORT","PRIVATE_OPERATOR_NOTE","PRIVATE_FILE",
                       "placement_evidence_uploaded","storage_key","operator_note"):
            self.assertNotIn(secret,text)

    def test_unauthorized_export_returns_401_without_db(self):
        with patch("app.main.psycopg.connect") as db:
            response=self.client.get(self.path)
        self.assertEqual(response.status_code,401)
        db.assert_not_called()

    def test_export_uses_owner_filter_and_private_headers(self):
        self.login()
        connection=MagicMock()
        cursor=connection.__enter__.return_value.execute.return_value
        cursor.fetchone.side_effect=[
            campaign(),
            {"visible_count":2,"not_visible_count":1,"private_screenshot_count":1,
             "latest_result":"not_visible","latest_observed_at":NOW},
        ]
        cursor.fetchall.return_value=[{"event_type":"campaign_submitted","created_at":NOW}]
        with patch("app.main.psycopg.connect",return_value=connection):
            response=self.client.get(self.path)
        self.assertEqual(response.status_code,200,response.text)
        self.assertTrue(response.content.startswith(bytes([239,187,191])))
        self.assertIn("text/csv",response.headers["content-type"])
        self.assertEqual(response.headers["cache-control"],"private, no-store")
        self.assertEqual(response.headers["x-content-type-options"],"nosniff")
        self.assertIn('attachment; filename=',response.headers["content-disposition"])
        sql=connection.__enter__.return_value.execute.call_args_list[0]
        self.assertIn("advertiser_user_id = %s",sql.args[0])
        self.assertEqual(sql.args[1],(CAMPAIGN,OWNER))

    def test_non_owner_export_is_404_and_does_not_query_private_tables(self):
        self.login()
        conn=MagicMock()
        conn.__enter__.return_value.execute.return_value.fetchone.return_value=None
        with patch("app.main.psycopg.connect",return_value=conn):
            response=self.client.get(self.path)
        self.assertEqual(response.status_code,404)
        self.assertEqual(conn.__enter__.return_value.execute.call_count,1)

    def test_ui_download_action_wired(self):
        script=(ROOT/"api/web/app.js").read_text(encoding="utf-8")
        self.assertIn("Выгрузить CSV",script)
        self.assertIn("Скачать CSV",script)
        self.assertIn("Authorization:'Bearer '+token",script)
        self.assertIn("report.csv",script)

if __name__=="__main__":
    unittest.main()
