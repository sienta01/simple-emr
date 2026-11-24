"""Normalises uploads: result photos and patient photos become compact JPEGs, PDFs get a cover thumbnail."""
import logging
import os
from dataclasses import dataclass
from io import BytesIO

from PIL import Image, ImageOps, UnidentifiedImageError

logger = logging.getLogger(__name__)

MAX_SIDE = 2000        # long side of the stored page; enough to read a lab printout
THUMB_SIDE = 480
PHOTO_SIDE = 800       # patient photo: sharp in the enlarged view, small on disk
JPEG_QUALITY = 82
IMAGE_FORMATS = {'JPEG', 'MPO', 'PNG', 'WEBP', 'HEIF'}
PHOTO_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp', '.heic', '.heif', ''}
ALLOWED_EXTENSIONS = PHOTO_EXTENSIONS | {'.pdf'}

ROTATIONS = {
    90: Image.Transpose.ROTATE_270,   # PIL rotates counter-clockwise; 90 here means clockwise
    -90: Image.Transpose.ROTATE_90,
    180: Image.Transpose.ROTATE_180,
}


class UploadError(Exception):
    pass


@dataclass
class Processed:
    data: bytes
    ext: str
    content_type: str
    thumb: bytes | None


def process_upload(uploaded):
    ext = os.path.splitext(uploaded.name or '')[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise UploadError('Format file tidak didukung. Gunakan JPEG, PNG, WebP, HEIC, atau PDF.')
    head = uploaded.read(5)
    uploaded.seek(0)
    if head.startswith(b'%PDF'):
        return _process_pdf(uploaded.read())
    img = _open_image(uploaded, MAX_SIDE, 'File tidak dapat dibaca sebagai gambar atau PDF.')
    return Processed(_jpeg(img, JPEG_QUALITY), '.jpg', 'image/jpeg', _thumb(img))


def process_photo(uploaded):
    """A patient's face photo (webcam snapshot or picked file) as JPEG bytes."""
    ext = os.path.splitext(uploaded.name or '')[1].lower()
    if ext not in PHOTO_EXTENSIONS:
        raise UploadError('Format foto tidak didukung. Gunakan JPEG, PNG, WebP, atau HEIC.')
    img = _open_image(uploaded, PHOTO_SIDE, 'File tidak dapat dibaca sebagai foto.')
    return _jpeg(img, JPEG_QUALITY)


def _open_image(uploaded, max_side, unreadable):
    try:
        img = Image.open(uploaded)
        if img.format in ('JPEG', 'MPO'):
            img.draft('RGB', (max_side, max_side))  # fast DCT downscale while decoding
        img.load()
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        raise UploadError(unreadable)
    if img.format not in IMAGE_FORMATS:
        raise UploadError('Format gambar tidak didukung. Gunakan JPEG, PNG, WebP, atau HEIC.')
    img = _to_rgb(ImageOps.exif_transpose(img))
    img.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    return img


def rotate_jpeg(fileobj, degrees):
    img = Image.open(fileobj)
    img.load()
    img = _to_rgb(img.transpose(ROTATIONS[degrees]))
    return _jpeg(img, JPEG_QUALITY), _thumb(img)


def _process_pdf(data):
    thumb = None
    try:
        import pypdfium2 as pdfium

        pdf = pdfium.PdfDocument(data)
        try:
            page = pdf[0]
            width, height = page.get_size()
            scale = (THUMB_SIDE * 1.5) / max(width, height, 1)
            thumb = _thumb(_to_rgb(page.render(scale=scale).to_pil()))
            page.close()
        finally:
            pdf.close()
    except ImportError:
        pass
    except Exception:  # an unreadable PDF is still stored; it just has no preview
        logger.warning('Could not render PDF thumbnail', exc_info=True)
    return Processed(data, '.pdf', 'application/pdf', thumb)


def _to_rgb(img):
    if img.mode in ('RGB', 'L'):
        return img
    if img.mode in ('RGBA', 'LA', 'P', 'PA'):
        img = img.convert('RGBA')
        background = Image.new('RGB', img.size, 'white')
        background.paste(img, mask=img.getchannel('A'))
        return background
    return img.convert('RGB')


def _thumb(img):
    small = img.copy()
    small.thumbnail((THUMB_SIDE, THUMB_SIDE), Image.Resampling.LANCZOS)
    return _jpeg(small, 75)


def _jpeg(img, quality):
    buf = BytesIO()
    img.save(buf, 'JPEG', quality=quality, optimize=True, progressive=True)
    return buf.getvalue()
