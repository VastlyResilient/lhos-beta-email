import unittest
from watchdog_status import google_workspace_outage, instatus_outage, github_statuspage_outage, partition_watchdog_alerts

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
 def test_predictive_alerts_do_not_mask_operational_failures(self):
  predictive,operational=partition_watchdog_alerts(['Railway cost projected above guardrail','backend health failed after repair'])
  self.assertEqual(len(predictive),1);self.assertEqual(operational,['backend health failed after repair'])
 def test_cost_only_alert_is_predictive(self):
  predictive,operational=partition_watchdog_alerts(['Railway workspace projected usage is high'])
  self.assertEqual(len(predictive),1);self.assertEqual(operational,[])
 def test_github_statuspage_schema(self):
  self.assertIsNone(github_statuspage_outage({"status":{"indicator":"none"}}))
  self.assertIn("GitHub",github_statuspage_outage({"status":{"indicator":"major"}}))

if __name__=='__main__':unittest.main()
