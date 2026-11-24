import base64
import gzip
from pathlib import Path
import tempfile
import unittest
from urllib.parse import unquote
import xml.etree.ElementTree as ET

from xmcd2md.assets import AssetExtractor


PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aF9sAAAAASUVORK5CYII=")


class AssetsTests(unittest.TestCase):
    def make_root(self, data=PNG, encoding=""):
        root = ET.Element("worksheet")
        container = ET.SubElement(root, "binaryContent")
        item = ET.SubElement(container, "item", {"item-id": "1", "content-encoding": encoding})
        item.text = base64.b64encode(data).decode("ascii")
        region = ET.SubElement(root, "region", {"region-id": "../../escape"})
        ET.SubElement(region, "rendering", {"item-idref": "1"})
        return root, region

    def test_png_path_is_portable_and_safe(self):
        root, region = self.make_root()
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "Расчёт (1).md"
            assets = AssetExtractor(root, output)
            result = assets.export_region(region)
            relative = unquote(result.split("](", 1)[1][:-1])
            path = output.parent / relative
            self.assertEqual(path.read_bytes(), PNG)
            self.assertEqual(path.parent, output.with_name("Расчёт (1)_assets"))
            self.assertNotIn(" ", result.split("](", 1)[1])
            self.assertEqual(assets.export_region(region), result)
            self.assertEqual(len(list(path.parent.iterdir())), 1)

    def test_gzip_png(self):
        root, region = self.make_root(gzip.compress(PNG), "gzip")
        with tempfile.TemporaryDirectory() as folder:
            assets = AssetExtractor(root, Path(folder) / "test.md")
            self.assertIsNotNone(assets.export_region(region))
            self.assertFalse(assets.warnings)

    def test_corrupt_payload_warns(self):
        root, region = self.make_root()
        root.find("binaryContent/item").text = "!not-base64!"
        with tempfile.TemporaryDirectory() as folder:
            assets = AssetExtractor(root, Path(folder) / "test.md")
            self.assertIsNone(assets.export_region(region))
            self.assertTrue(assets.warnings)
            self.assertFalse(assets.asset_dir.exists())

    def test_missing_and_non_raster_preview_warn(self):
        for payload in (b"COleDocItem", b""):
            with self.subTest(payload=payload):
                root, region = self.make_root(payload)
                if not payload:
                    root.find("binaryContent").clear()
                with tempfile.TemporaryDirectory() as folder:
                    assets = AssetExtractor(root, Path(folder) / "test.md")
                    self.assertIsNone(assets.export_region(region))
                    self.assertTrue(assets.warnings)


if __name__ == "__main__":
    unittest.main()
