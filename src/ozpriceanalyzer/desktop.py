"""Graphical Windows interface for OZPriceAnalyzer."""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime
import os
from pathlib import Path
from queue import Empty, Queue
import subprocess
import sys
from threading import Thread
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk
from typing import Any
import webbrowser

from .analyzer import analyze_products
from .app_storage import AppStorage, RunSummary
from .configured_client import ConfiguredOzonClient
from .excel import ExcelLoader
from .exporter import export_results, suggested_export_name
from .models import MatchingSettings, PriceResult, Product
from .theme import apply_theme

APP_VERSION = "0.2.0"
THEME_LABELS = {"Системная": "system", "Темная": "dark", "Светлая": "light"}
THEME_VALUES = {value: key for key, value in THEME_LABELS.items()}
PRICE_BASIS_LABELS = {
    "Автоматическая цена Ozon": "current",
    "Цена без Ozon Карты": "without_card",
    "Цена с Ozon Картой": "ozon_card",
}
PRICE_BASIS_VALUES = {value: key for key, value in PRICE_BASIS_LABELS.items()}


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
        self.base_dir = application_directory()
        self.data_dir = self.base_dir / "data"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.storage = AppStorage(self.data_dir / "ozpriceanalyzer.db")
        self.events: Queue[tuple[str, Any]] = Queue()
        self.worker: Thread | None = None
        self.current_run_id: int | None = None
        self.current_results: list[PriceResult] = []
        self.current_source_file = self.storage.get_setting("last_input_path", "")
        self.report_display_to_id: dict[str, int] = {}
        self.run_number_by_id: dict[int, int] = {}
        self.overview_analogue_meta: dict[str, list[tuple[str, str]]] = {}
        self.context_analogue: tuple[str, str, str] | None = None
        self.resizable_panes: list[tk.PanedWindow] = []

        theme = self.storage.get_setting("theme", "system")
        self.colors = apply_theme(root, theme)
        root.title(f"OZ Price Analyzer {APP_VERSION}")
        root.geometry("1540x900")
        root.minsize(1180, 700)
        root.option_add("*Font", "{Segoe UI} 10")
        root.protocol("WM_DELETE_WINDOW", self._close)

        self.status_var = tk.StringVar(value="Готово")
        self.report_var = tk.StringVar()
        self.overview_title_var = tk.StringVar(value="Нет сохраненных отчетов")
        self.overview_count_var = tk.StringVar(value="Товаров: 0")
        self.catalog_search_var = tk.StringVar()
        self.history_year_var = tk.StringVar(value="Все годы")

        self._load_setting_vars()
        self._build_ui()
        self.refresh_all()
        root.after(100, self._poll)

    def _load_setting_vars(self) -> None:
        rules = self.storage.matching_settings()
        self.overall_similarity_var = tk.StringVar(value=_percent_number(rules.overall_similarity))
        self.material_similarity_var = tk.StringVar(value=_percent_number(rules.material_similarity))
        self.max_dimension_difference_var = tk.StringVar(value=_percent_number(rules.max_dimension_difference))
        self.max_weight_difference_var = tk.StringVar(value=_percent_number(rules.max_weight_difference))
        self.material_weight_var = tk.StringVar(value=_percent_number(rules.material_weight))
        self.dimensions_weight_var = tk.StringVar(value=_percent_number(rules.dimensions_weight))
        self.weight_weight_var = tk.StringVar(value=_percent_number(rules.weight_weight))
        self.strict_category_var = tk.BooleanVar(value=rules.strict_category)
        self.min_rating_var = tk.StringVar(value=f"{rules.min_rating:g}")
        self.min_feedbacks_var = tk.StringVar(value=str(rules.min_feedbacks))
        self.min_dimension_count_var = tk.StringVar(value=str(rules.min_dimension_count))
        self.use_package_dimensions_var = tk.BooleanVar(value=rules.use_package_dimensions)
        self.price_basis_var = tk.StringVar(value=PRICE_BASIS_VALUES.get(rules.price_basis, "Автоматическая цена Ozon"))
        self.market_tolerance_var = tk.StringVar(value=_percent_number(rules.market_tolerance))

        self.analog_limit_var = tk.StringVar(value=self.storage.get_setting("analog_limit", "5"))
        self.candidate_check_limit_var = tk.StringVar(value=self.storage.get_setting("candidate_check_limit", "20"))
        self.market_time_limit_var = tk.StringVar(value=self.storage.get_setting("market_time_limit", "60"))
        self.timeout_var = tk.StringVar(value=self.storage.get_setting("timeout", "15"))
        self.workers_var = tk.StringVar(value=self.storage.get_setting("workers", "2"))
        self.theme_var = tk.StringVar(
            value=THEME_VALUES.get(self.storage.get_setting("theme", "system"), "Системная")
        )
        default_export = self.base_dir / "exports"
        self.export_path_var = tk.StringVar(
            value=self.storage.get_setting("export_path", str(default_export))
        )

    def _build_ui(self) -> None:
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(1, weight=1)
        self._build_header()

        self.notebook = ttk.Notebook(self.root)
        self.notebook.grid(row=1, column=0, sticky="nsew", padx=18, pady=(0, 12))
        self.overview_tab = ttk.Frame(self.notebook, padding=4)
        self.catalog_tab = ttk.Frame(self.notebook, padding=4)
        self.history_tab = ttk.Frame(self.notebook, padding=4)
        self.settings_tab = ttk.Frame(self.notebook, padding=4)
        self.notebook.add(self.overview_tab, text="Обзор")
        self.notebook.add(self.catalog_tab, text="Справочник")
        self.notebook.add(self.history_tab, text="История отчетов")
        self.notebook.add(self.settings_tab, text="Настройки аналогов")

        self._build_overview_tab()
        self._build_catalog_tab()
        self._build_history_tab()
        self._build_settings_tab()

        ttk.Label(self.root, textvariable=self.status_var, style="Muted.TLabel").grid(
            row=2, column=0, sticky="ew", padx=22, pady=(0, 10)
        )

    def _build_header(self) -> None:
        header = ttk.Frame(self.root, padding=(22, 18, 22, 16))
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(0, weight=1)
        title_box = ttk.Frame(header)
        title_box.grid(row=0, column=0, sticky="w")
        ttk.Label(title_box, text="OZ Price Analyzer", style="Title.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(
            title_box,
            text="Цены Ozon, автоматический подбор аналогов, справочник и история отчетов",
            style="Muted.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(2, 0))

        actions = ttk.Frame(header)
        actions.grid(row=0, column=1, rowspan=2, sticky="e")
        ttk.Label(actions, text="Отчет:", style="Muted.TLabel").grid(row=0, column=0, padx=(0, 6))
        self.report_combo = ttk.Combobox(actions, textvariable=self.report_var, state="readonly", width=33)
        self.report_combo.grid(row=0, column=1, padx=(0, 12))
        self.report_combo.bind("<<ComboboxSelected>>", self._on_report_selected)
        ttk.Button(
            actions,
            text="Загрузить products.xlsx",
            command=self.import_products,
        ).grid(row=0, column=2, padx=4)
        self.start_button = ttk.Button(
            actions,
            text="Запустить анализ",
            style="Accent.TButton",
            command=self.start_analysis,
        )
        self.start_button.grid(row=0, column=3, padx=4)
        ttk.Button(actions, text="Экспорт в Excel", command=self.export_current_run).grid(
            row=0, column=4, padx=4
        )
        ttk.Button(actions, text="О программе", command=self.show_about).grid(
            row=1, column=4, sticky="e", padx=4, pady=(6, 0)
        )

    def _build_overview_tab(self) -> None:
        self.overview_tab.columnconfigure(0, weight=1)
        self.overview_tab.rowconfigure(0, weight=1)
        pane = tk.PanedWindow(
            self.overview_tab,
            orient=tk.VERTICAL,
            borderwidth=0,
            relief=tk.FLAT,
            background=self.colors["window"],
            sashwidth=8,
            sashpad=3,
            showhandle=True,
            handlesize=12,
            handlepad=4,
            opaqueresize=True,
        )
        pane.grid(row=0, column=0, sticky="nsew")
        self.resizable_panes.append(pane)

        table_panel = ttk.Frame(pane)
        table_panel.columnconfigure(0, weight=1)
        table_panel.rowconfigure(2, weight=1)
        pane.add(table_panel, minsize=350, stretch="always")

        header = ttk.Frame(table_panel)
        header.grid(row=0, column=0, sticky="ew", pady=(10, 8))
        header.columnconfigure(1, weight=1)
        ttk.Label(header, text="Подобранные аналоги", style="Section.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(header, textvariable=self.overview_title_var, style="Muted.TLabel").grid(
            row=0, column=1, sticky="e", padx=(20, 12)
        )
        ttk.Label(header, textvariable=self.overview_count_var, style="Muted.TLabel").grid(row=0, column=2, sticky="e")

        ttk.Label(
            table_panel,
            text=(
                "Двойной щелчок по названию аналога открывает карточку Ozon. "
                "Правая кнопка мыши по аналогу добавляет его в справочник исключений."
            ),
            style="Muted.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(0, 8))

        self.overview_tree = self._create_tree(table_panel, [], [], row=2, selectmode="browse")
        self._configure_overview_columns(self._analogue_limit_value())
        self.overview_tree.bind("<Double-1>", self._open_overview_link)
        self.overview_tree.bind("<Button-3>", self._show_overview_context_menu)
        self.overview_tree.tag_configure("above", foreground=self.colors["negative"])
        self.overview_tree.tag_configure("below", foreground=self.colors["positive"])

        log_panel = ttk.Frame(pane)
        log_panel.columnconfigure(0, weight=1)
        log_panel.rowconfigure(1, weight=1)
        pane.add(log_panel, minsize=120, stretch="never")
        ttk.Label(log_panel, text="Журнал выполнения", style="Section.TLabel").grid(
            row=0, column=0, sticky="w", pady=(8, 6)
        )
        self.log = tk.Text(log_panel, wrap="word", height=7, font=("Consolas", 9), borderwidth=0)
        scroll = ttk.Scrollbar(log_panel, orient="vertical", command=self.log.yview)
        self.log.configure(yscrollcommand=scroll.set)
        self.log.grid(row=1, column=0, sticky="nsew")
        scroll.grid(row=1, column=1, sticky="ns")
        self._style_log()

        self.overview_context_menu = tk.Menu(self.root, tearoff=False)
        self.overview_context_menu.add_command(
            label="Добавить аналог в исключения",
            command=self.add_context_analogue_to_exclusions,
        )

    def _configure_overview_columns(self, analogue_limit: int) -> None:
        columns = ["sku", "name", "price"]
        headings = ["Исходный артикул", "Наименование исходного артикула", "Цена исходного артикула"]
        widths = [160, 320, 180]
        for position in range(1, analogue_limit + 1):
            columns.extend([f"analogue_{position}", f"analogue_price_{position}"])
            headings.extend([f"Аналог {position}", f"Цена аналога {position}"])
            widths.extend([320, 175])
        columns.extend(["market_price", "market_position", "market_percent"])
        headings.extend(["Медианная цена рынка", "Положение относительно рынка", "Отклонение, %"])
        widths.extend([190, 235, 150])
        self.overview_tree.configure(columns=columns, show="headings")
        for column, heading, width in zip(columns, headings, widths):
            self.overview_tree.heading(column, text=heading)
            self.overview_tree.column(
                column,
                width=width,
                minwidth=90,
                stretch=False,
                anchor="w" if "name" in column or "analogue_" in column and "price" not in column else "e",
            )

    def _build_catalog_tab(self) -> None:
        self.catalog_tab.columnconfigure(0, weight=1)
        self.catalog_tab.rowconfigure(2, weight=1)
        header = ttk.Frame(self.catalog_tab)
        header.grid(row=0, column=0, sticky="ew", pady=(10, 8))
        header.columnconfigure(0, weight=1)
        ttk.Label(header, text="Справочник товаров", style="Section.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Button(header, text="Загрузить products.xlsx", style="Accent.TButton", command=self.import_products).grid(
            row=0, column=1, padx=4
        )
        ttk.Button(header, text="Добавить товар", command=self.add_product).grid(row=0, column=2, padx=4)
        ttk.Button(header, text="Изменить выбранный", command=self.edit_product).grid(row=0, column=3, padx=4)
        ttk.Button(header, text="Удалить", command=self.delete_product).grid(row=0, column=4, padx=4)
        ttk.Button(header, text="Очистить справочник", style="Danger.TButton", command=self.clear_catalog).grid(
            row=0, column=5, padx=(12, 4)
        )

        filters = ttk.Frame(self.catalog_tab)
        filters.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        filters.columnconfigure(2, weight=1)
        ttk.Label(filters, text="Поиск:").grid(row=0, column=0, padx=(0, 6))
        search = ttk.Entry(filters, textvariable=self.catalog_search_var, width=30)
        search.grid(row=0, column=1, sticky="w")
        search.bind("<KeyRelease>", lambda _event: self.refresh_catalog())
        self.catalog_count_var = tk.StringVar(value="Показано: 0")
        ttk.Label(filters, textvariable=self.catalog_count_var, style="Muted.TLabel").grid(row=0, column=2, sticky="e")

        self.catalog_tree = self._create_tree(
            self.catalog_tab,
            ["sku", "name", "barcode", "target", "updated"],
            ["Артикул Ozon", "Наименование", "Штрихкод", "Целевая цена", "Обновлено"],
            row=2,
            widths=[170, 480, 210, 170, 190],
        )
        self.catalog_tree.bind("<Double-1>", lambda _event: self.edit_product())

    def _build_history_tab(self) -> None:
        self.history_tab.columnconfigure(0, weight=1)
        self.history_tab.rowconfigure(2, weight=1)
        header = ttk.Frame(self.history_tab)
        header.grid(row=0, column=0, sticky="ew", pady=(10, 8))
        header.columnconfigure(0, weight=1)
        ttk.Label(header, text="История отчетов", style="Section.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(header, text="Год:", style="Muted.TLabel").grid(row=0, column=1, padx=(8, 4))
        self.history_year_combo = ttk.Combobox(
            header,
            textvariable=self.history_year_var,
            state="readonly",
            width=14,
        )
        self.history_year_combo.grid(row=0, column=2, padx=(0, 8))
        self.history_year_combo.bind("<<ComboboxSelected>>", lambda _event: self.refresh_history())
        ttk.Button(header, text="Открыть", command=self.open_history_run).grid(row=0, column=3, padx=4)
        ttk.Button(header, text="Переименовать", command=self.rename_history_run).grid(row=0, column=4, padx=4)
        ttk.Button(header, text="Удалить", command=self.delete_history_runs).grid(row=0, column=5, padx=4)
        ttk.Button(
            header,
            text="Выгрузить выбранные",
            style="Accent.TButton",
            command=self.batch_export_history,
        ).grid(row=0, column=6, padx=(12, 4))

        ttk.Label(
            self.history_tab,
            text="Можно выбрать несколько отчетов через Ctrl/Shift и выгрузить их одной операцией в папку экспорта.",
            style="Muted.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(0, 8))

        self.history_tree = self._create_tree(
            self.history_tab,
            ["number", "name", "created", "source", "items", "analogues", "below", "market", "above", "errors"],
            ["№", "Наименование", "Дата расчета", "Источник", "Товаров", "Аналогов", "Ниже рынка", "На уровне", "Выше рынка", "Ошибок"],
            row=2,
            widths=[65, 310, 175, 310, 95, 100, 115, 115, 115, 90],
            selectmode="extended",
        )
        self.history_tree.bind("<Double-1>", lambda _event: self.open_history_run())

    def _build_settings_tab(self) -> None:
        self.settings_tab.columnconfigure(0, weight=1)
        self.settings_tab.rowconfigure(0, weight=1)
        pane = tk.PanedWindow(
            self.settings_tab,
            orient=tk.VERTICAL,
            borderwidth=0,
            relief=tk.FLAT,
            background=self.colors["window"],
            sashwidth=8,
            sashpad=3,
            showhandle=True,
            handlesize=12,
            handlepad=4,
            opaqueresize=True,
        )
        pane.grid(row=0, column=0, sticky="nsew")
        self.resizable_panes.append(pane)

        upper = ttk.Frame(pane)
        upper.columnconfigure(0, weight=1)
        upper.columnconfigure(1, weight=1)
        pane.add(upper, minsize=390, stretch="never")

        matching = ttk.LabelFrame(upper, text="Настройки подбора аналогов", padding=12)
        matching.grid(row=0, column=0, sticky="nsew", padx=(0, 6), pady=(8, 8))
        matching.columnconfigure(1, weight=1)
        matching.columnconfigure(3, weight=1)

        self._setting_row(matching, 0, 0, "Минимальное общее сходство, %", self.overall_similarity_var)
        self._setting_row(matching, 0, 2, "Материалы не ниже, %", self.material_similarity_var)
        self._setting_row(matching, 1, 0, "Количество аналогов", self.analog_limit_var)
        self._setting_row(matching, 1, 2, "Проверять кандидатов", self.candidate_check_limit_var)
        self._setting_row(matching, 2, 0, "Макс. отклонение габаритов, %", self.max_dimension_difference_var)
        self._setting_row(matching, 2, 2, "Макс. отклонение веса, %", self.max_weight_difference_var)
        self._setting_row(matching, 3, 0, "Минимум сопоставимых габаритов", self.min_dimension_count_var)
        self._setting_row(matching, 3, 2, "Минимальный рейтинг", self.min_rating_var)
        self._setting_row(matching, 4, 0, "Минимум отзывов", self.min_feedbacks_var)
        self._setting_row(matching, 4, 2, "Граница «на уровне рынка», ±%", self.market_tolerance_var)
        self._setting_row(matching, 5, 0, "Вес материалов, %", self.material_weight_var)
        self._setting_row(matching, 5, 2, "Вес габаритов, %", self.dimensions_weight_var)
        self._setting_row(matching, 6, 0, "Вес веса товара, %", self.weight_weight_var)

        ttk.Label(matching, text="Цена для сравнения рынка:").grid(row=6, column=2, sticky="w", padx=(18, 8), pady=5)
        price_combo = ttk.Combobox(
            matching,
            textvariable=self.price_basis_var,
            state="readonly",
            values=list(PRICE_BASIS_LABELS),
            width=25,
        )
        price_combo.grid(row=6, column=3, sticky="ew", pady=5)
        ttk.Checkbutton(
            matching,
            text="Только та же категория Ozon",
            variable=self.strict_category_var,
        ).grid(row=7, column=0, columnspan=2, sticky="w", pady=(8, 3))
        ttk.Checkbutton(
            matching,
            text="Использовать габариты упаковки, если нет габаритов товара",
            variable=self.use_package_dimensions_var,
        ).grid(row=7, column=2, columnspan=2, sticky="w", padx=(18, 0), pady=(8, 3))
        ttk.Label(
            matching,
            text=(
                "Цена не участвует в отборе похожих товаров. После прохождения порогов аналоги "
                "сортируются по сходству, затем по рейтингу и количеству отзывов."
            ),
            style="Muted.TLabel",
            wraplength=650,
        ).grid(row=8, column=0, columnspan=4, sticky="w", pady=(8, 0))

        app = ttk.LabelFrame(upper, text="Приложение и экспорт", padding=12)
        app.grid(row=0, column=1, sticky="nsew", padx=(6, 0), pady=(8, 8))
        app.columnconfigure(1, weight=1)
        ttk.Label(app, text="Тема:").grid(row=0, column=0, sticky="w", padx=(0, 8), pady=5)
        ttk.Combobox(
            app,
            textvariable=self.theme_var,
            state="readonly",
            values=list(THEME_LABELS),
            width=22,
        ).grid(row=0, column=1, sticky="w", pady=5)

        ttk.Label(app, text="Папка экспорта:").grid(row=1, column=0, sticky="w", padx=(0, 8), pady=5)
        export_box = ttk.Frame(app)
        export_box.grid(row=1, column=1, sticky="ew", pady=5)
        export_box.columnconfigure(0, weight=1)
        ttk.Entry(export_box, textvariable=self.export_path_var).grid(row=0, column=0, sticky="ew")
        ttk.Button(export_box, text="Выбрать…", command=self.choose_export_folder).grid(row=0, column=1, padx=(6, 0))
        ttk.Button(export_box, text="Открыть", command=self.open_export_folder).grid(row=0, column=2, padx=(6, 0))

        self._setting_row(app, 2, 0, "Параллельных товаров", self.workers_var, pad=False)
        self._setting_row(app, 3, 0, "Лимит поиска на товар, сек.", self.market_time_limit_var, pad=False)
        self._setting_row(app, 4, 0, "Тайм-аут запроса, сек.", self.timeout_var, pad=False)
        ttk.Label(
            app,
            text=(
                "По умолчанию тема системная. Экспорт выполняется только по кнопке — "
                "автоматические дополнительные Excel-файлы не создаются."
            ),
            style="Muted.TLabel",
            wraplength=500,
        ).grid(row=5, column=0, columnspan=2, sticky="w", pady=(12, 8))
        ttk.Button(app, text="Сохранить настройки", style="Accent.TButton", command=self.save_settings).grid(
            row=6, column=0, columnspan=2, sticky="w", pady=(8, 0)
        )

        exclusions = ttk.Frame(pane)
        exclusions.columnconfigure(0, weight=1)
        exclusions.rowconfigure(1, weight=1)
        pane.add(exclusions, minsize=200, stretch="always")
        exclusion_header = ttk.Frame(exclusions)
        exclusion_header.grid(row=0, column=0, sticky="ew", pady=(8, 8))
        exclusion_header.columnconfigure(0, weight=1)
        ttk.Label(exclusion_header, text="Справочник исключений аналогов", style="Section.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Button(exclusion_header, text="Добавить артикул", style="Accent.TButton", command=self.add_exclusion).grid(
            row=0, column=1, padx=4
        )
        ttk.Button(exclusion_header, text="Удалить выбранный", command=self.remove_exclusion).grid(row=0, column=2, padx=4)
        ttk.Button(exclusion_header, text="Очистить", style="Danger.TButton", command=self.clear_exclusions).grid(
            row=0, column=3, padx=(12, 4)
        )
        self.exclusion_tree = self._create_tree(
            exclusions,
            ["sku", "note", "created"],
            ["Артикул Ozon", "Комментарий", "Добавлен"],
            row=1,
            widths=[190, 760, 190],
        )

    @staticmethod
    def _setting_row(
        parent: ttk.Widget,
        row: int,
        column: int,
        label: str,
        variable: tk.StringVar,
        *,
        pad: bool = True,
    ) -> None:
        padx = (18, 8) if column else (0, 8)
        ttk.Label(parent, text=label + ":").grid(row=row, column=column, sticky="w", padx=padx, pady=5)
        ttk.Entry(parent, textvariable=variable, width=14).grid(
            row=row,
            column=column + 1,
            sticky="ew" if pad else "w",
            pady=5,
        )

    def _create_tree(
        self,
        parent: ttk.Widget,
        columns: list[str],
        headings: list[str],
        *,
        row: int,
        widths: list[int] | None = None,
        selectmode: str = "extended",
    ) -> ttk.Treeview:
        container = ttk.Frame(parent)
        container.grid(row=row, column=0, sticky="nsew")
        container.columnconfigure(0, weight=1)
        container.rowconfigure(0, weight=1)
        tree = ttk.Treeview(container, columns=columns, show="headings", selectmode=selectmode)
        xscroll = ttk.Scrollbar(container, orient="horizontal", command=tree.xview)
        yscroll = ttk.Scrollbar(container, orient="vertical", command=tree.yview)
        tree.configure(xscrollcommand=xscroll.set, yscrollcommand=yscroll.set)
        tree.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="ew")
        widths = widths or [150] * len(columns)
        for column, heading, width in zip(columns, headings, widths):
            tree.heading(column, text=heading)
            tree.column(column, width=width, minwidth=70, stretch=False, anchor="w")
        return tree

    def refresh_all(self) -> None:
        self.refresh_catalog()
        self.refresh_exclusions()
        self.refresh_runs()

    def refresh_catalog(self) -> None:
        if not hasattr(self, "catalog_tree"):
            return
        query = self.catalog_search_var.get().casefold().strip()
        entries = self.storage.list_catalog()
        visible = [
            item for item in entries
            if not query or query in f"{item.sku} {item.name} {item.barcode}".casefold()
        ]
        self.catalog_tree.delete(*self.catalog_tree.get_children())
        for item in visible:
            self.catalog_tree.insert(
                "",
                "end",
                iid=item.sku,
                values=(
                    item.sku,
                    item.name,
                    item.barcode,
                    _money(item.target_price),
                    _display_datetime(item.updated_at),
                ),
            )
        self.catalog_count_var.set(f"Показано: {len(visible)} из {len(entries)}")

    def refresh_exclusions(self) -> None:
        if not hasattr(self, "exclusion_tree"):
            return
        self.exclusion_tree.delete(*self.exclusion_tree.get_children())
        for item in self.storage.list_exclusions():
            self.exclusion_tree.insert(
                "",
                "end",
                iid=item.sku,
                values=(item.sku, item.note, _display_datetime(item.created_at)),
            )

    def refresh_runs(self) -> None:
        runs = self.storage.list_runs()
        self.run_number_by_id = {run.id: index for index, run in enumerate(runs, start=1)}
        self.report_display_to_id.clear()
        values: list[str] = []
        for run in runs:
            display = f"№{self.run_number_by_id[run.id]} · {run.name} · {_display_datetime(run.created_at)}"
            values.append(display)
            self.report_display_to_id[display] = run.id
        self.report_combo["values"] = values
        if runs:
            if self.current_run_id not in {run.id for run in runs}:
                self.current_run_id = runs[-1].id
            display = next(key for key, value in self.report_display_to_id.items() if value == self.current_run_id)
            self.report_var.set(display)
            self.select_run(self.current_run_id)
        else:
            self.current_run_id = None
            self.current_results = []
            self.report_var.set("Нет отчетов")
            self.overview_title_var.set("Нет сохраненных отчетов")
            self.populate_overview()
        self._refresh_history_years(runs)
        self.refresh_history(runs)

    def _refresh_history_years(self, runs: list[RunSummary]) -> None:
        years = sorted({run.created_at[:4] for run in runs if len(run.created_at) >= 4})
        values = ["Все годы", *years]
        self.history_year_combo["values"] = values
        if self.history_year_var.get() not in values:
            self.history_year_var.set("Все годы")

    def refresh_history(self, runs: list[RunSummary] | None = None) -> None:
        runs = runs if runs is not None else self.storage.list_runs()
        year = self.history_year_var.get()
        if year != "Все годы":
            runs = [run for run in runs if run.created_at.startswith(year)]
        self.history_tree.delete(*self.history_tree.get_children())
        for run in runs:
            self.history_tree.insert(
                "",
                "end",
                iid=str(run.id),
                values=(
                    self.run_number_by_id.get(run.id, ""),
                    run.name,
                    _display_datetime(run.created_at),
                    Path(run.source_file).name if run.source_file else "Справочник",
                    run.item_count,
                    run.analogue_count,
                    run.below_count,
                    run.at_count,
                    run.above_count,
                    run.error_count,
                ),
            )

    def select_run(self, run_id: int) -> None:
        self.current_run_id = run_id
        self.current_results = self.storage.load_run(run_id)
        try:
            name = self.storage.run_name(run_id)
        except KeyError:
            name = f"Отчет {run_id}"
        self.overview_title_var.set(f"№{self.run_number_by_id.get(run_id, '—')} · {name}")
        self.populate_overview()
        self.status_var.set(f"Открыт отчет №{self.run_number_by_id.get(run_id, '—')}: {name}")

    def _on_report_selected(self, _event=None) -> None:
        run_id = self.report_display_to_id.get(self.report_var.get())
        if run_id is not None:
            self.select_run(run_id)

    def populate_overview(self) -> None:
        self.overview_tree.delete(*self.overview_tree.get_children())
        self.overview_analogue_meta.clear()
        limit = self._analogue_limit_value()
        self._configure_overview_columns(limit)
        for row_index, result in enumerate(self.current_results, start=1):
            values: list[Any] = [
                result.sku,
                result.ozon_name or result.input_name,
                _money(result.comparison_price or result.current_price),
            ]
            meta: list[tuple[str, str]] = []
            for position in range(limit):
                if position < len(result.analogues):
                    analogue = result.analogues[position]
                    values.extend(
                        [
                            f"↗ {analogue.name or analogue.sku}",
                            _money(analogue.comparison_price or analogue.current_price),
                        ]
                    )
                    meta.append((analogue.sku, analogue.url))
                else:
                    values.extend(["", ""])
                    meta.append(("", ""))
            values.extend(
                [
                    _money(result.analog_median_price),
                    result.market_position or ("Ошибка" if result.status == "error" else "—"),
                    _signed_percent(result.price_vs_market_percent),
                ]
            )
            iid = f"r{row_index}"
            tag = "above" if result.market_position == "Выше рынка" else "below" if result.market_position == "Ниже рынка" else ""
            self.overview_tree.insert("", "end", iid=iid, values=values, tags=(tag,) if tag else ())
            self.overview_analogue_meta[iid] = meta
        self.overview_count_var.set(f"Товаров: {len(self.current_results)}")

    def import_products(self) -> None:
        initial = self.current_source_file if self.current_source_file else str(self.data_dir / "products.xlsx")
        path = filedialog.askopenfilename(
            title="Загрузить products.xlsx",
            initialdir=str(Path(initial).parent),
            filetypes=[("Книги Excel", "*.xlsx")],
            parent=self.root,
        )
        if not path:
            return
        try:
            products = ExcelLoader(path).load()
            if not products:
                raise ValueError("В файле нет заполненных артикулов Ozon.")
            count = self.storage.upsert_products(products)
            self.current_source_file = path
            self.storage.set_setting("last_input_path", path)
            self.refresh_catalog()
            self.status_var.set(f"Загружено/обновлено товаров в справочнике: {count}")
            messagebox.showinfo(
                "Справочник обновлен",
                f"Загружено или обновлено товаров: {count}.\n\nАнализ запускается по всему текущему справочнику.",
                parent=self.root,
            )
        except (FileNotFoundError, OSError, ValueError) as error:
            messagebox.showerror("Загрузка products.xlsx", str(error), parent=self.root)

    def add_product(self) -> None:
        product = self._product_dialog(None)
        if product is None:
            return
        self.storage.save_product(product)
        self.refresh_catalog()

    def edit_product(self) -> None:
        selection = self.catalog_tree.selection()
        if not selection:
            messagebox.showinfo("Справочник", "Выберите товар в таблице.", parent=self.root)
            return
        entry = next((item for item in self.storage.list_catalog() if item.sku == selection[0]), None)
        if entry is None:
            return
        product = Product(entry.sku, entry.name, entry.barcode, entry.target_price)
        updated = self._product_dialog(product)
        if updated is None:
            return
        self.storage.save_product(updated)
        self.refresh_catalog()
        self.catalog_tree.selection_set(updated.sku)

    def delete_product(self) -> None:
        selection = self.catalog_tree.selection()
        if not selection:
            messagebox.showinfo("Справочник", "Выберите товар в таблице.", parent=self.root)
            return
        if messagebox.askyesno("Удалить товар", f"Удалить артикул {selection[0]} из справочника?", parent=self.root):
            self.storage.delete_product(selection[0])
            self.refresh_catalog()

    def clear_catalog(self) -> None:
        if not messagebox.askyesno(
            "Очистить справочник",
            "Удалить все товары из справочника? История уже сохраненных отчетов останется.",
            parent=self.root,
        ):
            return
        self.storage.clear_products()
        self.refresh_catalog()

    def _product_dialog(self, product: Product | None) -> Product | None:
        dialog = tk.Toplevel(self.root)
        dialog.title("Изменить товар" if product else "Добавить товар")
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.resizable(False, False)
        frame = ttk.Frame(dialog, padding=16)
        frame.grid(row=0, column=0, sticky="nsew")
        frame.columnconfigure(1, weight=1)
        sku_var = tk.StringVar(value=product.sku if product else "")
        name_var = tk.StringVar(value=product.name if product else "")
        barcode_var = tk.StringVar(value=product.barcode if product else "")
        target_var = tk.StringVar(value="" if not product or product.target_price is None else f"{product.target_price:g}")
        fields = [
            ("Артикул Ozon", sku_var),
            ("Наименование", name_var),
            ("Штрихкод", barcode_var),
            ("Целевая цена", target_var),
        ]
        for row, (label, variable) in enumerate(fields):
            ttk.Label(frame, text=label + ":").grid(row=row, column=0, sticky="w", padx=(0, 10), pady=6)
            entry = ttk.Entry(frame, textvariable=variable, width=48)
            entry.grid(row=row, column=1, sticky="ew", pady=6)
            if product is not None and row == 0:
                entry.configure(state="disabled")
        result: list[Product] = []

        def save() -> None:
            sku = sku_var.get().strip()
            if not sku.isdigit():
                messagebox.showerror("Товар", "Артикул Ozon должен состоять из цифр.", parent=dialog)
                return
            target: float | None = None
            if target_var.get().strip():
                try:
                    target = float(target_var.get().replace(" ", "").replace(",", "."))
                    if target <= 0:
                        raise ValueError
                except ValueError:
                    messagebox.showerror("Товар", "Целевая цена должна быть положительным числом.", parent=dialog)
                    return
            result.append(Product(sku, name_var.get().strip(), barcode_var.get().strip(), target))
            dialog.destroy()

        buttons = ttk.Frame(frame)
        buttons.grid(row=len(fields), column=0, columnspan=2, sticky="e", pady=(12, 0))
        ttk.Button(buttons, text="Отмена", command=dialog.destroy).grid(row=0, column=0, padx=4)
        ttk.Button(buttons, text="Сохранить", style="Accent.TButton", command=save).grid(row=0, column=1, padx=4)
        dialog.wait_window()
        return result[0] if result else None

    def start_analysis(self) -> None:
        if self.worker is not None and self.worker.is_alive():
            return
        products = self.storage.catalog_products()
        if not products:
            messagebox.showinfo(
                "Анализ",
                "Справочник пуст. Сначала загрузите products.xlsx или добавьте товары вручную.",
                parent=self.root,
            )
            return
        try:
            rules = self.storage.matching_settings()
            analog_limit = self._integer(self.storage.get_setting("analog_limit", "5"), "Количество аналогов", 1, 10)
            candidate_limit = self._integer(
                self.storage.get_setting("candidate_check_limit", "20"),
                "Проверять кандидатов",
                1,
                30,
            )
            market_time_limit = self._positive(
                self.storage.get_setting("market_time_limit", "60"),
                "Лимит поиска",
            )
            timeout = self._positive(self.storage.get_setting("timeout", "15"), "Тайм-аут")
            workers = self._integer(self.storage.get_setting("workers", "2"), "Потоки", 1, 16)
        except ValueError as error:
            messagebox.showerror("Настройки", str(error), parent=self.root)
            self.notebook.select(self.settings_tab)
            return

        self._clear_log()
        self.start_button.configure(state="disabled")
        self.status_var.set(f"Выполняется анализ: {len(products)} товаров…")
        client = ConfiguredOzonClient(
            matching_settings=rules,
            excluded_skus=self.storage.exclusion_skus(),
            timeout=timeout,
            market_time_limit=market_time_limit,
            candidate_check_limit=candidate_limit,
        )
        self.worker = Thread(
            target=self._run_analysis,
            args=(products, client, rules, analog_limit, workers),
            daemon=True,
        )
        self.worker.start()
        self.notebook.select(self.overview_tab)

    def _run_analysis(
        self,
        products: list[Product],
        client: ConfiguredOzonClient,
        rules: MatchingSettings,
        analog_limit: int,
        workers: int,
    ) -> None:
        writer = QueueWriter(self.events)
        try:
            with redirect_stdout(writer), redirect_stderr(writer):
                print(f"OZ Price Analyzer {APP_VERSION}")
                print(f"Товаров в справочнике: {len(products)}")
                print(
                    f"Порог сходства: {rules.overall_similarity:.0%}; "
                    f"материалы: {rules.material_similarity:.0%}; аналогов: {analog_limit}."
                )

                def progress(completed: int, total: int, product: Product) -> None:
                    print(f"[{completed}/{total}] Обработан артикул {product.sku}", flush=True)

                def stage(product: Product, message: str) -> None:
                    print(f"[{product.sku}] {message}", flush=True)

                results = analyze_products(
                    products,
                    client,
                    progress=progress,
                    workers=workers,
                    market_enabled=True,
                    analog_limit=analog_limit,
                    stage_progress=stage,
                )
                run_id = self.storage.save_run(
                    results,
                    source_file=self.current_source_file,
                    settings=rules,
                )
                print(f"Отчет сохранен в локальной истории: №{run_id}")
            self.events.put(("done", run_id))
        except Exception as error:
            self.events.put(("error", str(error)))

    def export_current_run(self) -> None:
        if self.current_run_id is None or not self.current_results:
            messagebox.showinfo("Экспорт", "Нет открытого отчета для экспорта.", parent=self.root)
            return
        folder = self._export_folder()
        name = self.storage.run_name(self.current_run_id)
        path = folder / suggested_export_name(name)
        try:
            written = export_results(
                self.current_results,
                path,
                analogue_limit=self._analogue_limit_value(),
            )
            self.status_var.set(f"Экспортирован отчет: {written}")
            messagebox.showinfo("Экспорт завершен", f"Файл сохранен:\n{written}", parent=self.root)
        except OSError as error:
            messagebox.showerror("Экспорт", str(error), parent=self.root)

    def batch_export_history(self) -> None:
        selection = self.history_tree.selection()
        if not selection:
            messagebox.showinfo("Пакетный экспорт", "Выберите один или несколько отчетов.", parent=self.root)
            return
        folder = self._export_folder()
        written: list[Path] = []
        try:
            for iid in selection:
                run_id = int(iid)
                results = self.storage.load_run(run_id)
                name = self.storage.run_name(run_id)
                number = self.run_number_by_id.get(run_id, run_id)
                path = folder / f"№{number}_{suggested_export_name(name)}"
                written.append(
                    export_results(results, path, analogue_limit=self._analogue_limit_value())
                )
        except OSError as error:
            messagebox.showerror("Пакетный экспорт", str(error), parent=self.root)
            return
        self.status_var.set(f"Экспортировано отчетов: {len(written)}")
        messagebox.showinfo(
            "Пакетный экспорт завершен",
            f"Экспортировано отчетов: {len(written)}\nПапка:\n{folder}",
            parent=self.root,
        )

    def open_history_run(self) -> None:
        selection = self.history_tree.selection()
        if not selection:
            messagebox.showinfo("История", "Выберите отчет.", parent=self.root)
            return
        run_id = int(selection[0])
        self.current_run_id = run_id
        self.refresh_runs()
        self.notebook.select(self.overview_tab)

    def rename_history_run(self) -> None:
        selection = self.history_tree.selection()
        if len(selection) != 1:
            messagebox.showinfo("История", "Выберите один отчет для переименования.", parent=self.root)
            return
        run_id = int(selection[0])
        current = self.storage.run_name(run_id)
        name = simpledialog.askstring("Переименовать отчет", "Новое название:", initialvalue=current, parent=self.root)
        if name is None:
            return
        try:
            self.storage.rename_run(run_id, name)
            self.current_run_id = run_id
            self.refresh_runs()
        except ValueError as error:
            messagebox.showerror("История", str(error), parent=self.root)

    def delete_history_runs(self) -> None:
        selection = self.history_tree.selection()
        if not selection:
            messagebox.showinfo("История", "Выберите отчет или несколько отчетов.", parent=self.root)
            return
        if not messagebox.askyesno(
            "Удалить отчеты",
            f"Удалить выбранные отчеты: {len(selection)}? Это не удалит товары из справочника.",
            parent=self.root,
        ):
            return
        for iid in selection:
            self.storage.delete_run(int(iid))
        if self.current_run_id is not None and str(self.current_run_id) in selection:
            self.current_run_id = None
        self.refresh_runs()

    def add_exclusion(self) -> None:
        sku = simpledialog.askstring("Исключить аналог", "Артикул Ozon:", parent=self.root)
        if not sku:
            return
        note = simpledialog.askstring("Исключить аналог", "Комментарий (необязательно):", parent=self.root) or ""
        try:
            self.storage.add_exclusion(sku, note)
            self.refresh_exclusions()
        except ValueError as error:
            messagebox.showerror("Исключения", str(error), parent=self.root)

    def remove_exclusion(self) -> None:
        selection = self.exclusion_tree.selection()
        if not selection:
            messagebox.showinfo("Исключения", "Выберите артикул.", parent=self.root)
            return
        for sku in selection:
            self.storage.remove_exclusion(sku)
        self.refresh_exclusions()

    def clear_exclusions(self) -> None:
        if messagebox.askyesno("Очистить исключения", "Удалить весь справочник исключений?", parent=self.root):
            self.storage.clear_exclusions()
            self.refresh_exclusions()

    def _show_overview_context_menu(self, event: tk.Event) -> None:
        row_id = self.overview_tree.identify_row(event.y)
        column = self.overview_tree.identify_column(event.x)
        if not row_id or not column:
            return
        index = int(column.lstrip("#"))
        if index < 4 or (index - 4) % 2 != 0:
            return
        analogue_index = (index - 4) // 2
        meta = self.overview_analogue_meta.get(row_id, [])
        if analogue_index >= len(meta) or not meta[analogue_index][0]:
            return
        sku, url = meta[analogue_index]
        values = self.overview_tree.item(row_id, "values")
        name = str(values[index - 1]) if len(values) >= index else sku
        self.context_analogue = (sku, url, name.replace("↗", "").strip())
        self.overview_context_menu.tk_popup(event.x_root, event.y_root)

    def add_context_analogue_to_exclusions(self) -> None:
        if self.context_analogue is None:
            return
        sku, _url, name = self.context_analogue
        note = f"Исключено из обзора: {name}" if name else "Исключено из обзора"
        self.storage.add_exclusion(sku, note)
        self.refresh_exclusions()
        self.status_var.set(f"Артикул {sku} добавлен в исключения для следующих запусков")

    def _open_overview_link(self, event: tk.Event) -> None:
        row_id = self.overview_tree.identify_row(event.y)
        column = self.overview_tree.identify_column(event.x)
        if not row_id or not column:
            return
        index = int(column.lstrip("#"))
        if index == 2:
            try:
                row_number = int(row_id.lstrip("r")) - 1
                url = self.current_results[row_number].url
            except (ValueError, IndexError):
                return
            if url:
                webbrowser.open(url)
            return
        if index < 4 or (index - 4) % 2 != 0:
            return
        analogue_index = (index - 4) // 2
        meta = self.overview_analogue_meta.get(row_id, [])
        if analogue_index < len(meta) and meta[analogue_index][1]:
            webbrowser.open(meta[analogue_index][1])

    def save_settings(self) -> None:
        try:
            rules = MatchingSettings(
                overall_similarity=self._percentage(self.overall_similarity_var.get(), "Общее сходство"),
                material_similarity=self._percentage(self.material_similarity_var.get(), "Сходство материалов"),
                max_dimension_difference=self._percentage(self.max_dimension_difference_var.get(), "Отклонение габаритов"),
                max_weight_difference=self._percentage(self.max_weight_difference_var.get(), "Отклонение веса"),
                material_weight=self._percentage(self.material_weight_var.get(), "Вес материалов"),
                dimensions_weight=self._percentage(self.dimensions_weight_var.get(), "Вес габаритов"),
                weight_weight=self._percentage(self.weight_weight_var.get(), "Вес веса товара"),
                strict_category=self.strict_category_var.get(),
                min_rating=self._bounded_float(self.min_rating_var.get(), "Минимальный рейтинг", 0, 5),
                min_feedbacks=self._integer(self.min_feedbacks_var.get(), "Минимум отзывов", 0, 10_000_000),
                min_dimension_count=self._integer(self.min_dimension_count_var.get(), "Сопоставимые габариты", 1, 3),
                use_package_dimensions=self.use_package_dimensions_var.get(),
                price_basis=PRICE_BASIS_LABELS[self.price_basis_var.get()],
                market_tolerance=self._percentage(self.market_tolerance_var.get(), "Граница рынка"),
            )
            rules.validate()
            analog_limit = self._integer(self.analog_limit_var.get(), "Количество аналогов", 1, 10)
            candidate_limit = self._integer(self.candidate_check_limit_var.get(), "Проверять кандидатов", 1, 30)
            workers = self._integer(self.workers_var.get(), "Параллельных товаров", 1, 16)
            market_limit = self._positive(self.market_time_limit_var.get(), "Лимит поиска")
            timeout = self._positive(self.timeout_var.get(), "Тайм-аут")
            export_path = Path(self.export_path_var.get().strip()).expanduser()
            export_path.mkdir(parents=True, exist_ok=True)
            theme_value = THEME_LABELS[self.theme_var.get()]
        except (KeyError, OSError, ValueError) as error:
            messagebox.showerror("Настройки", str(error), parent=self.root)
            return

        self.storage.set_settings(
            {
                "overall_similarity": str(rules.overall_similarity),
                "material_similarity": str(rules.material_similarity),
                "max_dimension_difference": str(rules.max_dimension_difference),
                "max_weight_difference": str(rules.max_weight_difference),
                "material_weight": str(rules.material_weight),
                "dimensions_weight": str(rules.dimensions_weight),
                "weight_weight": str(rules.weight_weight),
                "strict_category": "1" if rules.strict_category else "0",
                "min_rating": str(rules.min_rating),
                "min_feedbacks": str(rules.min_feedbacks),
                "min_dimension_count": str(rules.min_dimension_count),
                "use_package_dimensions": "1" if rules.use_package_dimensions else "0",
                "price_basis": rules.price_basis,
                "market_tolerance": str(rules.market_tolerance),
                "analog_limit": str(analog_limit),
                "candidate_check_limit": str(candidate_limit),
                "workers": str(workers),
                "market_time_limit": str(market_limit),
                "timeout": str(timeout),
                "export_path": str(export_path),
                "theme": theme_value,
            }
        )
        self.colors = apply_theme(self.root, theme_value)
        self._style_log()
        for pane in self.resizable_panes:
            pane.configure(background=self.colors["window"])
        self.overview_tree.tag_configure("above", foreground=self.colors["negative"])
        self.overview_tree.tag_configure("below", foreground=self.colors["positive"])
        self._configure_overview_columns(analog_limit)
        self.populate_overview()
        self.status_var.set("Настройки сохранены")
        messagebox.showinfo("Настройки", "Настройки сохранены и будут применены к следующему анализу.", parent=self.root)

    def choose_export_folder(self) -> None:
        path = filedialog.askdirectory(
            title="Папка для экспорта Excel",
            initialdir=self.export_path_var.get() or str(self.base_dir),
            parent=self.root,
        )
        if path:
            self.export_path_var.set(path)

    def open_export_folder(self) -> None:
        self._open_path(self._export_folder())

    def _export_folder(self) -> Path:
        folder = Path(self.storage.get_setting("export_path", self.export_path_var.get())).expanduser()
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    def _poll(self) -> None:
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "log":
                    self._append_log(str(payload))
                elif kind == "done":
                    self.start_button.configure(state="normal")
                    self.current_run_id = int(payload)
                    self.refresh_runs()
                    self.status_var.set("Анализ завершен, отчет сохранен в истории")
                    messagebox.showinfo(
                        "Анализ завершен",
                        "Результат сохранен во внутренней истории. Для Excel используйте кнопку «Экспорт в Excel».",
                        parent=self.root,
                    )
                elif kind == "error":
                    self.start_button.configure(state="normal")
                    self.status_var.set("Ошибка анализа")
                    self._append_log(f"\nОшибка: {payload}\n")
                    messagebox.showerror("Ошибка анализа", str(payload), parent=self.root)
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

    def _style_log(self) -> None:
        if hasattr(self, "log"):
            self.log.configure(
                background=self.colors["surface"],
                foreground=self.colors["text"],
                insertbackground=self.colors["text"],
                selectbackground=self.colors["selection"],
            )

    def _analogue_limit_value(self) -> int:
        try:
            return self._integer(self.storage.get_setting("analog_limit", self.analog_limit_var.get()), "Аналогов", 1, 10)
        except ValueError:
            return 5

    @staticmethod
    def _percentage(value: str, label: str) -> float:
        number = DesktopApplication._bounded_float(value, label, 0, 100)
        return number / 100

    @staticmethod
    def _bounded_float(value: str, label: str, minimum: float, maximum: float) -> float:
        try:
            number = float(value.replace(" ", "").replace(",", "."))
        except ValueError as error:
            raise ValueError(f"«{label}» должно быть числом.") from error
        if number < minimum or number > maximum:
            raise ValueError(f"«{label}» должно быть от {minimum:g} до {maximum:g}.")
        return number

    @staticmethod
    def _integer(value: str, label: str, minimum: int, maximum: int) -> int:
        try:
            number = int(float(value.replace(" ", "").replace(",", ".")))
        except ValueError as error:
            raise ValueError(f"«{label}» должно быть целым числом.") from error
        if not minimum <= number <= maximum:
            raise ValueError(f"«{label}» должно быть от {minimum} до {maximum}.")
        return number

    @staticmethod
    def _positive(value: str, label: str) -> float:
        number = DesktopApplication._bounded_float(value, label, 0.01, 100000)
        return number

    @staticmethod
    def _open_path(path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True)
        try:
            if os.name == "nt":
                os.startfile(path)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path)])
        except OSError as error:
            messagebox.showerror("Открыть папку", str(error))

    def show_about(self) -> None:
        rules = self.storage.matching_settings()
        messagebox.showinfo(
            "О программе",
            (
                f"OZ Price Analyzer {APP_VERSION}\n\n"
                "Локальный анализ публичных карточек Ozon без Seller API-токена.\n"
                f"Текущий порог сходства: {rules.overall_similarity:.0%}.\n"
                "Справочник, исключения, настройки и история отчетов хранятся локально в SQLite."
            ),
            parent=self.root,
        )

    def _close(self) -> None:
        if self.worker is not None and self.worker.is_alive():
            if not messagebox.askyesno(
                "Анализ выполняется",
                "Закрыть приложение? Текущий анализ будет прерван.",
                parent=self.root,
            ):
                return
        self.root.destroy()


def _money(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:,.2f} ₽".replace(",", " ")


def _signed_percent(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:+.1%}"


def _percent_number(value: float) -> str:
    return f"{value * 100:g}"


def _display_datetime(value: str) -> str:
    try:
        return datetime.fromisoformat(value).strftime("%d.%m.%Y %H:%M")
    except ValueError:
        return value


def main() -> None:
    root = tk.Tk()
    DesktopApplication(root)
    root.mainloop()
