#!/usr/bin/env python3
"""
LifeHouse OS Beta Email HTML Template
Matches the established brand identity from previous emails.

Brand: Navy #0E1B33, Aqua #4BC0C4, Sand #E6B35B
Font: Nunito (Google Fonts)
Logo: https://files.catbox.moe/1nlat9.png (transparent)
Iris signature: https://files.catbox.moe/arzsbd.gif
"""

import html

NAVY = "#0E1B33"
AQUA = "#4BC0C4"
SAND = "#E6B35B"
FONT = "'Nunito', 'Quicksand', 'Avenir Next', 'Aptos', 'Segoe UI', Arial, sans-serif"
LOGO_URL = "https://files.catbox.moe/1nlat9.png"
IRIS_GIF = "https://files.catbox.moe/arzsbd.gif"


def build_beta_email(sections: dict, date_str: str) -> str:
    """Build the full HTML email from section content.
    
    Matches the LifeHouse OS brand identity used in previous emails:
    - White background, full-width layout
    - Logo centered with sand separator
    - Navy-bordered section cards
    - Full Iris signature block
    """
    
    section_configs = [
        ("beta_notes", "Today's Beta Notes"),
        ("what_changed", "What Changed"),
        ("known_issues", "Known Issues"),
        ("helpful_reminder", "Helpful Reminder"),
        ("what_were_watching", "What We're Watching"),
        ("thank_you", "Thank You"),
        ("support_contact", "Support & Feedback"),
    ]
    
    section_rows = []
    for key, title in section_configs:
        content = sections.get(key, "").strip()
        if not content:
            continue
        
        section_rows.append(f"""        <tr>
          <td style="padding: 0 48px 20px 48px;">
            <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border: 1px solid {NAVY}; border-radius: 10px;">
              <tr>
                <td style="padding: 22px 24px;">
                  <h2 style="margin: 0 0 10px 0; font-size: 17px; color: {NAVY}; font-weight: 700;">{title}</h2>
                  <div style="font-size: 15px; color: #2c3e50; line-height: 1.7;">
                    {content}
                  </div>
                </td>
              </tr>
            </table>
          </td>
        </tr>""")
    
    sections_html = "\n".join(section_rows)
    
    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<style>@import url('https://fonts.googleapis.com/css2?family=Nunito:wght@400;600;700;800&display=swap');</style>
</head>
<body style="margin: 0; padding: 0; background-color: #ffffff; font-family: {FONT};">

<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background-color: #ffffff;">

  <!-- HEADER: Logo centered, no colored banner -->
  <tr>
    <td style="padding: 36px 48px 20px 48px; text-align: center;">
      <img src="{LOGO_URL}" alt="LifeHouse OS" width="320" style="width: 320px; max-width: 100%; height: auto; border: 0; outline: none; text-decoration: none;">
    </td>
  </tr>

  <!-- Sand separator under logo -->
  <tr>
    <td style="padding: 0 48px 28px 48px;">
      <div style="border-top: 2px solid {SAND};"></div>
    </td>
  </tr>

  <!-- Greeting -->
  <tr>
    <td style="padding: 0 48px 8px 48px;">
      <p style="margin: 0 0 18px 0; font-size: 16px; color: {NAVY}; line-height: 1.7; font-weight: 600;">RECIPIENT_NAME_PLACEHOLDER</p>
      <p style="margin: 0 0 18px 0; font-size: 15px; color: #2c3e50; line-height: 1.7;">
        Here's your daily update on LifeHouse OS for {date_str}. We appreciate you being part of our beta journey.
      </p>
    </td>
  </tr>

  <!-- Content Sections -->
  {sections_html}

  <!-- Closing -->
  <tr>
    <td style="padding: 8px 48px 36px 48px;">
      <p style="margin: 0; font-size: 15px; color: #2c3e50; line-height: 1.7;">
        See you in the house!
      </p>
      <p style="margin: 18px 0 0 0; font-size: 15px; color: #2c3e50; line-height: 1.7;">
        Warm regards,<br>Iris
      </p>
    </td>
  </tr>

  <!-- Sand Separator before signature -->
  <tr>
    <td style="padding: 0 48px;">
      <div style="border-top: 2px solid {SAND}; margin-bottom: 20px;"></div>
    </td>
  </tr>

  <!-- Iris Signature -->
  <tr>
    <td style="padding: 0 48px 36px 48px;">
      <img src="{IRIS_GIF}" alt="Iris" width="94" height="96" style="width: 94px; height: 96px;"><br>
      <span style="font-family: {FONT}; font-size: 13px; color: #2c3e50;">Iris &mdash; Concierge and Chief of Staff</span><br>
      <span style="font-family: {FONT}; font-size: 14px; color: {NAVY}; font-weight: 800;">LifeHouse</span><span style="font-family: {FONT}; font-size: 14px; color: {AQUA}; font-weight: 800;">OS</span><br>
      <span style="font-family: {FONT}; font-size: 12px; color: #2c3e50;">
        <a href="mailto:iris@lifehouseos.com" style="color: {NAVY}; text-decoration: underline;">Iris@LifeHouseOS.com</a><br>
        <a href="https://lifehouseos.app/privacy" style="color: {NAVY}; text-decoration: underline;">Privacy Policy</a>&nbsp;&nbsp;|&nbsp;&nbsp;
        <a href="https://lifehouseos.app/terms" style="color: {NAVY}; text-decoration: underline;">Terms of Service</a>
      </span>
    </td>
  </tr>

  <!-- Footer -->
  <tr>
    <td style="padding: 16px 48px 28px 48px; border-top: 1px solid #ececec;">
      <p style="margin: 0 0 6px 0; font-size: 12px; color: #9aa7b3; line-height: 1.5; text-align: center;">
        You're receiving this email because you're an active beta tester for LifeHouse OS.<br>
        Questions? Reply to this email or visit <a href="https://lifehouseos.app/feedback" style="color: {NAVY}; text-decoration: underline;">lifehouseos.app/feedback</a>
      </p>
      <p style="margin: 6px 0 0 0; font-size: 11px; color: #b0bcc8; line-height: 1.5; text-align: center;">
        <a href="UNSUB_URL_PLACEHOLDER" style="color: #b0bcc8; text-decoration: underline;">Unsubscribe</a>
      </p>
    </td>
  </tr>

</table>
</body>
</html>"""


def _plain_text_html(text: str) -> str:
    """Render trusted plain-text editorial copy without permitting model-supplied HTML."""
    blocks = []
    for part in str(text or "").split("\n"):
        line = part.strip()
        if not line:
            continue
        escaped = html.escape(line)
        if line[:2].isdigit() and len(line) > 2 and line[1] in ".)":
            blocks.append(f'<p style="margin: 0 0 7px 0;">{escaped}</p>')
        else:
            blocks.append(f'<p style="margin: 0 0 12px 0;">{escaped}</p>')
    return "".join(blocks)


def build_varied_email(sections: list[dict], date_str: str, intro: str) -> str:
    """Build the flexible, separator-led Daily Briefing used for Iris fallbacks."""
    section_rows = []
    for index, section in enumerate(sections):
        title = html.escape(str(section.get("title") or "").strip())
        body = _plain_text_html(section.get("body") or "")
        if not title or not body:
            continue
        accent = AQUA if index % 2 == 0 else NAVY
        section_rows.append(f"""
  <tr>
    <td class="email-pad" style="padding: 22px 48px 6px 48px;">
      <div style="border-top: 2px solid {SAND}; padding-top: 22px;">
        <div style="font-size: 12px; font-weight: 800; letter-spacing: 1.2px; text-transform: uppercase; color: {accent}; margin-bottom: 9px;">{title}</div>
        <div class="body-copy" style="font-size: 15px; line-height: 1.72; color: #26364a;">{body}</div>
      </div>
    </td>
  </tr>""")
    sections_html = "\n".join(section_rows)
    safe_intro = html.escape(str(intro or "").strip())
    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<style>
@import url('https://fonts.googleapis.com/css2?family=Nunito:wght@400;600;700;800&display=swap');
@media only screen and (max-width: 600px) {{
  .email-pad {{ padding-left: 18px !important; padding-right: 18px !important; }}
  .body-copy {{ font-size: 13px !important; }}
}}
</style>
</head>
<body style="margin: 0; padding: 0; background-color: #ffffff; font-family: {FONT};">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="width: 100%; background-color: #ffffff;">
  <tr>
    <td class="email-pad" style="padding: 34px 48px 16px 48px; text-align: center;">
      <img src="{LOGO_URL}" alt="LifeHouse OS" width="320" style="width: 320px; max-width: 100%; height: auto; border: 0; outline: none; text-decoration: none;">
    </td>
  </tr>
  <tr>
    <td class="email-pad" style="padding: 0 48px 20px 48px; text-align: center;">
      <div style="font-size: 12px; font-weight: 800; letter-spacing: 1.5px; color: {AQUA}; text-transform: uppercase;">Daily Briefing &middot; {html.escape(date_str)}</div>
    </td>
  </tr>
  <tr>
    <td class="email-pad" style="padding: 4px 48px 8px 48px;">
      <p style="margin: 0 0 14px 0; font-size: 16px; line-height: 1.7; color: {NAVY}; font-weight: 700;">RECIPIENT_NAME_PLACEHOLDER</p>
      <p class="body-copy" style="margin: 0 0 10px 0; font-size: 15px; line-height: 1.72; color: #26364a;">Good day, Beta Team!</p>
      <p class="body-copy" style="margin: 0; font-size: 15px; line-height: 1.72; color: #26364a;">Welcome to today&rsquo;s edition of the LifeHouse OS Daily Briefing. {safe_intro}</p>
    </td>
  </tr>
  {sections_html}
  <tr>
    <td class="email-pad" style="padding: 24px 48px 36px 48px;">
      <p class="body-copy" style="margin: 0 0 16px 0; font-size: 15px; line-height: 1.7; color: #26364a;">See you in the house!</p>
      <p class="body-copy" style="margin: 0 0 18px 0; font-size: 15px; line-height: 1.7; color: #26364a;">Warm regards,<br>Iris</p>
      <img src="{IRIS_GIF}" alt="Iris" width="94" height="96" style="width: 94px; height: 96px; border: 0;"><br>
      <span style="font-size: 13px; line-height: 1.6; color: #26364a;"><strong style="color: {NAVY};">Iris &mdash; Concierge and Chief of Staff</strong><br>
      <a href="mailto:iris@lifehouseos.com" style="color: {AQUA};">Iris@LifeHouseOS.com</a><br>
      <a href="https://lifehouseos.app/feedback" style="color: {AQUA};">Support &amp; Feedback</a></span>
    </td>
  </tr>
  <tr>
    <td class="email-pad" style="padding: 18px 48px 28px 48px; border-top: 1px solid #e8edf1; text-align: center; font-size: 11px; line-height: 1.5; color: #8b98a8;">
      You&rsquo;re receiving this email because you&rsquo;re an active beta tester for LifeHouse OS.<br>
      <a href="UNSUB_URL_PLACEHOLDER" style="color: #8b98a8;">Unsubscribe</a>
    </td>
  </tr>
</table>
</body>
</html>"""


def build_draft_notification(approval_url: str, date_str: str, recipient_count_hint: str = "") -> str:
    """Build the plain-text draft notification email sent to approvers."""
    return f"""LifeHouse OS Beta Email - Draft Ready for Approval

Date: {date_str}

A new beta daily email has been composed and is ready for your review.

To review and approve, click the link below:
{approval_url}

Once approved by any one of Kristina, Thomas, or Bobby, the email will be sent immediately to all active beta users.

Recipient source: Google Contacts group "LifeHouseOS Beta Testers"
{recipient_count_hint}

- LifeHouse OS Automated Pipeline
"""
