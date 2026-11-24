"""End-to-end checks of user examples, when present in a source checkout."""
from collections import Counter
import hashlib
from pathlib import Path
import re
import tempfile
import unittest
from urllib.parse import unquote
import xml.etree.ElementTree as ET

from xmcd2md import convert_file


ROOT = Path(__file__).resolve().parents[1]
SAMPLES = list(ROOT.glob("*.xmcd"))


@unittest.skipUnless(SAMPLES, "User XMCD examples are not included in this checkout")
class SampleTests(unittest.TestCase):
    def test_every_region_is_preserved_once_and_assets_resolve(self):
        for source in SAMPLES:
            with self.subTest(source=source.name), tempfile.TemporaryDirectory() as folder:
                before = hashlib.sha256(source.read_bytes()).digest()
                root = ET.parse(source).getroot()
                namespace = root.tag.split("}")[0] + "}"
                regions = list(root.iter(namespace + "region"))
                expected = Counter(region.get("region-id") for region in regions)
                target = Path(folder) / (source.stem + ".md")
                result = convert_file(source, target)
                text = target.read_text(encoding="utf-8")
                self.assertEqual(result.warnings, [])
                self.assertEqual(Counter(re.findall(r"<!-- region id=(.*?) -->", text)), expected)
                self.assertEqual(result.region_count, len(regions))
                self.assertEqual(result.math_count, len(list(root.iter(namespace + "math"))))
                self.assertEqual(result.text_count, len(list(root.iter(namespace + "text"))))
                images = re.findall(r"!\[[^\]]*\]\(([^)]+)\)", text)
                self.assertEqual(len(images), result.image_count)
                for link in images:
                    path = target.parent / unquote(link)
                    self.assertTrue(path.is_file(), str(path))
                    self.assertTrue(path.read_bytes().startswith(b"\x89PNG"))
                self.assertEqual(hashlib.sha256(source.read_bytes()).digest(), before)


if __name__ == "__main__":
    unittest.main()
