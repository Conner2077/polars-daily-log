from fastapi import APIRouter, Request, HTTPException, Query
from pydantic import BaseModel
from typing import Optional
import httpx

router = APIRouter(tags=["settings"])

class SettingUpdate(BaseModel):
    value: str


class LLMCheckRequest(BaseModel):
    engine: str
    api_key: str
    model: Optional[str] = ""
    base_url: Optional[str] = ""


class JiraLoginRequest(BaseModel):
    mobile: str
    password: str
    jira_url: str = "https://work.fineres.com/"


@router.post("/settings/jira-login")
async def jira_sso_login(body: JiraLoginRequest, request: Request):
    """Auto-login to Jira via SSO, get cookie, save to settings."""
    db = request.app.state.db

    try:
        import subprocess, re, json as _json, os
        clean_env = {**os.environ, "http_proxy": "", "https_proxy": "", "all_proxy": "", "HTTP_PROXY": "", "HTTPS_PROXY": "", "ALL_PROXY": ""}

        # Step 1: Login to SSO via curl (same network path as Step 2)
        login_data = f"mobile={body.mobile}&password={body.password}&referrer={body.jira_url}&app=&openid=&lang=en"
        r1 = subprocess.run([
            "curl", "-s", "--noproxy", "*",
            "-X", "POST",
            "-H", "Content-Type: application/x-www-form-urlencoded",
            "-H", "X-Requested-With: XMLHttpRequest",
            "-d", login_data,
            "https://fanruanclub.com/login/verify"
        ], capture_output=True, text=True, timeout=15, env=clean_env)

        data = _json.loads(r1.stdout)
        if not data.get("success"):
            return {"success": False, "message": f"SSO login failed: {data.get('msg', 'Unknown error')}"}
        redirect_url = data["data"]["redirectUrl"]

        # Step 2: Follow redirects with cookie forwarding via curl
        debug_hops = []
        jira_cookies = {}
        url = redirect_url

        for hop_i in range(5):
            cmd = ["curl", "-s", "-D", "-", "-o", "/dev/null", "--noproxy", "*", url]
            cookie_header = "; ".join(f"{k}={v}" for k, v in jira_cookies.items())
            if cookie_header:
                cmd += ["-b", cookie_header]
            try:
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=15, env=clean_env)
            except Exception as e:
                debug_hops.append(f"hop{hop_i+1}:ERR {e}")
                break

            location = ""
            hop_cookies = []
            for line in result.stdout.split("\n"):
                line = line.strip()
                if line.lower().startswith("set-cookie:"):
                    m = re.match(r"set-cookie:\s*([^=]+)=([^;]*)", line, re.IGNORECASE)
                    if m:
                        jira_cookies[m.group(1).strip()] = m.group(2).strip()
                        hop_cookies.append(m.group(1).strip())
                elif line.lower().startswith("location:"):
                    location = line.split(":", 1)[1].strip()

            debug_hops.append(f"hop{hop_i+1}:{hop_cookies} loc={location[:80]}")

            if location:
                url = location
            else:
                break

        # Filter to only Jira-relevant cookies
        relevant = {k: v for k, v in jira_cookies.items()
                    if k in ("JSESSIONID", "seraph.rememberme.cookie", "atlassian.xsrf.token")}
        cookie_str = "; ".join(f"{k}={v}" for k, v in relevant.items())

        if not relevant.get("JSESSIONID"):
            return {"success": False, "message": f"SSO login succeeded but no Jira JSESSIONID received. Got: {list(jira_cookies.keys())}"}

        # Step 3: Verify cookie via curl
        user = None
        try:
            r3 = subprocess.run([
                "curl", "-s", "--noproxy", "*",
                "-b", cookie_str,
                f"{body.jira_url.rstrip('/')}/rest/api/2/myself"
            ], capture_output=True, text=True, timeout=10, env=clean_env)
            user = _json.loads(r3.stdout) if r3.stdout.strip().startswith("{") else None
        except Exception:
            pass

        # Step 4: Save to settings
        for key, value in [
            ("jira_server_url", body.jira_url.rstrip("/")),
            ("jira_auth_mode", "cookie"),
            ("jira_cookie", cookie_str),
        ]:
            existing = await db.fetch_one("SELECT key FROM settings WHERE key = ?", (key,))
            if existing:
                await db.execute("UPDATE settings SET value = ?, updated_at = datetime('now') WHERE key = ?", (value, key))
            else:
                await db.execute("INSERT INTO settings (key, value) VALUES (?, ?)", (key, value))

        debug_info = " | ".join(debug_hops)
        if user:
            msg = f"Login success: {user.get('displayName', user.get('name'))} ({user.get('emailAddress', '')})"
        else:
            msg = f"Cookie saved ({len(relevant)} cookies: {list(relevant.keys())}). Debug: {debug_info}"
        return {
            "success": True,
            "message": msg,
            "username": user.get("name") if user else None,
        }
    except Exception as e:
        return {"success": False, "message": f"Error: {str(e)}"}


@router.post("/settings/check-llm")
async def check_llm_key(body: LLMCheckRequest):
    """Validate LLM API key by making a minimal test call."""
    defaults = {
        "kimi": ("moonshot-v1-8k", "https://api.moonshot.cn/v1"),
        "openai": ("gpt-4o", "https://api.openai.com/v1"),
        "ollama": ("llama3", "http://localhost:11434"),
        "claude": ("claude-sonnet-4-20250514", "https://api.anthropic.com"),
    }
    from ...summarizer.url_helper import normalize_base_url
    default_model, default_url = defaults.get(body.engine, ("", ""))
    model = body.model or default_model
    base_url = normalize_base_url(body.base_url, engine=body.engine) or default_url

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            if body.engine == "ollama":
                # Ollama: just check if server is reachable
                resp = await client.get(f"{base_url}/api/tags")
                if resp.status_code == 200:
                    models = [m["name"] for m in resp.json().get("models", [])]
                    return {"valid": True, "message": f"Ollama connected. Models: {', '.join(models[:5])}"}
                return {"valid": False, "message": f"Ollama unreachable: {resp.status_code}"}

            elif body.engine == "claude":
                resp = await client.post(
                    f"{base_url}/v1/messages",
                    headers={"x-api-key": body.api_key, "anthropic-version": "2023-06-01", "Content-Type": "application/json"},
                    json={"model": model, "max_tokens": 1, "messages": [{"role": "user", "content": "hi"}]},
                )
            else:
                # OpenAI-compatible (Kimi, OpenAI)
                resp = await client.post(
                    f"{base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {body.api_key}", "Content-Type": "application/json"},
                    json={"model": model, "messages": [{"role": "user", "content": "hi"}], "max_tokens": 1},
                )

            if resp.status_code == 200:
                return {"valid": True, "message": f"Key valid. Engine: {body.engine}, Model: {model}"}
            elif resp.status_code == 401:
                return {"valid": False, "message": "API Key invalid or expired (401 Unauthorized)"}
            elif resp.status_code == 403:
                return {"valid": False, "message": "Access denied (403 Forbidden). Check key permissions."}
            elif resp.status_code == 429:
                return {"valid": True, "message": "Key valid (rate limited). Engine working."}
            else:
                error_text = resp.text[:200]
                return {"valid": False, "message": f"Error {resp.status_code}: {error_text}"}
    except httpx.ConnectError:
        return {"valid": False, "message": f"Cannot connect to {base_url}. Check URL."}
    except httpx.TimeoutException:
        return {"valid": False, "message": f"Connection timeout to {base_url}"}
    except Exception as e:
        return {"valid": False, "message": f"Error: {str(e)}"}

@router.get("/settings/default-prompts")
async def get_default_prompts():
    """Return all default prompt templates — single source of truth."""
    from ...summarizer.prompt import (
        DEFAULT_SUMMARIZE_PROMPT,
        DEFAULT_AUTO_APPROVE_PROMPT,
        DEFAULT_PERIOD_SUMMARY_PROMPT,
    )
    return {
        "summarize_prompt": DEFAULT_SUMMARIZE_PROMPT,
        "auto_approve_prompt": DEFAULT_AUTO_APPROVE_PROMPT,
        "period_summary_prompt": DEFAULT_PERIOD_SUMMARY_PROMPT,
    }


@router.get("/settings")
async def list_settings(request: Request):
    db = request.app.state.db
    return await db.fetch_all("SELECT key, value, updated_at FROM settings")

@router.get("/settings/jira-status")
async def jira_status(request: Request):
    """Check if Jira cookie is still valid, return username or null."""
    import subprocess, json as _json, os
    db = request.app.state.db

    jira_url = (await db.fetch_one("SELECT value FROM settings WHERE key = 'jira_server_url'") or {}).get("value", "")
    cookie = (await db.fetch_one("SELECT value FROM settings WHERE key = 'jira_cookie'") or {}).get("value", "")
    cached_user = (await db.fetch_one("SELECT value FROM settings WHERE key = 'jira_username'") or {}).get("value", "")

    if not jira_url or not cookie:
        return {"logged_in": False, "username": None}

    clean_env = {**os.environ, "http_proxy": "", "https_proxy": "", "all_proxy": "", "HTTP_PROXY": "", "HTTPS_PROXY": "", "ALL_PROXY": ""}
    try:
        result = subprocess.run([
            "curl", "-s", "--noproxy", "*", "-b", cookie,
            f"{jira_url}/rest/api/2/myself"
        ], capture_output=True, text=True, timeout=8, env=clean_env)
        if result.stdout.strip().startswith("{"):
            user = _json.loads(result.stdout)
            username = user.get("displayName", user.get("name"))
            if username and username != cached_user:
                existing = await db.fetch_one("SELECT key FROM settings WHERE key = 'jira_username'")
                if existing:
                    await db.execute("UPDATE settings SET value = ?, updated_at = datetime('now') WHERE key = 'jira_username'", (username,))
                else:
                    await db.execute("INSERT INTO settings (key, value) VALUES ('jira_username', ?)", (username,))
            return {"logged_in": True, "username": username}
    except Exception:
        pass
    return {"logged_in": False, "username": cached_user}


@router.get("/settings/do-jira-login")
async def do_jira_login_get(request: Request, mobile: str = Query(""), password: str = Query(""), jira_url: str = Query("https://work.fineres.com/")):
    """Full login+cookie flow via GET — bypasses POST/proxy issues."""
    import subprocess, json as _json, re, os
    db = request.app.state.db
    clean_env = {**os.environ, "http_proxy": "", "https_proxy": "", "all_proxy": "", "HTTP_PROXY": "", "HTTPS_PROXY": "", "ALL_PROXY": ""}

    if not mobile or not password:
        return {"success": False, "message": "Missing mobile or password"}

    jira_url = jira_url.rstrip("/") + "/"

    # Step 1: SSO login
    login_data = f"mobile={mobile}&password={password}&referrer={jira_url}&app=&openid=&lang=en"
    r1 = subprocess.run([
        "curl", "-s", "--noproxy", "*",
        "-X", "POST",
        "-H", "Content-Type: application/x-www-form-urlencoded",
        "-H", "X-Requested-With: XMLHttpRequest",
        "-d", login_data,
        "https://fanruanclub.com/login/verify"
    ], capture_output=True, text=True, timeout=15, env=clean_env)
    data = _json.loads(r1.stdout)
    if not data.get("success"):
        return {"success": False, "message": f"SSO failed: {data.get('msg')}"}
    redirect_url = data["data"]["redirectUrl"]

    # Step 2: Follow redirects
    debug_hops = []
    jira_cookies = {}
    url = redirect_url
    for hop_i in range(5):
        cmd = ["curl", "-s", "-D", "-", "-o", "/dev/null", "--noproxy", "*", url]
        cookie_header = "; ".join(f"{k}={v}" for k, v in jira_cookies.items())
        if cookie_header:
            cmd += ["-b", cookie_header]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15, env=clean_env)
        location = ""
        hop_cookies = []
        for line in result.stdout.split("\n"):
            line = line.strip()
            if line.lower().startswith("set-cookie:"):
                m = re.match(r"set-cookie:\s*([^=]+)=([^;]*)", line, re.IGNORECASE)
                if m:
                    jira_cookies[m.group(1).strip()] = m.group(2).strip()
                    hop_cookies.append(m.group(1).strip())
            elif line.lower().startswith("location:"):
                location = line.split(":", 1)[1].strip()
        debug_hops.append(f"hop{hop_i+1}:{hop_cookies} loc={location[:80]}")
        if location:
            url = location
        else:
            break

    # Step 3: Save
    relevant = {k: v for k, v in jira_cookies.items()
                if k in ("JSESSIONID", "seraph.rememberme.cookie", "atlassian.xsrf.token")}
    cookie_str = "; ".join(f"{k}={v}" for k, v in relevant.items())

    if len(relevant) >= 2:
        for key, value in [("jira_server_url", jira_url.rstrip("/")), ("jira_auth_mode", "cookie"), ("jira_cookie", cookie_str)]:
            existing = await db.fetch_one("SELECT key FROM settings WHERE key = ?", (key,))
            if existing:
                await db.execute("UPDATE settings SET value = ?, updated_at = datetime('now') WHERE key = ?", (value, key))
            else:
                await db.execute("INSERT INTO settings (key, value) VALUES (?, ?)", (key, value))

    # Get username
    username = None
    if cookie_str:
        try:
            r3 = subprocess.run([
                "curl", "-s", "--noproxy", "*", "-b", cookie_str,
                f"{jira_url.rstrip('/')}/rest/api/2/myself"
            ], capture_output=True, text=True, timeout=10, env=clean_env)
            if r3.stdout.strip().startswith("{"):
                user = _json.loads(r3.stdout)
                username = user.get("displayName", user.get("name"))
                # Save username for nav bar display
                existing = await db.fetch_one("SELECT key FROM settings WHERE key = 'jira_username'")
                if existing:
                    await db.execute("UPDATE settings SET value = ?, updated_at = datetime('now') WHERE key = 'jira_username'", (username,))
                else:
                    await db.execute("INSERT INTO settings (key, value) VALUES ('jira_username', ?)", (username,))
        except Exception:
            pass

    return {"success": len(relevant) >= 2, "username": username}


@router.get("/settings/{key}")
async def get_setting(key: str, request: Request):
    db = request.app.state.db
    row = await db.fetch_one("SELECT * FROM settings WHERE key = ?", (key,))
    return row or {"key": key, "value": None}

@router.put("/settings/{key}")
async def put_setting(key: str, body: SettingUpdate, request: Request):
    db = request.app.state.db
    value = body.value
    # Normalize LLM base URL (engine-aware) so we don't double-append endpoint paths later
    if key == "llm_base_url":
        from ...summarizer.url_helper import normalize_base_url
        engine_row = await db.fetch_one("SELECT value FROM settings WHERE key = 'llm_engine'")
        engine = engine_row["value"] if engine_row else None
        value = normalize_base_url(value, engine=engine)
    existing = await db.fetch_one("SELECT key FROM settings WHERE key = ?", (key,))
    if existing:
        await db.execute("UPDATE settings SET value = ?, updated_at = datetime('now') WHERE key = ?", (value, key))
    else:
        await db.execute("INSERT INTO settings (key, value) VALUES (?, ?)", (key, value))
    return {"key": key, "value": value}


