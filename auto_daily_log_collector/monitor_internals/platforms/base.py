from abc import ABC, abstractmethod
from typing import Optional, Tuple

class PlatformAPI(ABC):
    @abstractmethod
    def get_frontmost_app(self) -> Optional[str]: ...
    @abstractmethod
    def get_window_title(self, app_name: str) -> Optional[str]: ...
    @abstractmethod
    def get_browser_tab(self, app_name: str) -> Tuple[Optional[str], Optional[str]]:
        """Returns (title, url)"""
        ...
    @abstractmethod
    def get_wecom_chat_name(self, app_name: str) -> Optional[str]: ...
