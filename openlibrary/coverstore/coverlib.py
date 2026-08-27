"""Cover management."""

import datetime
import os
from io import BytesIO
from logging import getLogger
from typing import TYPE_CHECKING

import web
from PIL import Image, ImageOps

from openlibrary.coverstore import config, db
from openlibrary.coverstore.utils import random_string, rm_f

if TYPE_CHECKING:
    from openlibrary.coverstore.code import PartialCoverDetails

logger = getLogger("openlibrary.coverstore.coverlib")

__all__ = ["read_file", "read_image", "read_image_stream", "save_image"]


def save_image(data, category, olid, author=None, ip=None, source_url=None):
    """Save the provided image data, creates thumbnails and adds an entry in the database.

    ValueError is raised if the provided data is not a valid image.
    """
    prefix = make_path_prefix(olid)

    img = write_image(data, prefix)
    if img is None:
        raise ValueError("Bad Image")

    d = web.storage(
        {
            "category": category,
            "olid": olid,
            "author": author,
            "source_url": source_url,
        }
    )
    d["width"], d["height"] = img.size

    filename = prefix + ".jpg"
    d["ip"] = ip
    d["filename"] = filename
    d["filename_s"] = prefix + "-S.jpg"
    d["filename_m"] = prefix + "-M.jpg"
    d["filename_l"] = prefix + "-L.jpg"
    d.id = db.new(**d)
    return d


def make_path_prefix(olid, date=None):
    """Makes a file prefix for storing an image."""
    date = date or datetime.date.today()
    return "%04d/%02d/%02d/%s-%s" % (
        date.year,
        date.month,
        date.day,
        olid,
        random_string(5),
    )


def write_image(data: bytes, prefix: str) -> Image.Image | None:
    path_prefix = find_image_path(prefix)
    dirname = os.path.dirname(path_prefix)
    if not os.path.exists(dirname):
        os.makedirs(dirname)
    try:
        # save original image
        with open(path_prefix + ".jpg", "wb") as f:
            f.write(data)

        img = Image.open(BytesIO(data))

        try:
            # If the image EXIF contains a rotation, apply it so we get the correctly
            # oriented image
            ImageOps.exif_transpose(img, in_place=True)
        except (AttributeError, KeyError, OSError, ValueError) as e:
            logger.warning(f"Failed to apply EXIF orientation: {e}")

        if img.mode != "RGB":
            img = img.convert("RGB")  # type: ignore[assignment]

        for name, size in config.image_sizes.items():
            path = f"{path_prefix}-{name}.jpg"
            resize_image(img, size).save(path, quality=90)
        return img
    except OSError:
        logger.exception("write_image() failed")

        # cleanup
        rm_f(prefix + ".jpg")
        rm_f(prefix + "-S.jpg")
        rm_f(prefix + "-M.jpg")
        rm_f(prefix + "-L.jpg")

        return None


def resize_image(image, size):
    """Resizes image to specified size while making sure that aspect ratio is maintained."""
    # from PIL
    x, y = image.size
    if x > size[0]:
        y = max(y * size[0] // x, 1)
        x = size[0]
    if y > size[1]:
        x = max(x * size[1] // y, 1)
        y = size[1]
    size = x, y

    return image.resize(size, Image.LANCZOS)


def find_image_path(filename):
    if ":" in filename:
        return os.path.join(config.data_root, "items", filename.rsplit("_", 1)[0], filename)
    else:
        return os.path.join(config.data_root, "localdisk", filename)


def read_file(path):
    if ":" in path:
        path, offset, size = path.rsplit(":", 2)
        with open(path, "rb") as f:
            f.seek(int(offset))
            return f.read(int(size))
    with open(path, "rb") as f:
        return f.read()


def get_image_path(d: PartialCoverDetails | db.CoverDbDetails, size):
    if size:
        filename = d["filename_" + size.lower()] or d.filename + f"-{size.upper()}.jpg"
    else:
        filename = d.filename
    return find_image_path(filename)


def read_image(d: PartialCoverDetails | db.CoverDbDetails, size):
    return read_file(get_image_path(d, size))


def read_file_chunks(path, chunk_size=64 * 1024):
    """Like read_file, but yields the data in chunks instead of loading it all into memory at once."""
    if ":" in path:
        path, offset, size = path.rsplit(":", 2)
        offset, size = int(offset), int(size)
        with open(path, "rb") as f:
            f.seek(offset)
            remaining = size
            while remaining:
                data = f.read(min(chunk_size, remaining))
                if not data:
                    raise OSError("file truncated")
                remaining -= len(data)
                yield data
    else:
        with open(path, "rb") as f:
            while data := f.read(chunk_size):
                yield data


def read_image_stream(d: PartialCoverDetails | db.CoverDbDetails, size):
    """Like read_image, but returns an iterator of chunks for streaming the response."""
    return read_file_chunks(get_image_path(d, size))
