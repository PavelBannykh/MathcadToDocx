"""Pandoc export, destination safety, and optional real DOCX integration."""

import base64
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET
import zipfile

from xmcd2md.converter import ConversionError, convert_file
from xmcd2md.pandoc import PandocNotFoundError, ensure_pandoc


WS = "http://schemas.mathsoft.com/worksheet30"
ML = "http://schemas.mathsoft.com/math30"
PNG = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aF9sAAAAASUVORK5CYII="


def _write_docx(path):
    with zipfile.ZipFile(path, "w") as document:
        document.writestr("[Content_Types].xml", "<Types/>")
        document.writestr("word/document.xml", "<document/>")


class DocxTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="Расчёт с пробелами ")
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.source = self.directory / "Исходный расчёт.xmcd"
        self.source.write_text(
            f'<worksheet xmlns="{WS}" xmlns:ml="{ML}"><regions>'
            '<region region-id="text" top="0"><text><p><f>Русский текст</f></p></text></region>'
            '<region region-id="math" top="20"><math><ml:apply><ml:plus/>'
            '<ml:id>x</ml:id><ml:real>1</ml:real></ml:apply></math></region>'
            '<region region-id="picture" top="40"><picture item-idref="1"/></region>'
            f'</regions><binaryContent><item item-id="1">{PNG}</item></binaryContent></worksheet>',
            encoding="utf-8",
        )
        self.original = self.source.read_bytes()
        self.target = self.directory / "Результат с пробелами.docx"

    def successful_pandoc(self, command, **kwargs):
        output = Path(command[command.index("--output") + 1])
        markdown = Path(command[-1])
        self.assertEqual(kwargs["cwd"], markdown.parent)
        self.assertFalse(kwargs["shell"])
        self.assertEqual(kwargs["creationflags"],
                         getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.assertTrue(Path(command[command.index("--lua-filter") + 1]).is_file())
        self.assertEqual(command[command.index("--resource-path") + 1], str(markdown.parent))
        self.assertIn("Русский текст", markdown.read_text(encoding="utf-8"))
        self.assertTrue(list(markdown.parent.glob("worksheet_assets/*.png")))
        _write_docx(output)
        return subprocess.CompletedProcess(command, 0, "", "")

    def assert_clean(self, target=None):
        self.assertEqual(self.source.read_bytes(), self.original)
        self.assertEqual(set(self.directory.iterdir()),
                         {self.source} | ({target} if target is not None else set()))

    def test_missing_pandoc_reports_installation_without_creating_files(self):
        with patch("xmcd2md.pandoc.shutil.which", return_value=None):
            with self.assertRaises(PandocNotFoundError) as error:
                convert_file(self.source, self.directory / "не создано" / "результат.docx")
        message = str(error.exception)
        for text in ("установить Pandoc", "https://pandoc.org/installing.html", "PATH", "перезапустите"):
            self.assertIn(text, message)
        self.assert_clean()

    def test_ensure_pandoc_returns_executable_path(self):
        with patch("xmcd2md.pandoc.shutil.which", return_value="C:/Program Files/Pandoc/pandoc.exe"):
            self.assertEqual(ensure_pandoc(), "C:/Program Files/Pandoc/pandoc.exe")

    def test_docx_with_cyrillic_paths_has_private_markdown_and_assets(self):
        with patch("xmcd2md.pandoc.ensure_pandoc", return_value="pandoc"), \
                patch("xmcd2md.pandoc.subprocess.run", side_effect=self.successful_pandoc):
            result = convert_file(self.source, self.target)
        self.assertEqual(result.output_path, self.target.resolve())
        self.assertEqual((result.region_count, result.text_count, result.math_count, result.image_count),
                         (3, 1, 1, 1))
        self.assertTrue(zipfile.is_zipfile(self.target))
        self.assert_clean(self.target)

    def test_explicit_format_chooses_default_suffix(self):
        with patch("xmcd2md.pandoc.ensure_pandoc", return_value="pandoc"), \
                patch("xmcd2md.pandoc.subprocess.run", side_effect=self.successful_pandoc):
            result = convert_file(self.source, output_format="DOCX")
        self.assertEqual(result.output_path, self.source.with_suffix(".docx"))
        self.assert_clean(result.output_path)

    def test_invalid_format_or_mismatched_extension_does_not_write(self):
        for target, selected in ((self.target, "md"), (self.directory / "bad.md", "docx"),
                                 (self.directory / "bad.pdf", None), (None, "pdf")):
            with self.subTest(target=target, selected=selected), self.assertRaises(ConversionError):
                convert_file(self.source, target, output_format=selected)
        self.assert_clean()

    def test_markdown_never_requires_pandoc(self):
        with patch("xmcd2md.pandoc.ensure_pandoc", side_effect=AssertionError("Pandoc checked for MD")):
            result = convert_file(self.source, output_format="md")
        self.assertIn("Русский текст", result.output_path.read_text(encoding="utf-8"))

    def test_existing_output_requires_force_before_pandoc(self):
        self.target.write_bytes(b"keep original")
        with patch("xmcd2md.pandoc.ensure_pandoc") as ensure, self.assertRaises(ConversionError):
            convert_file(self.source, self.target)
        ensure.assert_not_called()
        self.assertEqual(self.target.read_bytes(), b"keep original")
        self.assert_clean(self.target)

    def test_subprocess_failures_preserve_existing_file_even_with_force(self):
        failures = [
            FileNotFoundError("gone"),
            OSError("cannot execute"),
            subprocess.TimeoutExpired("pandoc", 120),
            subprocess.SubprocessError("failure"),
            subprocess.CompletedProcess(["pandoc"], 2, "", "ошибка Pandoc"),
        ]
        for failure in failures:
            with self.subTest(failure=failure):
                self.target.write_bytes(b"keep original")
                options = ({"side_effect": failure} if isinstance(failure, Exception)
                           else {"return_value": failure})
                with patch("xmcd2md.pandoc.ensure_pandoc", return_value="pandoc"), \
                        patch("xmcd2md.pandoc.subprocess.run", **options), \
                        self.assertRaises(ConversionError):
                    convert_file(self.source, self.target, force=True)
                self.assertEqual(self.target.read_bytes(), b"keep original")
                self.assert_clean(self.target)

    def test_stderr_warning_is_returned_and_force_replaces_complete_file(self):
        self.target.write_bytes(b"old")

        def warned(command, **kwargs):
            self.successful_pandoc(command, **kwargs)
            return subprocess.CompletedProcess(command, 0, "", "[WARNING] формула не поддерживается\n")

        with patch("xmcd2md.pandoc.ensure_pandoc", return_value="pandoc"), \
                patch("xmcd2md.pandoc.subprocess.run", side_effect=warned):
            result = convert_file(self.source, self.target, force=True)
        self.assertIn("Pandoc: [WARNING] формула не поддерживается", result.warnings)
        self.assertTrue(zipfile.is_zipfile(self.target))
        self.assert_clean(self.target)

    def test_missing_or_corrupt_docx_cannot_replace_existing_output(self):
        for invalid in (None, b"not a DOCX"):
            def corrupt(command, **_kwargs):
                if invalid is not None:
                    Path(command[command.index("--output") + 1]).write_bytes(invalid)
                return subprocess.CompletedProcess(command, 0, "", "")

            with self.subTest(invalid=invalid):
                self.target.write_bytes(b"old")
                with patch("xmcd2md.pandoc.ensure_pandoc", return_value="pandoc"), \
                        patch("xmcd2md.pandoc.subprocess.run", side_effect=corrupt), \
                        self.assertRaises(ConversionError):
                    convert_file(self.source, self.target, force=True)
                self.assertEqual(self.target.read_bytes(), b"old")
                self.assert_clean(self.target)

    def test_file_created_during_conversion_is_never_overwritten(self):
        def raced(command, **kwargs):
            result = self.successful_pandoc(command, **kwargs)
            self.target.write_bytes(b"another process")
            return result

        with patch("xmcd2md.pandoc.ensure_pandoc", return_value="pandoc"), \
                patch("xmcd2md.pandoc.subprocess.run", side_effect=raced), \
                self.assertRaises(ConversionError):
            convert_file(self.source, self.target)
        self.assertEqual(self.target.read_bytes(), b"another process")
        self.assert_clean(self.target)

    def test_filesystem_without_hard_links_uses_exclusive_copy(self):
        with patch("xmcd2md.pandoc.ensure_pandoc", return_value="pandoc"), \
                patch("xmcd2md.pandoc.subprocess.run", side_effect=self.successful_pandoc), \
                patch("xmcd2md.pandoc.os.link", side_effect=OSError("not supported")):
            convert_file(self.source, self.target)
        self.assertTrue(zipfile.is_zipfile(self.target))
        self.assert_clean(self.target)

    def test_failed_exclusive_copy_cleans_partial_file(self):
        def failed_copy(source, target):
            target.write(b"partial")
            raise OSError("disk full")

        with patch("xmcd2md.pandoc.ensure_pandoc", return_value="pandoc"), \
                patch("xmcd2md.pandoc.subprocess.run", side_effect=self.successful_pandoc), \
                patch("xmcd2md.pandoc.os.link", side_effect=OSError("not supported")), \
                patch("xmcd2md.pandoc.shutil.copyfileobj", side_effect=failed_copy), \
                self.assertRaises(ConversionError):
            convert_file(self.source, self.target)
        self.assert_clean()

    @unittest.skipUnless(shutil.which("pandoc"), "Pandoc is not installed")
    def test_real_pandoc_preserves_nested_styles_line_breaks_and_inline_math(self):
        self.source.write_text(
            f'<worksheet xmlns="{WS}" xmlns:ml="{ML}"><regions><region region-id="text">'
            '<text><p><b>Жирный <i>Курсив <u>Подчёркнутый</u></i> '
            '<region region-id="inline"><math><ml:id>x</ml:id></math></region></b>'
            '<f line-through="true">Зачёркнутый</f><br/><f>После переноса</f>'
            '</p></text></region></regions></worksheet>', encoding="utf-8",
        )
        self.original = self.source.read_bytes()
        result = convert_file(self.source, self.target)
        with zipfile.ZipFile(result.output_path) as document:
            root = ET.fromstring(document.read("word/document.xml"))
        ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
              "m": "http://schemas.openxmlformats.org/officeDocument/2006/math"}
        runs = root.findall(".//w:r", ns)

        def run_with(text):
            return next(run for run in runs if text in "".join(run.itertext()))

        self.assertIsNotNone(run_with("Жирный").find("w:rPr/w:b", ns))
        self.assertIsNotNone(run_with("Курсив").find("w:rPr/w:b", ns))
        self.assertIsNotNone(run_with("Курсив").find("w:rPr/w:i", ns))
        underlined = run_with("Подчёркнутый")
        for tag in ("b", "i", "u"):
            self.assertIsNotNone(underlined.find("w:rPr/w:" + tag, ns))
        self.assertIsNotNone(run_with("Зачёркнутый").find("w:rPr/w:strike", ns))
        self.assertIsNotNone(root.find(".//w:br", ns))
        self.assertIn("После переноса", "".join(root.itertext()))
        self.assertIsNotNone(root.find(".//m:oMath", ns))
        self.assertIn("x", "".join(root.find(".//m:oMath", ns).itertext()))
        self.assertNotIn("region id", "".join(root.itertext()))
        self.assert_clean(self.target)

    @unittest.skipUnless(shutil.which("pandoc"), "Pandoc is not installed")
    def test_real_pandoc_embeds_editable_math_and_images(self):
        result = convert_file(self.source, self.target)
        with zipfile.ZipFile(result.output_path) as document:
            xml = document.read("word/document.xml").decode("utf-8")
            self.assertIn("oMath", xml)
            self.assertIn("Русский текст", xml)
            media = [name for name in document.namelist() if name.startswith("word/media/")]
            self.assertTrue(media)
            self.assertTrue(any(document.read(name) == base64.b64decode(PNG) for name in media))
        self.assert_clean(self.target)


if __name__ == "__main__":
    unittest.main()
