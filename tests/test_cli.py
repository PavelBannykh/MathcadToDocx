from contextlib import redirect_stderr, redirect_stdout
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from xmcd2md.cli import main
from xmcd2md.converter import ConversionResult
from xmcd2md.pandoc import PandocNotFoundError


WORKSHEET = '''<worksheet xmlns="http://schemas.mathsoft.com/worksheet30"
 xmlns:ml="http://schemas.mathsoft.com/math30"><regions><region region-id="1">
 <math><ml:define><ml:id>x</ml:id><ml:real>2</ml:real></ml:define></math>
 </region></regions></worksheet>'''


class CliTests(unittest.TestCase):
    def invoke(self, args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main([str(arg) for arg in args])
        return code, out.getvalue(), err.getvalue()

    def test_batch_partial_failure_does_not_skip_valid_files(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            (directory / "good.xmcd").write_text(WORKSHEET, encoding="utf-8")
            (directory / "bad.xmcd").write_text("invalid", encoding="utf-8")
            target = directory / "output"
            code, out, err = self.invoke([directory, "-d", target])
            self.assertEqual(code, 1)
            self.assertTrue((target / "good.md").exists())
            self.assertFalse((target / "bad.md").exists())
            self.assertIn("bad.xmcd", err)

    def test_force_is_explicit_and_duplicates_are_ignored(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "source.xmcd"
            source.write_text(WORKSHEET, encoding="utf-8")
            self.assertEqual(self.invoke([source, source])[0], 0)
            target = source.with_suffix(".md")
            target.write_text("Keep my edits", encoding="utf-8")
            self.assertEqual(self.invoke([source])[0], 1)
            self.assertEqual(target.read_text(encoding="utf-8"), "Keep my edits")
            self.assertEqual(self.invoke([source, "--force"])[0], 0)
            self.assertIn("coloneqq", target.read_text(encoding="utf-8"))
            self.assertEqual(source.read_text(encoding="utf-8"), WORKSHEET)

    def test_destination_collision_rejected_before_writing(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            sources = []
            for name in ("a", "b"):
                source = directory / name / "same.xmcd"
                source.parent.mkdir()
                source.write_text(WORKSHEET, encoding="utf-8")
                sources.append(source)
            target = directory / "out"
            code, _, _ = self.invoke([*sources, "-d", target, "--force"])
            self.assertEqual(code, 1)
            self.assertFalse(target.exists())

    def test_unmatched_pattern_and_wrong_extension_fail(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            self.assertEqual(self.invoke([directory / "*.xmcd"])[0], 1)
            source = directory / "file.txt"
            source.write_text(WORKSHEET, encoding="utf-8")
            self.assertEqual(self.invoke([source])[0], 1)

    def test_markdown_format_does_not_require_pandoc(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "source.xmcd"
            source.write_text(WORKSHEET, encoding="utf-8")
            with patch("xmcd2md.cli.ensure_pandoc") as ensure:
                code, _, err = self.invoke([source, "--format", "md"])
            self.assertEqual(code, 0, err)
            self.assertTrue(source.with_suffix(".md").exists())
            ensure.assert_not_called()

    def test_docx_batch_uses_selected_extension_and_checks_pandoc_once(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            sources = [directory / "a.xmcd", directory / "b.xmcd"]
            for source in sources:
                source.write_text(WORKSHEET, encoding="utf-8")
            target = directory / "out"
            def converted(source, output, **kwargs):
                return ConversionResult(output, [], 1, 1, 0, 0)
            with patch("xmcd2md.cli.ensure_pandoc", return_value="pandoc") as ensure, patch("xmcd2md.cli.convert_file", side_effect=converted) as convert:
                code, _, err = self.invoke([*sources, "-d", target, "-f", "docx", "--force"])
            self.assertEqual(code, 0, err)
            ensure.assert_called_once_with()
            self.assertEqual([call.args[1] for call in convert.call_args_list], [target / "a.docx", target / "b.docx"])
            for call in convert.call_args_list:
                self.assertEqual(call.kwargs, {"force": True, "output_format": "docx"})

    def test_docx_is_inferred_from_output_path(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "source.xmcd"
            source.write_text(WORKSHEET, encoding="utf-8")
            output = Path(folder) / "custom.DOCX"
            result = ConversionResult(output, [], 1, 1, 0, 0)
            with patch("xmcd2md.cli.ensure_pandoc", return_value="pandoc") as ensure, patch("xmcd2md.cli.convert_file", return_value=result) as convert:
                code, _, err = self.invoke([source, "-o", output])
            self.assertEqual(code, 0, err)
            ensure.assert_called_once_with()
            convert.assert_called_once_with(source.resolve(), output, force=False, output_format="docx")

    def test_missing_pandoc_fails_batch_before_any_conversion(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            for name in ("a", "b"):
                (directory / f"{name}.xmcd").write_text(WORKSHEET, encoding="utf-8")
            message = "Установите Pandoc: https://pandoc.org/installing.html, добавьте в PATH и перезапустите программу."
            with patch("xmcd2md.cli.ensure_pandoc", side_effect=PandocNotFoundError(message)) as ensure, patch("xmcd2md.cli.convert_file") as convert:
                code, out, err = self.invoke([directory, "--format", "docx"])
            self.assertEqual(code, 1)
            self.assertEqual(out, "")
            self.assertIn(message, err)
            ensure.assert_called_once_with()
            convert.assert_not_called()
            self.assertFalse(list(directory.glob("*.docx")))

    def test_explicit_format_mismatch_is_rejected_before_conversion(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "source.xmcd"
            source.write_text(WORKSHEET, encoding="utf-8")
            with patch("xmcd2md.cli.ensure_pandoc") as ensure, patch("xmcd2md.cli.convert_file") as convert:
                code, _, err = self.invoke([source, "--format", "md", "-o", source.with_suffix(".docx")])
            self.assertEqual(code, 1)
            self.assertIn("не соответствует", err)
            ensure.assert_not_called()
            convert.assert_not_called()


if __name__ == "__main__":
    unittest.main()
