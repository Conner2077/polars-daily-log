from pathlib import Path
from typing import Any, Optional

try:
    import imagehash
    from PIL import Image
except Exception:  # Optional dependency path for non-screenshot tests/runtime
    imagehash = None
    Image = None


def compute_phash(image_path: Path) -> Optional[Any]:
    if imagehash is None or Image is None:
        return None
    try:
        img = Image.open(image_path)
        return imagehash.phash(img)
    except Exception:
        return None


def is_similar(
    hash_a: Optional[Any],
    hash_b: Optional[Any],
    threshold: int = 10,
) -> bool:
    if hash_a is None or hash_b is None:
        return False
    return (hash_a - hash_b) <= threshold
