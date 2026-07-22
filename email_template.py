#!/usr/bin/env python3
"""
LifeHouse OS Beta Email HTML Template
Matches the established brand identity from previous emails.

Brand: Navy #0E1B33, Aqua #4BC0C4, Sand #E6B35B
Font: Nunito (Google Fonts)
Logo: https://files.catbox.moe/1nlat9.png (transparent)
Iris signature: https://files.catbox.moe/arzsbd.gif
"""

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
      <img src="{LOGO_URL}" alt="LifeHouse OS" width="280" height="75" style="width: 280px; height: auto; border: 0; outline: none; text-decoration: none;">
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
