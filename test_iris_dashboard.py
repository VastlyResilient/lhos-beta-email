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
  import re as _re
  self.assertIn('.liquid-glass{',DASHBOARD_HTML)
  self.assertIn('.liquid-glass::before',DASHBOARD_HTML)
  self.assertIn('.liquid-glass--primary',DASHBOARD_HTML)
  self.assertIn('@supports not ((backdrop-filter:blur(1px))',DASHBOARD_HTML)
  self.assertIn('.glass-pill',DASHBOARD_HTML)
  self.assertIn('.icon-tile{',DASHBOARD_HTML)
  # brief: dark, thin, wallpaper-readable glass — blur 10-16px, saturation 110-125%
  blurs=[int(b) for b in _re.findall(r'blur\((\d+)px\) saturate\((\d+)%\)',DASHBOARD_HTML) for b in b]
  self.assertTrue(blurs,'no glass blur rules found')
  base=_re.search(r'\.liquid-glass\{[^}]+',DASHBOARD_HTML).group(0)
  self.assertIn('rgba(6,13,28',base)
  fills=[int(a) for a in _re.findall(r'rgba\(6,13,28,\.(\d+)\)',base)]
  self.assertTrue(all(18<=a<=24 for a in fills),f'base fill out of brief range: {fills}')
  self.assertIn('blur(13px) saturate(118%)',base)
  primary=_re.search(r'\.liquid-glass--primary\{[^}]+',DASHBOARD_HTML).group(0)
  pfills=[int(a) for a in _re.findall(r'rgba\(6,13,28,\.(\d+)\)',primary)]
  self.assertTrue(all(26<=a<=32 for a in pfills),f'primary fill out of range: {pfills}')
 def test_v2_deterministic_inter_font(self):
  from pathlib import Path as _P
  self.assertIn("@font-face{font-family:'Inter'",DASHBOARD_HTML)
  self.assertIn('/assets/fonts/inter-var.woff2',DASHBOARD_HTML)
  self.assertIn('/assets/fonts/inter-var-italic.woff2',DASHBOARD_HTML)
  self.assertIn("font-weight:100 900",DASHBOARD_HTML)
  for f in ('inter-var.woff2','inter-var-italic.woff2'):
   fp=_P('/Users/bobby/lhos-beta-email/assets/fonts')/f
   self.assertTrue(fp.exists() and fp.stat().st_size>100000,str(fp))
  self.assertNotIn('fonts.googleapis.com',DASHBOARD_HTML)
  self.assertNotIn('fonts.gstatic.com',DASHBOARD_HTML)
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
 def test_v2_raster_icons_wired(self):
  from pathlib import Path as _P
  names=['iris-eye','pulse','layers','doc','arrow-ahead','check-circle','database','link','clock','shield-cloud','doc-spark','clock-poll','plane','bell','refresh','clock-key','shield-heal','info']
  for name in names:
   self.assertIn(f'/assets/icons/raster/{name}.webp',DASHBOARD_HTML,name)
   for suffix in ('.webp','.png','-64.png','-128.png','-256.png'):
    f=_P('/Users/bobby/lhos-beta-email/assets/icons/raster')/(name+suffix)
    self.assertTrue(f.exists() and f.stat().st_size>500,str(f))
  self.assertNotIn('<use href="#i-',DASHBOARD_HTML)
  self.assertIn('object-fit:contain',DASHBOARD_HTML)
  self.assertIn('/assets/icons/raster/iris-eye-64.png',DASHBOARD_HTML)
 def test_v2_truthful_status_logic(self):
  self.assertIn("green:['Everything is<br>operating normally'",DASHBOARD_HTML)
  self.assertIn("' of 4 verified'",DASHBOARD_HTML)
  self.assertIn('Manual repair needed',DASHBOARD_HTML)
  self.assertIn('Evidence-based',DASHBOARD_HTML)
  self.assertIn('IntersectionObserver',DASHBOARD_HTML)
  self.assertIn('prefers-reduced-motion',DASHBOARD_HTML)
 def test_mobile_topbar_fits_four_links(self):
  self.assertIn('.topbar .rail-item{flex-direction:row;gap:6px;padding:9px 6px;font-size:11.5px',DASHBOARD_HTML)
  self.assertIn('.topbar nav{display:flex;gap:4px;flex:1;overflow-x:auto',DASHBOARD_HTML)
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
