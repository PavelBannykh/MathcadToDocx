"""Read saved Mathcad worksheet regions without evaluating their contents."""

from __future__ import annotations

from dataclasses import dataclass
from html import escape
import math
from pathlib import Path
import re
import tempfile
import xml.etree.ElementTree as ET
from xml.parsers import expat

from .assets import AssetExtractor
from .math_renderer import MathRenderer


class ConversionError(Exception):
    """The input cannot be converted or the destination cannot be written."""


@dataclass
class ConversionResult:
    output_path: Path
    warnings: list[str]
    region_count: int
    math_count: int
    text_count: int
    image_count: int


def _local(element: ET.Element) -> str:
    return element.tag.rsplit("}", 1)[-1]


def _number(element: ET.Element, name: str, default: float = 0) -> float:
    try:
        value = float(element.get(name, str(default)))
        return value if math.isfinite(value) else default
    except ValueError:
        return default


def _ordered(regions: list[ET.Element]) -> list[ET.Element]:
    # align-y is an absolute baseline, NOT an offset from top. Sorting by top
    # alone places tall fractions/matrices ahead of their neighbours.
    pending = sorted(enumerate(regions), key=lambda pair: (
        _number(pair[1], "align-y", _number(pair[1], "top")), pair[0]
    ))
    ordered = []
    while pending:
        baseline = _number(pending[0][1], "align-y", _number(pending[0][1], "top"))
        end = 1
        while end < len(pending):
            other = pending[end][1]
            if _number(other, "align-y", _number(other, "top")) - baseline > 1.5:
                break
            end += 1
        ordered.extend(region for _, region in sorted(
            pending[:end], key=lambda pair: (_number(pair[1], "left"), pair[0])
        ))
        pending = pending[end:]
    return ordered


def _child_regions(element: ET.Element) -> list[ET.Element]:
    """Find immediate region descendants, leaving nested regions to their parent."""
    found = []
    for child in element:
        if _local(child) == "region":
            found.append(child)
        else:
            found.extend(_child_regions(child))
    return found


def _parse(data: bytes) -> ET.Element:
    # Expat sees declarations in every supported XML encoding, including UTF-16.
    # Reject before the parser can expand internal or external entities.
    check = expat.ParserCreate()

    def forbidden(*_args):
        raise ConversionError("DTD and entity declarations are not allowed in XMCD files")

    check.StartDoctypeDeclHandler = forbidden
    check.EntityDeclHandler = forbidden
    check.ExternalEntityRefHandler = forbidden
    try:
        check.Parse(data, True)
        root = ET.fromstring(data)
    except (expat.ExpatError, ET.ParseError, LookupError, ValueError) as exc:
        raise ConversionError(f"Invalid XML: {exc}") from exc
    if not re.fullmatch(r"\{http://schemas\.mathsoft\.com/worksheet\d+\}worksheet", root.tag):
        raise ConversionError("Not a Mathcad XMCD worksheet (unexpected root or namespace)")
    return root


def _markdown(value: str | None) -> str:
    if not value or (not value.strip() and ("\n" in value or "\r" in value)):
        return ""
    value = escape(value, quote=False)
    value = re.sub(r"([\\`*_\[\]$#|])", r"\\\1", value)
    return re.sub(r"(?m)^(\s*)([-+]|\d+[.)])(?=\s)",
                  lambda match: match.group(1) + "\\" + match.group(2)
                  if match.group(2) in {"-", "+"}
                  else match.group(1) + match.group(2)[:-1] + "\\" + match.group(2)[-1], value)


class _Converter:
    def __init__(self, root: ET.Element, output: Path):
        self.root = root
        self.assets = AssetExtractor(root, output)
        self.math = MathRenderer()
        self.warnings: list[str] = []
        self.visited: set[int] = set()
        self.math_count = self.text_count = self.image_count = 0
        self._unknown_text: list[ET.Element] = []
        self.imaginary_symbol = "i"
        for settings in root:
            if _local(settings) == "settings":
                for node in settings.iter():
                    if _local(node) == "general" and node.get("imaginary-value"):
                        self.imaginary_symbol = node.get("imaginary-value", "i")
                        break

    def comment(self, region: ET.Element) -> str:
        value = region.get("region-id", "unknown").replace("--", "- -")
        return f"<!-- region id={escape(value, quote=True)} -->"

    def warn(self, region: ET.Element, message: str):
        self.warnings.append(f"Region {region.get('region-id', 'unknown')}: {message}")

    def xml(self, element: ET.Element) -> str:
        source = ET.tostring(element, encoding="unicode")
        # Choose a fence longer than any sequence in the original text.
        runs = re.findall(r"`+", source)
        fence = "`" * max(3, max((len(run) + 1 for run in runs), default=3))
        return f"<details>\n<summary>Original XMCD XML</summary>\n\n{fence}xml\n{source}\n{fence}\n\n</details>"

    def preview(self, region: ET.Element) -> str | None:
        try:
            before = len(self.assets.warnings)
            image = self.assets.export_region(region)
            self.warnings.extend(self.assets.warnings[before:])
        except Exception as exc:
            self.warn(region, f"Cannot export saved preview: {exc}")
            return None
        if image:
            self.image_count += 1
        return image

    def formula(self, element: ET.Element, region: ET.Element) -> tuple[str, bool]:
        symbol = self.imaginary_symbol
        for child in list(region) + list(element):
            if _local(child) == "resultFormat":
                for general in child.iter():
                    if _local(general) == "general":
                        symbol = general.get("imaginary-value", symbol)
        self.math.imaginary_symbol = symbol
        self.math_count += 1
        before = len(self.math.warnings)
        try:
            result = self.math.render(element)
        except Exception as exc:
            self.warn(region, f"Cannot render saved formula: {exc}")
            return r"\text{Unsupported Mathcad expression}", True
        warnings = self.math.warnings[before:]
        for warning in warnings:
            self.warn(region, warning)
        if not result.strip():
            self.warn(region, "Empty or unsupported saved formula")
            return r"\text{Empty Mathcad expression}", True
        return result, bool(warnings)

    def inline(self, element: ET.Element, region: ET.Element) -> str:
        name = _local(element)
        if name == "sp":
            try:
                count = max(1, min(10000, int(element.get("count", "1"))))
            except ValueError:
                count = 1
                self.warn(region, "Invalid space count")
            return " " * count
        if name in {"br", "lineBreak"}:
            return "<br>\n"
        if name == "region":
            self.visited.add(id(element))
            content = []
            for child in element:
                if _local(child) == "math":
                    latex, unsupported = self.formula(child, element)
                    content.append(f"${latex}$")
                    if unsupported:
                        self._unknown_text.append(element)
                elif _local(child) not in {"rendering", "resultFormat"}:
                    self.warn(element, "Unsupported inline region content")
                    self._unknown_text.append(element)
            if not content:
                self.warn(element, "Inline region has no supported formula")
                self._unknown_text.append(element)
                content.append("[Unsupported inline Mathcad region]")
            return self.comment(element) + "".join(content)
        if name == "math":
            latex, unsupported = self.formula(element, region)
            if unsupported:
                self._unknown_text.append(element)
            return f"${latex}$"
        content = _markdown(element.text)
        for child in element:
            content += self.inline(child, region) + _markdown(child.tail)
        if name == "b":
            return f"<strong>{content}</strong>"
        if name == "i":
            return f"<em>{content}</em>"
        if name == "u":
            return f"<u>{content}</u>"
        if name in {"inlineAttr", "f"}:
            if element.get("font-weight", element.get("weight")) in {"bold", "700"}:
                content = f"<strong>{content}</strong>"
            if element.get("font-style", element.get("style")) == "italic":
                content = f"<em>{content}</em>"
            if element.get("underline") in {"true", "single"}:
                content = f"<u>{content}</u>"
            if element.get("line-through") == "true":
                content = f"<s>{content}</s>"
        elif name not in {"p", "text"}:
            self.warn(region, f"Unsupported text element {name}; text and XML preserved")
            self._unknown_text.append(element)
        return content

    def text(self, element: ET.Element, region: ET.Element) -> str:
        self.text_count += 1
        self._unknown_text = []
        paragraphs = []
        if element.text and element.text.strip():
            paragraphs.append(_markdown(element.text))
        for paragraph in element:
            content = self.inline(paragraph, region)
            style = paragraph.get("style", "")
            heading = re.fullmatch(r"(?:Heading|Заголовок)\s*([1-6])", style, re.IGNORECASE)
            if heading:
                content = "#" * int(heading.group(1)) + " " + content
            elif style.lower() == "title":
                content = "# " + content
            paragraphs.append(content)
            if paragraph.tail and paragraph.tail.strip():
                paragraphs.append(_markdown(paragraph.tail))
        content = "\n\n".join(paragraphs)
        if self._unknown_text:
            image = self.preview(region)
            content += "\n\n" + (image + "\n\n" if image else "") + self.xml(element)
        return content

    def region(self, region: ET.Element) -> str:
        if id(region) in self.visited:
            return ""
        self.visited.add(id(region))
        blocks = [self.comment(region)]
        supported = False
        unknown = []
        for child in region:
            name = _local(child)
            if name == "text":
                blocks.append(self.text(child, region))
                supported = True
            elif name == "math":
                latex, unsupported = self.formula(child, region)
                blocks.append("$$\n" + latex + "\n$$")
                supported = True
                if unsupported:
                    image = self.preview(region)
                    if image:
                        blocks.append(image)
                    blocks.append(self.xml(child))
            elif name in {"picture", "ole"}:
                image = self.preview(region)
                if image:
                    blocks.append(image)
                else:
                    self.warn(region, f"No usable saved preview for {name}; original XML preserved")
                    blocks.extend([f"[Unsupported Mathcad {name} region]", self.xml(child)])
                supported = True
            elif name in {"area", "regions"}:
                # Coordinates in nested areas are kept relative to their own
                # container. No inference about hidden/collapsed areas is made.
                self.warn(region, f"Nested {name} preserved in reading order")
                nested = _child_regions(child)
                blocks.extend(self.render_regions(nested))
                if not nested:
                    unknown.append(child)
                else:
                    # Area titles, collapse state, and future container fields
                    # have no Markdown equivalent; retain their source too.
                    blocks.append(self.xml(child))
                supported = True
            elif name not in {"rendering", "resultFormat", "protection"}:
                unknown.append(child)
        if unknown or not supported:
            names = ", ".join(_local(child) for child in unknown) or "empty region"
            self.warn(region, f"Unsupported region content ({names}); original XML preserved")
            image = self.preview(region)
            blocks.extend([image or f"[Unsupported Mathcad region: {names}]", self.xml(region)])
        # Catch regions inside unfamiliar wrappers too, without duplication.
        for nested in _ordered(_child_regions(region)):
            if id(nested) not in self.visited:
                blocks.append(self.region(nested))
        return "\n\n".join(blocks)

    def render_regions(self, regions: list[ET.Element]) -> list[str]:
        """Keep adjacent formulas on the same saved baseline in one display."""
        blocks = []
        comments: list[str] = []
        formulas: list[str] = []
        baseline = 0.0

        def flush():
            if formulas:
                blocks.append("\n".join(comments) + "\n\n$$\n"
                              + "\n\\qquad ".join(formulas) + "\n$$")
                comments.clear()
                formulas.clear()

        for region in _ordered(regions):
            content = self.region(region)
            if not content:
                continue
            names = [_local(child) for child in region]
            pure_math = names.count("math") == 1 and all(
                name in {"math", "rendering", "resultFormat", "protection"} for name in names
            )
            comment, marker, formula = content.partition("\n\n$$\n")
            current_y = _number(region, "align-y", _number(region, "top"))
            if pure_math and marker and formula.endswith("\n$$"):
                if formulas and abs(current_y - baseline) > 1.5:
                    flush()
                if not formulas:
                    baseline = current_y
                comments.append(comment)
                formulas.append(formula[:-3])
            else:
                flush()
                blocks.append(content)
        flush()
        return blocks

    def render(self) -> str:
        blocks = self.render_regions(_child_regions(self.root))
        for region in self.root.iter():
            if _local(region) == "region" and id(region) not in self.visited:
                self.warn(region, "Region outside standard structure preserved")
                blocks.append(self.region(region))
        blocks.extend(self.structure_extras(self.root))
        return "\n\n".join(blocks) + "\n"

    def structure_extras(self, container: ET.Element) -> list[str]:
        """Retain unfamiliar content outside regions, including new containers."""
        blocks = []
        document_fields = {"pointReleaseData", "metadata", "settings", "binaryContent"}
        if ((container.text and container.text.strip())
                or any(child.tail and child.tail.strip() for child in container)):
            self.warnings.append("Text outside a worksheet region; original XML preserved")
            blocks.append(self.xml(container))
        for child in container:
            name = _local(child)
            if name == "region" or (container is self.root and name in document_fields):
                continue
            if name == "regions":
                blocks.extend(self.structure_extras(child))
            else:
                self.warnings.append(f"Unsupported worksheet element {name}; original XML preserved")
                blocks.extend([f"[Unsupported Mathcad worksheet element: {name}]", self.xml(child)])
        return blocks


def convert_file(
    input_path: str | Path,
    output_path: str | Path | None = None,
    *,
    force: bool = False,
    output_format: str | None = None,
) -> ConversionResult:
    """Convert one XMCD worksheet, preserving saved results and region IDs."""
    source = Path(input_path).expanduser().resolve()
    selected_format = (output_format.strip().lower() if output_format is not None
                       else Path(output_path).suffix.lower().lstrip(".")
                       if output_path is not None else "md")
    if selected_format not in {"md", "docx"}:
        raise ConversionError("The output format must be md or docx")
    destination = (Path(output_path).expanduser() if output_path is not None
                   else source.with_suffix("." + selected_format)).resolve()
    if source == destination:
        raise ConversionError("The output must not overwrite the source worksheet")
    if destination.suffix.lower() != "." + selected_format:
        raise ConversionError(f"The output path must have a .{selected_format} extension")
    try:
        if destination.exists() and source.exists() and source.samefile(destination):
            raise ConversionError("The output must not overwrite the source worksheet")
        if destination.exists() and not force:
            raise ConversionError(f"Output already exists: {destination}; use force=True to replace it")
        root = _parse(source.read_bytes())
        if selected_format == "docx":
            # Lazy import: the Pandoc backend uses the shared renderer and errors.
            from .pandoc import _convert_docx
            return _convert_docx(root, destination, force=force)
        destination.parent.mkdir(parents=True, exist_ok=True)
        converter = _Converter(root, destination)
        content = converter.render()
        if force:
            temporary = None
            try:
                with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="\n",
                                                 dir=destination.parent, delete=False) as stream:
                    temporary = Path(stream.name)
                    stream.write(content)
                temporary.replace(destination)
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
        else:
            with destination.open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(content)
    except OSError as exc:
        raise ConversionError(f"Cannot convert {source}: {exc}") from exc
    return ConversionResult(destination, converter.warnings, len(converter.visited),
                            converter.math_count, converter.text_count, converter.image_count)
