"""Small standard-library desktop interface, suitable for Windows users."""
from __future__ import annotations

from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk

from .converter import ConversionError, convert_file
from .pandoc import ensure_pandoc


OUTPUT_FORMATS = {"Markdown (.md)": "md", "Word (.docx)": "docx"}


class ConverterApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.sources: list[Path] = []
        self.events: queue.Queue = queue.Queue()
        self.busy = False
        self.destination = tk.StringVar(value=str(Path.cwd() / "output"))
        self.overwrite = tk.BooleanVar(value=False)
        self.output_format = tk.StringVar(value="Markdown (.md)")
        self.status = tk.StringVar(value="Выберите один или несколько файлов .xmcd")
        root.title("Mathcad → Markdown / Word")
        root.geometry("800x640")
        root.minsize(650, 500)
        root.protocol("WM_DELETE_WINDOW", self.close)
        frame = ttk.Frame(root, padding=18)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Mathcad → Markdown / Word", font=("Segoe UI", 20, "bold")).pack(anchor="w")
        ttk.Label(frame, text="Текст, формулы, сохранённые результаты и изображения.").pack(anchor="w", pady=(4, 14))
        self.choose_button = ttk.Button(frame, text="Выбрать файлы XMCD…", command=self.choose_files)
        self.choose_button.pack(anchor="w")
        self.file_list = tk.Listbox(frame, height=5, activestyle="none")
        self.file_list.pack(fill="x", pady=(8, 14))
        ttk.Label(frame, text="Формат результата").pack(anchor="w")
        self.format_selector = ttk.Combobox(frame, textvariable=self.output_format, values=tuple(OUTPUT_FORMATS), state="readonly")
        self.format_selector.pack(anchor="w", pady=(4, 8))
        ttk.Label(frame, text="Папка результатов").pack(anchor="w")
        destination_row = ttk.Frame(frame)
        destination_row.pack(fill="x", pady=(4, 8))
        self.destination_entry = ttk.Entry(destination_row, textvariable=self.destination)
        self.destination_entry.pack(side="left", fill="x", expand=True)
        self.browse_button = ttk.Button(destination_row, text="Обзор…", command=self.choose_destination)
        self.browse_button.pack(side="left", padx=(8, 0))
        self.overwrite_check = ttk.Checkbutton(frame, text="Заменять существующие выходные файлы", variable=self.overwrite)
        self.overwrite_check.pack(anchor="w")
        self.convert_button = ttk.Button(frame, text="Конвертировать", command=self.convert)
        self.convert_button.pack(anchor="w", pady=(14, 8))
        self.progress = ttk.Progressbar(frame, mode="determinate")
        self.progress.pack(fill="x")
        ttk.Label(frame, textvariable=self.status).pack(anchor="w", pady=8)
        self.log = scrolledtext.ScrolledText(frame, height=8, wrap="word", state="disabled")
        self.log.pack(fill="both", expand=True)

    def choose_files(self):
        names = filedialog.askopenfilenames(title="Исходные файлы Mathcad", filetypes=[("Mathcad XML", "*.xmcd"), ("Все файлы", "*.*")])
        if names:
            self.sources = [Path(name) for name in names]
            self.file_list.delete(0, "end")
            for path in self.sources:
                self.file_list.insert("end", str(path))
            self.status.set(f"Выбрано файлов: {len(self.sources)}")

    def choose_destination(self):
        name = filedialog.askdirectory(title="Папка результатов", initialdir=self.destination.get())
        if name:
            self.destination.set(name)

    def append_log(self, text: str):
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def set_busy(self, busy: bool):
        self.busy = busy
        for widget in (self.choose_button, self.browse_button, self.destination_entry, self.overwrite_check, self.convert_button):
            widget.configure(state="disabled" if busy else "normal")
        self.format_selector.configure(state="disabled" if busy else "readonly")

    def convert(self):
        if self.busy:
            return
        if not self.sources:
            messagebox.showinfo("Выбор файлов", "Сначала выберите файлы .xmcd.", parent=self.root)
            return
        if not self.destination.get().strip():
            messagebox.showinfo("Папка результатов", "Укажите папку для результатов.", parent=self.root)
            return
        sources = list(self.sources)
        directory = Path(self.destination.get()).expanduser()
        output_format = OUTPUT_FORMATS[self.output_format.get()]
        targets = [(directory / path.name).with_suffix("." + output_format).resolve() for path in sources]
        if len(set(targets)) != len(targets):
            messagebox.showerror("Одинаковые имена", "Файлы с одинаковыми именами нужно конвертировать в разные папки.", parent=self.root)
            return
        if output_format == "docx":
            try:
                ensure_pandoc()
            except (ConversionError, OSError) as exc:
                messagebox.showerror("Pandoc недоступен", str(exc), parent=self.root)
                return
        overwrite = self.overwrite.get()
        self.set_busy(True)
        self.progress.configure(maximum=len(sources), value=0)
        self.status.set("Конвертация…")

        def worker():
            failures = 0
            warnings = 0
            for index, (source, target) in enumerate(zip(sources, targets), 1):
                try:
                    result = convert_file(source, target, force=overwrite, output_format=output_format)
                    warnings += len(result.warnings)
                    self.events.put(("log", f"Готово: {result.output_path}\n  Формул: {result.math_count}, текстовых блоков: {result.text_count}, изображений: {result.image_count}"))
                    for warning in result.warnings:
                        self.events.put(("log", "  Предупреждение: " + warning))
                except (ConversionError, OSError) as exc:
                    failures += 1
                    self.events.put(("log", f"Ошибка ({source.name}): {exc}"))
                except Exception as exc:
                    failures += 1
                    self.events.put(("log", f"Непредвиденная ошибка ({source.name}): {type(exc).__name__}: {exc}"))
                self.events.put(("progress", index))
            self.events.put(("done", f"Готово: {len(sources) - failures} из {len(sources)}. Ошибок: {failures}. Предупреждений: {warnings}."))

        threading.Thread(target=worker, daemon=True).start()
        self.root.after(100, self.poll)

    def poll(self):
        while True:
            try:
                kind, value = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == "log":
                self.append_log(value)
            elif kind == "progress":
                self.progress.configure(value=value)
            elif kind == "done":
                self.status.set(value)
                self.set_busy(False)
        if self.busy:
            self.root.after(100, self.poll)

    def close(self):
        if self.busy:
            messagebox.showinfo("Конвертация выполняется", "Дождитесь завершения записи файлов.", parent=self.root)
        else:
            self.root.destroy()


def launch():
    root = tk.Tk()
    ConverterApp(root)
    root.mainloop()
