import pytest
from auto_daily_log_collector.monitor_internals.classifier import classify_activity

def test_classify_coding_by_app():
    cat, conf, hints = classify_activity("Visual Studio Code", "main.py — project", None)
    assert cat == "coding"
    assert conf >= 0.85
    assert "editor" in hints

def test_classify_meeting_by_app():
    cat, conf, hints = classify_activity("zoom.us", "Sprint Review", None)
    assert cat == "meeting"
    assert conf >= 0.90

def test_classify_research_by_url():
    cat, conf, hints = classify_activity("Google Chrome", "Issues", "https://github.com/org/repo/issues")
    assert cat == "research"

def test_classify_browsing_generic_browser():
    cat, conf, hints = classify_activity("Google Chrome", "Some Page", "https://example.com")
    assert cat == "browsing"
    assert conf >= 0.5

def test_classify_communication():
    cat, conf, hints = classify_activity("Slack", "general - Team", None)
    assert cat == "communication"

def test_classify_unknown():
    cat, conf, hints = classify_activity("SomeRandomApp", "Window", None)
    assert cat == "other"
    assert conf < 0.5
