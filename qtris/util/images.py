"""Image files: finding Qtris's own images, and processing images with Pillow

Nothing here draws anything; frontends load the files these return, or that
these write, with their own toolkit.
"""

from PIL import Image


def thumbnail_image(base_image: Image.Image, target_size: tuple[int, int]) -> Image.Image:
    """Scales an image to cover the target size, and crops what overflows it equally on both sides."""
    base_width, base_height = base_image.size
    base_ratio = base_width / base_height
    target_width, target_height = target_size
    target_ratio = target_width / target_height

    # Resize and crop coverart
    if base_ratio >= target_ratio:
        width = int(base_width * (target_height / base_height))
        height = target_height
    else:
        width = target_width
        height = int(base_height * (target_width / base_width))
    x_offset = int((width - target_width) / 2)
    y_offset = int((height - target_height) / 2)
    base_image = base_image.resize((width, height), resample=Image.Resampling.BICUBIC)
    base_image = base_image.crop((x_offset, y_offset, width - x_offset, height - y_offset))
    return base_image


def paste_overlay(base_image: Image.Image, overlay_image: Image.Image) -> Image.Image:
    """Pastes an image, through its own transparency, in the middle of another."""
    base_width, base_height = base_image.size
    overlay_width, overlay_height = overlay_image.size
    offset_x = int((base_width - overlay_width) / 2)
    offset_y = int((base_height - overlay_height) / 2)
    base_image.paste(
        overlay_image, (offset_x, offset_y, overlay_width + offset_x, overlay_height + offset_y), mask=overlay_image
    )
    return base_image
