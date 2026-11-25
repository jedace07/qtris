"""Tests for qtris.util.images: image processing that needs no UI toolkit."""

from PIL import Image

from qtris.util.images import paste_overlay, thumbnail_image


def test_thumbnail_covers_the_target_and_crops_the_overflow():
    wide = Image.new("RGB", (400, 100), "red")
    thumbnail = thumbnail_image(wide, (100, 100))
    assert thumbnail.size == (100, 100)

    tall = Image.new("RGB", (100, 400), "red")
    assert thumbnail_image(tall, (200, 100)).size == (200, 100)


def test_overlay_is_pasted_in_the_middle_through_its_transparency():
    base = Image.new("RGBA", (30, 30), (0, 0, 0, 255))
    overlay = Image.new("RGBA", (10, 10), (255, 255, 255, 255))
    overlay.putpixel((0, 0), (255, 255, 255, 0))

    result = paste_overlay(base, overlay)

    assert result.getpixel((15, 15)) == (255, 255, 255, 255)
    assert result.getpixel((10, 10)) == (0, 0, 0, 255)  # The overlay's transparent corner
    assert result.getpixel((5, 5)) == (0, 0, 0, 255)
