import platform

import pytest


@pytest.fixture
def state_file(tmp_path, monkeypatch):
    path = tmp_path / 'wayland-state.json'
    monkeypatch.setenv('AUTO_DAILY_LOG_WAYLAND_STATE_FILE', str(path))
    return path


def test_gnome_wayland_api_reads_state_file(state_file):
    state_file.write_text(
        '{"app_name":"Google Chrome","window_title":"Jira - Chrome","browser_url":null}',
        encoding='utf-8',
    )

    from auto_daily_log_collector.monitor_internals.platforms.gnome_wayland import GnomeWaylandAPI

    api = GnomeWaylandAPI()

    assert api.get_frontmost_app() == 'Google Chrome'
    assert api.get_window_title('Google Chrome') == 'Jira - Chrome'
    assert api.get_browser_tab('Google Chrome') == ('Jira - Chrome', None)


def test_detect_prefers_gnome_wayland_provider(monkeypatch, state_file):
    state_file.write_text('{"app_name":"Terminal","window_title":"bash"}', encoding='utf-8')
    monkeypatch.setattr(platform, 'system', lambda: 'Linux')
    monkeypatch.setenv('WAYLAND_DISPLAY', 'wayland-0')
    monkeypatch.setenv('GNOME_SHELL_SESSION_MODE', 'zorin')

    from auto_daily_log_collector.monitor_internals.platforms.detect import get_platform_module
    from auto_daily_log_collector.monitor_internals.platforms.gnome_wayland import GnomeWaylandAPI

    module = get_platform_module()
    assert isinstance(module, GnomeWaylandAPI)


def test_gnome_wayland_api_prefers_active_window(monkeypatch):
    class FakeStates:
        def __init__(self, active=False, focused=False, showing=True):
            self._active = active
            self._focused = focused
            self._showing = showing

        def contains(self, value):
            name = getattr(value, 'value_nick', '').lower()
            if name == 'active':
                return self._active
            if name == 'focused':
                return self._focused
            if name == 'showing':
                return self._showing
            return False

    class FakeChild:
        def __init__(self, name, active=False, focused=False):
            self._name = name
            self._states = FakeStates(active=active, focused=focused)

        def get_name(self):
            return self._name

        def get_state_set(self):
            return self._states

    class FakeApp:
        def __init__(self, name, children):
            self._name = name
            self._children = children

        def get_name(self):
            return self._name

        def get_child_count(self):
            return len(self._children)

        def get_child_at_index(self, idx):
            return self._children[idx]

    class FakeDesktop:
        def __init__(self, apps):
            self._apps = apps

        def get_child_count(self):
            return len(self._apps)

        def get_child_at_index(self, idx):
            return self._apps[idx]

    class FakeStateType:
        ACTIVE = type('EnumVal', (), {'value_nick': 'active'})()
        FOCUSED = type('EnumVal', (), {'value_nick': 'focused'})()
        SHOWING = type('EnumVal', (), {'value_nick': 'showing'})()

    class FakeAtspi:
        StateType = FakeStateType

        @staticmethod
        def get_desktop_count():
            return 1

        @staticmethod
        def get_desktop(index):
            return FakeDesktop([
                FakeApp('gnome-shell', [FakeChild('', focused=True)]),
                FakeApp('Google Chrome', [FakeChild('Wayland - Chrome', active=True)]),
            ])

    from auto_daily_log_collector.monitor_internals.platforms import gnome_wayland
    monkeypatch.setattr(gnome_wayland, '_import_atspi', lambda: FakeAtspi)

    api = gnome_wayland.GnomeWaylandAPI(state_file='/tmp/does-not-exist')
    assert api.get_frontmost_app() == 'Google Chrome'
    assert api.get_window_title('Google Chrome') == 'Wayland - Chrome'


def test_gnome_wayland_api_sanitizes_window_title(state_file):
    state_file.write_text('{"app_name":"Google Chrome","window_title":"\u200d\u200bMpp query time analyze - Google Chrome"}', encoding='utf-8')

    from auto_daily_log_collector.monitor_internals.platforms.gnome_wayland import GnomeWaylandAPI

    api = GnomeWaylandAPI()
    assert api.get_window_title('Google Chrome') == 'Mpp query time analyze - Google Chrome'
