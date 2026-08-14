"""Windows-11-like light, dark and system themes."""

from __future__ import annotations

import sys
import tkinter as tk
from tkinter import ttk

PALETTES = {
    "dark": {
        "window": "#202020",
        "surface": "#2B2B2B",
        "surface_alt": "#323232",
        "hover": "#3A3A3A",
        "border": "#454545",
        "text": "#F5F5F5",
        "muted": "#B8B8B8",
        "accent": "#60CDFF",
        "accent_text": "#102A36",
        "positive": "#6CCB5F",
        "warning": "#FCE100",
        "negative": "#F1707B",
        "selection": "#094771",
    },
    "light": {
        "window": "#F3F3F3",
        "surface": "#FBFBFB",
        "surface_alt": "#FFFFFF",
        "hover": "#EAEAEA",
        "border": "#D2D2D2",
        "text": "#1B1B1B",
        "muted": "#5F5F5F",
        "accent": "#0067C0",
        "accent_text": "#FFFFFF",
        "positive": "#0F7B0F",
        "warning": "#8A6D00",
        "negative": "#C42B1C",
        "selection": "#C7E0F4",
    },
}


def resolve_theme(preference: str) -> str:
    if preference in {"dark", "light"}:
        return preference
    if sys.platform == "win32":
        try:
            import winreg

            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
            )
            value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
            return "light" if value else "dark"
        except OSError:
            pass
    return "light"


def apply_theme(root: tk.Misc, preference: str) -> dict[str, str]:
    colors = PALETTES[resolve_theme(preference)]
    style = ttk.Style(root)
    style.theme_use("clam")
    root.configure(background=colors["window"])
    default_font = ("Segoe UI", 10)
    heading_font = ("Segoe UI Semibold", 10)

    style.configure(".", font=default_font, background=colors["window"], foreground=colors["text"])
    style.configure("TFrame", background=colors["window"])
    style.configure("Card.TFrame", background=colors["surface"], relief="flat")
    style.configure("TLabel", background=colors["window"], foreground=colors["text"])
    style.configure("Muted.TLabel", background=colors["window"], foreground=colors["muted"])
    style.configure("Warning.TLabel", background=colors["window"], foreground=colors["warning"])
    style.configure("Title.TLabel", font=("Segoe UI Semibold", 20), background=colors["window"], foreground=colors["text"])
    style.configure("Section.TLabel", font=("Segoe UI Semibold", 13), background=colors["window"], foreground=colors["text"])

    style.configure(
        "TButton",
        padding=(14, 8),
        background=colors["surface_alt"],
        foreground=colors["text"],
        bordercolor=colors["border"],
    )
    style.map("TButton", background=[("active", colors["hover"]), ("pressed", colors["selection"])])
    style.configure(
        "Accent.TButton",
        padding=(16, 9),
        background=colors["accent"],
        foreground=colors["accent_text"],
        bordercolor=colors["accent"],
        font=heading_font,
    )
    style.configure("Danger.TButton", foreground=colors["negative"])
    style.configure(
        "TEntry",
        fieldbackground=colors["surface_alt"],
        foreground=colors["text"],
        insertcolor=colors["text"],
        bordercolor=colors["border"],
        padding=7,
    )
    style.configure(
        "TCombobox",
        fieldbackground=colors["surface_alt"],
        background=colors["surface_alt"],
        foreground=colors["text"],
        arrowcolor=colors["text"],
        bordercolor=colors["border"],
        padding=6,
    )
    style.map(
        "TCombobox",
        fieldbackground=[("readonly", colors["surface_alt"])],
        foreground=[("readonly", colors["text"])],
        selectbackground=[("readonly", colors["surface_alt"])],
        selectforeground=[("readonly", colors["text"])],
    )
    style.configure("TSpinbox", fieldbackground=colors["surface_alt"], foreground=colors["text"], padding=6)
    style.configure("TCheckbutton", background=colors["window"], foreground=colors["text"], padding=4)
    style.configure("TNotebook", background=colors["window"], borderwidth=0, tabmargins=(0, 8, 0, 0))
    style.configure(
        "TNotebook.Tab",
        background=colors["window"],
        foreground=colors["muted"],
        padding=(16, 10),
        borderwidth=0,
        font=heading_font,
    )
    style.map(
        "TNotebook.Tab",
        background=[("selected", colors["surface"]), ("active", colors["hover"])],
        foreground=[("selected", colors["text"]), ("active", colors["text"])],
    )
    style.configure(
        "Treeview",
        background=colors["surface"],
        fieldbackground=colors["surface"],
        foreground=colors["text"],
        bordercolor=colors["border"],
        rowheight=30,
    )
    style.map(
        "Treeview",
        background=[("selected", colors["selection"])],
        foreground=[("selected", colors["text"])],
    )
    style.configure(
        "Treeview.Heading",
        background=colors["surface_alt"],
        foreground=colors["text"],
        bordercolor=colors["border"],
        font=heading_font,
        padding=(8, 8),
    )
    style.configure("TLabelframe", background=colors["window"], foreground=colors["text"], bordercolor=colors["border"])
    style.configure("TLabelframe.Label", background=colors["window"], foreground=colors["text"], font=heading_font)
    return colors
