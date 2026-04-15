import subprocess
from pathlib import Path
from typing import Optional
from .platforms.detect import get_current_platform

def get_ocr_engine(configured: str) -> str:
    if configured != "auto":
        return configured
    platform = get_current_platform()
    return {"macos": "vision", "windows": "winocr", "linux": "tesseract"}[platform]

def ocr_image(image_path: Path, engine: str = "auto") -> Optional[str]:
    resolved_engine = get_ocr_engine(engine)
    if resolved_engine == "vision": return _ocr_vision(image_path)
    elif resolved_engine == "winocr": return _ocr_winocr(image_path)
    else: return _ocr_tesseract(image_path)

def _ocr_vision(image_path: Path) -> Optional[str]:
    try:
        import objc
        from Quartz import CIImage
        from Foundation import NSURL
        import Vision
        url = NSURL.fileURLWithPath_(str(image_path))
        request = Vision.VNRecognizeTextRequest.alloc().init()
        request.setRecognitionLanguages_(["zh-Hans", "en"])
        request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
        handler = Vision.VNImageRequestHandler.alloc().initWithURL_options_(url, {})
        handler.performRequests_error_([request], None)
        results = request.results()
        if not results: return None
        texts = []
        for obs in results:
            candidate = obs.topCandidates_(1)
            if candidate: texts.append(candidate[0].string())
        return "\n".join(texts) if texts else None
    except Exception:
        return _ocr_tesseract(image_path)

def _ocr_winocr(image_path: Path) -> Optional[str]:
    try:
        import winocr
        import asyncio
        result = asyncio.run(winocr.recognize_pil(image_path, lang="zh-Hans-CN"))
        return result.text if result and result.text else None
    except Exception:
        return _ocr_tesseract(image_path)

def _ocr_tesseract(image_path: Path) -> Optional[str]:
    try:
        result = subprocess.run(
            ["tesseract", str(image_path), "stdout", "-l", "chi_sim+eng"],
            capture_output=True, text=True, timeout=30,
        )
        text = result.stdout.strip()
        return text if text else None
    except (subprocess.TimeoutExpired, FileNotFoundError, Exception):
        return None
