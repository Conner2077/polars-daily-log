# pHash Dedup + Idle Detection + sqlite-vec Search Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add pHash screenshot deduplication, idle detection, and sqlite-vec vector search to the existing auto_daily_log project.

**Architecture:** pHash and idle detection integrate into MonitorService's sampling loop. sqlite-vec adds a new search module with embedding generation via LLM APIs and a vec0 virtual table in the existing SQLite database.

**Tech Stack:** imagehash, Pillow, sqlite-vec, httpx (existing), FastAPI (existing), Vue.js (existing)

**Design spec:** `docs/superpowers/specs/2026-04-12-phash-vec-search-design.md`

---

## Task 1: Add Dependencies

**Files:**
- Modify: `pyproject.toml`
- Modify: `requirements.txt`

- [ ] **Step 1: Update pyproject.toml**

Add `imagehash`, `Pillow`, and `sqlite-vec` to core dependencies:

```toml
[project]
name = "auto-daily-log"
version = "0.1.0"
description = "Automated Jira worklog tool with activity monitoring and LLM summarization"
requires-python = ">=3.9"
dependencies = [
    "fastapi>=0.104.0",
    "uvicorn[standard]>=0.24.0",
    "aiosqlite>=0.19.0",
    "httpx>=0.25.0",
    "apscheduler>=3.10.0",
    "pyyaml>=6.0",
    "pydantic>=2.5.0",
    "pydantic-settings>=2.1.0",
    "imagehash>=4.3.0",
    "Pillow>=10.0",
    "sqlite-vec>=0.1.0",
]
```

- [ ] **Step 2: Update requirements.txt**

Append:
```
imagehash>=4.3.0
Pillow>=10.0
sqlite-vec>=0.1.0
```

- [ ] **Step 3: Install new dependencies**

```bash
cd /Users/conner/Zone/code/ai_project/auto_daily_log
source .venv/bin/activate
pip install -e ".[dev,macos]"
python -c "import imagehash; import sqlite_vec; print('OK')"
```
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml requirements.txt
git commit -m "deps: add imagehash, Pillow, sqlite-vec"
```

---

## Task 2: pHash Screenshot Deduplication

**Files:**
- Create: `auto_daily_log/monitor/phash.py`
- Create: `tests/test_phash.py`

- [ ] **Step 1: Write failing test**

`tests/test_phash.py`:
```python
import pytest
from pathlib import Path
from PIL import Image
from auto_daily_log.monitor.phash import compute_phash, is_similar


@pytest.fixture
def identical_images(tmp_path):
    img = Image.new("RGB", (100, 100), color="red")
    path_a = tmp_path / "a.png"
    path_b = tmp_path / "b.png"
    img.save(path_a)
    img.save(path_b)
    return path_a, path_b


@pytest.fixture
def different_images(tmp_path):
    img_a = Image.new("RGB", (100, 100), color="red")
    img_b = Image.new("RGB", (100, 100), color="blue")
    # Draw some text-like pattern to make hashes differ
    from PIL import ImageDraw
    draw = ImageDraw.Draw(img_b)
    draw.rectangle([10, 10, 90, 90], fill="white")
    draw.rectangle([20, 20, 80, 80], fill="black")
    path_a = tmp_path / "a.png"
    path_b = tmp_path / "b.png"
    img_a.save(path_a)
    img_b.save(path_b)
    return path_a, path_b


def test_compute_phash_returns_hash(identical_images):
    h = compute_phash(identical_images[0])
    assert h is not None


def test_identical_images_are_similar(identical_images):
    hash_a = compute_phash(identical_images[0])
    hash_b = compute_phash(identical_images[1])
    assert is_similar(hash_a, hash_b, threshold=10)


def test_different_images_are_not_similar(different_images):
    hash_a = compute_phash(different_images[0])
    hash_b = compute_phash(different_images[1])
    assert not is_similar(hash_a, hash_b, threshold=5)


def test_none_hash_is_not_similar():
    assert not is_similar(None, None, threshold=10)


def test_compute_phash_nonexistent_file():
    h = compute_phash(Path("/nonexistent.png"))
    assert h is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_phash.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement phash module**

`auto_daily_log/monitor/phash.py`:
```python
from pathlib import Path
from typing import Optional

import imagehash
from PIL import Image


def compute_phash(image_path: Path) -> Optional[imagehash.ImageHash]:
    try:
        img = Image.open(image_path)
        return imagehash.phash(img)
    except Exception:
        return None


def is_similar(
    hash_a: Optional[imagehash.ImageHash],
    hash_b: Optional[imagehash.ImageHash],
    threshold: int = 10,
) -> bool:
    if hash_a is None or hash_b is None:
        return False
    return (hash_a - hash_b) <= threshold
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_phash.py -v`
Expected: 5 tests PASS

- [ ] **Step 5: Commit**

```bash
git add auto_daily_log/monitor/phash.py tests/test_phash.py
git commit -m "feat: pHash computation and similarity comparison"
```

---

## Task 3: Idle Detection

**Files:**
- Create: `auto_daily_log/monitor/idle.py`
- Create: `tests/test_idle.py`
- Modify: `auto_daily_log/monitor/platforms/base.py`
- Modify: `auto_daily_log/monitor/platforms/macos.py`
- Modify: `auto_daily_log/monitor/platforms/windows.py`
- Modify: `auto_daily_log/monitor/platforms/linux.py`

- [ ] **Step 1: Write failing test**

`tests/test_idle.py`:
```python
import pytest
from unittest.mock import patch
from auto_daily_log.monitor.idle import get_idle_seconds


def test_get_idle_seconds_returns_number():
    result = get_idle_seconds()
    assert isinstance(result, (int, float))
    assert result >= 0


@patch("auto_daily_log.monitor.idle.get_current_platform", return_value="macos")
@patch("auto_daily_log.monitor.idle._get_idle_macos", return_value=120.0)
def test_idle_dispatches_to_macos(mock_idle, mock_platform):
    result = get_idle_seconds()
    assert result == 120.0
    mock_idle.assert_called_once()


@patch("auto_daily_log.monitor.idle.get_current_platform", return_value="windows")
@patch("auto_daily_log.monitor.idle._get_idle_windows", return_value=60.0)
def test_idle_dispatches_to_windows(mock_idle, mock_platform):
    result = get_idle_seconds()
    assert result == 60.0


@patch("auto_daily_log.monitor.idle.get_current_platform", return_value="linux")
@patch("auto_daily_log.monitor.idle._get_idle_linux", return_value=30.0)
def test_idle_dispatches_to_linux(mock_idle, mock_platform):
    result = get_idle_seconds()
    assert result == 30.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_idle.py -v`
Expected: FAIL

- [ ] **Step 3: Implement idle detection**

`auto_daily_log/monitor/idle.py`:
```python
import subprocess
import re
from typing import Optional
from .platforms.detect import get_current_platform


def get_idle_seconds() -> float:
    platform = get_current_platform()
    if platform == "macos":
        return _get_idle_macos()
    elif platform == "windows":
        return _get_idle_windows()
    else:
        return _get_idle_linux()


def _get_idle_macos() -> float:
    try:
        result = subprocess.run(
            ["ioreg", "-c", "IOHIDSystem"],
            capture_output=True, text=True, timeout=5,
        )
        match = re.search(r'"HIDIdleTime"\s*=\s*(\d+)', result.stdout)
        if match:
            nanoseconds = int(match.group(1))
            return nanoseconds / 1_000_000_000
    except Exception:
        pass
    return 0.0


def _get_idle_windows() -> float:
    try:
        ps_script = (
            "Add-Type @'\n"
            "using System;\n"
            "using System.Runtime.InteropServices;\n"
            "public struct LASTINPUTINFO { public uint cbSize; public uint dwTime; }\n"
            "public class IdleTime {\n"
            "    [DllImport(\"user32.dll\")] public static extern bool GetLastInputInfo(ref LASTINPUTINFO plii);\n"
            "    public static uint Get() {\n"
            "        LASTINPUTINFO lii = new LASTINPUTINFO();\n"
            "        lii.cbSize = (uint)Marshal.SizeOf(lii);\n"
            "        GetLastInputInfo(ref lii);\n"
            "        return (uint)Environment.TickCount - lii.dwTime;\n"
            "    }\n"
            "}\n"
            "'@\n"
            "[IdleTime]::Get()"
        )
        result = subprocess.run(
            ["powershell", "-Command", ps_script],
            capture_output=True, text=True, timeout=10,
        )
        ms = int(result.stdout.strip())
        return ms / 1000.0
    except Exception:
        pass
    return 0.0


def _get_idle_linux() -> float:
    try:
        result = subprocess.run(
            ["xprintidle"],
            capture_output=True, text=True, timeout=5,
        )
        ms = int(result.stdout.strip())
        return ms / 1000.0
    except (FileNotFoundError, ValueError, Exception):
        pass
    return 0.0
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_idle.py -v`
Expected: 4 tests PASS

- [ ] **Step 5: Commit**

```bash
git add auto_daily_log/monitor/idle.py tests/test_idle.py
git commit -m "feat: cross-platform idle detection (macOS/Windows/Linux)"
```

---

## Task 4: Integrate pHash + Idle into MonitorService

**Files:**
- Modify: `auto_daily_log/monitor/service.py`
- Modify: `auto_daily_log/config.py`
- Modify: `tests/test_monitor_service.py`

- [ ] **Step 1: Update MonitorConfig with new fields**

In `auto_daily_log/config.py`, change MonitorConfig to:

```python
class MonitorConfig(BaseModel):
    interval_sec: int = 30
    ocr_enabled: bool = True
    ocr_engine: str = "auto"
    screenshot_retention_days: int = 7
    privacy: PrivacyConfig = PrivacyConfig()
    phash_enabled: bool = True
    phash_threshold: int = 10
    idle_threshold_sec: int = 180
```

- [ ] **Step 2: Write new tests for idle and phash integration**

Append to `tests/test_monitor_service.py`:

```python
@pytest.mark.asyncio
async def test_idle_records_idle_category(db, tmp_path):
    config = MonitorConfig(ocr_enabled=False, interval_sec=30, idle_threshold_sec=60)
    service = MonitorService(db, config, screenshot_dir=tmp_path / "screenshots")

    with patch.object(service, "_capture_raw") as mock_capture, \
         patch("auto_daily_log.monitor.service.get_idle_seconds", return_value=120.0):
        mock_capture.return_value = {
            "app_name": "IntelliJ IDEA",
            "window_title": "Main.java",
            "url": None,
            "wecom_group": None,
            "screenshot_path": None,
            "ocr_text": None,
        }
        await service.sample_once()

    rows = await db.fetch_all("SELECT * FROM activities")
    assert len(rows) == 1
    assert rows[0]["category"] == "idle"


@pytest.mark.asyncio
async def test_idle_merges_consecutive(db, tmp_path):
    config = MonitorConfig(ocr_enabled=False, interval_sec=30, idle_threshold_sec=60)
    service = MonitorService(db, config, screenshot_dir=tmp_path / "screenshots")

    with patch.object(service, "_capture_raw") as mock_capture, \
         patch("auto_daily_log.monitor.service.get_idle_seconds", return_value=120.0):
        mock_capture.return_value = {
            "app_name": "IntelliJ IDEA",
            "window_title": "Main.java",
            "url": None,
            "wecom_group": None,
            "screenshot_path": None,
            "ocr_text": None,
        }
        await service.sample_once()
        await service.sample_once()

    rows = await db.fetch_all("SELECT * FROM activities")
    assert len(rows) == 1
    assert rows[0]["category"] == "idle"
    assert rows[0]["duration_sec"] == 60


@pytest.mark.asyncio
async def test_phash_skips_ocr_for_similar_screenshots(db, tmp_path):
    config = MonitorConfig(ocr_enabled=True, interval_sec=30, phash_enabled=True, phash_threshold=10)
    service = MonitorService(db, config, screenshot_dir=tmp_path / "screenshots")

    # Create a fake screenshot
    from PIL import Image
    ss_dir = tmp_path / "screenshots" / "2026-04-12"
    ss_dir.mkdir(parents=True)
    img = Image.new("RGB", (100, 100), color="red")
    ss_path = ss_dir / "test.png"
    img.save(ss_path)

    with patch.object(service, "_capture_raw_inner") as mock_inner, \
         patch("auto_daily_log.monitor.service.capture_screenshot", return_value=ss_path), \
         patch("auto_daily_log.monitor.service.ocr_image", return_value="some text") as mock_ocr:
        mock_inner.return_value = {
            "app_name": "Chrome",
            "window_title": "Page",
            "url": None,
            "wecom_group": None,
        }
        await service.sample_once()
        await service.sample_once()

    # OCR should only be called once (second time phash matches)
    assert mock_ocr.call_count == 1
```

- [ ] **Step 3: Run tests to see them fail**

Run: `pytest tests/test_monitor_service.py -v`
Expected: new tests FAIL

- [ ] **Step 4: Rewrite MonitorService with pHash + idle**

Replace `auto_daily_log/monitor/service.py`:

```python
import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from ..config import MonitorConfig
from ..models.database import Database
from .classifier import classify_activity
from .platforms.detect import get_platform_module
from .screenshot import capture_screenshot
from .ocr import ocr_image
from .phash import compute_phash, is_similar
from .idle import get_idle_seconds


class MonitorService:
    def __init__(self, db: Database, config: MonitorConfig, screenshot_dir: Path):
        self._db = db
        self._config = config
        self._screenshot_dir = screenshot_dir
        self._platform = get_platform_module()
        self._last_app: Optional[str] = None
        self._last_title: Optional[str] = None
        self._last_id: Optional[int] = None
        self._last_phash = None
        self._last_ocr_text: Optional[str] = None
        self._last_was_idle: bool = False
        self._running = False

    def _capture_raw_inner(self) -> dict:
        app_name = self._platform.get_frontmost_app()
        window_title = self._platform.get_window_title(app_name) if app_name else None
        tab_title, url = (
            self._platform.get_browser_tab(app_name) if app_name else (None, None)
        )
        wecom_group = self._platform.get_wecom_chat_name(app_name) if app_name else None
        return {
            "app_name": app_name,
            "window_title": tab_title or window_title,
            "url": url,
            "wecom_group": wecom_group,
        }

    def _capture_raw(self) -> dict:
        raw = self._capture_raw_inner()

        screenshot_path = None
        ocr_text = None
        if self._config.ocr_enabled:
            today_dir = self._screenshot_dir / datetime.now().strftime("%Y-%m-%d")
            screenshot_path = capture_screenshot(today_dir)
            if screenshot_path:
                # pHash dedup: skip OCR if screenshot is similar to last one
                if self._config.phash_enabled:
                    current_hash = compute_phash(screenshot_path)
                    if is_similar(current_hash, self._last_phash, self._config.phash_threshold):
                        # Similar screenshot — reuse last OCR, delete duplicate file
                        ocr_text = self._last_ocr_text
                        try:
                            screenshot_path.unlink()
                        except OSError:
                            pass
                        screenshot_path = None
                    else:
                        ocr_text = ocr_image(screenshot_path, self._config.ocr_engine)
                        self._last_phash = current_hash
                        self._last_ocr_text = ocr_text
                else:
                    ocr_text = ocr_image(screenshot_path, self._config.ocr_engine)

        raw["screenshot_path"] = str(screenshot_path) if screenshot_path else None
        raw["ocr_text"] = ocr_text
        return raw

    def _is_blocked(self, raw: dict) -> bool:
        app = raw.get("app_name") or ""
        url = raw.get("url") or ""
        for blocked in self._config.privacy.blocked_apps:
            if blocked.lower() in app.lower():
                return True
        for blocked in self._config.privacy.blocked_urls:
            if blocked.lower() in url.lower():
                return True
        return False

    async def sample_once(self) -> None:
        # Check idle first
        idle_sec = get_idle_seconds()
        is_idle = idle_sec >= self._config.idle_threshold_sec

        if is_idle:
            # Idle: don't capture screenshots/OCR, just record idle
            if self._last_was_idle and self._last_id:
                await self._db.execute(
                    "UPDATE activities SET duration_sec = duration_sec + ? WHERE id = ?",
                    (self._config.interval_sec, self._last_id),
                )
                return

            row_id = await self._db.execute(
                """INSERT INTO activities
                   (timestamp, app_name, window_title, category, confidence, duration_sec)
                   VALUES (?, ?, ?, 'idle', 0.99, ?)""",
                (datetime.now().isoformat(), "System", "Idle", self._config.interval_sec),
            )
            self._last_app = None
            self._last_title = None
            self._last_id = row_id
            self._last_was_idle = True
            return

        self._last_was_idle = False

        raw = self._capture_raw()
        if not raw["app_name"] or self._is_blocked(raw):
            return

        app_name = raw["app_name"]
        window_title = raw["window_title"]

        # Merge consecutive same activity
        if app_name == self._last_app and window_title == self._last_title and self._last_id:
            await self._db.execute(
                "UPDATE activities SET duration_sec = duration_sec + ? WHERE id = ?",
                (self._config.interval_sec, self._last_id),
            )
            return

        category, confidence, hints = classify_activity(app_name, window_title, raw["url"])

        signals = {
            "browser_url": raw["url"],
            "wecom_group_name": raw["wecom_group"],
            "screenshot_path": raw["screenshot_path"],
            "ocr_text": raw["ocr_text"],
            "hints": hints,
        }

        row_id = await self._db.execute(
            """INSERT INTO activities
               (timestamp, app_name, window_title, category, confidence, url, signals, duration_sec)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                datetime.now().isoformat(),
                app_name,
                window_title,
                category,
                confidence,
                raw["url"],
                json.dumps(signals, ensure_ascii=False),
                self._config.interval_sec,
            ),
        )

        self._last_app = app_name
        self._last_title = window_title
        self._last_id = row_id

    async def start(self) -> None:
        self._running = True
        while self._running:
            try:
                await self.sample_once()
            except Exception as e:
                print(f"[Monitor] Error: {e}")
            await asyncio.sleep(self._config.interval_sec)

    def stop(self) -> None:
        self._running = False
```

- [ ] **Step 5: Run all monitor tests**

Run: `pytest tests/test_monitor_service.py tests/test_phash.py tests/test_idle.py -v`
Expected: All tests PASS

- [ ] **Step 6: Run full test suite**

Run: `pytest tests/ -v`
Expected: All tests PASS (no regressions)

- [ ] **Step 7: Commit**

```bash
git add auto_daily_log/monitor/service.py auto_daily_log/config.py tests/test_monitor_service.py
git commit -m "feat: integrate pHash dedup and idle detection into MonitorService"
```

---

## Task 5: Embedding Engine

**Files:**
- Create: `auto_daily_log/search/__init__.py`
- Create: `auto_daily_log/search/embedding.py`
- Create: `tests/test_embedding.py`

- [ ] **Step 1: Write failing test**

`tests/test_embedding.py`:
```python
import pytest
from auto_daily_log.search.embedding import get_embedding_engine, EmbeddingEngine
from auto_daily_log.config import LLMConfig, LLMProviderConfig, EmbeddingConfig


def test_get_kimi_embedding_engine():
    llm_config = LLMConfig(engine="kimi", kimi=LLMProviderConfig(
        api_key="test-key", base_url="https://api.moonshot.cn/v1"
    ))
    emb_config = EmbeddingConfig(enabled=True)
    engine = get_embedding_engine(llm_config, emb_config)
    assert isinstance(engine, EmbeddingEngine)
    assert engine.dimensions > 0


def test_get_openai_embedding_engine():
    llm_config = LLMConfig(engine="openai", openai=LLMProviderConfig(
        api_key="test-key", base_url="https://api.openai.com/v1"
    ))
    emb_config = EmbeddingConfig(enabled=True)
    engine = get_embedding_engine(llm_config, emb_config)
    assert isinstance(engine, EmbeddingEngine)


def test_get_ollama_embedding_engine():
    llm_config = LLMConfig(engine="ollama", ollama=LLMProviderConfig(
        base_url="http://localhost:11434"
    ))
    emb_config = EmbeddingConfig(enabled=True)
    engine = get_embedding_engine(llm_config, emb_config)
    assert isinstance(engine, EmbeddingEngine)


def test_disabled_returns_none():
    llm_config = LLMConfig(engine="kimi")
    emb_config = EmbeddingConfig(enabled=False)
    engine = get_embedding_engine(llm_config, emb_config)
    assert engine is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_embedding.py -v`
Expected: FAIL

- [ ] **Step 3: Add EmbeddingConfig to config.py**

In `auto_daily_log/config.py`, add before `AppConfig`:

```python
class EmbeddingConfig(BaseModel):
    enabled: bool = True
    model: str = ""
    dimensions: int = 1536
```

And add to `AppConfig`:

```python
class AppConfig(BaseModel):
    server: ServerConfig = ServerConfig()
    monitor: MonitorConfig = MonitorConfig()
    git: GitConfig = GitConfig()
    jira: JiraConfig = JiraConfig()
    llm: LLMConfig = LLMConfig()
    scheduler: SchedulerConfig = SchedulerConfig()
    auto_approve: AutoApproveConfig = AutoApproveConfig()
    system: SystemConfig = SystemConfig()
    embedding: EmbeddingConfig = EmbeddingConfig()
```

- [ ] **Step 4: Implement embedding engine**

`auto_daily_log/search/__init__.py`: empty file

`auto_daily_log/search/embedding.py`:
```python
from abc import ABC, abstractmethod
from typing import Optional

import httpx

from ..config import LLMConfig, LLMProviderConfig, EmbeddingConfig

_DEFAULT_MODELS = {
    "kimi": ("moonshot-v1-embedding", 1024),
    "openai": ("text-embedding-3-small", 1536),
    "ollama": ("nomic-embed-text", 768),
}


class EmbeddingEngine(ABC):
    dimensions: int

    @abstractmethod
    async def embed(self, text: str) -> list[float]:
        ...

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [await self.embed(t) for t in texts]


class OpenAICompatibleEmbedding(EmbeddingEngine):
    """Works for Kimi and OpenAI (same API format)."""

    def __init__(self, config: LLMProviderConfig, model: str, dimensions: int):
        self._config = config
        self._model = model
        self.dimensions = dimensions

    async def embed(self, text: str) -> list[float]:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{self._config.base_url}/embeddings",
                headers={
                    "Authorization": f"Bearer {self._config.api_key}",
                    "Content-Type": "application/json",
                },
                json={"model": self._model, "input": text},
            )
            response.raise_for_status()
            return response.json()["data"][0]["embedding"]

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                f"{self._config.base_url}/embeddings",
                headers={
                    "Authorization": f"Bearer {self._config.api_key}",
                    "Content-Type": "application/json",
                },
                json={"model": self._model, "input": texts},
            )
            response.raise_for_status()
            data = response.json()["data"]
            return [d["embedding"] for d in sorted(data, key=lambda x: x["index"])]


class OllamaEmbedding(EmbeddingEngine):
    def __init__(self, config: LLMProviderConfig, model: str, dimensions: int):
        self._config = config
        self._model = model
        self.dimensions = dimensions

    async def embed(self, text: str) -> list[float]:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                f"{self._config.base_url}/api/embeddings",
                json={"model": self._model, "prompt": text},
            )
            response.raise_for_status()
            return response.json()["embedding"]


def get_embedding_engine(
    llm_config: LLMConfig, emb_config: EmbeddingConfig
) -> Optional[EmbeddingEngine]:
    if not emb_config.enabled:
        return None

    engine_name = llm_config.engine.lower()
    default_model, default_dims = _DEFAULT_MODELS.get(engine_name, ("", 1536))
    model = emb_config.model or default_model
    dimensions = emb_config.dimensions or default_dims

    if engine_name == "kimi":
        return OpenAICompatibleEmbedding(llm_config.kimi, model, dimensions)
    elif engine_name == "openai":
        return OpenAICompatibleEmbedding(llm_config.openai, model, dimensions)
    elif engine_name == "ollama":
        return OllamaEmbedding(llm_config.ollama, model, dimensions)
    elif engine_name == "claude":
        # Claude has no embedding API; return None
        return None
    return None
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/test_embedding.py -v`
Expected: 4 tests PASS

- [ ] **Step 6: Commit**

```bash
git add auto_daily_log/search/ auto_daily_log/config.py tests/test_embedding.py
git commit -m "feat: embedding engine abstraction for Kimi/OpenAI/Ollama"
```

---

## Task 6: sqlite-vec Integration in Database

**Files:**
- Modify: `auto_daily_log/models/database.py`
- Create: `tests/test_vec_search.py`

- [ ] **Step 1: Write failing test**

`tests/test_vec_search.py`:
```python
import pytest
import pytest_asyncio
from auto_daily_log.models.database import Database


@pytest_asyncio.fixture
async def db(tmp_path):
    database = Database(tmp_path / "test.db")
    await database.initialize()
    yield database
    await database.close()


@pytest.mark.asyncio
async def test_embeddings_table_exists(db):
    # vec0 tables appear as virtual tables
    tables = await db.fetch_all(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    )
    table_names = [t["name"] for t in tables]
    assert "embeddings" in table_names


@pytest.mark.asyncio
async def test_insert_and_search_embedding(db):
    # Insert two embeddings (4-dim for testing)
    await db.execute(
        "INSERT INTO embeddings (source_type, source_id, text_content, embedding) VALUES (?, ?, ?, ?)",
        ("activity", 1, "coding in IntelliJ", "[1.0, 0.0, 0.0, 0.0]"),
    )
    await db.execute(
        "INSERT INTO embeddings (source_type, source_id, text_content, embedding) VALUES (?, ?, ?, ?)",
        ("activity", 2, "meeting in Zoom", "[0.0, 1.0, 0.0, 0.0]"),
    )

    # Search for similar to [1.0, 0.1, 0.0, 0.0] — should find "coding" first
    rows = await db.fetch_all(
        "SELECT source_type, source_id, text_content, distance "
        "FROM embeddings WHERE embedding MATCH ? ORDER BY distance LIMIT 2",
        ("[1.0, 0.1, 0.0, 0.0]",),
    )
    assert len(rows) == 2
    assert rows[0]["source_id"] == 1  # coding is closer
    assert rows[0]["text_content"] == "coding in IntelliJ"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_vec_search.py -v`
Expected: FAIL (no embeddings table)

- [ ] **Step 3: Update Database to load sqlite-vec and create embeddings table**

Modify `auto_daily_log/models/database.py`. The key changes:

1. Load the sqlite-vec extension in `initialize()`
2. Add embeddings virtual table to schema

Replace the `initialize` method and add the vec table creation:

```python
import sqlite_vec

# ... existing code ...

class Database:
    def __init__(self, db_path: Path | str):
        self._db_path = str(db_path)
        self._conn: Optional[aiosqlite.Connection] = None

    async def initialize(self) -> None:
        self._conn = await aiosqlite.connect(self._db_path)
        self._conn.row_factory = aiosqlite.Row
        # Load sqlite-vec extension
        await self._conn.enable_load_extension(True)
        await self._conn.load_extension(sqlite_vec.loadable_path())
        await self._conn.enable_load_extension(False)
        # Create standard tables
        await self._conn.executescript(_SCHEMA_SQL)
        # Create vec0 virtual table (cannot be in executescript)
        await self._conn.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS embeddings USING vec0("
            "source_type TEXT, source_id INTEGER, text_content TEXT, "
            "embedding FLOAT[4])"
        )
        await self._conn.commit()
```

Note: We use `FLOAT[4]` as a default dimension. The actual dimension will be configured at runtime. Since sqlite-vec `vec0` tables can't ALTER dimensions after creation, we'll use a helper method to ensure the table matches the configured dimensions.

Actually, a cleaner approach: make dimension configurable at init time:

```python
class Database:
    def __init__(self, db_path: Path | str, embedding_dimensions: int = 1536):
        self._db_path = str(db_path)
        self._conn: Optional[aiosqlite.Connection] = None
        self._embedding_dimensions = embedding_dimensions

    async def initialize(self) -> None:
        self._conn = await aiosqlite.connect(self._db_path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.enable_load_extension(True)
        await self._conn.load_extension(sqlite_vec.loadable_path())
        await self._conn.enable_load_extension(False)
        await self._conn.executescript(_SCHEMA_SQL)
        await self._conn.execute(
            f"CREATE VIRTUAL TABLE IF NOT EXISTS embeddings USING vec0("
            f"source_type TEXT, source_id INTEGER, text_content TEXT, "
            f"embedding FLOAT[{self._embedding_dimensions}])"
        )
        await self._conn.commit()

    # ... rest unchanged ...
```

For tests, use `Database(path, embedding_dimensions=4)` so we can test with tiny vectors.

- [ ] **Step 4: Update test fixture to pass dimensions**

Update the fixture in `tests/test_vec_search.py`:
```python
@pytest_asyncio.fixture
async def db(tmp_path):
    database = Database(tmp_path / "test.db", embedding_dimensions=4)
    await database.initialize()
    yield database
    await database.close()
```

Also update `tests/conftest.py` to keep existing tests working (default dimensions):
```python
@pytest_asyncio.fixture
async def app_client(tmp_path):
    db = Database(tmp_path / "test.db", embedding_dimensions=4)
    await db.initialize()
    app = create_app(db)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    await db.close()
```

And update `tests/test_database.py` fixture similarly.

- [ ] **Step 5: Run tests**

Run: `pytest tests/test_vec_search.py tests/test_database.py -v`
Expected: All PASS

- [ ] **Step 6: Run full test suite**

Run: `pytest tests/ -v`
Expected: All PASS

- [ ] **Step 7: Commit**

```bash
git add auto_daily_log/models/database.py tests/test_vec_search.py tests/conftest.py tests/test_database.py
git commit -m "feat: sqlite-vec integration with embeddings virtual table"
```

---

## Task 7: Indexer and Searcher

**Files:**
- Create: `auto_daily_log/search/indexer.py`
- Create: `auto_daily_log/search/searcher.py`
- Create: `tests/test_searcher.py`

- [ ] **Step 1: Write failing test**

`tests/test_searcher.py`:
```python
import pytest
import pytest_asyncio
from unittest.mock import AsyncMock
from auto_daily_log.models.database import Database
from auto_daily_log.search.indexer import Indexer
from auto_daily_log.search.searcher import Searcher


@pytest_asyncio.fixture
async def db(tmp_path):
    database = Database(tmp_path / "test.db", embedding_dimensions=4)
    await database.initialize()
    yield database
    await database.close()


@pytest.fixture
def mock_engine():
    engine = AsyncMock()
    engine.dimensions = 4
    # Return different vectors for different texts
    async def fake_embed(text):
        if "coding" in text.lower() or "intellij" in text.lower():
            return [1.0, 0.0, 0.0, 0.0]
        elif "meeting" in text.lower() or "zoom" in text.lower():
            return [0.0, 1.0, 0.0, 0.0]
        else:
            return [0.5, 0.5, 0.0, 0.0]
    engine.embed = fake_embed
    return engine


@pytest.mark.asyncio
async def test_index_activity(db, mock_engine):
    # Insert an activity
    await db.execute(
        "INSERT INTO activities (timestamp, app_name, window_title, category, confidence, duration_sec) "
        "VALUES ('2026-04-12T10:00:00', 'IntelliJ IDEA', 'Main.java', 'coding', 0.92, 3600)"
    )

    indexer = Indexer(db, mock_engine)
    count = await indexer.index_activities("2026-04-12")
    assert count == 1

    rows = await db.fetch_all("SELECT * FROM embeddings WHERE source_type = 'activity'")
    assert len(rows) == 1
    assert "IntelliJ" in rows[0]["text_content"]


@pytest.mark.asyncio
async def test_search_returns_ranked_results(db, mock_engine):
    # Insert two activities and index them
    await db.execute(
        "INSERT INTO activities (timestamp, app_name, window_title, category, confidence, duration_sec) "
        "VALUES ('2026-04-12T10:00:00', 'IntelliJ IDEA', 'Main.java', 'coding', 0.92, 3600)"
    )
    await db.execute(
        "INSERT INTO activities (timestamp, app_name, window_title, category, confidence, duration_sec) "
        "VALUES ('2026-04-12T11:00:00', 'Zoom', 'Sprint Meeting', 'meeting', 0.95, 1800)"
    )

    indexer = Indexer(db, mock_engine)
    await indexer.index_activities("2026-04-12")

    searcher = Searcher(db, mock_engine)
    results = await searcher.search("coding in IntelliJ", limit=2)
    assert len(results) == 2
    assert results[0]["source_type"] == "activity"
    # Coding should be ranked first (closer to query vector)
    assert "IntelliJ" in results[0]["text_content"]


@pytest.mark.asyncio
async def test_search_with_source_type_filter(db, mock_engine):
    await db.execute(
        "INSERT INTO activities (timestamp, app_name, window_title, category, confidence, duration_sec) "
        "VALUES ('2026-04-12T10:00:00', 'IntelliJ', 'Main.java', 'coding', 0.92, 3600)"
    )
    await db.execute(
        "INSERT INTO git_commits (repo_id, hash, message, author, committed_at, files_changed, date) "
        "VALUES (1, 'abc', 'fix coding bug', 'test', '2026-04-12T10:30:00', '[]', '2026-04-12')"
    )

    indexer = Indexer(db, mock_engine)
    await indexer.index_activities("2026-04-12")
    await indexer.index_commits("2026-04-12")

    searcher = Searcher(db, mock_engine)
    results = await searcher.search("coding", limit=10, source_type="git_commit")
    assert all(r["source_type"] == "git_commit" for r in results)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_searcher.py -v`
Expected: FAIL

- [ ] **Step 3: Implement Indexer**

`auto_daily_log/search/indexer.py`:
```python
import json
from ..models.database import Database
from .embedding import EmbeddingEngine


class Indexer:
    def __init__(self, db: Database, engine: EmbeddingEngine):
        self._db = db
        self._engine = engine

    async def index_activities(self, target_date: str) -> int:
        activities = await self._db.fetch_all(
            "SELECT * FROM activities WHERE date(timestamp) = ? AND category != 'idle'",
            (target_date,),
        )
        count = 0
        for a in activities:
            existing = await self._db.fetch_one(
                "SELECT rowid FROM embeddings WHERE source_type = 'activity' AND source_id = ?",
                (a["id"],),
            )
            if existing:
                continue

            text = self._activity_to_text(a)
            if not text.strip():
                continue

            vec = await self._engine.embed(text)
            await self._db.execute(
                "INSERT INTO embeddings (source_type, source_id, text_content, embedding) "
                "VALUES (?, ?, ?, ?)",
                ("activity", a["id"], text, json.dumps(vec)),
            )
            count += 1
        return count

    async def index_commits(self, target_date: str) -> int:
        commits = await self._db.fetch_all(
            "SELECT * FROM git_commits WHERE date = ?", (target_date,)
        )
        count = 0
        for c in commits:
            existing = await self._db.fetch_one(
                "SELECT rowid FROM embeddings WHERE source_type = 'git_commit' AND source_id = ?",
                (c["id"],),
            )
            if existing:
                continue

            text = f"{c['message']} {c.get('files_changed', '')}"
            vec = await self._engine.embed(text)
            await self._db.execute(
                "INSERT INTO embeddings (source_type, source_id, text_content, embedding) "
                "VALUES (?, ?, ?, ?)",
                ("git_commit", c["id"], text, json.dumps(vec)),
            )
            count += 1
        return count

    async def index_worklog(self, draft_id: int) -> None:
        draft = await self._db.fetch_one(
            "SELECT * FROM worklog_drafts WHERE id = ?", (draft_id,)
        )
        if not draft:
            return
        text = f"{draft['issue_key']} {draft['summary']}"
        vec = await self._engine.embed(text)
        await self._db.execute(
            "INSERT INTO embeddings (source_type, source_id, text_content, embedding) "
            "VALUES (?, ?, ?, ?)",
            ("worklog", draft_id, text, json.dumps(vec)),
        )

    def _activity_to_text(self, activity: dict) -> str:
        parts = []
        if activity.get("app_name"):
            parts.append(activity["app_name"])
        if activity.get("window_title"):
            parts.append(activity["window_title"])
        if activity.get("url"):
            parts.append(activity["url"])
        if activity.get("signals"):
            try:
                signals = json.loads(activity["signals"])
                if signals.get("ocr_text"):
                    parts.append(signals["ocr_text"][:500])
            except (json.JSONDecodeError, TypeError):
                pass
        return " ".join(parts)
```

- [ ] **Step 4: Implement Searcher**

`auto_daily_log/search/searcher.py`:
```python
import json
from typing import Optional
from ..models.database import Database
from .embedding import EmbeddingEngine


class Searcher:
    def __init__(self, db: Database, engine: EmbeddingEngine):
        self._db = db
        self._engine = engine

    async def search(
        self,
        query: str,
        limit: int = 20,
        source_type: Optional[str] = None,
    ) -> list[dict]:
        query_vec = await self._engine.embed(query)

        if source_type:
            rows = await self._db.fetch_all(
                "SELECT source_type, source_id, text_content, distance "
                "FROM embeddings WHERE embedding MATCH ? AND source_type = ? "
                "ORDER BY distance LIMIT ?",
                (json.dumps(query_vec), source_type, limit),
            )
        else:
            rows = await self._db.fetch_all(
                "SELECT source_type, source_id, text_content, distance "
                "FROM embeddings WHERE embedding MATCH ? "
                "ORDER BY distance LIMIT ?",
                (json.dumps(query_vec), limit),
            )

        return [dict(r) for r in rows]
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/test_searcher.py -v`
Expected: 3 tests PASS

- [ ] **Step 6: Commit**

```bash
git add auto_daily_log/search/indexer.py auto_daily_log/search/searcher.py tests/test_searcher.py
git commit -m "feat: embedding indexer and vector searcher with sqlite-vec"
```

---

## Task 8: Search API Route

**Files:**
- Create: `auto_daily_log/web/api/search.py`
- Modify: `auto_daily_log/web/app.py`

- [ ] **Step 1: Implement search API route**

`auto_daily_log/web/api/search.py`:
```python
from fastapi import APIRouter, Request, Query, HTTPException
from typing import Optional

router = APIRouter(tags=["search"])


@router.get("/search")
async def search(
    request: Request,
    q: str = Query(..., description="Search query"),
    limit: int = Query(default=20, le=100),
    source_type: Optional[str] = Query(default=None, description="Filter: activity/git_commit/worklog"),
):
    db = request.app.state.db
    searcher = getattr(request.app.state, "searcher", None)
    if not searcher:
        raise HTTPException(503, "Search not available — embedding engine not configured")

    results = await searcher.search(q, limit=limit, source_type=source_type)
    return results
```

- [ ] **Step 2: Register route in app.py**

Update `auto_daily_log/web/app.py`:

```python
from .api import settings, issues, activities, worklogs, dashboard, git_repos, search

def create_app(db: Database) -> FastAPI:
    app = FastAPI(title="Auto Daily Log", version="0.1.0")
    app.state.db = db
    app.include_router(settings.router, prefix="/api")
    app.include_router(issues.router, prefix="/api")
    app.include_router(activities.router, prefix="/api")
    app.include_router(worklogs.router, prefix="/api")
    app.include_router(dashboard.router, prefix="/api")
    app.include_router(git_repos.router, prefix="/api")
    app.include_router(search.router, prefix="/api")
    from fastapi.staticfiles import StaticFiles
    from pathlib import Path
    frontend_dist = Path(__file__).parent.parent.parent / "web" / "frontend" / "dist"
    if frontend_dist.exists():
        app.mount("/", StaticFiles(directory=str(frontend_dist), html=True), name="frontend")
    return app
```

- [ ] **Step 3: Add search to frontend API client**

Append to `web/frontend/src/api/index.js`:

```javascript
  search: (q, limit = 20, sourceType = null) => {
    const params = { q, limit }
    if (sourceType) params.source_type = sourceType
    return api.get('/search', { params })
  },
```

- [ ] **Step 4: Commit**

```bash
git add auto_daily_log/web/api/search.py auto_daily_log/web/app.py web/frontend/src/api/index.js
git commit -m "feat: search API route and frontend API client"
```

---

## Task 9: Frontend Search UI

**Files:**
- Modify: `web/frontend/src/views/Dashboard.vue`

- [ ] **Step 1: Add search box to Dashboard**

Replace `web/frontend/src/views/Dashboard.vue`:

```vue
<template>
  <div>
    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px">
      <h2>Dashboard</h2>
      <el-date-picker v-model="selectedDate" type="date" value-format="YYYY-MM-DD" @change="loadData" />
    </div>

    <!-- Search -->
    <el-card style="margin-bottom: 20px">
      <div style="display: flex; gap: 10px">
        <el-input
          v-model="searchQuery"
          placeholder="Search activities, commits, worklogs..."
          @keyup.enter="doSearch"
          clearable
          @clear="searchResults = []"
        >
          <template #prefix>
            <el-icon><Search /></el-icon>
          </template>
        </el-input>
        <el-select v-model="searchType" placeholder="All" style="width: 150px" clearable>
          <el-option label="All" value="" />
          <el-option label="Activities" value="activity" />
          <el-option label="Git Commits" value="git_commit" />
          <el-option label="Worklogs" value="worklog" />
        </el-select>
        <el-button type="primary" @click="doSearch" :loading="searching">Search</el-button>
      </div>

      <div v-if="searchResults.length > 0" style="margin-top: 16px">
        <el-table :data="searchResults" stripe max-height="400">
          <el-table-column label="Type" width="100">
            <template #default="{ row }">
              <el-tag size="small" :type="sourceTagType(row.source_type)">{{ row.source_type }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="text_content" label="Content" show-overflow-tooltip />
          <el-table-column label="Relevance" width="100">
            <template #default="{ row }">
              {{ row.distance !== undefined ? (1 - row.distance).toFixed(2) : '-' }}
            </template>
          </el-table-column>
        </el-table>
      </div>
      <div v-else-if="searchQuery && searched" style="text-align: center; padding: 20px; color: #909399">
        No results
      </div>
    </el-card>

    <el-row :gutter="20" style="margin-bottom: 20px">
      <el-col :span="8">
        <el-card>
          <template #header>Pending Review</template>
          <div style="font-size: 36px; text-align: center; color: #E6A23C">
            {{ dashboard.pending_review_count }}
          </div>
        </el-card>
      </el-col>
      <el-col :span="8">
        <el-card>
          <template #header>Submitted Hours</template>
          <div style="font-size: 36px; text-align: center; color: #67C23A">
            {{ dashboard.submitted_hours }}h
          </div>
        </el-card>
      </el-col>
      <el-col :span="8">
        <el-card>
          <template #header>Total Activity</template>
          <div style="font-size: 36px; text-align: center; color: #409EFF">
            {{ totalActivityHours }}h
          </div>
        </el-card>
      </el-col>
    </el-row>

    <el-card>
      <template #header>Activity Breakdown</template>
      <el-table :data="dashboard.activity_summary" stripe>
        <el-table-column prop="category" label="Category" />
        <el-table-column label="Duration">
          <template #default="{ row }">
            {{ (row.total_sec / 3600).toFixed(1) }}h
          </template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import api from '../api'

const selectedDate = ref(new Date().toISOString().split('T')[0])
const dashboard = ref({ pending_review_count: 0, submitted_hours: 0, activity_summary: [] })

const searchQuery = ref('')
const searchType = ref('')
const searchResults = ref([])
const searching = ref(false)
const searched = ref(false)

const totalActivityHours = computed(() => {
  const total = (dashboard.value.activity_summary || []).reduce((s, a) => s + a.total_sec, 0)
  return (total / 3600).toFixed(1)
})

function sourceTagType(type) {
  return { activity: 'success', git_commit: 'warning', worklog: 'info' }[type] || ''
}

async function loadData() {
  const res = await api.getDashboard(selectedDate.value)
  dashboard.value = res.data
}

async function doSearch() {
  if (!searchQuery.value.trim()) return
  searching.value = true
  searched.value = false
  try {
    const res = await api.search(searchQuery.value, 20, searchType.value || null)
    searchResults.value = res.data
  } catch (e) {
    ElMessage.warning(e.response?.data?.detail || 'Search unavailable')
    searchResults.value = []
  } finally {
    searching.value = false
    searched.value = true
  }
}

onMounted(loadData)
</script>
```

- [ ] **Step 2: Build frontend**

```bash
cd /Users/conner/Zone/code/ai_project/auto_daily_log/web/frontend
npm run build
```

- [ ] **Step 3: Commit**

```bash
cd /Users/conner/Zone/code/ai_project/auto_daily_log
git add web/frontend/
git commit -m "feat: search UI in Dashboard with type filter and relevance display"
```

---

## Task 10: Wire Embedding into Application + Config Update

**Files:**
- Modify: `auto_daily_log/app.py`
- Modify: `config.yaml`

- [ ] **Step 1: Update config.yaml**

Append embedding section:
```yaml
embedding:
  enabled: true
  model: ""
  dimensions: 1536
```

And update monitor section:
```yaml
monitor:
  interval_sec: 30
  ocr_enabled: true
  ocr_engine: auto
  screenshot_retention_days: 7
  phash_enabled: true
  phash_threshold: 10
  idle_threshold_sec: 180
  privacy:
    blocked_apps: []
    blocked_urls: []
```

- [ ] **Step 2: Update Application to init searcher and attach to app.state**

In `auto_daily_log/app.py`, add embedding/search initialization:

```python
import asyncio
from datetime import datetime
from pathlib import Path

import uvicorn
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from .config import AppConfig, load_config
from .models.database import Database
from .monitor.service import MonitorService
from .scheduler.jobs import DailyWorkflow
from .summarizer.engine import get_llm_engine
from .search.embedding import get_embedding_engine
from .search.searcher import Searcher
from .search.indexer import Indexer
from .web.app import create_app


class Application:
    def __init__(self, config: AppConfig):
        self.config = config
        self.db: Database = None
        self.monitor: MonitorService = None
        self.scheduler: AsyncIOScheduler = None

    async def _init_db(self) -> None:
        db_path = Path.home() / ".auto_daily_log" / "data.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db = Database(db_path, embedding_dimensions=self.config.embedding.dimensions)
        await self.db.initialize()

    async def _init_monitor(self) -> None:
        screenshot_dir = Path.home() / ".auto_daily_log" / "screenshots"
        self.monitor = MonitorService(self.db, self.config.monitor, screenshot_dir)

    def _init_scheduler(self) -> None:
        if not self.config.scheduler.enabled:
            return

        self.scheduler = AsyncIOScheduler()
        hour, minute = map(int, self.config.scheduler.trigger_time.split(":"))

        async def daily_job():
            engine = get_llm_engine(self.config.llm)
            workflow = DailyWorkflow(self.db, engine, self.config.auto_approve)
            await workflow.run_daily_summary()

            # Index today's data for search
            emb_engine = get_embedding_engine(self.config.llm, self.config.embedding)
            if emb_engine:
                indexer = Indexer(self.db, emb_engine)
                today = datetime.now().strftime("%Y-%m-%d")
                await indexer.index_activities(today)
                await indexer.index_commits(today)

            if self.config.auto_approve.enabled:
                timeout = self.config.auto_approve.timeout_min * 60
                await asyncio.sleep(timeout)
                today = datetime.now().strftime("%Y-%m-%d")
                await workflow.auto_approve_pending(today)

        self.scheduler.add_job(
            daily_job, "cron", hour=hour, minute=minute, id="daily_summary"
        )
        self.scheduler.start()

    async def run(self) -> None:
        await self._init_db()
        await self._init_monitor()
        self._init_scheduler()

        app = create_app(self.db)

        # Attach searcher to app state if embedding is enabled
        emb_engine = get_embedding_engine(self.config.llm, self.config.embedding)
        if emb_engine:
            app.state.searcher = Searcher(self.db, emb_engine)

        monitor_task = asyncio.create_task(self.monitor.start())

        config = uvicorn.Config(
            app,
            host=self.config.server.host,
            port=self.config.server.port,
            log_level="info",
        )
        server = uvicorn.Server(config)

        try:
            await server.serve()
        finally:
            self.monitor.stop()
            monitor_task.cancel()
            if self.scheduler:
                self.scheduler.shutdown()
            await self.db.close()
```

- [ ] **Step 3: Run full test suite**

Run: `pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 4: Commit**

```bash
git add auto_daily_log/app.py config.yaml
git commit -m "feat: wire embedding engine and searcher into application lifecycle"
```
