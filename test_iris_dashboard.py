import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from iris_dashboard import DASHBOARD_HTML, build_snapshot

ET=ZoneInfo("America/New_York")

class IrisDashboardHealthTests(unittest.TestCase):
 def test_dashboard_includes_inline_favicon(self):
  self.assertIn('rel="icon"',DASHBOARD_HTML)
 def snap(self, *, now=None, state=None, heartbeat=None, connectors=None, reports=None, alerts=None):
  now=now or datetime(2026,7,25,14,0,tzinfo=ET)
  return build_snapshot(now=now,state=state or {},heartbeat=heartbeat or {},connectors=connectors or {"google":"green","detail":"OAuth refresh and Google APIs verified."},reports=reports or {},alerts=alerts or {})
 def test_missing_content_is_edition_orange_but_system_green(self):
  now=datetime(2026,7,25,14,0,tzinfo=ET);fresh=(now-timedelta(seconds=30)).isoformat()
  s=self.snap(now=now,state={"stage":"hold","content_valid":False,"source":{"name":"260725.docx","missing":True}},heartbeat={"prepare":fresh,"check_replies":fresh,"watchdog":fresh})
  self.assertEqual(s["overall"]["light"],"green")
  self.assertEqual(s["edition"]["light"],"orange")
  self.assertIn("Waiting for content",s["edition"]["label"])
  self.assertIn("No system repair",' '.join(x["detail"] for x in s["awareness"]))
 def test_stale_scheduler_during_active_window_is_red(self):
  now=datetime(2026,7,25,11,0,tzinfo=ET);stale=(now-timedelta(minutes=12)).isoformat();watch=(now-timedelta(minutes=5)).isoformat()
  s=self.snap(now=now,state={"stage":"hold","content_valid":False},heartbeat={"prepare":stale,"check_replies":stale,"watchdog":watch})
  self.assertEqual(s["systems"]["scheduler"]["light"],"red")
  self.assertEqual(s["overall"]["light"],"red")
 def test_failed_google_refresh_is_red_without_fake_expiry_date(self):
  now=datetime(2026,7,25,11,0,tzinfo=ET);fresh=(now-timedelta(seconds=20)).isoformat()
  s=self.snap(now=now,state={"stage":"review_sent","content_valid":True},heartbeat={"prepare":fresh,"check_replies":fresh,"watchdog":fresh},connectors={"google":"red","detail":"OAuth refresh failed."})
  self.assertEqual(s["systems"]["google"]["light"],"red")
  self.assertEqual(s["overall"]["light"],"red")
  self.assertNotIn("expires on",str(s).lower())
 def test_safe_no_content_close_is_orange_not_red(self):
  now=datetime(2026,7,25,16,10,tzinfo=ET);today=now.replace(hour=15,minute=0).isoformat();watch=(now-timedelta(minutes=5)).isoformat()
  s=self.snap(now=now,state={"stage":"not_sent","content_valid":False,"not_sent_reason":"The dated source was missing or invalid."},heartbeat={"auto_send":today,"watchdog":watch})
  self.assertEqual(s["edition"]["light"],"orange")
  self.assertNotEqual(s["overall"]["light"],"red")
 def test_valid_content_not_sent_is_red_business_incident(self):
  now=datetime(2026,7,25,16,10,tzinfo=ET);today=now.replace(hour=15,minute=0).isoformat();watch=(now-timedelta(minutes=5)).isoformat()
  s=self.snap(now=now,state={"stage":"not_sent","content_valid":True,"not_sent_reason":"No approval."},heartbeat={"auto_send":today,"watchdog":watch})
  self.assertEqual(s["edition"]["light"],"red")
 def test_before_window_is_sleeping_not_stale(self):
  now=datetime(2026,7,25,6,30,tzinfo=ET);watch=(now-timedelta(minutes=10)).isoformat()
  s=self.snap(now=now,state={},heartbeat={"watchdog":watch})
  self.assertEqual(s["systems"]["scheduler"]["light"],"green")
  self.assertIn("scheduled",s["systems"]["scheduler"]["detail"].lower())
 def test_missing_watchdog_heartbeat_is_orange_until_observed(self):
  now=datetime(2026,7,25,14,0,tzinfo=ET);fresh=(now-timedelta(seconds=15)).isoformat()
  s=self.snap(now=now,state={"stage":"hold","content_valid":False},heartbeat={"prepare":fresh,"check_replies":fresh})
  self.assertEqual(s["systems"]["watchdog"]["light"],"orange")

if __name__=='__main__':unittest.main()
