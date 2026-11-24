"""Convert Mathcad 15 worksheets into Markdown or DOCX via Pandoc."""

__version__ = "0.2.0"

from .converter import ConversionError, ConversionResult, convert_file

__all__ = ["ConversionError", "ConversionResult", "convert_file", "__version__"]
