import json
import logging
import urllib.parse
from os import makedirs, path

from pydantic import BaseModel, Field

from soundcork.config import Settings
from soundcork.i18n import DEFAULT_LANGUAGE, normalize_language

logger = logging.getLogger(__name__)

USER_SETTINGS_FILE = "MiniappSettings.json"
DEFAULT_TUNEIN_SEARCH_ENDPOINT = (
    "https://api.radiotime.com/profiles?fulltextsearch=true&version=1.3&query="
)
DEFAULT_TOPBAR_ORDER = ["status", "settings", "logout", "dashboard", "home"]
DEFAULT_TOPBAR_ROW = {
    "status": "primary",
    "settings": "primary",
    "logout": "primary",
    "dashboard": "secondary",
    "home": "secondary",
}
TOPBAR_ROWS = {"primary", "secondary"}
TIMER_SECTION_LABEL_KEYS = {"sleep", "alarm", "jobs"}

LIGHT_PANEL_STYLE_DEFAULTS = {
    "background_color": "#fbf7ef",
    "text_color": "#1d2329",
    "border_color": "#ded3c3",
    "button_background_color": "#f6efe4",
}
LIGHT_SITE_STYLE_DEFAULTS = {
    "page_background_color": "#f3ecdf",
    "input_background_color": "#faf4ea",
    "topbar_background_color": "#f3ecdf",
    "topbar_text_color": "#1d2329",
    "topbar_muted_color": "#6f665a",
    "topbar_accent_color": "#1868db",
    "topbar_border_color": "#ded3c3",
}
LEGACY_LIGHT_PANEL_STYLE = {
    "background_color": "#ffffff",
    "text_color": "#1d2329",
    "border_color": "#d9dee4",
    "button_background_color": "#ffffff",
}
LEGACY_LIGHT_SITE_STYLE = {
    "page_background_color": "#f7f5f0",
    "input_background_color": "#ffffff",
    "topbar_background_color": "#f7f5f0",
    "topbar_text_color": "#1d2329",
    "topbar_muted_color": "#65717d",
    "topbar_accent_color": "#1868db",
    "topbar_border_color": "#d9dee4",
}


class MenuButton(BaseModel):
    label: str
    url: str


class PanelStyle(BaseModel):
    background_color: str = LIGHT_PANEL_STYLE_DEFAULTS["background_color"]
    text_color: str = LIGHT_PANEL_STYLE_DEFAULTS["text_color"]
    border_color: str = LIGHT_PANEL_STYLE_DEFAULTS["border_color"]
    button_background_color: str = LIGHT_PANEL_STYLE_DEFAULTS[
        "button_background_color"
    ]


class SiteStyle(BaseModel):
    page_background_color: str = LIGHT_SITE_STYLE_DEFAULTS["page_background_color"]
    input_background_color: str = LIGHT_SITE_STYLE_DEFAULTS[
        "input_background_color"
    ]
    topbar_background_color: str = LIGHT_SITE_STYLE_DEFAULTS[
        "topbar_background_color"
    ]
    topbar_text_color: str = LIGHT_SITE_STYLE_DEFAULTS["topbar_text_color"]
    topbar_muted_color: str = LIGHT_SITE_STYLE_DEFAULTS["topbar_muted_color"]
    topbar_accent_color: str = LIGHT_SITE_STYLE_DEFAULTS["topbar_accent_color"]
    topbar_border_color: str = LIGHT_SITE_STYLE_DEFAULTS["topbar_border_color"]


class UserSettings(BaseModel):
    language: str = DEFAULT_LANGUAGE
    visual_theme: str = "light"
    tunein_search_endpoint: str = DEFAULT_TUNEIN_SEARCH_ENDPOINT
    menu_settings_label: str = "Settings"
    menu_custom_buttons: list[MenuButton] = Field(default_factory=list)
    topbar_order: list[str] = Field(default_factory=lambda: DEFAULT_TOPBAR_ORDER.copy())
    topbar_row: dict[str, str] = Field(
        default_factory=lambda: DEFAULT_TOPBAR_ROW.copy()
    )
    panel_order: dict[str, list[str]] = Field(default_factory=dict)
    panel_surface: dict[str, str] = Field(default_factory=dict)
    panel_style: dict[str, PanelStyle] = Field(default_factory=dict)
    panel_label: dict[str, str] = Field(default_factory=dict)
    timer_section_label: dict[str, str] = Field(default_factory=dict)
    global_panel_style: PanelStyle = Field(default_factory=PanelStyle)
    site_style: SiteStyle = Field(default_factory=SiteStyle)
    preset_drag_opacity: float = 0.58
    preset_thumbnail_size_px: int = 104
    preset_long_press_delay_ms: int = 2000
    preset_search_result_delay_ms: int = 55
    timer_job_visible_count: int = 5
    topbar_long_press_delay_ms: int = 900
    now_playing_poll_interval_ms: int = 15000
    volume_poll_interval_ms: int = 15000
    preset_legacy_ui: bool = False
    volume_legacy_ui: bool = False


def normalize_menu_label(label: str) -> str:
    label = label.strip()
    return label[:30] if label else "Settings"


def normalize_menu_url(url: str) -> str:
    url = url.strip()
    if not url:
        raise ValueError("Menu URL is required")
    if url.startswith("/"):
        return url

    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Menu URL must be a relative path or http/https URL")
    return url


def normalize_panel_list(panel_ids: list[str]) -> list[str]:
    normalized = []
    for panel_id in panel_ids:
        if not isinstance(panel_id, str):
            continue
        panel_id = panel_id.strip()
        if panel_id and panel_id not in normalized:
            normalized.append(panel_id[:80])
    return normalized


def normalize_topbar_order(item_ids: object) -> list[str]:
    if not isinstance(item_ids, list):
        return DEFAULT_TOPBAR_ORDER.copy()

    normalized = []
    for item_id in item_ids:
        if not isinstance(item_id, str):
            continue
        item_id = item_id.strip()
        if item_id in DEFAULT_TOPBAR_ORDER and item_id not in normalized:
            normalized.append(item_id)

    for item_id in DEFAULT_TOPBAR_ORDER:
        if item_id not in normalized:
            normalized.append(item_id)
    return normalized


def normalize_topbar_row(rows: object) -> dict[str, str]:
    normalized = DEFAULT_TOPBAR_ROW.copy()
    if not isinstance(rows, dict):
        return normalized

    for item_id, row_id in rows.items():
        item = str(item_id or "").strip()
        row = str(row_id or "").strip()
        if item in DEFAULT_TOPBAR_ORDER and row in TOPBAR_ROWS:
            normalized[item] = row
    return normalized


def normalize_hex_color(value: object, fallback: str) -> str:
    color = str(value or "").strip()
    if len(color) == 7 and color.startswith("#"):
        hex_value = color[1:]
        if all(character in "0123456789abcdefABCDEF" for character in hex_value):
            return f"#{hex_value.lower()}"
    return fallback


def normalize_visual_theme(value: object) -> str:
    theme = str(value or "").strip().lower()
    return theme if theme in {"light", "dark", "custom"} else "light"


def normalize_panel_style(style: object) -> PanelStyle:
    if isinstance(style, PanelStyle):
        raw_style = style.model_dump()
    elif isinstance(style, dict):
        raw_style = style
    else:
        raw_style = {}

    background_color = normalize_hex_color(
        raw_style.get("background_color"),
        LIGHT_PANEL_STYLE_DEFAULTS["background_color"],
    )
    text_color = normalize_hex_color(
        raw_style.get("text_color"), LIGHT_PANEL_STYLE_DEFAULTS["text_color"]
    )
    border_color = normalize_hex_color(
        raw_style.get("border_color"), LIGHT_PANEL_STYLE_DEFAULTS["border_color"]
    )
    button_background_color = normalize_hex_color(
        raw_style.get("button_background_color"),
        LIGHT_PANEL_STYLE_DEFAULTS["button_background_color"],
    )
    return PanelStyle(
        background_color=background_color,
        text_color=text_color,
        border_color=border_color,
        button_background_color=button_background_color,
    )


def normalize_site_style(style: object) -> SiteStyle:
    if isinstance(style, SiteStyle):
        raw_style = style.model_dump()
    elif isinstance(style, dict):
        raw_style = style
    else:
        raw_style = {}

    return SiteStyle(
        page_background_color=normalize_hex_color(
            raw_style.get("page_background_color"),
            LIGHT_SITE_STYLE_DEFAULTS["page_background_color"],
        ),
        input_background_color=normalize_hex_color(
            raw_style.get("input_background_color"),
            LIGHT_SITE_STYLE_DEFAULTS["input_background_color"],
        ),
        topbar_background_color=normalize_hex_color(
            raw_style.get("topbar_background_color"),
            LIGHT_SITE_STYLE_DEFAULTS["topbar_background_color"],
        ),
        topbar_text_color=normalize_hex_color(
            raw_style.get("topbar_text_color"),
            LIGHT_SITE_STYLE_DEFAULTS["topbar_text_color"],
        ),
        topbar_muted_color=normalize_hex_color(
            raw_style.get("topbar_muted_color"),
            LIGHT_SITE_STYLE_DEFAULTS["topbar_muted_color"],
        ),
        topbar_accent_color=normalize_hex_color(
            raw_style.get("topbar_accent_color"),
            LIGHT_SITE_STYLE_DEFAULTS["topbar_accent_color"],
        ),
        topbar_border_color=normalize_hex_color(
            raw_style.get("topbar_border_color"),
            LIGHT_SITE_STYLE_DEFAULTS["topbar_border_color"],
        ),
    )


def panel_style_matches(style: PanelStyle, values: dict[str, str]) -> bool:
    return style.model_dump() == values


def site_style_matches(style: SiteStyle, values: dict[str, str]) -> bool:
    return style.model_dump() == values


def normalize_user_settings(settings: UserSettings) -> UserSettings:
    settings.language = normalize_language(settings.language)
    settings.visual_theme = normalize_visual_theme(settings.visual_theme)
    settings.tunein_search_endpoint = normalize_tunein_search_endpoint(
        settings.tunein_search_endpoint
    )
    settings.menu_settings_label = normalize_menu_label(settings.menu_settings_label)
    settings.menu_custom_buttons = [
        MenuButton(
            label=button.label.strip()[:30],
            url=normalize_menu_url(button.url),
        )
        for button in settings.menu_custom_buttons[:6]
        if button.label.strip()
    ]
    settings.topbar_order = normalize_topbar_order(settings.topbar_order)
    settings.topbar_row = normalize_topbar_row(settings.topbar_row)
    settings.panel_order = {
        str(surface).strip()[:40]: normalize_panel_list(order)
        for surface, order in settings.panel_order.items()
        if str(surface).strip() and isinstance(order, list)
    }
    settings.panel_surface = {
        str(panel_id).strip()[:80]: str(surface).strip()[:40]
        for panel_id, surface in settings.panel_surface.items()
        if str(panel_id).strip() and str(surface).strip()
    }
    settings.panel_style = {
        str(panel_id).strip()[:80]: normalize_panel_style(style)
        for panel_id, style in settings.panel_style.items()
        if str(panel_id).strip()
    }
    settings.panel_label = {
        str(panel_id).strip()[:80]: str(label or "").strip()[:80]
        for panel_id, label in settings.panel_label.items()
        if str(panel_id).strip()
    }
    settings.timer_section_label = {
        str(label_key).strip(): str(label or "").strip()[:80]
        for label_key, label in settings.timer_section_label.items()
        if str(label_key).strip() in TIMER_SECTION_LABEL_KEYS
    }
    settings.global_panel_style = normalize_panel_style(settings.global_panel_style)
    settings.site_style = normalize_site_style(settings.site_style)
    if settings.visual_theme == "light":
        if panel_style_matches(settings.global_panel_style, LEGACY_LIGHT_PANEL_STYLE):
            settings.global_panel_style = normalize_panel_style(
                LIGHT_PANEL_STYLE_DEFAULTS
            )
        if site_style_matches(settings.site_style, LEGACY_LIGHT_SITE_STYLE):
            settings.site_style = normalize_site_style(LIGHT_SITE_STYLE_DEFAULTS)
    settings.preset_drag_opacity = min(1.0, max(0.15, settings.preset_drag_opacity))
    settings.preset_thumbnail_size_px = min(
        220, max(80, int(settings.preset_thumbnail_size_px))
    )
    settings.preset_long_press_delay_ms = min(
        4000, max(500, int(settings.preset_long_press_delay_ms))
    )
    settings.preset_search_result_delay_ms = min(
        250, max(0, int(settings.preset_search_result_delay_ms))
    )
    settings.timer_job_visible_count = min(
        12, max(1, int(settings.timer_job_visible_count))
    )
    settings.topbar_long_press_delay_ms = min(
        4000, max(500, int(settings.topbar_long_press_delay_ms))
    )
    settings.now_playing_poll_interval_ms = min(
        60000, max(3000, int(settings.now_playing_poll_interval_ms))
    )
    settings.volume_poll_interval_ms = min(
        60000, max(3000, int(settings.volume_poll_interval_ms))
    )
    settings.preset_legacy_ui = bool(settings.preset_legacy_ui)
    settings.volume_legacy_ui = bool(settings.volume_legacy_ui)
    return settings


def _settings_file() -> str:
    data_dir = Settings().data_dir or "."
    return path.join(data_dir, USER_SETTINGS_FILE)


def normalize_tunein_search_endpoint(endpoint: str) -> str:
    endpoint = endpoint.strip()
    if not endpoint:
        return DEFAULT_TUNEIN_SEARCH_ENDPOINT

    parsed = urllib.parse.urlsplit(endpoint)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Search endpoint must be an http or https URL")

    return endpoint


def load_user_settings() -> UserSettings:
    settings_path = _settings_file()
    try:
        with open(settings_path, "r", encoding="utf-8") as settings_file:
            raw_settings = json.load(settings_file)
        settings = UserSettings(**raw_settings)
        return normalize_user_settings(settings)
    except FileNotFoundError:
        return UserSettings()
    except Exception as e:
        logger.warning("Could not load miniapp settings, using defaults: %s", e)
        return UserSettings()


def save_user_settings(settings: UserSettings) -> None:
    settings = normalize_user_settings(settings)
    settings_path = _settings_file()
    settings_dir = path.dirname(settings_path)
    if settings_dir and not path.exists(settings_dir):
        makedirs(settings_dir)

    with open(settings_path, "w", encoding="utf-8") as settings_file:
        json.dump(settings.model_dump(), settings_file, indent=4)


def reset_user_settings() -> UserSettings:
    settings = UserSettings()
    save_user_settings(settings)
    return settings


def build_tunein_search_uri(query: str) -> str:
    endpoint = load_user_settings().tunein_search_endpoint
    encoded_query = urllib.parse.quote_plus(query)
    if "{query}" in endpoint:
        return endpoint.replace("{query}", encoded_query)
    return f"{endpoint}{encoded_query}"
