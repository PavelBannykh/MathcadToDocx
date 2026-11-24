"""Worksheet traversal, safe I/O, and text preservation checks."""

from pathlib import Path
import os
import re
import tempfile
import unittest
from unittest.mock import patch

from xmcd2md.converter import ConversionError, convert_file


WS = "http://schemas.mathsoft.com/worksheet30"
ML = "http://schemas.mathsoft.com/math30"


class _Math:
    def __init__(self):
        self.warnings = []
        self.imaginary_symbol = "i"

    def render(self, element):
        if element.find(f"{{{ML}}}unsupported") is not None:
            self.warnings.append("Unsupported expression")
            return r"\text{unsupported}"
        return "".join(element.itertext()).strip() or self.imaginary_symbol


class _Assets:
    def __init__(self, root, path):
        self.warnings = []

    def export_region(self, region):
        if region.get("preview") == "yes":
            return "![saved preview](sheet_assets/preview.png)"
        return None


class ConverterTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.source = self.directory / "расчёт.xmcd"
        self.addCleanup(patch.stopall)
        patch("xmcd2md.converter.MathRenderer", _Math).start()
        patch("xmcd2md.converter.AssetExtractor", _Assets).start()

    def worksheet(self, regions, settings=""):
        self.source.write_text(
            f'<worksheet xmlns="{WS}" xmlns:ml="{ML}">{settings}<regions>'
            + regions + "</regions></worksheet>", encoding="utf-8"
        )

    def region(self, id, content, **attrs):
        attributes = " ".join(f'{key.replace("_", "-")}="{value}"' for key, value in attrs.items())
        return f'<region region-id="{id}" {attributes}>{content}</region>'

    def test_russian_text_formatting_headings_and_blank_paragraphs(self):
        self.worksheet(self.region("t", '<text><p style="Heading 2"><f>Расчёт</f></p>'
                                   '<p/><p><f>Ток <b>жирный</b> <i>курсив</i> '
                                   '<u>подчёркнутый</u><sp count="2"/>А_1</f></p></text>'))
        result = convert_file(self.source)
        text = result.output_path.read_text(encoding="utf-8")
        self.assertIn("## Расчёт\n\n\n\nТок", text)
        self.assertIn("<strong>жирный</strong>", text)
        self.assertIn("<em>курсив</em>", text)
        self.assertIn("<u>подчёркнутый</u>  А\\_1", text)
        self.assertEqual((result.region_count, result.text_count, result.math_count), (1, 1, 0))
        self.assertFalse(result.warnings)

    def test_formula_order_uses_baseline_then_left_despite_different_heights(self):
        self.worksheet(
            self.region("right", "<math><ml:id>right</ml:id></math>", left=200, top=10, align_y=50)
            + self.region("later", "<math><ml:id>later</ml:id></math>", left=0, top=52, align_y=60)
            + self.region("left", "<math><ml:id>left</ml:id></math>", left=0, top=40, align_y=50)
            + self.region("earlier", "<text><p><f>earlier</f></p></text>", left=0, top=20, align_y=30)
        )
        result = convert_file(self.source)
        text = result.output_path.read_text(encoding="utf-8")
        self.assertEqual(re.findall(r"<!-- region id=(.*?) -->", text),
                         ["earlier", "left", "right", "later"])
        self.assertIn("$$\nleft\n\\qquad right\n$$", text)
        self.assertIn("$$\nlater\n$$", text)

    def test_inline_math_counted_once_and_remains_in_paragraph(self):
        inline = self.region("inline", "<math><ml:id>I</ml:id></math>", left=4, top=1)
        self.worksheet(self.region("text", f"<text><p><f>Ток </f>{inline}<f> ампер</f></p></text>"))
        result = convert_file(self.source)
        text = result.output_path.read_text(encoding="utf-8")
        self.assertIn("Ток <!-- region id=inline -->$I$ ампер", text)
        self.assertEqual(text.count("<!-- region id=inline -->"), 1)
        self.assertEqual((result.region_count, result.math_count, result.text_count), (2, 1, 1))

    def test_nested_area_preserves_every_region(self):
        nested = self.region("nested", "<text><p><f>Внутри</f></p></text>")
        self.worksheet(self.region("area", f"<area><regions>{nested}</regions></area>"))
        result = convert_file(self.source)
        self.assertEqual(result.region_count, 2)
        self.assertIn("Внутри", result.output_path.read_text(encoding="utf-8"))
        self.assertTrue(result.warnings)

    def test_unknown_region_and_unknown_text_preserve_xml(self):
        self.worksheet(self.region("unknown", '<future secret="kept">saved</future>')
                       + self.region("text", '<text><p><future>Текст</future></p></text>'))
        result = convert_file(self.source)
        text = result.output_path.read_text(encoding="utf-8")
        self.assertIn("Unsupported Mathcad region", text)
        self.assertIn('secret="kept"', text)
        self.assertIn("Текст", text)
        self.assertIn("Original XMCD XML", text)
        self.assertGreaterEqual(len(result.warnings), 2)

    def test_unknown_container_content_is_preserved(self):
        self.worksheet('<future secret="outside">Сохранить XML</future>'
                       + self.region("known", "<text><p><f>Текст</f></p></text>"))
        result = convert_file(self.source)
        text = result.output_path.read_text(encoding="utf-8")
        self.assertIn('secret="outside"', text)
        self.assertIn("Сохранить XML", text)
        self.assertEqual(result.region_count, 1)
        self.assertTrue(result.warnings)

    def test_plain_text_markdown_markers_remain_literal(self):
        self.worksheet(self.region("literal", '<text><p><f># Не заголовок</f></p>'
                                   '<p><f>- Не список</f></p><p><f>1. Текст</f></p></text>'))
        result = convert_file(self.source)
        text = result.output_path.read_text(encoding="utf-8")
        self.assertIn(r"\# Не заголовок", text)
        self.assertIn(r"\- Не список", text)
        self.assertIn(r"1\. Текст", text)

    def test_image_preview_and_missing_preview(self):
        self.worksheet(self.region("image", "<picture/>", preview="yes")
                       + self.region("missing", "<ole/>"))
        result = convert_file(self.source)
        text = result.output_path.read_text(encoding="utf-8")
        self.assertEqual(result.image_count, 1)
        self.assertIn("![saved preview]", text)
        self.assertIn("Unsupported Mathcad ole region", text)
        self.assertTrue(result.warnings)

    def test_unsupported_math_includes_preview_and_source(self):
        self.worksheet(self.region("math", "<math><ml:unsupported/></math>", preview="yes"))
        result = convert_file(self.source)
        text = result.output_path.read_text(encoding="utf-8")
        self.assertIn("$$", text)
        self.assertIn("![saved preview]", text)
        self.assertIn("unsupported", text)
        self.assertIn("Original XMCD XML", text)
        self.assertTrue(result.warnings)

    def test_worksheet_and_region_imaginary_symbol(self):
        settings = '<settings><presentation><math><results><general imaginary-value="j"/></results></math></presentation></settings>'
        self.worksheet(self.region("one", "<math/>", top=0)
                       + self.region("two", '<math><resultFormat><general imaginary-value="i"/></resultFormat></math>', top=30)
                       + self.region("three", "<math/>", top=60), settings)
        result = convert_file(self.source)
        text = result.output_path.read_text(encoding="utf-8")
        self.assertEqual(re.findall(r"\$\$\n(.*?)\n\$\$", text), ["j", "i", "j"])

    def test_existing_output_requires_force_and_input_never_overwritten(self):
        self.worksheet(self.region("t", "<text><p><f>Сохранить</f></p></text>"))
        before = self.source.read_bytes()
        output = self.source.with_suffix(".md")
        output.write_text("old", encoding="utf-8")
        with self.assertRaises(ConversionError):
            convert_file(self.source)
        self.assertEqual(output.read_text(encoding="utf-8"), "old")
        convert_file(self.source, force=True)
        self.assertIn("Сохранить", output.read_text(encoding="utf-8"))
        with self.assertRaises(ConversionError):
            convert_file(self.source, self.source, force=True)
        self.assertEqual(self.source.read_bytes(), before)

    def test_output_hardlink_to_source_is_rejected_even_with_force(self):
        self.worksheet(self.region("t", "<text><p><f>Original</f></p></text>"))
        output = self.directory / "hardlink.md"
        try:
            os.link(self.source, output)
        except OSError as exc:
            self.skipTest(f"Hard links unavailable: {exc}")
        before = self.source.read_bytes()
        with self.assertRaises(ConversionError):
            convert_file(self.source, output, force=True)
        self.assertEqual(self.source.read_bytes(), before)

    def test_invalid_xml_root_and_dtd_are_rejected_before_output(self):
        cases = [b"not xml", b"<worksheet/>", b'<worksheet xmlns="https://example.com"/>',
                 b'<?xml version="1.0" encoding="made-up"?><worksheet/>',
                 f'<!DOCTYPE worksheet [<!ENTITY x "expanded">]><worksheet xmlns="{WS}">&x;</worksheet>'.encode(),
                 f'<!DOCTYPE worksheet SYSTEM "file:///secret"><worksheet xmlns="{WS}"/>'.encode("utf-16")]
        for data in cases:
            with self.subTest(data=data[:50]):
                self.source.write_bytes(data)
                with self.assertRaises(ConversionError):
                    convert_file(self.source)
                self.assertFalse(self.source.with_suffix(".md").exists())

    def test_missing_source_reports_conversion_error(self):
        with self.assertRaises(ConversionError):
            convert_file(self.source)

    def test_explicit_destination_creates_parents_and_is_utf8(self):
        self.worksheet(self.region("t", "<text><p><f>Русский Ω</f></p></text>"))
        target = self.directory / "nested" / "result.md"
        result = convert_file(self.source, target)
        self.assertEqual(result.output_path, target.resolve())
        self.assertIn("Русский Ω", target.read_bytes().decode("utf-8"))


if __name__ == "__main__":
    unittest.main()
