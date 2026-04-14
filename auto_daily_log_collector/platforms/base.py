"""Platform adapter contract for collectors.

Each platform/session combination implements this interface. The
factory picks one at startup. Adapters must declare their
capabilities so the server knows what features each machine supports.
"""
from abc import ABC, abstractmethod
from typing import Optional


class PlatformAdapter(ABC):
    @abstractmethod
    def platform_id(self) -> str:
        """e.g. 'macos', 'windows', 'linux-x11'."""
        ...

    @abstractmethod
    def platform_detail(self) -> str:
        """Human-readable platform version, e.g. 'macOS 14.2'."""
        ...

    @abstractmethod
    def capabilities(self) -> set[str]:
        """Subset of shared.schemas.ALL_CAPABILITIES this adapter supports."""
        ...

    @abstractmethod
    def get_frontmost_app(self) -> Optional[str]:
        ...

    @abstractmethod
    def get_window_title(self, app_name: str) -> Optional[str]:
        ...

    @abstractmethod
    def get_browser_tab(self, app_name: str) -> tuple[Optional[str], Optional[str]]:
        """Returns (tab_title, url)."""
        ...

    @abstractmethod
    def capture_screenshot(self, output_path) -> bool:
        """Capture full-screen screenshot to output_path. Returns True on success."""
        ...

    @abstractmethod
    def get_idle_seconds(self) -> float:
        ...
