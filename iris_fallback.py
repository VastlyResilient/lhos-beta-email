"""Safe, varied evergreen fallback content for the LifeHouse OS Daily Briefing."""
from __future__ import annotations

import json
import re
from datetime import date, datetime

import httpx


# Four distinct weeks. Human-authored dated content always takes precedence over this catalog.
TOPICS = {
    0: [
        ("weekly-command-center", "A Smarter Start to the Week", "A Household Win", "Create a 15-minute household command center before the week gets busy.", ["Put work, school, travel, appointments, and home commitments in one view.", "Circle the three moments most likely to create friction.", "Give every must-do task one clear owner.", "Choose one task that can intentionally wait."], "Which commitment is most likely to surprise your household this week?"),
        ("calendar-collisions", "Prevent Calendar Collisions", "Work + Home", "Look for schedule conflicts before they become rushed handoffs.", ["Compare each person’s calendar for the next seven days.", "Mark pickups, departures, appointments, and deadline windows.", "Name the backup plan for the busiest transition.", "Place a short buffer around the most fragile commitment."], "Where would a 20-minute buffer create the most breathing room?"),
        ("meal-grocery-map", "Build a Simple Meal and Grocery Map", "A Household Win", "Plan enough food structure to reduce decisions without overplanning the week.", ["Choose three dependable dinners and one flexible leftovers night.", "List the ingredients shared across more than one meal.", "Identify the evening that needs the easiest option.", "Assign one person to confirm the grocery list."], "Which evening needs a minimum-effort meal plan?"),
        ("monthly-admin-reset", "Reset the Household Admin", "A Household Win", "Give recurring household administration one visible place and one short review.", ["Collect bills, forms, appointments, and renewals in one list.", "Remove anything already resolved.", "Assign an owner and due date to every remaining item.", "Schedule the next 20-minute review before closing the list."], "Which household task stays invisible until it becomes urgent?"),
    ],
    1: [
        ("entryway-landing-zone", "Create an Entryway Landing Zone", "Weekend Challenge", "Give the items that enter and leave your home a dependable landing place.", ["Remove anything that does not belong near the door.", "Choose one home each for keys, bags, mail, and shoes.", "Create a small outgoing area for returns and errands.", "Reset the zone for two minutes each evening."], "What item causes the most last-minute searching in your house?"),
        ("pantry-zones", "Organize the Pantry by Use", "A Household Win", "Group food by how your household actually cooks rather than by perfect-looking categories.", ["Create simple zones for breakfast, snacks, dinner basics, and backstock.", "Move everyday items where they are easiest to reach.", "Place soon-to-expire food where it will be seen first.", "Add missing staples to one shared list."], "Which pantry category creates the most duplicate buying?"),
        ("laundry-flow", "Build a Laundry Flow That Avoids Piles", "A Household Win", "Treat laundry as a visible sequence with clear handoffs instead of one endless task.", ["Decide where dirty clothes are collected.", "Choose predictable wash windows for the busiest categories.", "Create one place for items waiting to be folded.", "Set a same-day destination for clean clothes."], "Where does laundry most often stop moving in your house?"),
        ("household-records", "Make Household Records Easier to Find", "Weekend Challenge", "Create one dependable index for the documents your household needs repeatedly.", ["List the records people search for most often.", "Separate active documents from long-term archives.", "Use clear names that include the subject and year.", "Record where originals are stored without putting sensitive details in shared notes."], "Which document would be hardest to locate under time pressure?"),
    ],
    2: [
        ("work-home-handoff", "Create a Better Work-to-Home Handoff", "Work + Home", "Close the workday clearly before stepping into household responsibilities.", ["Write down tomorrow’s first work task.", "Check the household calendar for the next transition.", "Choose the minimum viable version of the evening.", "Use one physical action to mark the change of roles."], "Which transition creates more friction: ending work, dinner, or bedtime?"),
        ("protect-focus", "Protect Focus While Managing a Household", "Work + Home", "Make focus visible so household needs and concentrated work can coexist.", ["Name the specific block of time that needs protection.", "Tell the household what counts as an interruption.", "Prepare one place to capture nonurgent requests.", "Plan a clear check-in when the focus block ends."], "What household request most often interrupts concentrated work?"),
        ("partner-handoffs", "Improve Household Handoffs", "Work + Home", "A good handoff transfers responsibility, context, and the definition of done.", ["Name the task and the person who owns the outcome.", "Share only the context needed for the next action.", "Agree on when the handoff is complete.", "Avoid keeping invisible responsibility after transferring ownership."], "Which recurring task needs a clearer owner?"),
        ("emergency-coverage", "Create an Everyday Backup Plan", "Work + Home", "Prepare simple coverage for the ordinary disruptions that can derail a household day.", ["Choose one common disruption such as a late meeting or sick day.", "Identify the first responsibility that needs coverage.", "Name the backup person or simplified option.", "Store the plan where the household can find it quickly."], "What predictable disruption currently has no backup plan?"),
    ],
    3: [
        ("travel-48-hour", "The 48-Hour Travel-Ready Checklist", "Travel Ready", "Prepare the household as carefully as the suitcase before a trip.", ["Confirm documents, medications, charging cables, and transportation.", "Assign care for mail, packages, pets, and plants.", "Clear perishables and trash before departure.", "Prepare one easy return-home meal."], "Which pre-trip responsibility is easiest to forget?"),
        ("pack-by-activity", "Pack by Activity Instead of by Day", "Travel Ready", "Organize packing around what you will do so missing items become easier to notice.", ["List the trip’s major activities.", "Create a small group of items for each activity.", "Identify what can serve more than one purpose.", "Keep first-day essentials together and easy to reach."], "Which activity on your next trip needs its own checklist?"),
        ("house-shutdown", "Leave the House Travel-Ready", "Travel Ready", "Use a short shutdown sequence to reduce uncertainty after you leave.", ["Check doors, windows, appliances, and temperature settings.", "Pause or redirect deliveries when appropriate.", "Photograph only the non-sensitive details you may question later.", "Give one trusted person the information they actually need."], "What do you most often wonder about after leaving home?"),
        ("travel-reentry", "Plan the Post-Travel Re-entry", "Travel Ready", "Leave one favor for your future self so returning home feels less disruptive.", ["Prepare clean sheets or towels before leaving.", "Keep the first needed outfit easy to find.", "Choose a simple first meal.", "Reserve a short unpacking window instead of losing the entire day."], "What one preparation would make coming home noticeably easier?"),
    ],
    4: [
        ("family-ops-meeting", "Run a 20-Minute Family Operations Meeting", "A Household Win", "Use a short household meeting to surface decisions without turning the evening into a long discussion.", ["Review only the next seven days.", "Name schedule conflicts and decisions that need an owner.", "Confirm transportation, meals, and unusual commitments.", "End with one written list of agreed next actions."], "What decision keeps getting discussed without being assigned?"),
        ("invisible-work", "Make Invisible Household Work Visible", "A Household Win", "Notice the planning and remembering that happens before a task is ever completed.", ["List recurring work that someone quietly tracks.", "Separate ownership from occasional help.", "Move reminders into a shared system where appropriate.", "Redistribute one task from beginning to end."], "Which invisible responsibility deserves a clearer owner?"),
        ("delegate-clearly", "Delegate Without Micromanaging", "Work + Home", "Transfer outcomes rather than individual motions so responsibility is real and clear.", ["Describe the finished result.", "Share the deadline and genuine constraints.", "Let the owner choose the method.", "Agree on one check-in instead of repeated monitoring."], "Where could your household trade instructions for clearer ownership?"),
        ("weekend-plan", "Build a Low-Stress Weekend Plan", "A Household Win", "Balance commitments, maintenance, connection, and rest before the weekend fills itself.", ["Choose one must-do household task.", "Choose one activity that restores energy.", "Protect an unscheduled block.", "Decide what can move to next week without guilt."], "What would make this weekend feel successful rather than merely busy?"),
    ],
    5: [
        ("declutter-sprint", "Try a 20-Minute Declutter Sprint", "Weekend Challenge", "Improve one visible area without creating an exhausting whole-house project.", ["Choose a space small enough to finish in 20 minutes.", "Sort items into keep here, move elsewhere, donate, and discard.", "Return moved items before starting another zone.", "Stop when the timer ends and notice what changed."], "Which small space would give your household the quickest visible win?"),
        ("maintenance-triage", "Triage Household Maintenance", "Weekend Challenge", "Separate urgent maintenance from important work and cosmetic wishes.", ["List every open maintenance concern without solving it yet.", "Mark anything affecting safety or active damage first.", "Schedule important preventive work next.", "Keep cosmetic ideas on a separate someday list."], "Which small repair is likely to become expensive if ignored?"),
        ("seasonal-rotation", "Create a Seasonal Rotation", "Weekend Challenge", "Move seasonal items with a repeatable process instead of rediscovering the same clutter each year.", ["Choose one category such as clothing, sports gear, or decorations.", "Remove damaged or unused items before storing anything.", "Label containers by category and season.", "Record where the next needed items are stored."], "Which seasonal category takes the most effort to find or put away?"),
        ("photo-document-cleanup", "Clean Up Photos and Household Files", "Weekend Challenge", "Reduce digital clutter by finishing one bounded category rather than sorting an entire archive.", ["Choose one month, event, or document type.", "Remove obvious duplicates and unusable files.", "Give important files clear names.", "Back up the finished group before moving on."], "Which digital category would be most valuable to organize first?"),
    ],
    6: [
        ("sunday-reset", "The Sunday Household Reset", "Sunday Reset", "Create a calm weekly reset that covers logistics without consuming the entire day.", ["Review the next seven days together.", "Confirm meals, transportation, and unusual commitments.", "Reset one high-traffic household area.", "Choose one pressure point to simplify before Monday."], "What can your household decide today that will make Monday easier?"),
        ("meal-prep-light", "Meal Prep Without Losing Sunday", "Sunday Reset", "Prepare only the parts of meals that remove the most weekday friction.", ["Wash or portion the ingredients used most often.", "Prepare one dependable protein or base ingredient.", "Choose one night for leftovers or an easy backup.", "Stop before preparation becomes an all-day project."], "Which single prep task saves the most time during your week?"),
        ("recovery-plan", "Put Recovery on the Household Schedule", "Sunday Reset", "Treat rest as part of household capacity rather than what happens after everything is finished.", ["Identify the person or day with the least margin.", "Protect one block without errands or obligations.", "Reduce one optional commitment.", "Agree on what genuine recovery looks like this week."], "Where does your household need more margin rather than more efficiency?"),
        ("weekly-buffers", "Plan for the Week You Will Actually Have", "Sunday Reset", "Build buffers and simplified options into the plan before the unexpected appears.", ["Add transition time around the busiest commitments.", "Choose the first task to drop if the week becomes overloaded.", "Keep one easy meal and one backup transportation plan.", "Reserve a short catch-up block instead of filling every hour."], "Which part of next week needs a backup plan now?"),
    ],
}


def _topic_from_item(item) -> dict:
    return {"id": item[0], "title": item[1], "section_title": item[2], "core": item[3], "steps": list(item[4]), "question": item[5]}

def select_topic(day: date) -> dict:
    week = (day.toordinal() // 7) % 4
    return _topic_from_item(TOPICS[day.weekday()][week])

def select_topic_for_reference(day: date, reference: str) -> dict:
    """Use an incomplete trusted reference only to select a safe evergreen theme."""
    text=(reference or "").lower();week=(day.toordinal() // 7) % 4
    routes=[
        (r"\b(?:travel|trip|packing|vacation|flight|hotel)\b",3),
        (r"\b(?:work|office|career|handoff|focus)\b",2),
        (r"\b(?:declutter|clutter|organize|storage|entryway|pantry|laundry)\b",5),
        (r"\b(?:calendar|schedule|meal|grocery|admin)\b",0),
        (r"\b(?:family|delegate|responsibilit|weekend)\b",4),
        (r"\b(?:reset|rest|recovery|sunday|buffer)\b",6),
    ]
    for pattern,weekday in routes:
        if re.search(pattern,text,re.I):return _topic_from_item(TOPICS[weekday][week])
    return select_topic(day)


def usable_reference(raw: str) -> str:
    text = re.sub(r"\s+", " ", raw or "").strip()
    if len(text) < 30:
        return ""
    low = text.lower()
    visible = re.sub(r"(?:tbd|todo|placeholder|coming soon|add content|insert text|\(\(.*?\)\))", "", low)
    if len(re.sub(r"\W", "", visible)) < 20:
        return ""
    return text[:5000]


def _paragraphs(*parts: str) -> str:
    return "\n\n".join(p.strip() for p in parts if p and p.strip())


def deterministic_bundle(day: date, reference: str = "") -> dict:
    topic = select_topic_for_reference(day, usable_reference(reference))
    steps = "\n".join(f"{i}. {step}" for i, step in enumerate(topic["steps"], 1))
    intro = f"Today’s briefing combines a focused beta mission with a practical idea for {topic['title'].lower()}."
    sections = [
        {"title": "Today’s Beta Notes", "body": _paragraphs(f"Today’s focus is {topic['title'].lower()}.", topic["core"], "Small, repeatable household systems often create more breathing room than a complicated plan that is difficult to maintain.")},
        {"title": topic["section_title"], "body": _paragraphs("Try this practical sequence today:", steps, "Choose the smallest useful version first. A system becomes valuable when the people in the house can understand it and repeat it.")},
        {"title": "Today’s Beta Mission", "body": _paragraphs("Bring one real household situation into your LifeHouse OS beta experience today.", "Notice what feels immediately clear, where you hesitate, and what additional guidance would help you reach the outcome. Testing one realistic scenario gives the team more useful feedback than exploring without a specific goal.")},
        {"title": "Helpful Reminder", "body": _paragraphs("Specific feedback is the most useful feedback.", "Tell us what you expected to happen, what actually happened, and what would have made the next step clearer. That context helps the team understand the household problem behind the screen.")},
        {"title": "A Question for Your House", "body": topic["question"]},
        {"title": "Thank You", "body": _paragraphs("LifeHouse OS is being shaped by people willing to test real situations, notice the details, and share thoughtful feedback.", "Thank you for helping us make everyday life a little less overwhelming and a little more intentional.")},
    ]
    raw = "\n\n".join(f"{s['title']}\n{s['body']}" for s in sections)
    return {"subject": f"LifeHouse OS Daily Briefing — {topic['title']}", "intro": intro, "sections": sections, "raw": raw, "topic_id": topic["id"], "generator": "curated-v1", "reference_used": bool(reference)}


_FORBIDDEN_WITHOUT_REFERENCE = [
    r"\bsprint\s*\d+\b", r"\bsurvey (?:is|opens?|closes?)\b", r"\b(?:new|released|launched|shipping) feature\b", r"\bfeature\b.{0,30}\b(?:launches|released|ships|available)\b",
    r"\bbeta access\b", r"\bcomplimentary \d+-day\b", r"\b(?:survey|beta|access|application|sprint).{0,24}deadline\b", r"\bdeadline\b.{0,24}\b(?:survey|beta|access|application|sprint)\b", r"\b(?:users|testers) (?:asked|reported|completed)\b",
]


def validate_generated_bundle(bundle: dict, *, has_reference: bool) -> tuple[bool, list[str]]:
    reasons = []
    if not isinstance(bundle, dict):
        return False, ["bundle must be an object"]
    subject = str(bundle.get("subject") or "").strip()
    intro = str(bundle.get("intro") or "").strip()
    sections = bundle.get("sections")
    if not subject or "LifeHouse OS" not in subject:
        reasons.append("subject is missing the LifeHouse OS identity")
    if len(intro) < 40 or len(intro) > 500:
        reasons.append("intro length is outside the safe range")
    if not isinstance(sections, list) or not 4 <= len(sections) <= 8:
        reasons.append("four to eight varied sections are required")
        sections = []
    text_parts = [subject, intro]
    for i, section in enumerate(sections):
        if not isinstance(section, dict):
            reasons.append(f"section {i + 1} is not an object"); continue
        title = str(section.get("title") or "").strip(); body = str(section.get("body") or "").strip()
        if not 3 <= len(title) <= 70:
            reasons.append(f"section {i + 1} title is invalid")
        minimum_body = 35 if "question" in title.lower() else 60
        if len(body) < minimum_body or len(body) > 3000:
            reasons.append(f"section {i + 1} body length is invalid")
        if "<" in title or "<" in body:
            reasons.append(f"section {i + 1} must be plain text")
        text_parts.extend([title, body])
    blob = "\n".join(text_parts)
    if len(blob) < 900 or len(blob) > 14000:
        reasons.append("generated briefing length is outside the safe range")
    if re.search(r"\b(?:tbd|todo|placeholder|insert text|coming soon)\b|\(\(.*?\)\)", blob, re.I | re.S):
        reasons.append("placeholder language is not allowed")
    if re.search(r"\b(?:medical diagnosis|legal advice|financial advice|guaranteed result)\b", blob, re.I):
        reasons.append("regulated or guaranteed advice is not allowed")
    urls = re.findall(r"https?://[^\s)]+", blob)
    allowed = ("https://lifehouseos.app/feedback", "https://lifehouseos.com/", "https://lifehouseos.app/")
    if any(not u.startswith(allowed) for u in urls):
        reasons.append("an unapproved external link is present")
    # Incomplete references can guide the theme, but never authorize product claims.
    if any(re.search(pattern, blob, re.I) for pattern in _FORBIDDEN_WITHOUT_REFERENCE):
        reasons.append("an unverified product claim is present")
    if not re.search(r"\b(?:try|choose|list|review|identify|create|decide|notice|spend|tell|share)\b", blob, re.I):
        reasons.append("briefing lacks a concrete action")
    return not reasons, reasons


def _extract_json(text: str) -> dict:
    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.I | re.S)
    start = cleaned.find("{"); end = cleaned.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("model did not return a JSON object")
    return json.loads(cleaned[start:end + 1])


def generate_bundle(day: date, reference: str, api_key: str, base_url: str) -> dict:
    """Return validated curated copy; publication never depends on model output."""
    curated=deterministic_bundle(day,usable_reference(reference))
    ok,reasons=validate_generated_bundle(curated,has_reference=bool(reference))
    if not ok:raise RuntimeError("Curated fallback failed validation: "+"; ".join(reasons))
    return curated

def generate_for_date(date_key: str, reference: str, api_key: str, base_url: str) -> dict:
    return generate_bundle(datetime.strptime(date_key, "%Y-%m-%d").date(), reference, api_key, base_url)
