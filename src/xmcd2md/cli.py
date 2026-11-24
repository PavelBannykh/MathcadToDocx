"""Command-line entry point; inputs can be files, directories or glob patterns."""
from __future__ import annotations

import argparse
import glob
from pathlib import Path
import sys

from . import __version__
from .converter import ConversionError, convert_file
from .pandoc import ensure_pandoc


def collect_inputs(arguments: list[str]) -> list[Path]:
    found: list[Path] = []
    seen: set[Path] = set()
    for argument in arguments:
        path = Path(argument)
        if path.is_dir():
            matches = sorted(p for p in path.iterdir() if p.is_file() and p.suffix.lower() == ".xmcd")
        elif path.is_file():
            matches = [path]
        else:
            matches = sorted(Path(p) for p in glob.glob(argument) if Path(p).is_file())
        if not matches:
            raise ConversionError(f"Не найдены файлы XMCD: {argument}")
        for match in matches:
            if match.suffix.lower() != ".xmcd":
                raise ConversionError(f"Ожидается расширение .xmcd: {match}")
            resolved = match.resolve()
            if resolved not in seen:
                found.append(resolved)
                seen.add(resolved)
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Mathcad 15 XMCD → Markdown или Word (.docx).")
    parser.add_argument("inputs", nargs="*", help="файлы .xmcd, папки или маски (например, *.xmcd)")
    destination = parser.add_mutually_exclusive_group()
    destination.add_argument("-o", "--output", type=Path, help="выходной .md или .docx для одного файла")
    destination.add_argument("-d", "--output-dir", type=Path, help="папка для результатов")
    parser.add_argument("-f", "--format", choices=("md", "docx"), help="формат результата (по расширению --output, иначе md)")
    parser.add_argument("--force", action="store_true", help="разрешить замену существующих выходных файлов")
    parser.add_argument("--gui", action="store_true", help="открыть окно выбора файлов")
    parser.add_argument("--version", action="version", version=f"xmcd2md {__version__}")
    args = parser.parse_args(argv)
    if args.gui:
        if args.inputs or args.output or args.output_dir or args.force or args.format:
            parser.error("--gui используется отдельно от параметров конвертации")
        try:
            from .gui import launch
            launch()
        except ImportError as exc:
            print(f"Графический интерфейс недоступен: {exc}. Используйте командную строку.", file=sys.stderr)
            return 1
        return 0
    if not args.inputs:
        parser.error("укажите .xmcd, папку или --gui")
    try:
        inputs = collect_inputs(args.inputs)
        if args.output and len(inputs) != 1:
            raise ConversionError("--output допускается только для одного входного файла")
        output_format = args.format or (args.output.suffix.lower().lstrip(".") if args.output else "md")
        if output_format not in ("md", "docx"):
            raise ConversionError("Выходной файл должен иметь расширение .md или .docx")
        if args.output and args.output.suffix.lower() != "." + output_format:
            raise ConversionError("Расширение выходного файла не соответствует выбранному формату")
        extension = "." + output_format
        targets = [args.output or ((args.output_dir / p.name).with_suffix(extension) if args.output_dir else p.with_suffix(extension)) for p in inputs]
        resolved_targets = [p.resolve() for p in targets]
        if len(set(resolved_targets)) != len(resolved_targets):
            raise ConversionError("У входных файлов одинаковые имена: выберите разные папки результатов")
        if output_format == "docx":
            ensure_pandoc()
    except (ConversionError, OSError) as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 1
    failures = 0
    for source, target in zip(inputs, targets):
        try:
            result = convert_file(source, target, force=args.force, output_format=output_format)
            print(f"Готово: {result.output_path}")
            print(f"  Регионов: {result.region_count}; формул: {result.math_count}; текстовых блоков: {result.text_count}; изображений: {result.image_count}")
            for warning in result.warnings:
                print(f"  Предупреждение: {warning}", file=sys.stderr)
        except (ConversionError, OSError) as exc:
            failures += 1
            print(f"Ошибка ({source.name}): {exc}", file=sys.stderr)
    return 1 if failures else 0
