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
