import unittest
from watchdog_status import google_workspace_outage, instatus_outage, github_statuspage_outage

class WatchdogStatusTests(unittest.TestCase):
 def test_google_active_gmail_incident_is_outage(self):
  p=[{"end":None,"service_name":"Gmail","most_recent_update":{"status":"SERVICE_DISRUPTION"}}]
  self.assertIn("Gmail",google_workspace_outage(p))
 def test_google_resolved_or_invalid_schema_is_not_outage(self):
  self.assertIsNone(google_workspace_outage([{"end":"2026-07-01T00:00:00Z","service_name":"Gmail","most_recent_update":{"status":"AVAILABLE"}}]))
  self.assertIsNone(google_workspace_outage({"html":"redirect"}))
 def test_railway_instatus_schema(self):
  self.assertIsNone(instatus_outage({"page":{"name":"Railway","status":"UP"}}))
  self.assertIn("Railway",instatus_outage({"page":{"name":"Railway","status":"HASISSUES"}}))
  self.assertIsNone(instatus_outage("<html>"))
 def test_github_statuspage_schema(self):
  self.assertIsNone(github_statuspage_outage({"status":{"indicator":"none"}}))
  self.assertIn("GitHub",github_statuspage_outage({"status":{"indicator":"major"}}))

if __name__=='__main__':unittest.main()
