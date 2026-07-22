#!/usr/bin/env python3
"""Fail-closed content validation for LifeHouse OS daily beta emails."""
import re
from html import unescape

PLACEHOLDER_PATTERNS = [
    r"\[\s*(?:insert|tbd|todo|placeholder|add|enter|update)[^\]]*\]",
    r"\b(?:tbd|todo|content goes here|insert here)\b",
]
GENERIC_ONLY_PHRASES = {
    "daily update from the lifehouse os team",
    "thank you for being a valued beta tester",
    "the lifehouse os beta is ongoing",
    "thank you for your continued participation and feedback",
    "no specific content found",
    "today's beta notes",
    "thank you",
}
MEANINGFUL_HINTS = {
    "fix", "fixed", "issue", "bug", "feature", "changed", "change", "new",
    "update", "test", "testing", "reminder", "meeting", "feedback", "release",
    "improved", "improvement", "resolved", "watching", "announcement", "challenge",
    "stat", "users", "referral", "dashboard", "login", "workflow", "mobile", "app",
}

def plain_text(value: str) -> str:
    value = unescape(re.sub(r"<[^>]+>", " ", value or ""))
    return re.sub(r"\s+", " ", value).strip()

def validate_daily_content(raw_content: str):
    text = plain_text(raw_content)
    lower = text.lower()
    reasons = []
    if not text:
        reasons.append("source is empty")
    if len(text) < 180:
        reasons.append(f"source is too short ({len(text)} characters; minimum 180)")
    found_placeholders = [pat for pat in PLACEHOLDER_PATTERNS if re.search(pat, lower, re.I)]
    if found_placeholders:
        reasons.append("source contains unfilled placeholder/instruction text")
    meaningful_words = {w for w in re.findall(r"[a-z0-9']+", lower) if w in MEANINGFUL_HINTS}
    # Short copy must contain concrete operational terms. Long, structured briefings
    # can be substantive without using words such as "update" or "issue".
    structured_markers = len(re.findall(r"(?:today(?:’|'|s)?|daily|challenge|project|reminder|conversation|thought|room|staff|tester)", lower, re.I))
    if len(meaningful_words) < 2 and not (len(text) >= 700 and structured_markers >= 4):
        reasons.append("source lacks concrete update/test/issue/reminder details")
    normalized = re.sub(r"[^a-z0-9 ]+", " ", lower)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    generic_hits = sum(1 for phrase in GENERIC_ONLY_PHRASES if phrase in normalized)
    if generic_hits >= 2 and len(text) < 500:
        reasons.append("source appears to be generic fallback copy")
    return (not reasons), reasons

def validate_composed_sections(sections: dict):
    if not isinstance(sections, dict) or not sections:
        return False, ["composer returned no sections"]
    combined = plain_text(" ".join(str(v) for v in sections.values()))
    ok, reasons = validate_daily_content(combined)
    substantive = [k for k,v in sections.items() if len(plain_text(str(v))) >= 45 and k != "thank_you"]
    if not substantive:
        reasons.append("composed email has no substantive section beyond thanks")
    return (not reasons), reasons
