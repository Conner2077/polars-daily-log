import pytest
from pathlib import Path
from PIL import Image, ImageDraw
from auto_daily_log_collector.monitor_internals.phash import compute_phash, is_similar

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
