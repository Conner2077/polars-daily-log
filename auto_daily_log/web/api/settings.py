from fastapi import APIRouter, Request, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import Optional
from pathlib import Path
import httpx

router = APIRouter(tags=["settings"])


def _save_jira_avatar(user: dict, cookie: str, data_dir: Path) -> Optional[str]:
    """Download Jira avatar (48x48) to local file using stored cookie.

    Returns the absolute file path on success, or None if there was no avatar
    URL / the download failed. Called from jira-status + jira-login flows.
    """
    import subprocess, os
    try:
        avatar_url = (user.get("avatarUrls") or {}).get("48x48")
        if not avatar_url or not cookie:
            return None
        data_dir.mkdir(parents=True, exist_ok=True)
        target = data_dir / "jira_avatar.png"
        clean_env = {**os.environ, "http_proxy": "", "https_proxy": "", "all_proxy": "", "HTTP_PROXY": "", "HTTPS_PROXY": "", "ALL_PROXY": ""}
        result = subprocess.run(
            ["curl", "-sL", "--noproxy", "*", "-b", cookie, "-o", str(target), avatar_url],
            capture_output=True, timeout=10, env=clean_env,
        )
        if result.returncode != 0 or not target.exists() or target.stat().st_size == 0:
            return None
        return str(target)
    except Exception:
        return None


async def _upsert_setting(db, key: str, value: str) -> None:
    existing = await db.fetch_one("SELECT key FROM settings WHERE key = ?", (key,))
    if existing:
        await db.execute("UPDATE settings SET value = ?, updated_at = datetime('now') WHERE key = ?", (value, key))
    else:
        await db.execute("INSERT INTO settings (key, value) VALUES (?, ?)", (key, value))

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
            await _upsert_setting(db, key, value)

        # Step 4b: Cache avatar locally so we don't hit Jira on every page load.
        if user:
            config = getattr(request.app.state, "config", None)
            data_dir = config.system.resolved_data_dir if config else Path.home() / ".auto_daily_log"
            avatar_path = _save_jira_avatar(user, cookie_str, data_dir)
            if avatar_path:
                await _upsert_setting(db, "jira_avatar_path", avatar_path)

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
    """Validate LLM API key by making a minimal test call.

    `body.engine` must be one of: openai_compat / anthropic / ollama.
    """
    from ...summarizer.engine import VALID_PROTOCOLS
    from ...summarizer.url_helper import normalize_base_url

    protocol = (body.engine or "").lower()
    if protocol not in VALID_PROTOCOLS:
        return {"valid": False, "message": f"Unknown protocol: {body.engine}"}

    default_url = {
        "openai_compat": "https://api.openai.com/v1",
        "anthropic": "https://api.anthropic.com",
        "ollama": "http://localhost:11434",
    }[protocol]

    model = body.model or ""
    base_url = normalize_base_url(body.base_url, engine=protocol) or default_url
    if not base_url:
        return {"valid": False, "message": "Base URL 为空，无法连接"}

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            if protocol == "ollama":
                resp = await client.get(f"{base_url}/api/tags")
                if resp.status_code == 200:
                    models = [m["name"] for m in resp.json().get("models", [])]
                    return {"valid": True, "message": f"Ollama connected. Models: {', '.join(models[:5])}"}
                return {"valid": False, "message": f"Ollama unreachable: {resp.status_code}"}

            if protocol == "anthropic":
                resp = await client.post(
                    f"{base_url}/v1/messages",
                    headers={"x-api-key": body.api_key, "anthropic-version": "2023-06-01", "Content-Type": "application/json", "Accept": "text/event-stream"},
                    json={"model": model, "max_tokens": 1, "stream": True, "messages": [{"role": "user", "content": "hi"}]},
                )
            else:
                # openai_compat
                resp = await client.post(
                    f"{base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {body.api_key}", "Content-Type": "application/json"},
                    json={"model": model, "messages": [{"role": "user", "content": "hi"}], "max_tokens": 1, "stream": True},
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
        DEFAULT_ACTIVITY_SUMMARY_PROMPT,
    )
    return {
        "summarize_prompt": DEFAULT_SUMMARIZE_PROMPT,
        "auto_approve_prompt": DEFAULT_AUTO_APPROVE_PROMPT,
        "period_summary_prompt": DEFAULT_PERIOD_SUMMARY_PROMPT,
        "activity_summary_prompt": DEFAULT_ACTIVITY_SUMMARY_PROMPT,
    }


@router.get("/settings")
async def list_settings(request: Request):
    db = request.app.state.db
    return await db.fetch_all("SELECT key, value, updated_at FROM settings")

@router.post("/settings/jira-test")
async def jira_test_connection(request: Request):
    """Test Jira PAT connection — on success, save settings and fetch avatar."""
    from ...jira_client.client import JiraClient
    from ...config import JiraConfig
    try:
        body = await request.json()
        server_url = (body.get("server_url") or "").strip()
        username = (body.get("username") or "").strip()
        pat = (body.get("pat") or "").strip()

        if not server_url:
            return {"success": False, "error": "Server URL 不能为空"}
        if not username:
            return {"success": False, "error": "用户名不能为空"}
        if not pat:
            return {"success": False, "error": "PAT 不能为空"}

        config = JiraConfig(server_url=server_url, username=username, pat=pat, auth_mode="pat")
        client = JiraClient(config)
        user = await client.get_myself()
        if not user:
            return {"success": False, "error": "认证失败，请检查用户名和 Token"}

        # Save settings on success (same as SSO login flow)
        db = request.app.state.db
        display_name = user.get("displayName", user.get("name", username))
        await _upsert_setting(db, "jira_server_url", server_url)
        await _upsert_setting(db, "jira_username", username)  # login name for Basic Auth
        await _upsert_setting(db, "jira_display_name", display_name)
        await _upsert_setting(db, "jira_auth_mode", "pat")
        await _upsert_setting(db, "jira_pat", pat)

        # Fetch avatar using PAT auth (Basic Auth, not cookie)
        # Note: Jira Server's /secure/useravatar may reject PAT auth (returns
        # HTML 401). We validate the downloaded file is actually an image.
        import subprocess, os, base64
        app_config = getattr(request.app.state, "config", None)
        data_dir = app_config.system.resolved_data_dir if app_config else Path.home() / ".auto_daily_log"
        avatar_url = (user.get("avatarUrls") or {}).get("48x48")
        if avatar_url:
            data_dir.mkdir(parents=True, exist_ok=True)
            target = data_dir / "jira_avatar.png"
            cred = base64.b64encode(f"{username}:{pat}".encode()).decode()
            clean_env = {**os.environ, "http_proxy": "", "https_proxy": "", "all_proxy": "",
                         "HTTP_PROXY": "", "HTTPS_PROXY": "", "ALL_PROXY": ""}
            result = subprocess.run(
                ["curl", "-sL", "--noproxy", "*",
                 "-H", f"Authorization: Basic {cred}",
                 "-o", str(target), avatar_url],
                capture_output=True, timeout=10, env=clean_env,
            )
            # Validate: PNG starts with \x89PNG, JPEG with \xff\xd8
            if result.returncode == 0 and target.exists() and target.stat().st_size > 100:
                header = target.read_bytes()[:4]
                if header[:4] == b'\x89PNG' or header[:2] == b'\xff\xd8':
                    await _upsert_setting(db, "jira_avatar_path", str(target))
                else:
                    target.unlink(missing_ok=True)  # HTML 401 etc.

        return {"success": True, "message": f"连接成功 — {display_name}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.get("/settings/jira-status")
async def jira_status(request: Request):
    """Check if Jira is authenticated (cookie or PAT), return username or null."""
    import subprocess, json as _json, os, base64
    db = request.app.state.db

    jira_url = (await db.fetch_one("SELECT value FROM settings WHERE key = 'jira_server_url'") or {}).get("value", "")
    auth_mode = (await db.fetch_one("SELECT value FROM settings WHERE key = 'jira_auth_mode'") or {}).get("value", "cookie")
    cached_user = (await db.fetch_one("SELECT value FROM settings WHERE key = 'jira_username'") or {}).get("value", "")

    if not jira_url:
        return {"logged_in": False, "username": None}

    clean_env = {**os.environ, "http_proxy": "", "https_proxy": "", "all_proxy": "", "HTTP_PROXY": "", "HTTPS_PROXY": "", "ALL_PROXY": ""}

    # Build curl auth args based on mode
    if auth_mode == "pat":
        pat_username = cached_user or ""
        pat = (await db.fetch_one("SELECT value FROM settings WHERE key = 'jira_pat'") or {}).get("value", "")
        if not pat:
            return {"logged_in": False, "username": None}
        cred = base64.b64encode(f"{pat_username}:{pat}".encode()).decode()
        auth_args = ["-H", f"Authorization: Basic {cred}"]
    else:
        cookie = (await db.fetch_one("SELECT value FROM settings WHERE key = 'jira_cookie'") or {}).get("value", "")
        if not cookie:
            return {"logged_in": False, "username": None}
        auth_args = ["-b", cookie]

    try:
        result = subprocess.run(
            ["curl", "-s", "--noproxy", "*"] + auth_args + [f"{jira_url}/rest/api/2/myself"],
            capture_output=True, text=True, timeout=8, env=clean_env,
        )
        if result.stdout.strip().startswith("{"):
            user = _json.loads(result.stdout)
            display_name = user.get("displayName", user.get("name"))
            if auth_mode == "pat":
                # PAT mode: jira_username holds the login name, display_name is separate
                await _upsert_setting(db, "jira_display_name", display_name)
            elif display_name and display_name != cached_user:
                await _upsert_setting(db, "jira_username", display_name)
            # Refresh avatar only when missing
            existing_avatar = (await db.fetch_one("SELECT value FROM settings WHERE key = 'jira_avatar_path'") or {}).get("value", "")
            if not existing_avatar or not Path(existing_avatar).exists():
                config = getattr(request.app.state, "config", None)
                data_dir = config.system.resolved_data_dir if config else Path.home() / ".auto_daily_log"
                if auth_mode == "pat":
                    avatar_url = (user.get("avatarUrls") or {}).get("48x48")
                    if avatar_url:
                        data_dir.mkdir(parents=True, exist_ok=True)
                        target = data_dir / "jira_avatar.png"
                        dl = subprocess.run(
                            ["curl", "-sL", "--noproxy", "*", "-H", f"Authorization: Basic {cred}",
                             "-o", str(target), avatar_url],
                            capture_output=True, timeout=10, env=clean_env,
                        )
                        if dl.returncode == 0 and target.exists() and target.stat().st_size > 100:
                            hdr = target.read_bytes()[:4]
                            if hdr[:4] == b'\x89PNG' or hdr[:2] == b'\xff\xd8':
                                await _upsert_setting(db, "jira_avatar_path", str(target))
                            else:
                                target.unlink(missing_ok=True)
                else:
                    avatar_path = _save_jira_avatar(user, cookie, data_dir)
                    if avatar_path:
                        await _upsert_setting(db, "jira_avatar_path", avatar_path)
            return {"logged_in": True, "username": display_name}
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
            await _upsert_setting(db, key, value)

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
                if username:
                    await _upsert_setting(db, "jira_username", username)
                # Cache avatar locally — login implies user may have changed.
                config = getattr(request.app.state, "config", None)
                data_dir = config.system.resolved_data_dir if config else Path.home() / ".auto_daily_log"
                avatar_path = _save_jira_avatar(user, cookie_str, data_dir)
                if avatar_path:
                    await _upsert_setting(db, "jira_avatar_path", avatar_path)
        except Exception:
            pass

    return {"success": len(relevant) >= 2, "username": username}


@router.get("/settings/jira-avatar")
async def get_jira_avatar(request: Request):
    """Serve the cached Jira avatar file. Refreshed by jira-status / jira-login."""
    db = request.app.state.db
    row = await db.fetch_one("SELECT value FROM settings WHERE key = 'jira_avatar_path'")
    path = (row or {}).get("value", "")
    if not path:
        raise HTTPException(404, "No Jira avatar cached yet")
    f = Path(path)
    if not f.exists():
        raise HTTPException(404, "Jira avatar file missing")
    return FileResponse(f, media_type="image/png", headers={"Cache-Control": "private, max-age=300"})


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
        protocol = engine_row["value"] if engine_row else None
        value = normalize_base_url(value, engine=protocol)
    existing = await db.fetch_one("SELECT key FROM settings WHERE key = ?", (key,))
    if existing:
        await db.execute("UPDATE settings SET value = ?, updated_at = datetime('now') WHERE key = ?", (value, key))
    else:
        await db.execute("INSERT INTO settings (key, value) VALUES (?, ?)", (key, value))
    return {"key": key, "value": value}


