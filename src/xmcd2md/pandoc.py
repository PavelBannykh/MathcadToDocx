"""Build Word documents with Pandoc from the shared Markdown renderer."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
import zipfile

from .converter import ConversionError, ConversionResult, _Converter


PANDOC_TIMEOUT_SECONDS = 120
_INSTALL_MESSAGE = (
    "Для экспорта DOCX необходимо установить Pandoc: "
    "https://pandoc.org/installing.html . "
    "Убедитесь, что Pandoc доступен в PATH, и перезапустите программу."
)


class PandocNotFoundError(ConversionError):
    """Pandoc is unavailable on this computer's PATH."""


def ensure_pandoc() -> str:
    """Return the Pandoc executable, or actionable installation instructions."""
    executable = shutil.which("pandoc")
    if executable is None:
        raise PandocNotFoundError(_INSTALL_MESSAGE)
    return executable


def _validate_docx(path: Path) -> None:
    """Do not publish a missing, truncated, or non-DOCX subprocess result."""
    try:
        with zipfile.ZipFile(path) as document:
            if (not {"[Content_Types].xml", "word/document.xml"}.issubset(document.namelist())
                    or document.testzip() is not None):
                raise ConversionError("Pandoc не создал корректный DOCX-файл")
    except (OSError, zipfile.BadZipFile) as exc:
        raise ConversionError(f"Pandoc не создал корректный DOCX-файл: {exc}") from exc


def _publish_docx(document: Path, destination: Path, *, force: bool) -> None:
    if force:
        document.replace(destination)
        return
    try:
        # Atomic no-clobber publication on filesystems supporting hard links.
        os.link(document, destination)
        return
    except FileExistsError:
        raise
    except OSError:
        # Some Windows filesystems/sandboxes disallow hard links. Exclusive
        # creation still protects a destination created during conversion.
        pass
    created = None
    try:
        with destination.open("xb") as target:
            created = os.fstat(target.fileno())
            with document.open("rb") as source:
                shutil.copyfileobj(source, target)
    except BaseException:
        if created is not None:
            try:
                if os.path.samestat(created, destination.stat()):
                    destination.unlink()
            except FileNotFoundError:
                pass
        raise


def _convert_docx(root: ET.Element, destination: Path, *, force: bool) -> ConversionResult:
    # Check before mkdir, rendering, or writing any temporary assets.
    executable = ensure_pandoc()
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".xmcd2docx-", dir=destination.parent) as folder:
        workdir = Path(folder)
        markdown = workdir / "worksheet.md"
        document = workdir / "worksheet.docx"
        converter = _Converter(root, markdown)
        markdown.write_text(converter.render(), encoding="utf-8")
        command = [
            executable,
            "--from=markdown+tex_math_dollars",
            "--to=docx",
            "--standalone",
            "--lua-filter", str(Path(__file__).with_name("docx-inline.lua")),
            "--resource-path", str(workdir),
            "--output", str(document),
            str(markdown),
        ]
        try:
            completed = subprocess.run(
                command, cwd=workdir, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=PANDOC_TIMEOUT_SECONDS,
                check=False, shell=False,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        except FileNotFoundError as exc:
            # The executable can disappear after shutil.which succeeds.
            raise PandocNotFoundError(_INSTALL_MESSAGE) from exc
        except subprocess.TimeoutExpired as exc:
            raise ConversionError(
                f"Pandoc не завершил экспорт DOCX за {PANDOC_TIMEOUT_SECONDS} секунд"
            ) from exc
        except OSError as exc:
            raise ConversionError(f"Не удалось запустить Pandoc: {exc}") from exc
        except subprocess.SubprocessError as exc:
            raise ConversionError(f"Ошибка Pandoc: {exc}") from exc
        diagnostic = (completed.stderr or "").strip()
        if completed.returncode != 0:
            detail = f": {diagnostic}" if diagnostic else ""
            raise ConversionError(f"Pandoc завершился с кодом {completed.returncode}{detail}")
        if diagnostic:
            converter.warnings.append("Pandoc: " + diagnostic)
        _validate_docx(document)
        _publish_docx(document, destination, force=force)
        return ConversionResult(
            destination, converter.warnings, len(converter.visited),
            converter.math_count, converter.text_count, converter.image_count,
        )
