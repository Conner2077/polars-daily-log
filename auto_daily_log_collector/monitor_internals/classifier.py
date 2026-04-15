import re
from typing import List, Optional, Tuple

_APP_CATEGORIES = {
    "meeting": {
        "apps": ["zoom", "teams", "webex", "google meet", "facetime", "腾讯会议", "飞书会议"],
        "confidence": 0.95,
        "hint": "meeting-app",
    },
    "coding": {
        "apps": [
            "visual studio code", "cursor", "pycharm", "intellij", "goland",
            "clion", "xcode", "android studio", "webstorm", "rustrover",
            "sublime text", "vim", "neovim",
        ],
        "confidence": 0.92,
        "hint": "editor",
    },
    "coding_terminal": {
        "apps": ["terminal", "iterm2", "iterm", "warp", "alacritty", "kitty", "hyper", "windows terminal"],
        "confidence": 0.85,
        "hint": "terminal",
    },
    "communication": {
        "apps": [
            "slack", "discord", "telegram", "wechat", "wecom", "企业微信",
            "微信", "mail", "outlook", "thunderbird", "飞书", "钉钉",
        ],
        "confidence": 0.85,
        "hint": "comms",
    },
    "design": {
        "apps": ["figma", "sketch", "adobe xd"],
        "confidence": 0.90,
        "hint": "design-app",
    },
    "writing": {
        "apps": ["notion", "obsidian", "word", "pages", "typora"],
        "confidence": 0.85,
        "hint": "docs",
    },
    "reading": {
        "apps": ["preview", "skim", "adobe acrobat"],
        "confidence": 0.75,
        "hint": "pdf",
    },
}

_DOMAIN_CATEGORIES = [
    (r"(?:^|\.)zoom\.us", "meeting"),
    (r"meet\.google\.com", "meeting"),
    (r"teams\.microsoft\.com", "meeting"),
    (r"webex\.com", "meeting"),
    (r"figma\.com", "design"),
    (r"docs\.google\.com", "writing"),
    (r"notion\.so", "writing"),
    (r"github\.com", "research"),
    (r"gitlab\.com", "research"),
    (r"stackoverflow\.com", "research"),
    (r"stackexchange\.com", "research"),
    (r"kaggle\.com", "reading"),
    (r"arxiv\.org", "reading"),
]

_BROWSERS = {"google chrome", "chrome", "microsoft edge", "brave browser", "arc", "safari", "firefox"}

_CODE_FILE_PATTERNS = re.compile(
    r"\.(py|ts|tsx|js|jsx|java|go|rs|cpp|c|h|rb|php|swift|kt|scala|sql|vue|svelte|ipynb)"
    r"[\s\-—]",
    re.IGNORECASE,
)

_MEETING_KEYWORDS = re.compile(
    r"(meeting|standup|retro|sprint|review|daily|sync|huddle|会议|站会|评审)",
    re.IGNORECASE,
)


def classify_activity(
    app_name: Optional[str],
    window_title: Optional[str],
    url: Optional[str],
) -> Tuple[str, float, List[str]]:
    if not app_name:
        return "other", 0.4, []

    app_lower = app_name.lower()
    hints: List[str] = []

    # 1. Direct app match
    for cat, info in _APP_CATEGORIES.items():
        for app in info["apps"]:
            if app in app_lower:
                real_cat = "coding" if cat == "coding_terminal" else cat
                return real_cat, info["confidence"], [info["hint"]]

    # 2. Browser → check URL domain
    if app_lower in _BROWSERS or "browser" in app_lower:
        hints.append("browser")
        if url:
            for pattern, cat in _DOMAIN_CATEGORIES:
                if re.search(pattern, url):
                    return cat, 0.70, hints

        # 3. Check window title for code files
        if window_title and _CODE_FILE_PATTERNS.search(window_title):
            return "coding", 0.70, hints + ["code-file"]

        # 4. Check window title for meeting keywords
        if window_title and _MEETING_KEYWORDS.search(window_title):
            return "meeting", 0.70, hints + ["meeting-keyword"]

        return "browsing", 0.70, hints

    # 5. Window title fallback for non-browser apps
    if window_title:
        if _CODE_FILE_PATTERNS.search(window_title):
            return "coding", 0.70, ["code-file"]
        if _MEETING_KEYWORDS.search(window_title):
            return "meeting", 0.70, ["meeting-keyword"]

    return "other", 0.40, []
