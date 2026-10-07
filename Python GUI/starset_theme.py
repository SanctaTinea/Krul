"""Application theme, palette, stylesheet, and layout constants for Starset."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QPushButton

# Layout dimensions (pixels). Keep geometry tuning in one place.
COMMAND_GROUP_SPACING = 0
COMMAND_FORM_SPACING = 6
COMMAND_SECTION_SPACING = 4
COMMAND_EXECUTE_TOP_SPACING = 20
COMMAND_GRID_MIN_CARD_WIDTH = 300
COMMAND_GRID_MIN_GROUP_WIDTH = 360
COMMAND_GRID_HORIZONTAL_SPACING = 12
COMMAND_GRID_VERTICAL_SPACING = 12
COMMAND_GRID_MAX_COLUMNS = 3
IO_FILTER_FIELD_SPACING = 8
IO_SECTION_TOP_MARGIN = 15
IO_OUTPUT_ACTIONS_TOP_SPACING = 12
PIN_GRID_VERTICAL_SPACING = 2
PIN_GRID_HORIZONTAL_SPACING = 15
PIN_CARD_VERTICAL_MARGIN = 3

# Collapsible header arrow sizing. "size" is a scale factor for the arrow
# icon only; the header text keeps the normal font size. Group headers use
# a half-size arrow.
COLLAPSE_HEADER_SCALE = 1.0
COLLAPSE_HEADER_GROUP_SCALE = 0.5
COLLAPSE_HEADER_ARROW = 12


# ---------------------------------------------------------------------------
# Centralized color themes
# Change only DEFAULT_THEME to select the theme used at startup.
# ---------------------------------------------------------------------------
DEFAULT_THEME = "dark"

THEMES: dict[str, dict[str, str]] = {
    "light": {
        "background": "#FFFFFF",
        "surface": "#F7F8FA",
        "terminal_background": "#F7F8FA",
        "default_input_background": "#FFFFFF",
        "card_color": "#F7F8FA",
        "white": "#1F2937",
        "red": "#FF0000",
        "button_text": "#FFFFFF",
        "surface_alt": "#F1F3F5",
        "input_background": "#FFFFFF",
        "border": "#DDE1E6",
        "border_strong": "#C8CED6",
        "text": "#1F2937",
        "text_secondary": "#6B7280",
        "text_disabled": "#9CA3AF",
        "accent": "#2563EB",
        "accent_hover": "#1D4ED8",
        "accent_pressed": "#1E40AF",
        "accent_text": "#FFFFFF",
        "success": "#22C55E",
        "success_hover": "#28E06A",
        "success_pressed": "#16A34A",
        "success_border": "#15803D",
        "success_soft": "#D8F5DF",
        "success_text": "#16752D",
        "danger": "#EF4444",
        "danger_hover": "#FF5555",
        "danger_pressed": "#D92D2D",
        "danger_border": "#B91C1C",
        "danger_soft": "#F5DDDD",
        "danger_text": "#8B2020",
        "inactive": "#5F5AE2",
        "inactive_border": "#4540C9",
        "pending": "#C4C92E",
        "pending_border": "#929617",
        "pending_text": "#1F2937",
        "utility_button": "#E5E7EB",
        "utility_button_hover": "#D1D5DB",
        "utility_button_pressed": "#C4C8CF",
        "warning": "#B7791F",
        "debug": "#7D8790",
        "terminal_warning": "#B7791F",
        "terminal_debug": "#7D8790",
        "terminal_info": "#1F6FEB",
        "terminal_error": "#C53030",
        "group_title": "#888888",
        "group_title_text": "#FFFFFF",
        "welcome_accent": "#6D3AA8",
    },
    "dark": {
        "background": "#1E1F22",
        "surface": "#2B2D30",
        "terminal_background": "#101015",
        "default_input_background": "#2B2D30",
        "accent": "#3574F0",
        "border": "#5A5D63",
        "card_color": "#2B2D30",
        "white": "#FFFFFF",

        "button_text": "#FFFFFF",

        "terminal_warning": "#E0A84B",
        "terminal_debug": "#8D98A7",
        "terminal_info": "#69A2FF",
        "terminal_error": "#FF7474",

        "red": "#FF0000",

        "surface_alt": "#252A33",
        "input_background": "#20252D",

        "border_strong": "#46505E",
        "text": "#E7EAF0",
        "text_secondary": "#AAB2BF",
        "text_disabled": "#707987",

        "accent_hover": "#6AA1FF",
        "accent_pressed": "#3478E5",
        "accent_text": "#FFFFFF",
        "success": "#35C96F",
        "success_hover": "#49DB80",
        "success_pressed": "#22AD5B",
        "success_border": "#24864D",
        "success_soft": "#173825",
        "success_text": "#76E39C",
        "danger": "#F05A5A",
        "danger_hover": "#FF7070",
        "danger_pressed": "#D94747",
        "danger_border": "#A63D3D",
        "danger_soft": "#3A2023",
        "danger_text": "#FF9494",
        "inactive": "#5F5AE2",
        "inactive_border": "#4540C9",
        "pending": "#C4C92E",
        "pending_border": "#929617",
        "pending_text": "#1F2937",
        "utility_button": "#3A3F47",
        "utility_button_hover": "#48505B",
        "utility_button_pressed": "#30353C",
        "warning": "#E0A84B",
        "debug": "#8D98A7",


        "group_title": "#555555",
        "group_title_text": "#FFFFFF",
        "welcome_accent": "#FFFFFF",
    },
}

_active_theme = DEFAULT_THEME


def theme_color(name: str) -> str:
    return THEMES[_active_theme][name]


def current_theme() -> str:
    """Return the active application theme name."""

    return _active_theme


def set_button_pending(button: QPushButton, pending: bool) -> None:
    """Refresh the shared visual state for an in-flight command button."""

    button.setProperty("commandPending", pending)
    button.style().unpolish(button)
    button.style().polish(button)
    button.update()


def set_theme(name: str) -> None:
    global _active_theme
    if name not in THEMES:
        raise ValueError(f"Unknown theme: {name}")
    _active_theme = name


def application_palette() -> QPalette:
    c = THEMES[_active_theme]
    palette = QPalette()

    # QPalette меняет цвета стандартных Qt-контролов, не подменяя их
    # нативную геометрию, padding и размеры, как это делает глобальный QSS.
    palette.setColor(QPalette.Window, QColor(c["background"]))
    palette.setColor(QPalette.WindowText, QColor(c["text"]))
    palette.setColor(QPalette.Base, QColor(c["input_background"]))
    palette.setColor(QPalette.AlternateBase, QColor(c["surface_alt"]))
    palette.setColor(QPalette.Text, QColor(c["text"]))
    palette.setColor(QPalette.Button, QColor(c["surface"]))
    palette.setColor(QPalette.ButtonText, QColor(c["button_text"]))
    palette.setColor(QPalette.Highlight, QColor(c["accent"]))
    palette.setColor(QPalette.HighlightedText, QColor(c["accent_text"]))
    palette.setColor(QPalette.PlaceholderText, QColor(c["text_disabled"]))
    palette.setColor(QPalette.ToolTipBase, QColor(c["surface"]))
    palette.setColor(QPalette.ToolTipText, QColor(c["text"]))

    disabled = QPalette.Disabled
    palette.setColor(disabled, QPalette.WindowText, QColor(c["text_disabled"]))
    palette.setColor(disabled, QPalette.Text, QColor(c["text_disabled"]))
    palette.setColor(disabled, QPalette.ButtonText, QColor(c["text_disabled"]))

    return palette


def application_stylesheet() -> str:
    c = THEMES[_active_theme]
    checkmark = (Path(__file__).resolve().parent / "assets" / "check.svg").as_posix()
    combo_arrow = (
        Path(__file__).resolve().parent
        / "assets"
        / ("combo-arrow-dark.svg" if _active_theme == "dark" else "combo-arrow-light.svg")
    ).as_posix()
    return f"""
        /* Только специальные элементы приложения.
           Стандартные QPushButton/QLineEdit/QComboBox/... намеренно здесь
           не стилизуются, чтобы Qt сохранил их родные padding и метрики. */
           
        QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit {{
            background: {c["default_input_background"]};
            border: 1px solid {c["border"]};
            border-radius: 5px;
            padding: 3px;
        }}

        QSpinBox, QDoubleSpinBox, QComboBox {{
            padding-right: 3px;
        }}

        QSpinBox::up-button, QSpinBox::down-button,
        QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{
            border: none;
            background: transparent;
            width: 0px;
        }}

        QSpinBox::up-arrow, QSpinBox::down-arrow,
        QDoubleSpinBox::up-arrow, QDoubleSpinBox::down-arrow {{
            image: none;
            width: 0px;
            height: 0px;
        }}

        QComboBox::drop-down {{
            border: none;
            background: transparent;
            width: 22px;
        }}

        QComboBox::down-arrow {{
            image: url("{combo_arrow}");
            width: 10px;
            height: 6px;
        }}

        QMessageBox {{
            background-color: {c["surface"]};
        }}

        QMessageBox QLabel {{
            color: {c["text"]};
            background: transparent;
        }}

        QMessageBox QPushButton {{
            color: {c["accent_text"]};
            min-width: 72px;
        }}

        QMenu {{
            color: #000000;
            background-color: #FFFFFF;
            border: 1px solid {c["border"]};
        }}

        QMenu::item:selected {{
            background-color: #E5E7EB;
            color: #000000;
        }}
        
        QPushButton {{
            background-color: {c["accent"]};
            border: 0px solid {c["border"]};
            border-radius: 5px;
            padding: 5px;
        }}

        QPushButton[commandPending="true"],
        QPushButton[commandPending="true"]:disabled {{
            background-color: {c["pending"]};
            color: {c["pending_text"]};
        }}

        QToolButton#utilityButton {{
            background-color: {c["utility_button"]};
            border: 1px solid {c["border_strong"]};
            border-radius: 5px;
            padding: 3px;
        }}

        QToolButton#utilityButton:hover {{
            background-color: {c["utility_button_hover"]};
        }}

        QToolButton#utilityButton:pressed {{
            background-color: {c["utility_button_pressed"]};
        }}

        QLabel#commandDescription {{
            color: {c["text_secondary"]};
        }}

        QLabel#welcomeTitle {{
            color: {c["welcome_accent"]};
        }}

        QPlainTextEdit#terminal {{
            background: {c["terminal_background"]};
        }}

        QCheckBox::indicator {{
            width: 14px;
            height: 14px;
            background-color: {c["surface"]};
            border: 1px solid {c["border"]};
            border-radius: 2px;
        }}

        QCheckBox::indicator:checked {{
            background-color: {c["accent"]};
            border: 1px solid {c["accent"]};
            image: url("{checkmark}");
        }}
        
        QFrame {{
            border: 0px solid {c["red"]};;
        }}

        QTabWidget::pane {{
            border: 0px solid {c["red"]};
        }}
        
        QTabBar::tab {{
            background-color: {c["surface"]};
            color: {c["text_secondary"]};
            border: 0px solid {c["red"]};
            padding: 5px 10px 5px 10px;
        }}

        QTabBar::tab:selected {{
            background-color: {c["background"]};
            color: {c["text"]};
            padding: 7px 12px 7px 12px;
        }}

        QGroupBox#commandForm {{
            background-color: {c["card_color"]};
            color: {c["white"]};
            border: 0px solid red;
            margin-top: 0px;
            padding-top: 8px;
            
        }}

        QGroupBox#commandGroup {{
            background-color: {c["surface"]};
            border: 0px solid {c["red"]};
            border-radius: 4px;
            margin-top: 12px;
            padding: 8px;
        }}

        QGroupBox#commandGroup::title {{
            
            left: 12px;
            padding: 0 6px;
            letter-spacing: 2px;
            color: {c["group_title_text"]};
            font-weight: 600;
            background-color: {c["group_title"]};
        }}

        QToolButton#commandGroupHeader {{
            background-color: {c["group_title"]};
            color: {c["group_title_text"]};
            border: 0px solid {c["red"]};
            border-radius: 4px;
            padding: 4px 8px;
            letter-spacing: 2px;
            font-weight: 600;
            text-align: left;
        }}

        QToolButton#commandGroupHeader:hover {{
            background-color: {c["accent"]};
            color: {c["accent_text"]};
        }}

        QToolButton#commandTitle {{
            background: transparent;
            border: 0px solid {c["red"]};
            color: {c["white"]};
            font-weight: 600;
            padding: 2px 0px;
            text-align: left;
        }}

        QToolButton#commandTitle:hover {{
            color: {c["accent"]};
        }}
        
        QGroupBox {{
            border: 0px solid {c["red"]};
            color: {c["text"]};
            background-color: {c["surface"]};
            border-radius: 4px;
            margin-top: 12px;
            padding: 8px;
        }}
        
        QWidget#GPIO_outer {{
            background-color: {c["surface"]};
            border: 0px solid {c["red"]};
        }}

        QGroupBox#gpioSection {{
            padding-left: 0px;
            padding-right: 0px;
        }}
        
        
    
        
        QScrollArea {{
            border: none;
            background: transparent;
        }}
        
        QScrollBar:vertical {{
            background: transparent;
            width: 10px;
            margin: 2px;
        }}
        
        QScrollBar::handle:vertical {{
            background: {c["border"]};
            min-height: 30px;
            border-radius: 4px;
        }}
        
        QScrollBar::handle:vertical:hover {{
            background: {c["text_disabled"]};
        }}
        
        QScrollBar::handle:vertical:pressed {{
            background: {c["text_secondary"]};
        }}
        
        QScrollBar::add-line:vertical,
        QScrollBar::sub-line:vertical {{
            height: 0px;
        }}
        
        QScrollBar::add-page:vertical,
        QScrollBar::sub-page:vertical {{
            background: transparent;
        }}
        
        
        QScrollBar:horizontal {{
            background: transparent;
            height: 10px;
            margin: 2px;
        }}
        
        QScrollBar::handle:horizontal {{
            background: {c["border"]};
            min-width: 30px;
            border-radius: 4px;
        }}
        
        QScrollBar::handle:horizontal:hover {{
            background: {c["text_disabled"]};
        }}
        
        QScrollBar::handle:horizontal:pressed {{
            background: {c["text_secondary"]};
        }}
        
        QScrollBar::add-line:horizontal,
        QScrollBar::sub-line:horizontal {{
            width: 0px;
        }}
        
        QScrollBar::add-page:horizontal,
        QScrollBar::sub-page:horizontal {{
            background: transparent;
        }}
    """

