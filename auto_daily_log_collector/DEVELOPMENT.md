# Collector 开发规划与测试规范

> 给跨平台协作者（包括 AI 助手）的实施指引。任何平台的 collector 新实现都必须符合本文档的契约。

---

## 1. 项目概览

**Auto Daily Log** 采用 **Server + Collector** 分布式架构：

- **Server**（中心节点）：Web UI、SQLite 中心库、LLM 总结、Jira 提交
- **Collector**（采集节点，本项目）：在每台工作机上运行，采集活动/截图/commits，通过 HTTP 推送到 server

### 平台代码分两层

加新平台或修 bug **必须先看懂这张图**。写错层是 review 阶段最常见的退回原因。

```
          Server 内置 collector           Standalone collector
          (machine_id='local')            (任意机器，推 HTTP 到 server)
                   │                              │
                   ▼                              ▼
          ┌────────────────┐            ┌────────────────────────┐
          │ MonitorService │            │ PlatformAdapter (契约) │
          │ (采样循环)      │            │ • platform_id()        │
          └────────┬───────┘            │ • capabilities()       │
                   │                     │ • get_frontmost_app()  │
                   │                     │ • capture_screenshot() │
                   │                     │ • ...                  │
                   │                     └───────────┬────────────┘
                   │                                 │
                   ▼                                 ▼
┌────────────────────────────────────────────────────────────────┐
│ auto_daily_log/monitor/  ← 底层 raw OS API（共享底座）            │
│   platforms/{macos,linux,windows,gnome_wayland}.py : PlatformAPI│
│   idle.py / screenshot.py / portal_screencast.py / ocr.py       │
│   phash.py / classifier.py / watchdog.py                        │
└────────────────────────────────────────────────────────────────┘
```

**职责分工**：

| 层 | 位置 | 干什么 | 不干什么 |
|----|------|--------|----------|
| 底层 | `auto_daily_log/monitor/` | 调 AppleScript / xdotool / Atspi / gdbus / `screencapture` / gstreamer 等原生 API；返回原始数据 | 不声明 capabilities，不做 HTTP，不关心分布式 |
| Adapter | `auto_daily_log_collector/platforms/` | 包装底层 API + 声明能力集 + 在 factory 注册 platform_id | 不写新的 OS 调用；发现底层缺东西要先去底层加 |

**加新平台 = 两层都要动**：
- 只加底层不做 adapter → 分布式 collector 用户完全拿不到
- 只做 adapter 不动底层 → 底层没有 API 可调用
- 两层 class 名不同：底层是 `XxxAPI`，adapter 是 `XxxAdapter`

> **关于"独立可运行"**（2026-04-15 更新）：collector 包现在是真的 standalone。
> 历史上底层实现在 `auto_daily_log/monitor/`，collector adapter 层通过 import
> 复用。经过一次大重构后，这些底层模块已整体搬到 `auto_daily_log_collector/monitor_internals/`，
> collector 代码里不再 `from auto_daily_log.monitor.*`。Server 端（`auto_daily_log/`）
> 反过来也可以复用 collector 的底座——`CollectorRuntime + LocalSQLiteBackend`
> 就是 server 内嵌模式（`monitor.enabled=true`）的实现。所以现在是**单向依赖**：
> server 依赖 collector 包，collector 不依赖 server。

### 核心模块

```
auto_daily_log_collector/
├── __main__.py           # CLI 入口（含 --uninstall）
├── config.py             # Pydantic 配置模型 + load_config()
├── credentials.py        # machine_id + token 本地持久化
├── client.py             # 注册 HTTP client
├── runner.py             # 主循环 + 心跳 + override 消费 + pause
├── platforms/
│   ├── base.py           # PlatformAdapter 抽象接口 ← 实现目标
│   ├── factory.py        # 根据 OS + 会话类型自动选 adapter
│   ├── macos.py          # ✅ 已实现
│   ├── windows.py        # ⚠️  占位实现，待验证/增强
│   └── linux.py          # X11/Wayland/Headless 三个 adapter；Wayland 底层已补（Atspi+portal），但 adapter 尚未接入
```

---

## 2. PlatformAdapter 契约（硬性约定）

**所有平台 adapter 必须实现以下方法，签名不可改**：

```python
class PlatformAdapter(ABC):
    @abstractmethod
    def platform_id(self) -> str:
        """返回 shared.schemas.PLATFORM_* 常量之一。"""

    @abstractmethod
    def platform_detail(self) -> str:
        """人类可读的系统版本描述，e.g. 'macOS 14.2', 'Ubuntu 22.04', 'Windows 11 22H2'."""

    @abstractmethod
    def capabilities(self) -> set[str]:
        """声明本 adapter 支持的能力集合。必须是 shared.schemas.ALL_CAPABILITIES 子集。"""

    @abstractmethod
    def get_frontmost_app(self) -> Optional[str]:
        """返回当前前台应用名。无前台/出错时返回 None，绝不抛异常。"""

    @abstractmethod
    def get_window_title(self, app_name: str) -> Optional[str]:
        """返回指定 app 的前台窗口标题。同样不抛异常。"""

    @abstractmethod
    def get_browser_tab(self, app_name: str) -> tuple[Optional[str], Optional[str]]:
        """浏览器专用：返回 (tab_title, url)。非浏览器 app 返回 (None, None)。"""

    @abstractmethod
    def capture_screenshot(self, output_path) -> bool:
        """
        截屏保存到 output_path。
        返回 True 表示成功且文件存在；False 表示失败（权限 / 工具缺失 / OS 限制）。
        绝不抛异常。
        """

    @abstractmethod
    def get_idle_seconds(self) -> float:
        """返回用户空闲秒数。无法检测时返回 0.0（表示"始终活跃"）。"""
```

### 关键设计原则

1. **方法绝不抛异常** — 任何失败（权限、工具缺失、系统限制）都返回 `None` / `False` / `0.0`
2. **Capabilities 是契约** — 只有声明了某能力，server 才会期望该 adapter 产出对应数据
3. **空闲值语义** — 0.0 = 活跃，大于 `idle_threshold_sec` = 真正空闲
4. **URL 提取仅限浏览器** — 其他 app 返回 `(None, None)`，别试图从系统 UI 抠 URL

### Capabilities 常量

见 `shared/schemas.py`：

| 常量 | 含义 | 声明条件 |
|------|------|----------|
| `CAPABILITY_WINDOW_TITLE` | 能读取前台窗口标题 | `get_window_title` 非 None |
| `CAPABILITY_BROWSER_TAB` | 能读取浏览器当前 tab URL | 至少一个浏览器的 tab 可读 |
| `CAPABILITY_SCREENSHOT` | 能截全屏 | `capture_screenshot` 成功返回文件 |
| `CAPABILITY_OCR` | 安装了 OCR 引擎（tesseract/Vision/WinOCR） | 对应库/二进制存在 |
| `CAPABILITY_IDLE` | 能读取空闲时长 | `get_idle_seconds` 非恒为 0 |
| `CAPABILITY_GIT` | 能读取本机 git 仓库（通常都能） | 总是声明 |

### 平台 ID 常量

`shared/schemas.py` 里定义了：

```python
PLATFORM_MACOS = "macos"
PLATFORM_WINDOWS = "windows"
PLATFORM_LINUX_X11 = "linux-x11"
PLATFORM_LINUX_WAYLAND = "linux-wayland"
PLATFORM_LINUX_HEADLESS = "linux-headless"
```

**新平台必须提交 PR 到 `shared/schemas.py` 添加常量后再实现 adapter。**

---

## 3. 平台特定实施要求

### 3.1 Windows

**目标文件**：`auto_daily_log_collector/platforms/windows.py`（已有占位）

**推荐技术栈**：

| 能力 | 方案 | Python 库 |
|------|------|-----------|
| 前台窗口 | `GetForegroundWindow` + `GetWindowText` | `pywin32` 或 `ctypes` |
| 进程名 | `GetWindowThreadProcessId` → `QueryFullProcessImageNameW` | 同上 |
| 浏览器 tab/URL | UI Automation（Chrome/Edge） | `pywinauto` |
| 截图 | `Windows.Graphics.Capture` API 或 PowerShell Fallback | `mss` 或 CLI |
| 空闲时间 | `GetLastInputInfo` | `ctypes` |
| OCR | Windows.Media.Ocr | `winocr` |

**强制要求**：

1. **不依赖 pywin32 以外的重型库** — 最小化依赖，避免部署复杂
2. **浏览器 URL 优雅降级** — 无法读取时返回 `(tab_title, None)` 也行
3. **截图选用 `mss` 库**（跨 Windows 版本稳定），PowerShell 作为 fallback
4. **Unicode 路径支持** — 用户名/路径含中文必须 OK

**测试重点**：

- 切换到 Chrome / Edge 时能读到 active tab（至少 title）
- 多显示器环境下截图覆盖所有屏幕
- Wecom（企业微信）切换不会导致采集进程崩溃（**已知敏感 app**，详见下方）
- 中文窗口标题正确保留，不乱码
- UAC 弹窗出现时不卡死（osascript 等待超时要有上限）

### 3.2 Linux

**目标文件**：`auto_daily_log_collector/platforms/linux.py`（X11 已可用，Wayland 降级）

**会话类型检测**（必须）：

```python
if os.environ.get("XDG_SESSION_TYPE") == "wayland":
    return LinuxWaylandAdapter()
elif os.environ.get("DISPLAY"):
    return LinuxX11Adapter()
else:
    return LinuxHeadlessAdapter()  # SSH / Docker / Server
```

**X11 实现要求**：

| 能力 | 推荐工具 | 备注 |
|------|----------|------|
| 前台窗口 | `xdotool getactivewindow getwindowname` | 大部分发行版预装或易装 |
| 进程名 | `xdotool getactivewindow getwindowpid` + `ps -p $pid -o comm=` | |
| 浏览器 tab | **无稳定方案** | 返回 `(None, None)` 即可 |
| 截图 | `gnome-screenshot -f` / `scrot` / `maim` / `import`（任一） | 多重 fallback |
| 空闲 | `xprintidle`（毫秒输出） | 仅 X11 可用 |
| OCR | `tesseract` + `pytesseract` | 必须安装中文语言包 `tesseract-ocr-chi-sim` |

**Wayland 实现要求**（难点多）：

- **窗口信息**：Wayland 禁止跨 app introspection。仅支持：
  - Sway/Hyprland: `swaymsg -t get_tree` → JSON 解析
  - GNOME (>= 42): D-Bus `org.gnome.Shell.Introspect`（需要扩展）
  - KDE: `kdotool`（如已安装）
- **截图**：`grim`（wlroots）/ `gnome-screenshot`（GNOME portal）/ `spectacle -b`（KDE）
- **空闲检测**：`gnome-idle-monitor` D-Bus / `swayidle` 不支持读取，只能设超时
- **允许大量能力缺失** — 返回空集合，不要强凑

**发行版差异处理**：

```python
def _distro() -> str:
    with open("/etc/os-release") as f:
        for line in f:
            if line.startswith("PRETTY_NAME="):
                return line.split("=", 1)[1].strip().strip('"')
    return "Linux"
```

在 `platform_detail()` 返回 `f"{_distro()} (X11)"` 方便 server 端区分统计。

**Headless 实现**：

无 GUI 环境（SSH 服务器 / CI / Docker without X）。所有方法返回 `None/False/0.0`，`capabilities()` 返回 `set()`。仅 git 采集可用。

### 3.3 macOS（参考实现，勿改）

已完整实现。如需扩展（如 Shortcuts App 集成），新增方法而非改动现有契约。

**已知企业微信自退问题**：
- `tell process "企业微信"` 会触发企业微信自我保护退出
- 已在 macos.py 加了 hostile-app 白名单跳过深入 AppleScript 调用
- **对应的 Windows adapter 需用 UI Automation 做同样保护**

---

## 4. Runner 集成契约

Collector 主循环 `runner.py` 已经写好，新 adapter **不用**动它。Adapter 只需正确实现接口，Runner 会自动：

1. 首次启动时调 `platform_id / platform_detail / capabilities` 注册到 server
2. 每个采样周期调 `get_frontmost_app → get_window_title → get_browser_tab`
3. OCR 开启时调 `capture_screenshot → <ocr_engine>`
4. 每 30s 调 `get_idle_seconds` 判断是否记录 idle 活动
5. 心跳时 apply 从 server 下发的 config override

---

## 5. 测试规范

### 5.1 单元测试（必须，跨平台可跑）

在 `tests/` 下为新 adapter 建对应文件（如 `test_windows_adapter.py`），**必须满足**：

- **每个抽象方法至少 2 条测试**：happy path + 失败路径
- **断言具体值**：严禁 `assert x` 或 `assert x is not None`，必须 `assert x == "expected_value"`
- **所有 subprocess 调用 mock**：用 `unittest.mock.patch` 控制返回值，不依赖真实工具存在
- **capabilities 断言精确集合**：`assert caps == {"screenshot", "idle"}`，不能只判长度

**示例模板**：

```python
import pytest
from unittest.mock import patch, MagicMock
from auto_daily_log_collector.platforms import create_adapter


class TestWindowsAdapter:
    def test_platform_id_is_windows(self):
        adapter = create_adapter("windows")
        assert adapter.platform_id() == "windows"

    def test_platform_detail_contains_version(self):
        adapter = create_adapter("windows")
        detail = adapter.platform_detail()
        # Must include "Windows" and a version number
        assert "Windows" in detail
        assert any(c.isdigit() for c in detail)

    @patch("auto_daily_log_collector.platforms.windows.win32gui.GetForegroundWindow")
    @patch("auto_daily_log_collector.platforms.windows.win32gui.GetWindowText")
    def test_get_frontmost_app_reads_foreground(self, mock_title, mock_fg):
        mock_fg.return_value = 12345
        mock_title.return_value = "Notepad"
        adapter = create_adapter("windows")
        assert adapter.get_frontmost_app() == "Notepad"

    @patch("auto_daily_log_collector.platforms.windows.win32gui.GetForegroundWindow")
    def test_get_frontmost_app_returns_none_on_zero_handle(self, mock_fg):
        mock_fg.return_value = 0  # no foreground window
        adapter = create_adapter("windows")
        assert adapter.get_frontmost_app() is None

    def test_capabilities_is_subset_of_all(self):
        from shared.schemas import ALL_CAPABILITIES
        adapter = create_adapter("windows")
        assert adapter.capabilities() <= ALL_CAPABILITIES

    def test_get_browser_tab_returns_tuple_of_two(self):
        adapter = create_adapter("windows")
        result = adapter.get_browser_tab("Non-Browser.exe")
        assert isinstance(result, tuple)
        assert len(result) == 2
        assert result == (None, None)

    def test_capture_screenshot_returns_false_when_tool_missing(self, tmp_path):
        with patch("subprocess.run", side_effect=FileNotFoundError):
            adapter = create_adapter("windows")
            assert adapter.capture_screenshot(tmp_path / "x.png") is False
```

**绝对禁止的断言**（会被拒收）：

```python
assert result              # 太笼统
assert result is not None  # 没验证内容
assert len(result) > 0     # 没验证具体值
assert "something" in str(result)  # 模糊匹配（除非是合理的子串检查）
```

### 5.2 集成测试（条件性，需真实环境）

新 adapter 实现后，**必须**在目标平台上手动跑下面 6 个场景，提交一份 `MANUAL_TEST.md` 记录结果（含截图）：

1. **基础采集**：打开浏览器 / 编辑器 / 终端，确认 `get_frontmost_app` 返回正确
2. **窗口切换**：3 秒切一次窗口，连续 10 次，每次都能正确捕获
3. **浏览器 URL**（如声明了 BROWSER_TAB 能力）：Chrome/Edge 切 3 个不同 tab，URL 都正确
4. **截图**：开启 OCR 模式，跑 5 分钟，确认截图保存到 `~/.auto_daily_log_collector/screenshots/`
5. **空闲检测**：离开键盘 5 分钟，确认 server 端收到 `category='idle'` 活动
6. **反侦测 app**（Windows/macOS）：打开企业微信 10 分钟，确认它**不会**因为 collector 采集而退出

### 5.3 CI 要求（可选但推荐）

新增平台时在 `.github/workflows/` 加对应 runner：

```yaml
# .github/workflows/collector-windows.yml
jobs:
  test:
    runs-on: windows-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12" }
      - run: pip install -e ".[dev,windows]"
      - run: pytest tests/test_windows_adapter.py -v
```

---

## 6. 依赖与打包约定

### 6.1 pyproject.toml 添加新依赖

Windows 新增依赖统一放在 `optional-dependencies.windows`：

```toml
[project.optional-dependencies]
windows = [
    "winocr>=0.1.0",
    "pywin32>=306",
    "mss>=9.0",       # 新增
]
```

**严禁**把平台专有依赖（pywin32、pyobjc）加到 `dependencies`（主依赖），否则另外平台安装会炸。

### 6.2 系统依赖

Linux 需要系统工具（如 `xdotool`）在 `install.sh` 里自动装。其他平台同理。

### 6.3 导入守则

- **条件导入**：平台专属库必须 `try/except ImportError`，让 adapter 在其他平台至少能 import 不崩
- **延迟导入**：把 `import winocr` 放到 `capabilities()` 方法内部，不要顶层 import

---

## 7. 提交检查清单

交付新 adapter 前，请对照：

- [ ] 实现了 `PlatformAdapter` 所有抽象方法
- [ ] 所有方法在异常时返回 None/False/0.0，**没有抛出**
- [ ] `platform_id()` 返回值在 `shared.schemas` 已定义
- [ ] `capabilities()` 只声明**真正能用**的能力（例如 OCR 引擎未装则不声明）
- [ ] `tests/test_<platform>_adapter.py` ≥ 10 条测试，每个抽象方法 ≥ 2 条
- [ ] 所有测试用**精确值断言**，跑 `pytest tests/test_<platform>_adapter.py` 全绿
- [ ] pyproject.toml 的 `optional-dependencies.<platform>` 已更新
- [ ] README 或 install.sh 描述了新平台的系统依赖
- [ ] 目标平台上手动跑过 §5.2 的 6 个集成场景
- [ ] 若有反侦测 app 问题（类似企业微信），已加 hostile-app 跳过逻辑

---

## 8. 共同的注意事项

1. **不要动 runner.py** 除非修 bug。新功能加在 adapter 里。
2. **protocol 变更**（`shared/schemas.py`）需要同步 server 端确认兼容
3. **反侦测 app** 是真实存在的问题（macOS 企业微信案例），见 `auto_daily_log/monitor/platforms/macos.py` 中 `_HOSTILE_APPS` 的处理。Windows adapter 请镜像同样策略
4. **隐私** — 永远不记录 OCR 中的敏感内容（密码、银行卡）。用户在 config 里配 `blocked_apps` / `blocked_urls` 必须严格尊重
5. **性能** — 每次采样总耗时 < 500ms，否则会影响 UI 响应。subprocess 调用加 3s 超时

---

## 9. 问题反馈

在目标平台发现契约无法实现时，提 Issue 讨论是否需要调整 `PlatformAdapter` 接口。**不要**私自改接口，会破坏其他平台。

**参考实现**：`auto_daily_log_collector/platforms/macos.py`
**联合测试**：`tests/test_phase_d_collector.py`
