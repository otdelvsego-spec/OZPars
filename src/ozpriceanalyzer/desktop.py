"""Graphical Windows interface for OZPriceAnalyzer."""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import os
from pathlib import Path
from queue import Empty, Queue
import subprocess
import sys
from threading import Thread
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Any

from .main import run

APP_VERSION = "0.1.0"


def application_directory() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path.cwd().resolve()


class QueueWriter:
    def __init__(self, events: Queue[tuple[str, Any]]) -> None:
        self.events = events

    def write(self, text: str) -> int:
        if text:
            self.events.put(("log", text))
        return len(text)

    def flush(self) -> None:
        return None


class DesktopApplication:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.events: Queue[tuple[str, Any]] = Queue()
        self.worker: Thread | None = None
        base = application_directory()
        data = base / "data"
        data.mkdir(parents=True, exist_ok=True)

        self.input_path = tk.StringVar(value=str(data / "products.xlsx"))
        self.output_path = tk.StringVar(value=str(data / "result.xlsx"))
        self.history_path = tk.StringVar(value=str(data / "history.xlsx"))
        self.history_enabled = tk.BooleanVar(value=True)
        self.market_enabled = tk.BooleanVar(value=True)
        self.workers = tk.StringVar(value="2")
        self.analogues = tk.StringVar(value="5")
        self.candidate_checks = tk.StringVar(value="20")
        self.market_time_limit = tk.StringVar(value="60")
        self.timeout = tk.StringVar(value="15")
        self.status = tk.StringVar(value="Готово к запуску")

        root.title(f"OZPriceAnalyzer {APP_VERSION}")
        root.geometry("940x720")
        root.minsize(840, 620)
        self._build()
        root.after(100, self._poll)

    def _build(self) -> None:
        outer = ttk.Frame(self.root, padding=12)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(4, weight=1)

        ttk.Label(
            outer,
            text="Анализ цен и аналогов Ozon",
            font=("Segoe UI", 16, "bold"),
        ).grid(row=0, column=0, sticky="w", pady=(0, 10))

        files = ttk.LabelFrame(outer, text="Файлы", padding=10)
        files.grid(row=1, column=0, sticky="ew")
        files.columnconfigure(1, weight=1)
        self._file_row(files, 0, "Входной Excel", self.input_path, self._select_input)
        self._file_row(files, 1, "Итоговый отчёт", self.output_path, self._select_output)
        self._file_row(files, 2, "История цен", self.history_path, self._select_history)

        settings = ttk.LabelFrame(outer, text="Настройки анализа", padding=10)
        settings.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        for column in range(6):
            settings.columnconfigure(column, weight=1)
        ttk.Checkbutton(settings, text="Обновлять историю", variable=self.history_enabled).grid(
            row=0, column=0, columnspan=3, sticky="w"
        )
        ttk.Checkbutton(settings, text="Искать аналоги", variable=self.market_enabled).grid(
            row=0, column=3, columnspan=3, sticky="w"
        )
        self._setting(settings, 1, 0, "Потоки", self.workers)
        self._setting(settings, 1, 2, "Аналогов", self.analogues)
        self._setting(settings, 1, 4, "Проверить карточек", self.candidate_checks)
        self._setting(settings, 2, 0, "Лимит сравнения, сек.", self.market_time_limit)
        self._setting(settings, 2, 2, "Тайм-аут запроса, сек.", self.timeout)

        controls = ttk.Frame(outer)
        controls.grid(row=3, column=0, sticky="ew", pady=10)
        controls.columnconfigure(2, weight=1)
        self.start_button = ttk.Button(controls, text="Запустить анализ", command=self._start)
        self.start_button.grid(row=0, column=0, padx=(0, 8))
        ttk.Button(controls, text="Открыть папку отчёта", command=self._open_folder).grid(row=0, column=1)
        ttk.Label(controls, textvariable=self.status, font=("Segoe UI", 9, "bold")).grid(
            row=0, column=2, sticky="e"
        )

        log_frame = ttk.LabelFrame(outer, text="Журнал выполнения", padding=6)
        log_frame.grid(row=4, column=0, sticky="nsew")
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)
        self.log = tk.Text(log_frame, wrap="word", font=("Consolas", 10), state="disabled")
        scroll = ttk.Scrollbar(log_frame, command=self.log.yview)
        self.log.configure(yscrollcommand=scroll.set)
        self.log.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")

    @staticmethod
    def _file_row(parent, row: int, label: str, variable: tk.StringVar, command) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=(0, 8), pady=4)
        ttk.Entry(parent, textvariable=variable).grid(row=row, column=1, sticky="ew", pady=4)
        ttk.Button(parent, text="Обзор…", command=command).grid(row=row, column=2, padx=(8, 0), pady=4)

    @staticmethod
    def _setting(parent, row: int, column: int, label: str, variable: tk.StringVar) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=column, sticky="w", pady=(10, 2))
        ttk.Entry(parent, textvariable=variable, width=10).grid(
            row=row, column=column + 1, sticky="ew", padx=(6, 14), pady=(10, 2)
        )

    def _select_input(self) -> None:
        path = filedialog.askopenfilename(filetypes=[("Excel", "*.xlsx")])
        if path:
            self.input_path.set(path)

    def _select_output(self) -> None:
        path = filedialog.asksaveasfilename(defaultextension=".xlsx", filetypes=[("Excel", "*.xlsx")])
        if path:
            self.output_path.set(path)

    def _select_history(self) -> None:
        path = filedialog.asksaveasfilename(defaultextension=".xlsx", filetypes=[("Excel", "*.xlsx")])
        if path:
            self.history_path.set(path)

    def _start(self) -> None:
        if self.worker and self.worker.is_alive():
            return
        try:
            config = self._config()
        except ValueError as error:
            messagebox.showerror("Некорректные настройки", str(error))
            return
        self._clear_log()
        self.status.set("Выполняется анализ…")
        self.start_button.configure(state="disabled")
        self.worker = Thread(target=self._run, kwargs=config, daemon=True)
        self.worker.start()

    def _config(self) -> dict[str, Any]:
        input_file = Path(self.input_path.get().strip())
        if not input_file.is_file():
            raise ValueError(f"Входной Excel не найден:\n{input_file}")
        return {
            "input_file": input_file,
            "output_file": Path(self.output_path.get().strip()),
            "history_file": Path(self.history_path.get().strip()),
            "history_enabled": self.history_enabled.get(),
            "market_enabled": self.market_enabled.get(),
            "analog_limit": self._integer(self.analogues.get(), "Аналогов", 1, 30),
            "candidate_check_limit": self._integer(self.candidate_checks.get(), "Проверить карточек", 1, 30),
            "market_time_limit": self._positive(self.market_time_limit.get(), "Лимит сравнения"),
            "timeout": self._positive(self.timeout.get(), "Тайм-аут"),
            "workers": self._integer(self.workers.get(), "Потоки", 1, 16),
        }

    @staticmethod
    def _integer(value: str, label: str, minimum: int, maximum: int) -> int:
        try:
            number = int(value)
        except ValueError as error:
            raise ValueError(f"«{label}» должно быть целым числом.") from error
        if not minimum <= number <= maximum:
            raise ValueError(f"«{label}» должно быть от {minimum} до {maximum}.")
        return number

    @staticmethod
    def _positive(value: str, label: str) -> float:
        try:
            number = float(value.replace(",", "."))
        except ValueError as error:
            raise ValueError(f"«{label}» должно быть числом.") from error
        if number <= 0:
            raise ValueError(f"«{label}» должно быть больше нуля.")
        return number

    def _run(self, **config: Any) -> None:
        writer = QueueWriter(self.events)
        try:
            with redirect_stdout(writer), redirect_stderr(writer):
                code = run(**config)
            self.events.put(("done", (code, config["output_file"])))
        except Exception as error:
            self.events.put(("error", str(error)))

    def _poll(self) -> None:
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "log":
                    self._append_log(str(payload))
                elif kind == "done":
                    code, output = payload
                    self.start_button.configure(state="normal")
                    self.status.set("Готово" if code == 0 else "Завершено с ошибкой")
                    messagebox.showinfo("Анализ завершён", f"Отчёт сохранён:\n{output}")
                elif kind == "error":
                    self.start_button.configure(state="normal")
                    self.status.set("Ошибка")
                    messagebox.showerror("Ошибка анализа", str(payload))
        except Empty:
            pass
        self.root.after(100, self._poll)

    def _append_log(self, text: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", text)
        self.log.see("end")
        self.log.configure(state="disabled")

    def _clear_log(self) -> None:
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    def _open_folder(self) -> None:
        folder = Path(self.output_path.get().strip()).parent
        folder.mkdir(parents=True, exist_ok=True)
        if os.name == "nt":
            os.startfile(folder)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(folder)])
        else:
            subprocess.Popen(["xdg-open", str(folder)])


def main() -> None:
    root = tk.Tk()
    try:
        style = ttk.Style(root)
        if "vista" in style.theme_names():
            style.theme_use("vista")
    except tk.TclError:
        pass
    DesktopApplication(root)
    root.mainloop()
