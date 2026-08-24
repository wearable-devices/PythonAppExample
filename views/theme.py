"""Dark theme palette and ttk styling for the Mudra test app."""

import tkinter as tk
from tkinter import ttk

# Backgrounds
BG = '#000000'
BG_SURFACE = '#141414'
BG_CARD = '#1c1c1c'
BG_INPUT = '#252525'
BG_SELECTED = '#1a3a5c'

# Borders & rails
BORDER = '#3a3a3a'
RAIL = '#404040'

# Text
TEXT = '#e8e8e8'
TEXT_DIM = '#9a9a9a'
TEXT_MUTED = '#666666'

# Output / data fields
OUTPUT_BG = '#1e2a33'
OUTPUT_FG = '#b8d4e8'

# Navigation pad
NAV_CANVAS_BG = '#1a2430'
NAV_GRID = '#2d3d4d'
NAV_DOT = '#4fc3f7'

# Status
SUCCESS = '#4caf50'
WARNING = '#ffc107'
ERROR = '#f44336'
INFO = '#64b5f6'
ACCENT = '#4fc3f7'


def configure_centered_tabs(style: ttk.Style):
    """Notebook-like tab buttons for the centered tab bar."""
    style.configure(
        'AppTab.TButton',
        background=BG_SURFACE,
        foreground=TEXT_DIM,
        padding=[14, 7],
        borderwidth=1,
        relief='flat',
    )
    style.configure(
        'AppTabSelected.TButton',
        background=BG_CARD,
        foreground=TEXT,
        padding=[14, 7],
        borderwidth=1,
        relief='flat',
    )
    style.map(
        'AppTab.TButton',
        background=[('active', BG_INPUT)],
        foreground=[('active', TEXT)],
    )
    style.map(
        'AppTabSelected.TButton',
        background=[('active', BG_CARD)],
        foreground=[('active', TEXT)],
    )


def apply_dark_theme(root: tk.Tk, style: ttk.Style):
    """Apply black theme to the root window and ttk widgets."""
    root.configure(bg=BG)

    if style.theme_use() not in ('clam', 'alt', 'default'):
        style.theme_use('clam')

    style.configure('.', background=BG, foreground=TEXT)
    style.configure('TFrame', background=BG)
    style.configure('TLabel', background=BG, foreground=TEXT)
    style.configure('TButton', background=BG_CARD, foreground=TEXT, bordercolor=BORDER)
    style.map(
        'TButton',
        background=[('active', BG_INPUT), ('disabled', BG_SURFACE)],
        foreground=[('disabled', TEXT_MUTED)],
    )
    style.configure('TEntry', fieldbackground=BG_INPUT, foreground=TEXT, insertcolor=TEXT, bordercolor=BORDER)
    style.configure(
        'TCombobox',
        fieldbackground=BG_INPUT,
        foreground=TEXT,
        background=BG_CARD,
        arrowcolor=TEXT,
        bordercolor=BORDER,
    )
    style.map('TCombobox', fieldbackground=[('readonly', BG_INPUT)])
    style.configure('TLabelframe', background=BG, foreground=TEXT, bordercolor=BORDER)
    style.configure('TLabelframe.Label', background=BG, foreground=TEXT_DIM)
    configure_centered_tabs(style)
    style.configure('TCheckbutton', background=BG_CARD, foreground=TEXT)
    style.map('TCheckbutton', background=[('active', BG_CARD)])
    style.configure('TRadiobutton', background=BG_CARD, foreground=TEXT)
    style.map('TRadiobutton', background=[('active', BG_CARD)])
    style.configure('TProgressbar', background=ACCENT, troughcolor=BG_INPUT, bordercolor=BORDER)
    style.configure('TSeparator', background=BORDER)
    style.configure('Vertical.TScrollbar', background=BG_SURFACE, troughcolor=BG, bordercolor=BORDER)
