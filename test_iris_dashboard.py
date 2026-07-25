import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from iris_dashboard import DASHBOARD_HTML, build_snapshot

ET=ZoneInfo("America/New_York")

class IrisDashboardHealthTests(unittest.TestCase):
 def test_dashboard_includes_inline_favicon(self):
  self.assertIn('rel="icon"',DASHBOARD_HTML)
 def test_v2_vertical_rail_and_navigation(self):
  self.assertIn('class="rail liquid-glass"',DASHBOARD_HTML)
  self.assertIn('aria-label="IRIS Health navigation"',DASHBOARD_HTML)
  for section in ('overview','systems','edition','ahead'):
   self.assertIn(f'data-nav="{section}"',DASHBOARD_HTML)
   self.assertIn(f'href="#{section}"',DASHBOARD_HTML)
  self.assertIn('aria-current="page"',DASHBOARD_HTML)
  self.assertIn('LIVE',DASHBOARD_HTML)
 def test_v2_liquid_glass_system(self):
  self.assertIn('.liquid-glass{',DASHBOARD_HTML)
  self.assertIn('.liquid-glass::before',DASHBOARD_HTML)
  self.assertIn('.liquid-glass::after',DASHBOARD_HTML)
  self.assertIn('.liquid-glass--primary',DASHBOARD_HTML)
  import re as _re
  blur=_re.search(r'--iris-blur:(\d+)px',DASHBOARD_HTML)
  self.assertTrue(blur and int(blur.group(1))>=28,'backdrop blur must stay >=28px')
  # glass must stay genuinely translucent so the wallpaper reads through
  fills=[float(a) for a in _re.findall(r'\.liquid-glass\{[^}]*?rgba\(\d+,\d+,\d+,\.(\d+)\)',DASHBOARD_HTML)]
  alphas=_re.findall(r'rgba\(\d+,\s*\d+,\s*\d+,\.(\d{2})\)',DASHBOARD_HTML.split('.liquid-glass{')[1].split('}')[0])
  self.assertTrue(all(int(a)<=45 for a in alphas),f'base glass fill too opaque: {alphas}')
  self.assertIn('saturate(190%)',DASHBOARD_HTML)
  self.assertIn('@supports not ((backdrop-filter:blur(1px))',DASHBOARD_HTML)
  self.assertIn('.glass-pill',DASHBOARD_HTML)
  self.assertIn('.icon-tile{',DASHBOARD_HTML)
 def test_v2_four_sections_and_wallpapers(self):
  for section in ('overview','systems','edition','ahead'):
   self.assertIn(f'data-section="{section}"',DASHBOARD_HTML)
   self.assertIn(f'/assets/wallpaper-{section}.webp',DASHBOARD_HTML)
  self.assertIn('background-size:cover',DASHBOARD_HTML)
  self.assertIn('Everything is<br>operating normally',DASHBOARD_HTML)
  self.assertIn('Core systems',DASHBOARD_HTML)
  self.assertIn('Today’s edition',DASHBOARD_HTML)
  self.assertIn('See <em>ahead</em> clearly',DASHBOARD_HTML)
 def test_v2_no_external_assets_or_placeholders(self):
  import re as _re
  hosts=set(_re.findall(r'https?://([^/"\'\s)]+)',DASHBOARD_HTML))
  allowed={'d8j0ntlcm91z4.cloudfront.net','www.w3.org'}
  self.assertEqual(hosts-allowed,set(),f'unexpected external hosts: {hosts-allowed}')
  self.assertIn('hf_20260611_104107_121bfb5a-b1df-4e0d-8240-25b81f7cc85d.mp4',DASHBOARD_HTML)
  self.assertNotIn('https://db.onlinewebfonts.com',DASHBOARD_HTML)
  self.assertNotIn('NOVA_AI',DASHBOARD_HTML)
  self.assertNotIn('Today AI',DASHBOARD_HTML)
  self.assertNotIn('Flexo',DASHBOARD_HTML)
  self.assertNotIn('lucide',DASHBOARD_HTML.lower())
 def test_v2_custom_icon_sprite(self):
  for icon in ('iris-eye','pulse','layers','doc','arrow-ahead','check-circle','database','link','clock','shield-cloud','doc-spark','clock-poll','plane','bell','refresh','clock-key','shield-heal','info'):
   self.assertIn(f'id="i-{icon}"',DASHBOARD_HTML)
  self.assertIn('stroke-linecap="round"',DASHBOARD_HTML)
 def test_v2_truthful_status_logic(self):
  self.assertIn("green:['Everything is<br>operating normally'",DASHBOARD_HTML)
  self.assertIn("' of 4 verified'",DASHBOARD_HTML)
  self.assertIn('Manual repair needed',DASHBOARD_HTML)
  self.assertIn('Evidence-based',DASHBOARD_HTML)
  self.assertIn('IntersectionObserver',DASHBOARD_HTML)
  self.assertIn('prefers-reduced-motion',DASHBOARD_HTML)
 def test_v2_scroll_experience(self):
  self.assertIn('class="scroll-video"',DASHBOARD_HTML)
  self.assertIn('id="fallbackVideo"',DASHBOARD_HTML)
  self.assertIn('id="videoCanvas"',DASHBOARD_HTML)
  self.assertIn('createImageBitmap',DASHBOARD_HTML)
  self.assertIn('requestAnimationFrame',DASHBOARD_HTML)
  self.assertIn('smoothed',DASHBOARD_HTML)
  self.assertIn('class="spacer"',DASHBOARD_HTML)
  self.assertGreaterEqual(DASHBOARD_HTML.count('class="spacer"'),3)
  self.assertIn('min-height:100vh',DASHBOARD_HTML)
  self.assertIn('scroll spy',DASHBOARD_HTML)
  self.assertIn('aria-current',DASHBOARD_HTML)
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
