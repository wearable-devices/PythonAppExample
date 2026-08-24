import tkinter as tk

from views.theme import BG_CARD, BORDER, SUCCESS, TEXT, TEXT_MUTED

BOX_SIZE = 14


class ToggleSwitch(tk.Frame):
    """Compact Off/On toggle with a green tick when enabled."""

    def __init__(self, parent, on_change, *, bg=BG_CARD, width=8):
        super().__init__(parent, bg=bg)
        self._on_change = on_change
        self._suppress = False
        self._interactive = True
        self._enabled = False
        self._bg = bg

        self._indicator = tk.Canvas(
            self,
            width=BOX_SIZE,
            height=BOX_SIZE,
            bg=bg,
            highlightthickness=0,
            borderwidth=0,
            relief='flat',
            cursor='',
        )
        self._indicator.pack(side=tk.LEFT, padx=(0, 4))
        self._indicator.create_rectangle(
            0, 0, BOX_SIZE - 1, BOX_SIZE - 1,
            outline=BORDER,
            fill='#ffffff',
            width=1,
        )
        self._tick_id = self._indicator.create_text(
            BOX_SIZE // 2,
            BOX_SIZE // 2,
            text='',
            fill=SUCCESS,
            font=('Segoe UI', 8, 'bold'),
        )

        self._text = tk.Label(
            self,
            text='Off',
            font=('Segoe UI', 9),
            bg=bg,
            fg=TEXT,
            width=width,
            anchor='w',
            cursor='',
        )
        self._text.pack(side=tk.LEFT)

        for widget in (self, self._indicator, self._text):
            widget.bind('<Button-1>', self._toggle_click)

        self._render(False)

    def _toggle_click(self, _event=None):
        if not self._interactive or self._suppress:
            return
        self._render(not self._enabled)
        self._on_change(self._enabled)

    def _render(self, enabled: bool):
        self._enabled = enabled
        if enabled:
            tick_color = SUCCESS if self._interactive else TEXT_MUTED
            self._indicator.itemconfig(self._tick_id, text='✓', fill=tick_color)
            self._text.config(
                text='On',
                fg=TEXT if self._interactive else TEXT_MUTED,
            )
        else:
            self._indicator.itemconfig(self._tick_id, text='')
            self._text.config(
                text='Off',
                fg=TEXT if self._interactive else TEXT_MUTED,
            )

    def set(self, enabled: bool):
        self._suppress = True
        self._render(enabled)
        self._suppress = False

    def set_enabled(self, enabled: bool):
        self._interactive = enabled
        self._render(self._enabled)
