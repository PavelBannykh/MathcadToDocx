"""Extract saved region previews without running Mathcad or embedded OLE objects."""
from __future__ import annotations

import base64
import binascii
import gzip
import hashlib
import io
from pathlib import Path
from urllib.parse import quote
import xml.etree.ElementTree as ET


MAX_IMAGE_BYTES = 64 * 1024 * 1024


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _extension(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return ".gif"
    if data.startswith(b"BM"):
        return ".bmp"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return ".webp"
    return None


class AssetExtractor:
    """Write only referenced raster previews into a sibling assets directory."""

    def __init__(self, root: ET.Element, output_path: Path):
        self.output_path = Path(output_path)
        self.asset_dir = self.output_path.with_name(self.output_path.stem + "_assets")
        self.warnings: list[str] = []
        self.items = {
            item.get("item-id"): item
            for container in root if _local(container.tag) == "binaryContent"
            for item in container if _local(item.tag) == "item"
        }

    def _decode(self, item: ET.Element) -> bytes:
        encoded = "".join((item.text or "").split())
        if len(encoded) > MAX_IMAGE_BYTES * 2:
            raise ValueError("изображение превышает допустимый размер")
        data = base64.b64decode(encoded, validate=True)
        encoding = item.get("content-encoding", "").lower()
        if encoding == "gzip":
            with gzip.GzipFile(fileobj=io.BytesIO(data)) as stream:
                data = stream.read(MAX_IMAGE_BYTES + 1)
        elif encoding not in ("", "base64"):
            raise ValueError(f"неподдерживаемая кодировка {encoding!r}")
        if len(data) > MAX_IMAGE_BYTES:
            raise ValueError("изображение превышает допустимый размер")
        return data

    def export_region(self, region: ET.Element) -> str | None:
        # A rendering is a portable picture of an OLE/plot region. The OLE
        # payload itself is never executed, and usually is not a picture.
        references: list[str] = []
        children = list(region)
        for child in sorted(children, key=lambda el: _local(el.tag) != "rendering"):
            if _local(child.tag) in ("rendering", "picture", "image", "plot", "plot2D", "plot3D"):
                for el in child.iter():
                    ref = el.get("item-idref")
                    if ref and ref not in references:
                        references.append(ref)
        failures: list[str] = []
        region_id = region.get("region-id", "?")
        for ref in references:
            item = self.items.get(ref)
            if item is None:
                failures.append(f"нет binaryContent item {ref}")
                continue
            try:
                data = self._decode(item)
            except (ValueError, OSError, EOFError, binascii.Error) as exc:
                failures.append(f"item {ref}: {exc}")
                continue
            extension = _extension(data)
            if extension is None:
                failures.append(f"item {ref}: нет растрового предпросмотра")
                continue
            name = "image-" + hashlib.sha256(data).hexdigest()[:24] + extension
            self.asset_dir.mkdir(parents=True, exist_ok=True)
            path = self.asset_dir / name
            if not path.exists():
                with path.open("xb") as stream:
                    stream.write(data)
            elif path.read_bytes() != data:
                raise OSError(f"Файл изображения уже существует с другим содержимым: {path}")
            relative_url = quote(path.relative_to(self.output_path.parent).as_posix(), safe="/")
            return f"![Изображение Mathcad]({relative_url})"
        if failures:
            self.warnings.append(f"Регион {region_id}: " + "; ".join(failures))
        return None
