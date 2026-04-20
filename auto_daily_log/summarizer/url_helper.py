"""Normalize LLM base URLs to prevent double-path mistakes.

Users often paste the full endpoint URL (e.g.
`https://api.moonshot.cn/v1/chat/completions`) thinking "base URL"
means "the URL you hit". Our clients then append `/chat/completions`
again, producing a 404 like
`/v1/chat/completions/chat/completions`.

Normalization is protocol-aware:

  - openai_compat:
      base must END with /v1, client appends /chat/completions
  - anthropic:
      base must NOT end with /v1, client appends /v1/messages
  - ollama:
      base is just the root, client appends /api/tags|chat|generate

We only **strip** trailing leaf endpoints and, for anthropic, the extra
`/v1`. We do NOT silently append `/v1` to bare hosts — reverse proxies
and custom gateways legitimately mount OpenAI-compatible endpoints at
arbitrary paths, and auto-appending would break them. UX/hinting for
"forgot /v1" cases belongs in the frontend, not in this normalizer.
"""
from typing import Optional


_LEAF_ENDPOINTS = (
    "/chat/completions",
    "/completions",
    "/messages",
    "/api/tags",
    "/api/chat",
    "/api/generate",
)


def normalize_base_url(url: str, engine: Optional[str] = None) -> str:
    """Strip trailing endpoint paths + slashes. Protocol-aware for Anthropic.

    Args:
        url: Raw URL from user.
        engine: Optional protocol name ("openai_compat"/"anthropic"/"ollama").
                "anthropic" -> strip trailing /v1 because client adds /v1/messages.
    """
    if not url:
        return ""
    out = url.strip().rstrip("/")

    lowered = out.lower()
    for leaf in _LEAF_ENDPOINTS:
        if lowered.endswith(leaf):
            out = out[: -len(leaf)].rstrip("/")
            break

    if engine and engine.lower() == "anthropic":
        if out.lower().endswith("/v1"):
            out = out[:-3].rstrip("/")

    return out
