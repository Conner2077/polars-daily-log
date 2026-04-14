"""Normalize LLM base URLs to prevent double-path mistakes.

Users often paste the full endpoint URL (e.g.
`https://api.moonshot.cn/v1/chat/completions`) thinking "base URL"
means "the URL you hit". Our clients then append `/chat/completions`
again, producing a 404 like
`/v1/chat/completions/chat/completions`.

Normalization is engine-aware because different providers use
different conventions:

  - OpenAI-compatible (openai, kimi):
      base must END with /v1, client appends /chat/completions
  - Anthropic (claude):
      base must NOT end with /v1, client appends /v1/messages
  - Ollama:
      base is just the root, client appends /api/tags|chat|generate
"""
from typing import Optional


# Leaf endpoint paths users commonly paste at the end of the URL.
# These get stripped regardless of engine.
_LEAF_ENDPOINTS = (
    "/chat/completions",
    "/completions",
    "/messages",
    "/api/tags",
    "/api/chat",
    "/api/generate",
)


def normalize_base_url(url: str, engine: Optional[str] = None) -> str:
    """Strip trailing endpoint paths + slashes. Engine-aware for Claude.

    Args:
        url: Raw URL from user.
        engine: Optional engine name for engine-specific fixups.
                "claude" -> strip trailing /v1 because client adds /v1/messages.
                Other values (or None) -> leave /v1 in place (OpenAI convention).
    """
    if not url:
        return ""
    out = url.strip().rstrip("/")

    # Strip one leaf endpoint if user pasted a full URL
    lowered = out.lower()
    for leaf in _LEAF_ENDPOINTS:
        if lowered.endswith(leaf):
            out = out[: -len(leaf)].rstrip("/")
            break

    # Claude: client appends /v1/messages itself, so base must not end with /v1
    if engine and engine.lower() == "claude":
        if out.lower().endswith("/v1"):
            out = out[:-3].rstrip("/")

    return out
