import pytest
from unittest.mock import patch
from auto_daily_log_collector.monitor_internals.ocr import get_ocr_engine

@patch("auto_daily_log_collector.monitor_internals.ocr.get_current_platform", return_value="macos")
def test_auto_selects_vision_on_macos(mock_platform):
    engine = get_ocr_engine("auto")
    assert engine == "vision"

@patch("auto_daily_log_collector.monitor_internals.ocr.get_current_platform", return_value="windows")
def test_auto_selects_winocr_on_windows(mock_platform):
    engine = get_ocr_engine("auto")
    assert engine == "winocr"

@patch("auto_daily_log_collector.monitor_internals.ocr.get_current_platform", return_value="linux")
def test_auto_selects_tesseract_on_linux(mock_platform):
    engine = get_ocr_engine("auto")
    assert engine == "tesseract"

def test_explicit_engine_override():
    engine = get_ocr_engine("tesseract")
    assert engine == "tesseract"
