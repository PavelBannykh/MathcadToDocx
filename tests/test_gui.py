"""Exercise GUI decisions without a display or an actual worker thread."""
from pathlib import Path
import queue
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from xmcd2md.converter import ConversionResult
from xmcd2md.gui import ConverterApp
from xmcd2md.pandoc import PandocNotFoundError


class GuiTests(unittest.TestCase):
    def make_app(self, output_format="Word (.docx)"):
        app = ConverterApp.__new__(ConverterApp)
        app.busy = False
        app.root = Mock()
        app.sources = [Path("source.xmcd")]
        app.destination = SimpleNamespace(get=lambda: "output")
        app.output_format = SimpleNamespace(get=lambda: output_format)
        app.overwrite = SimpleNamespace(get=lambda: False)
        app.events = queue.Queue()
        app.set_busy = Mock()
        app.progress = Mock()
        app.status = Mock()
        return app

    def test_missing_pandoc_shows_installation_message_before_starting_worker(self):
        app = self.make_app()
        message = "Установите Pandoc: https://pandoc.org/installing.html, добавьте в PATH и перезапустите программу."
        with patch("xmcd2md.gui.ensure_pandoc", side_effect=PandocNotFoundError(message)) as ensure, patch("xmcd2md.gui.messagebox.showerror") as showerror, patch("xmcd2md.gui.threading.Thread") as thread:
            app.convert()
        ensure.assert_called_once_with()
        showerror.assert_called_once_with("Pandoc недоступен", message, parent=app.root)
        thread.assert_not_called()
        app.set_busy.assert_not_called()
        app.root.after.assert_not_called()

    def test_selected_format_reaches_worker_and_markdown_does_not_check_pandoc(self):
        for label, extension in (("Markdown (.md)", "md"), ("Word (.docx)", "docx")):
            with self.subTest(format=extension):
                app = self.make_app(label)
                output = (Path("output") / f"source.{extension}").resolve()
                result = ConversionResult(output, [], 1, 1, 0, 0)
                with patch("xmcd2md.gui.ensure_pandoc", return_value="pandoc") as ensure, patch("xmcd2md.gui.convert_file", return_value=result) as convert, patch("xmcd2md.gui.threading.Thread") as thread:
                    app.convert()
                    thread.call_args.kwargs["target"]()
                if extension == "docx":
                    ensure.assert_called_once_with()
                else:
                    ensure.assert_not_called()
                app.set_busy.assert_called_once_with(True)
                thread.return_value.start.assert_called_once_with()
                convert.assert_called_once_with(Path("source.xmcd"), output, force=False, output_format=extension)

    def test_format_selection_is_locked_while_busy(self):
        app = ConverterApp.__new__(ConverterApp)
        for name in ("choose_button", "browse_button", "destination_entry", "overwrite_check", "convert_button", "format_selector"):
            setattr(app, name, Mock())
        app.set_busy(True)
        self.assertTrue(app.busy)
        app.format_selector.configure.assert_called_with(state="disabled")
        app.set_busy(False)
        self.assertFalse(app.busy)
        app.format_selector.configure.assert_called_with(state="readonly")


if __name__ == "__main__":
    unittest.main()
