"""
Endpoints for a miniapp UI.
"""

import asyncio
import json
import logging
import os
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from urllib.parse import quote, unquote
from uuid import uuid4

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from soundcork.bmx import tunein_station_search_results
from soundcork.constants import DEFAULT_DEVICE_IMAGE, DEVICE_IMAGE_MAP
from soundcork.datastore import DataStore
from soundcork.i18n import (
    LANGUAGE_OPTIONS,
    normalize_language,
    translate,
    translate_message,
    translation_bundle,
)
from soundcork.model import Preset
from soundcork.ui.speakers import Speakers
from soundcork.user_settings import (
    DEFAULT_TUNEIN_SEARCH_ENDPOINT,
    LIGHT_PANEL_STYLE_DEFAULTS,
    LIGHT_SITE_STYLE_DEFAULTS,
    MenuButton,
    UserSettings,
    load_user_settings,
    normalize_menu_label,
    normalize_menu_url,
    normalize_panel_list,
    normalize_panel_style,
    normalize_site_style,
    normalize_topbar_order,
    normalize_topbar_row,
    normalize_tunein_search_endpoint,
    save_user_settings,
)

logger = logging.getLogger(__name__)


def encode_cookie_value(value: object) -> str:
    """Encode text for Set-Cookie's latin-1 constrained header value."""
    return quote(str(value), safe="")


def decode_cookie_value(value: str | None, default: str | None = None) -> str | None:
    if value is None:
        return default
    return unquote(value)


def get_device_image(product_code: str) -> str:
    """Map product code to device image file."""
    return DEVICE_IMAGE_MAP.get(product_code.lower(), DEFAULT_DEVICE_IMAGE)


DARK_PANEL_STYLE = {
    "background_color": "#1d2024",
    "text_color": "#f5f7f2",
    "border_color": "#3d4652",
    "button_background_color": "#2b3139",
}
DARK_SITE_STYLE = {
    "page_background_color": "#0f1012",
    "input_background_color": "#252b33",
    "topbar_background_color": "#161719",
    "topbar_text_color": "#f5f7f2",
    "topbar_muted_color": "#abb1b8",
    "topbar_accent_color": "#4cc9a7",
    "topbar_border_color": "#2f343a",
}
LIGHT_PANEL_STYLE = LIGHT_PANEL_STYLE_DEFAULTS
LIGHT_SITE_STYLE = LIGHT_SITE_STYLE_DEFAULTS


def hex_color_luminance(color: object) -> float:
    raw_color = str(color or "").strip()
    if len(raw_color) != 7 or not raw_color.startswith("#"):
        return 1.0
    try:
        red = int(raw_color[1:3], 16) / 255
        green = int(raw_color[3:5], 16) / 255
        blue = int(raw_color[5:7], 16) / 255
    except ValueError:
        return 1.0

    def linearize(channel: float) -> float:
        if channel <= 0.03928:
            return channel / 12.92
        return ((channel + 0.055) / 1.055) ** 2.4

    return (
        0.2126 * linearize(red) + 0.7152 * linearize(green) + 0.0722 * linearize(blue)
    )


def sync_icon_path(settings: UserSettings) -> str:
    preset_style = normalize_panel_style(
        settings.panel_style.get("presets") or settings.global_panel_style
    )
    if hex_color_luminance(preset_style.button_background_color) < 0.45:
        return "/static/images/resync_white.png"
    return "/static/images/resync_black.png"


def format_frequency_khz(value: object) -> str:
    try:
        frequency_khz = int(str(value))
    except (TypeError, ValueError):
        return ""
    if frequency_khz <= 0:
        return ""
    return f"{frequency_khz / 1_000_000:.2f} GHz"


def network_kind_key(kind: object) -> str:
    normalized = str(kind or "").strip().lower()
    if normalized == "wired":
        return "network_kind_wired"
    if normalized == "wireless":
        return "network_kind_wireless"
    return ""


def format_network_label(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    for prefix in ("NETWORK_", "SETUP_"):
        if text.startswith(prefix):
            text = text[len(prefix) :]
    text = text.replace("_INTERFACE", "").replace("_", " ").title()
    return text.replace("Wifi", "Wi-Fi")


def format_network_interfaces(
    network: dict[str, object] | None,
) -> list[dict[str, str]]:
    if not network:
        return []

    interfaces = network.get("network_info_interfaces", [])
    if not isinstance(interfaces, list):
        return []

    formatted = []
    for interface in interfaces:
        if not isinstance(interface, dict):
            continue
        formatted.append(
            {
                "name": str(interface.get("name") or ""),
                "type": format_network_label(interface.get("type_value")),
                "state": format_network_label(interface.get("state_value")),
                "ip_address": str(interface.get("ip_address") or ""),
                "ssid": str(interface.get("ssid") or ""),
                "mac_address": str(interface.get("mac_address") or ""),
                "mode": format_network_label(interface.get("mode")),
                "signal": format_network_label(interface.get("signal")),
                "frequency": format_frequency_khz(interface.get("frequency_khz")),
            }
        )
    return formatted


def get_miniapp_router(datastore: DataStore, speakers: Speakers):
    templates = Jinja2Templates(directory="templates")

    router = APIRouter(tags=["miniapp"])
    scheduled_jobs: dict[str, dict] = {}
    WEEKDAY_KEYS = [
        "weekday_monday",
        "weekday_tuesday",
        "weekday_wednesday",
        "weekday_thursday",
        "weekday_friday",
        "weekday_saturday",
        "weekday_sunday",
    ]

    def miniapp_settings_payload(settings: UserSettings) -> dict:
        payload = settings.model_dump(mode="json")
        payload.pop("menu_settings_label", None)
        payload.pop("menu_custom_buttons", None)
        payload.setdefault("panel_order", {}).setdefault("home", [])
        payload["translations"] = translation_bundle(settings.language)
        payload["language_options"] = LANGUAGE_OPTIONS
        return payload

    def theme_style_vars(settings: UserSettings) -> str:
        site_style = normalize_site_style(settings.site_style)
        panel_style = normalize_panel_style(settings.global_panel_style)
        return (
            f"--page-bg: {site_style.page_background_color}; "
            f"--surface: {panel_style.background_color}; "
            f"--surface-soft: {panel_style.button_background_color}; "
            f"--ink: {site_style.topbar_text_color}; "
            f"--muted: {site_style.topbar_muted_color}; "
            f"--line: {panel_style.border_color}; "
            f"--input-bg: {site_style.input_background_color}; "
            f"--input-text: {site_style.topbar_text_color}; "
            f"--global-panel-bg: {panel_style.background_color}; "
            f"--global-panel-text: {panel_style.text_color}; "
            f"--global-panel-border: {panel_style.border_color}; "
            f"--global-panel-button-bg: {panel_style.button_background_color}; "
            f"--topbar-bg: {site_style.topbar_background_color}; "
            f"--topbar-text: {site_style.topbar_text_color}; "
            f"--topbar-muted: {site_style.topbar_muted_color}; "
            f"--topbar-accent: {site_style.topbar_accent_color}; "
            f"--topbar-border: {site_style.topbar_border_color};"
        )

    def template_language_context(settings: UserSettings | None = None) -> dict:
        user_settings = settings or load_user_settings()
        language = normalize_language(user_settings.language)
        return {
            "settings": user_settings,
            "miniapp_settings": miniapp_settings_payload(user_settings),
            "theme_style": theme_style_vars(user_settings),
            "sync_icon_path": sync_icon_path(user_settings),
            "language": language,
            "language_options": LANGUAGE_OPTIONS,
            "t": lambda key, **values: translate(key, language, **values),
        }

    def get_presets_or_resync(
        account_id: str, selected_device_id: str | None
    ) -> list[Preset]:
        try:
            return datastore.get_presets(account_id)
        except ET.ParseError as e:
            logger.warning(
                "Preset cache for account %s is invalid (%s); resyncing from radio",
                account_id,
                e,
            )
            if selected_device_id and speakers.sync_presets_from_speaker(
                account_id, selected_device_id
            ):
                return datastore.get_presets(account_id)
            logger.error(
                "Could not repair preset cache for account %s because no radio "
                "resync succeeded",
                account_id,
            )
            return []

    def repeat_label(repeat_days: list[int], language: str) -> str:
        if not repeat_days:
            return ""
        if sorted(repeat_days) == list(range(7)):
            return translate("repeat_every_day", language)
        names = [
            translate(WEEKDAY_KEYS[day], language)
            for day in sorted(repeat_days)
            if 0 <= day <= 6
        ]
        return translate("repeat_on_days", language, days=", ".join(names))

    def jobs_for_device(device_id: str | None, kind: str | None = None) -> list[tuple[str, dict]]:
        if not device_id:
            return []
        jobs = [
            (job_id, job)
            for job_id, job in scheduled_jobs.items()
            if job.get("device_id") == device_id
            and (kind is None or job.get("kind") == kind)
        ]
        return sorted(
            jobs,
            key=lambda item: (
                item[1].get("run_at", datetime.max),
                item[1].get("created_at", datetime.min),
            ),
        )

    def job_summary_from_entry(job_id: str, job: dict) -> dict:
        run_at = job["run_at"]
        language = load_user_settings().language
        repeat_days = job.get("repeat_days", [])
        status = ""
        if job.get("paused"):
            status = translate("paused", language)
        elif job.get("last_error"):
            status = translate("scheduled_action_failed", language)
        return {
            "id": job_id,
            "kind": job.get("kind", ""),
            "label": translate_message(job["label"], language),
            "run_at": run_at.strftime("%H:%M"),
            "repeat_label": repeat_label(repeat_days, language),
            "paused": bool(job.get("paused")),
            "status": status,
        }

    def job_summary_by_id(job_id: str | None) -> dict | None:
        if not job_id:
            return None
        job = scheduled_jobs.get(job_id)
        if not job:
            return None
        return job_summary_from_entry(job_id, job)

    def job_summary(kind: str, device_id: str | None) -> dict | None:
        jobs = jobs_for_device(device_id, kind)
        if not jobs:
            return None
        return job_summary_from_entry(jobs[0][0], jobs[0][1])

    def scheduled_job_list(device_id: str | None) -> list[dict]:
        return [
            job_summary_from_entry(job_id, job)
            for job_id, job in jobs_for_device(device_id)
        ]

    def jobs_payload(device_id: str | None) -> dict:
        return {
            "sleep": job_summary("sleep", device_id),
            "alarm": job_summary("alarm", device_id),
            "items": scheduled_job_list(device_id),
        }

    def matching_job_ids(
        kind: str, device_id: str, job_id: str | None = None
    ) -> list[str]:
        if job_id:
            job = scheduled_jobs.get(job_id)
            if (
                job
                and job.get("kind") == kind
                and job.get("device_id") == device_id
            ):
                return [job_id]
            return []
        return [item[0] for item in jobs_for_device(device_id, kind)]

    def cancel_job(kind: str, device_id: str, job_id: str | None = None) -> bool:
        job_ids = matching_job_ids(kind, device_id, job_id)
        if not job_ids:
            return False
        for target_job_id in job_ids:
            job = scheduled_jobs.pop(target_job_id, None)
            if not job:
                continue
            task = job.get("task")
            if task:
                task.cancel()
        return True

    def arm_job(key: str):
        job = scheduled_jobs.get(key)
        if not job or job.get("paused"):
            return

        delay = max(0.0, (job["run_at"] - datetime.now()).total_seconds())

        async def runner():
            action_succeeded = False
            try:
                await asyncio.sleep(delay)
                result = await asyncio.to_thread(job["action"])
                action_succeeded = result is not False
            except asyncio.CancelledError:
                raise
            except Exception as e:
                job["last_error"] = str(e) or "Scheduled action failed"
                logger.error("Scheduled job %s failed: %s", key, e)

            if key not in scheduled_jobs:
                return

            if not action_succeeded:
                job["paused"] = True
                job["task"] = None
                if not job.get("last_error"):
                    job["last_error"] = "Scheduled action failed"
                return

            job["last_error"] = ""
            if job.get("repeat_days"):
                job["run_at"] = next_repeating_clock_time(
                    job["time_value"],
                    job["repeat_days"],
                    datetime.now() + timedelta(seconds=1),
                )
                arm_job(key)
                return

            scheduled_jobs.pop(key, None)

        task = asyncio.create_task(runner())
        job["task"] = task

    def schedule_job(
        kind: str,
        device_id: str,
        run_at: datetime,
        label: str,
        action,
        repeat_days: list[int] | None = None,
        time_value: str = "",
    ) -> str:
        key = f"{kind}:{device_id}:{uuid4().hex}"
        scheduled_jobs[key] = {
            "task": None,
            "id": key,
            "kind": kind,
            "device_id": device_id,
            "run_at": run_at,
            "label": label,
            "action": action,
            "repeat_days": repeat_days or [],
            "time_value": time_value,
            "paused": False,
            "created_at": datetime.now(),
        }
        arm_job(key)
        return key

    def set_job_paused(
        kind: str, device_id: str, paused: bool, job_id: str | None = None
    ) -> bool:
        job_ids = matching_job_ids(kind, device_id, job_id)
        if not job_ids:
            return False

        for target_job_id in job_ids:
            job = scheduled_jobs.get(target_job_id)
            if not job:
                continue
            job["paused"] = paused
            task = job.get("task")
            if task:
                task.cancel()
                job["task"] = None

            if not paused:
                if job.get("repeat_days") and job["run_at"] <= datetime.now():
                    job["run_at"] = next_repeating_clock_time(
                        job["time_value"], job["repeat_days"]
                    )
                arm_job(target_job_id)
        return True

    def normalize_repeat_days(raw_days: object) -> list[int]:
        if not isinstance(raw_days, list):
            raw_days = [raw_days]
        days = []
        for raw_day in raw_days:
            try:
                day = int(str(raw_day))
            except (TypeError, ValueError):
                continue
            if 0 <= day <= 6 and day not in days:
                days.append(day)
        return sorted(days)

    def repeat_days_from_form(form_data) -> list[int]:
        repeat_daily = str(form_data.get("repeat_daily", "")).lower() in {
            "1",
            "true",
            "on",
        }
        if repeat_daily:
            return list(range(7))
        repeat_weekly = str(form_data.get("repeat_weekly", "")).lower() in {
            "1",
            "true",
            "on",
        }
        if repeat_weekly:
            days = normalize_repeat_days(form_data.getlist("repeat_days"))
            if not days:
                raise ValueError("Weekly repeat needs a weekday")
            return days
        return []

    def next_clock_time(time_value: str) -> datetime:
        hour_raw, minute_raw = time_value.split(":", 1)
        hour = int(hour_raw)
        minute = int(minute_raw)
        if hour < 0 or hour > 23 or minute < 0 or minute > 59:
            raise ValueError("Invalid time")
        now = datetime.now()
        run_at = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if run_at <= now:
            run_at += timedelta(days=1)
        return run_at

    def next_repeating_clock_time(
        time_value: str, repeat_days: list[int], after: datetime | None = None
    ) -> datetime:
        hour_raw, minute_raw = time_value.split(":", 1)
        hour = int(hour_raw)
        minute = int(minute_raw)
        if hour < 0 or hour > 23 or minute < 0 or minute > 59:
            raise ValueError("Invalid time")
        days = normalize_repeat_days(repeat_days)
        if not days:
            raise ValueError("Missing repeat days")

        reference = after or datetime.now()
        for offset in range(8):
            candidate = (reference + timedelta(days=offset)).replace(
                hour=hour, minute=minute, second=0, microsecond=0
            )
            if candidate.weekday() in days and candidate > reference:
                return candidate
        raise ValueError("Invalid repeat schedule")

    def backup_dir() -> str:
        directory = os.path.join(datastore.data_dir, "backups")
        if not os.path.exists(directory):
            os.makedirs(directory)
        return directory

    def backup_path(filename: str) -> str:
        if (
            not filename.endswith(".json")
            or os.path.basename(filename) != filename
            or not filename.startswith("soundcork-backup-")
        ):
            raise ValueError("Invalid backup filename")
        return os.path.join(backup_dir(), filename)

    def list_backup_files() -> list[str]:
        return sorted(
            [
                filename
                for filename in os.listdir(backup_dir())
                if filename.endswith(".json")
                and filename.startswith("soundcork-backup-")
            ],
            reverse=True,
        )

    def backup_return_url(value: object, notice: str = "", error: str = "") -> str:
        path_value = str(value or "").strip()
        if path_value not in {"/miniapp/dashboard", "/miniapp/settings"}:
            path_value = "/miniapp/dashboard"
        if notice:
            return f"{path_value}?notice={encode_cookie_value(notice)}"
        if error:
            return f"{path_value}?error={encode_cookie_value(error)}"
        return path_value

    def presets_from_backup(backup: dict) -> list[Preset]:
        presets = [Preset(**preset) for preset in backup.get("presets", [])]
        if not presets:
            raise ValueError("No presets")
        for preset in presets:
            if not preset.id.isdigit() or int(preset.id) < 1 or int(preset.id) > 6:
                raise ValueError("Invalid preset")
        return presets

    def apply_settings_from_backup(backup: dict) -> None:
        settings_backup = backup.get("miniapp_settings")
        settings = load_user_settings()
        if isinstance(settings_backup, dict):
            restored = UserSettings(**settings_backup)
            save_user_settings(restored)
            return

        panel_order = backup.get("panel_order", [])
        if isinstance(panel_order, list):
            settings.panel_order["dashboard"] = normalize_panel_list(panel_order)
            save_user_settings(settings)

    def account_device_cards(account_id: str) -> list[dict[str, str]]:
        combined_devices = speakers.all_devices()
        devices = []
        for device_id in datastore.list_devices(account_id):
            try:
                device_info = datastore.get_device_info(account_id, device_id)
                combined_device = combined_devices.get(device_id)
                ready = "offline"
                ssid = ""
                network: dict[str, object] | None = None
                if (
                    combined_device
                    and combined_device.online
                    and combined_device.in_soundcork
                    and combined_device.marge_server == "Soundcork"
                ):
                    ready = "online"
                    try:
                        network = speakers.network_status(device_id)
                        ssid = str(network.get("ssid", "") if network else "")
                    except Exception:
                        ssid = ""
                network_bindings = network.get("bindings", []) if network else []
                if not isinstance(network_bindings, list):
                    network_bindings = []
                current_ip = str(
                    network.get("ip_address", "") if network else ""
                ) or str(getattr(combined_device, "ip", "") or "")
                network_kind = str(network.get("kind", "") if network else "")
                wifi_profile_count = (
                    network.get("wifi_profile_count", "") if network else ""
                )
                devices.append(
                    {
                        "name": device_info.name,
                        "product_code": device_info.product_code,
                        "device_id": device_info.device_id,
                        "ip_address": device_info.ip_address,
                        "status": ready,
                        "ssid": ssid,
                        "configured_ssid": str(
                            network.get("configured_ssid", "") if network else ""
                        ),
                        "current_ip": current_ip,
                        "network_kind": network_kind,
                        "network_kind_key": network_kind_key(network_kind),
                        "network_interface": str(
                            network.get("name", "") if network else ""
                        ),
                        "network_mac": str(
                            network.get("mac_address", "") if network else ""
                        ),
                        "network_rssi": str(network.get("rssi", "") if network else ""),
                        "network_frequency": format_frequency_khz(
                            network.get("frequency_khz", "") if network else ""
                        ),
                        "network_bindings": ", ".join(
                            str(binding) for binding in network_bindings if binding
                        ),
                        "network_info_interfaces": format_network_interfaces(network),
                        "wifi_profile_count": (
                            ""
                            if wifi_profile_count is None
                            else str(wifi_profile_count)
                        ),
                        "image_file": get_device_image(device_info.product_code),
                    }
                )
            except Exception as e:
                logger.error("Error getting device info for %s: %s", device_id, e)
        return devices

    def selected_device_response(
        device: dict[str, str], redirect_url: str = "/miniapp/dashboard"
    ) -> RedirectResponse:
        response = RedirectResponse(url=redirect_url, status_code=303)
        response.set_cookie(
            key="soundcork_selected_device",
            value=encode_cookie_value(device["name"]),
            max_age=86400 * 30,
            httponly=False,
            samesite="strict",
        )
        response.set_cookie(
            key="soundcork_selected_device_id",
            value=device["device_id"],
            max_age=86400 * 30,
            httponly=True,
            samesite="strict",
        )
        return response

    def clear_selected_device_cookies(response: RedirectResponse) -> RedirectResponse:
        response.delete_cookie("soundcork_selected_device")
        response.delete_cookie("soundcork_selected_device_id")
        return response

    def require_account(request: Request) -> str | None:
        account_id = request.cookies.get("soundcork_account_id", "")
        if not account_id or not datastore.account_exists(account_id):
            return None
        return account_id

    def wants_json(request: Request) -> bool:
        return request.headers.get("x-soundcork-request") == "fetch"

    def set_selected_content_cookies(
        response,
        content_item_id: str,
        content_item_name: str,
        is_playing: bool | None = None,
    ):
        response.set_cookie(
            key="soundcork_selected_content_item_name",
            value=encode_cookie_value(content_item_name),
            max_age=86400 * 30,
            httponly=False,
            samesite="strict",
        )
        response.set_cookie(
            key="soundcork_selected_content_item_id",
            value=content_item_id,
            max_age=86400 * 30,
            httponly=False,
            samesite="strict",
        )
        if is_playing is not None:
            response.set_cookie(
                key="soundcork_is_playing",
                value="true" if is_playing else "false",
                max_age=86400 * 30,
                httponly=False,
                samesite="strict",
            )
        return response

    def playback_json_response(
        content_item_id: str,
        content_item_name: str,
        is_playing: bool,
        selected_device_id: str | None,
        art: str = "",
    ) -> JSONResponse:
        volume_state = None
        if selected_device_id:
            try:
                volume_state = speakers.volume_state(selected_device_id)
            except Exception:
                volume_state = None
        response = JSONResponse(
            {
                "ok": is_playing,
                "selected_content_item_id": content_item_id,
                "selected_content_item_name": content_item_name,
                "is_playing": is_playing,
                "now_playing": {
                    "item_name": content_item_name,
                    "station_name": content_item_name,
                    "track": "",
                    "artist": "",
                    "source": "SoundCork",
                    "art": art,
                    "play_status": "PLAY_STATE" if is_playing else "UNKNOWN",
                },
                "volume_state": volume_state,
            },
            status_code=200 if is_playing else 502,
        )
        set_selected_content_cookies(
            response,
            content_item_id,
            content_item_name,
            is_playing,
        )
        return response

    def preset_slots_payload(
        account_id: str, selected_device_id: str | None
    ) -> list[dict]:
        preset_numbers = [1, 2, 3, 4, 5, 6]
        presets = get_presets_or_resync(account_id, selected_device_id)
        presets_by_number = {
            int(preset.id): preset
            for preset in presets
            if preset.id.isdigit() and int(preset.id) in preset_numbers
        }
        slots = []
        for number in preset_numbers:
            preset = presets_by_number.get(number)
            slots.append(
                {
                    "number": number,
                    "preset": preset.model_dump(mode="json") if preset else None,
                }
            )
        return slots

    @router.get("/miniapp", response_class=HTMLResponse)
    async def main_page(request: Request):
        """Redirect to login or dashboard based on session."""
        account_id = request.cookies.get("soundcork_account_id")
        if account_id and datastore.account_exists(account_id):
            return RedirectResponse(url="/miniapp/dashboard", status_code=303)
        else:
            return RedirectResponse(url="/miniapp/login", status_code=303)

    @router.get("/miniapp/open/{device_id}")
    async def open_device_shortcut(device_id: str):
        """Set the account/device cookies for a direct phone shortcut."""
        try:
            for account_id in datastore.list_accounts():
                if not account_id or not datastore.account_exists(account_id):
                    continue

                matching_device = next(
                    (
                        device
                        for device in account_device_cards(account_id)
                        if device["device_id"] == device_id
                    ),
                    None,
                )
                if not matching_device:
                    continue

                account_label = datastore.get_account_info(account_id)
                response = selected_device_response(matching_device)
                response.set_cookie(
                    key="soundcork_account_id",
                    value=account_id,
                    max_age=86400 * 30,
                    httponly=True,
                    samesite="strict",
                )
                response.set_cookie(
                    key="soundcork_account_label",
                    value=encode_cookie_value(account_label),
                    max_age=86400 * 30,
                    httponly=False,
                    samesite="strict",
                )
                return response
        except Exception as e:
            logger.error("Error opening miniapp shortcut for %s: %s", device_id, e)

        return RedirectResponse(
            url="/miniapp/login?error=Radio%20not%20found", status_code=303
        )

    @router.get("/miniapp/login", response_class=HTMLResponse)
    async def login_page(request: Request):
        """Display login page with account selection."""
        user_settings = load_user_settings()
        try:
            account_ids = datastore.list_accounts()
            accounts_data = {}

            for account_id in account_ids:
                if account_id:
                    try:
                        label = datastore.get_account_info(account_id)
                        device_count = len(datastore.list_devices(account_id))
                        accounts_data[account_id] = {
                            "label": label,
                            "device_count": device_count,
                        }
                    except Exception as e:
                        logger.error(
                            f"Error getting info for account {account_id}: {e}"
                        )
                        continue

            logger.info(f"Rendering login with {len(accounts_data)} accounts")
            return templates.TemplateResponse(
                request=request,
                name="login.html",
                context={
                    "accounts": accounts_data,
                    "error": translate_message(
                        request.query_params.get("error", ""), user_settings.language
                    )
                    or None,
                    **template_language_context(user_settings),
                },
            )
        except Exception as e:
            logger.error(f"Error rendering login page: {e}")
            return templates.TemplateResponse(
                request=request,
                name="login.html",
                context={
                    "accounts": {},
                    "error": translate(
                        "error_loading_accounts", user_settings.language
                    ),
                    **template_language_context(user_settings),
                },
            )

    @router.post("/miniapp/login")
    async def login_submit(request: Request):
        """Handle account selection and set cookie."""
        try:
            form_data = await request.form()
            account_id_raw = form_data.get("account_id")

            if not account_id_raw or not isinstance(account_id_raw, str):
                return RedirectResponse(
                    url="/miniapp/login?error=No account selected", status_code=303
                )

            account_id: str = account_id_raw

            # Verify account exists
            if not datastore.account_exists(account_id):
                return RedirectResponse(
                    url="/miniapp/login?error=Invalid account", status_code=303
                )

            # Get account label
            account_label = datastore.get_account_info(account_id)
            devices = account_device_cards(account_id)
            redirect_url = "/miniapp/dashboard"
            if len(devices) > 1:
                redirect_url = "/miniapp/devices"
            elif len(devices) == 0:
                redirect_url = "/miniapp/dashboard?error=No%20radios%20found"

            # Create response with redirect
            response = RedirectResponse(url=redirect_url, status_code=303)

            # Set cookies for account
            response.set_cookie(
                key="soundcork_account_id",
                value=account_id,
                max_age=86400 * 30,  # 30 days
                httponly=True,
                samesite="strict",
            )
            response.set_cookie(
                key="soundcork_account_label",
                value=encode_cookie_value(account_label),
                max_age=86400 * 30,
                httponly=False,  # Allow JS to read for display
                samesite="strict",
            )
            response.delete_cookie("soundcork_selected_device")
            response.delete_cookie("soundcork_selected_device_id")
            if len(devices) == 1:
                response.set_cookie(
                    key="soundcork_selected_device",
                    value=encode_cookie_value(devices[0]["name"]),
                    max_age=86400 * 30,
                    httponly=False,
                    samesite="strict",
                )
                response.set_cookie(
                    key="soundcork_selected_device_id",
                    value=devices[0]["device_id"],
                    max_age=86400 * 30,
                    httponly=True,
                    samesite="strict",
                )

            logger.info(f"User logged in to account {account_id}")
            return response

        except Exception as e:
            logger.error(f"Error during login: {e}")
            return RedirectResponse(
                url="/miniapp/login?error=Login failed", status_code=303
            )

    @router.get("/miniapp/devices", response_class=HTMLResponse)
    async def device_picker_page(request: Request):
        """Show only radio selection before entering the dashboard."""
        account_id = request.cookies.get("soundcork_account_id", "")
        if not account_id or not datastore.account_exists(account_id):
            return RedirectResponse(url="/miniapp/login", status_code=303)

        account_label = decode_cookie_value(
            request.cookies.get("soundcork_account_label"),
            datastore.get_account_info(account_id),
        )
        devices = account_device_cards(account_id)
        if len(devices) == 1:
            return selected_device_response(devices[0])

        return templates.TemplateResponse(
            request=request,
            name="device_picker.html",
            context={
                "account_id": account_id,
                "account_label": account_label,
                "devices": devices,
                "error": translate_message(
                    request.query_params.get("error", ""), load_user_settings().language
                )
                or None,
                **template_language_context(),
            },
        )

    @router.get("/miniapp/dashboard", response_class=HTMLResponse)
    async def dashboard_page(
        request: Request, page_id: str = "dashboard", panel_stack: str = "dashboard"
    ):
        """Display dashboard with devices and presets."""
        account_id = ""
        try:
            # Get account from cookie
            account_id = request.cookies.get("soundcork_account_id", "")
            account_label = decode_cookie_value(
                request.cookies.get("soundcork_account_label"), "Unknown Account"
            )

            if not account_id:
                return RedirectResponse(url="/miniapp/login", status_code=303)

            # Verify account still exists
            if not datastore.account_exists(account_id):
                response = RedirectResponse(url="/miniapp/login", status_code=303)
                response.delete_cookie("soundcork_account_id")
                response.delete_cookie("soundcork_account_label")
                return response

            # Get devices and speakers for this account
            selected_device = decode_cookie_value(
                request.cookies.get("soundcork_selected_device")
            )
            selected_device_id = request.cookies.get("soundcork_selected_device_id")
            devices = account_device_cards(account_id)
            device_ids = {device["device_id"] for device in devices}
            if selected_device_id and selected_device_id not in device_ids:
                selected_device = None
                selected_device_id = None

            if not selected_device_id:
                if len(devices) == 1:
                    return selected_device_response(devices[0])
                if len(devices) > 1:
                    return RedirectResponse(url="/miniapp/devices", status_code=303)

            preset_numbers = [1, 2, 3, 4, 5, 6]
            presets = get_presets_or_resync(account_id, selected_device_id)

            used_presets = {int(preset.id) for preset in presets if preset.id.isdigit()}
            default_preset = next(
                (number for number in preset_numbers if number not in used_presets), 1
            )
            presets_by_number = {
                int(preset.id): preset
                for preset in presets
                if preset.id.isdigit() and int(preset.id) in preset_numbers
            }
            preset_slots = [
                {"number": number, "preset": presets_by_number.get(number)}
                for number in preset_numbers
            ]
            preset_number_raw = request.query_params.get(
                "preset_number", str(default_preset)
            )
            try:
                selected_preset_number = int(preset_number_raw)
            except ValueError:
                selected_preset_number = default_preset
            if selected_preset_number not in preset_numbers:
                selected_preset_number = default_preset

            preset_query = request.query_params.get("preset_query", "").strip()
            preset_results: list[dict[str, str]] = []
            preset_search_error = ""
            if preset_query:
                try:
                    preset_results = tunein_station_search_results(preset_query)
                except Exception as e:
                    preset_search_error = "Station search failed"
                    logger.error("TuneIn preset search failed: %s", e)

            notice = request.query_params.get("notice", "")
            request_error = request.query_params.get("error", "")
            user_settings = load_user_settings()

            logger.info(
                f"Rendering dashboard for account {account_id} with {len(devices)} devices and {len(presets)} presets"
            )

            # Get selected content_item and device from cookies
            selected_content_item = decode_cookie_value(
                request.cookies.get("soundcork_selected_content_item_name")
            )
            is_playing = request.cookies.get("soundcork_is_playing", "false")
            volume_state = None
            now_playing_state = None
            source_list: list[dict[str, str | bool]] = []
            bluetooth_available = False
            if selected_device_id:
                now_playing_state = speakers.now_playing_state(selected_device_id)
                volume_state = speakers.volume_state(selected_device_id)
                source_list = speakers.source_list(selected_device_id)
                bluetooth_available = any(
                    source.get("source") == "BLUETOOTH" for source in source_list
                )

            return templates.TemplateResponse(
                request=request,
                name="dashboard.html",
                context={
                    "account_id": account_id,
                    "account_label": account_label,
                    "page_id": page_id,
                    "panel_stack": panel_stack,
                    "devices": devices,
                    "presets": presets,
                    "preset_slots": preset_slots,
                    "selected_content_item": selected_content_item,
                    "selected_device": selected_device,
                    "selected_device_id": selected_device_id,
                    "is_playing": is_playing,
                    "now_playing_state": now_playing_state,
                    "volume_state": volume_state,
                    "source_list": source_list,
                    "bluetooth_available": bluetooth_available,
                    "sleep_job": job_summary("sleep", selected_device_id),
                    "alarm_job": job_summary("alarm", selected_device_id),
                    "scheduled_jobs": scheduled_job_list(selected_device_id),
                    "backup_files": list_backup_files(),
                    "preset_numbers": preset_numbers,
                    "selected_preset_number": selected_preset_number,
                    "preset_query": preset_query,
                    "preset_results": preset_results,
                    "preset_search_error": translate_message(
                        preset_search_error, user_settings.language
                    ),
                    **template_language_context(user_settings),
                    "default_tunein_search_endpoint": DEFAULT_TUNEIN_SEARCH_ENDPOINT,
                    "notice": translate_message(notice, user_settings.language),
                    "error": translate_message(request_error, user_settings.language)
                    or None,
                },
            )

        except Exception as e:
            logger.error(f"Error rendering dashboard: {e}")
            fallback_settings = load_user_settings()

            # Still try to get selected content_item/device from cookies
            selected_content_item = decode_cookie_value(
                request.cookies.get("soundcork_selected_content_item_name")
            )
            selected_device = decode_cookie_value(
                request.cookies.get("soundcork_selected_device")
            )
            selected_device_id = request.cookies.get("soundcork_selected_device_id")
            is_playing = request.cookies.get("soundcork_is_playing", "false")

            return templates.TemplateResponse(
                request=request,
                name="dashboard.html",
                context={
                    "account_id": account_id,
                    "account_label": "Unknown",
                    "page_id": page_id,
                    "panel_stack": panel_stack,
                    "devices": [],
                    "presets": [],
                    "preset_slots": [],
                    "selected_content_item": selected_content_item,
                    "selected_device": selected_device,
                    "selected_device_id": selected_device_id,
                    "is_playing": is_playing,
                    "now_playing_state": None,
                    "volume_state": None,
                    "source_list": [],
                    "bluetooth_available": False,
                    "sleep_job": None,
                    "alarm_job": None,
                    "scheduled_jobs": [],
                    "backup_files": [],
                    "preset_numbers": [1, 2, 3, 4, 5, 6],
                    "selected_preset_number": 1,
                    "preset_query": "",
                    "preset_results": [],
                    "preset_search_error": "",
                    **template_language_context(fallback_settings),
                    "default_tunein_search_endpoint": DEFAULT_TUNEIN_SEARCH_ENDPOINT,
                    "notice": "",
                    "error": translate(
                        "error_loading_dashboard_data", fallback_settings.language
                    ),
                },
            )

    @router.get("/miniapp/home", response_class=HTMLResponse)
    async def home_page(request: Request):
        """Display the user-configurable home surface."""
        return await dashboard_page(request, page_id="home", panel_stack="home")

    @router.get("/miniapp/settings", response_class=HTMLResponse)
    async def settings_page(request: Request):
        """Display account, network, and provider settings."""
        account_id = request.cookies.get("soundcork_account_id", "")
        if not account_id:
            return RedirectResponse(url="/miniapp/login", status_code=303)

        if not datastore.account_exists(account_id):
            response = RedirectResponse(url="/miniapp/login", status_code=303)
            response.delete_cookie("soundcork_account_id")
            response.delete_cookie("soundcork_account_label")
            return response

        account_label = datastore.get_account_info(account_id)
        selected_device = decode_cookie_value(
            request.cookies.get("soundcork_selected_device")
        )
        selected_device_id = request.cookies.get("soundcork_selected_device_id")
        selected_content_item = decode_cookie_value(
            request.cookies.get("soundcork_selected_content_item_name")
        )
        is_playing = request.cookies.get("soundcork_is_playing", "false")
        combined_devices = speakers.all_devices()
        devices = []
        account_device_ids = datastore.list_devices(account_id)
        presets = get_presets_or_resync(account_id, selected_device_id)
        preset_numbers = [1, 2, 3, 4, 5, 6]
        presets_by_number = {
            int(preset.id): preset
            for preset in presets
            if preset.id.isdigit() and int(preset.id) in preset_numbers
        }
        preset_slots = [
            {"number": number, "preset": presets_by_number.get(number)}
            for number in preset_numbers
        ]
        now_playing_state = None
        volume_state = None
        source_list: list[dict[str, str | bool]] = []
        bluetooth_available = False
        if selected_device_id:
            now_playing_state = speakers.now_playing_state(selected_device_id)
            volume_state = speakers.volume_state(selected_device_id)
            source_list = speakers.source_list(selected_device_id)
            bluetooth_available = any(
                source.get("source") == "BLUETOOTH" for source in source_list
            )
        for device_id in account_device_ids:
            device_info = datastore.get_device_info(account_id, device_id)
            cd = combined_devices.get(device_id)
            online = bool(cd and cd.online)
            network = speakers.network_status(device_id) if online else None
            network_bindings = network.get("bindings", []) if network else []
            if not isinstance(network_bindings, list):
                network_bindings = []
            current_ip = str(network.get("ip_address", "") if network else "") or str(
                getattr(cd, "ip", "") or ""
            )
            network_kind = str(network.get("kind", "") if network else "")
            wifi_profile_count = (
                network.get("wifi_profile_count", "") if network else ""
            )
            devices.append(
                {
                    "device_id": device_id,
                    "name": device_info.name,
                    "product_code": device_info.product_code,
                    "ip_address": device_info.ip_address,
                    "status": "online" if online else "offline",
                    "ssid": str(network.get("ssid", "") if network else ""),
                    "configured_ssid": str(
                        network.get("configured_ssid", "") if network else ""
                    ),
                    "current_ip": current_ip,
                    "network_kind": network_kind,
                    "network_kind_key": network_kind_key(network_kind),
                    "network_interface": str(
                        network.get("name", "") if network else ""
                    ),
                    "network_mac": str(
                        network.get("mac_address", "") if network else ""
                    ),
                    "network_rssi": str(network.get("rssi", "") if network else ""),
                    "network_frequency": format_frequency_khz(
                        network.get("frequency_khz", "") if network else ""
                    ),
                    "network_bindings": ", ".join(
                        str(binding) for binding in network_bindings if binding
                    ),
                    "network_info_interfaces": format_network_interfaces(network),
                    "wifi_profile_count": (
                        "" if wifi_profile_count is None else str(wifi_profile_count)
                    ),
                    "image_file": get_device_image(device_info.product_code),
                }
            )
        user_settings = load_user_settings()

        return templates.TemplateResponse(
            request=request,
            name="settings.html",
            context={
                "account_id": account_id,
                "account_label": account_label,
                "devices": devices,
                "selected_content_item": selected_content_item,
                "selected_device": selected_device,
                "selected_device_id": selected_device_id,
                "is_playing": is_playing,
                "now_playing_state": now_playing_state,
                "volume_state": volume_state,
                "source_list": source_list,
                "bluetooth_available": bluetooth_available,
                "sleep_job": job_summary("sleep", selected_device_id),
                "alarm_job": job_summary("alarm", selected_device_id),
                "scheduled_jobs": scheduled_job_list(selected_device_id),
                "backup_files": list_backup_files(),
                "preset_numbers": preset_numbers,
                "preset_slots": preset_slots,
                "selected_preset_number": 1,
                "preset_query": "",
                "preset_results": [],
                "preset_search_error": "",
                **template_language_context(user_settings),
                "default_tunein_search_endpoint": DEFAULT_TUNEIN_SEARCH_ENDPOINT,
                "notice": translate_message(
                    request.query_params.get("notice", ""), user_settings.language
                ),
                "error": translate_message(
                    request.query_params.get("error", ""), user_settings.language
                )
                or None,
            },
        )

    @router.post("/miniapp/settings/account")
    async def update_account_settings(request: Request):
        account_id = request.cookies.get("soundcork_account_id", "")
        if not account_id or not datastore.account_exists(account_id):
            return RedirectResponse(url="/miniapp/login", status_code=303)

        form_data = await request.form()
        account_label = str(form_data.get("account_label", "")).strip()
        if not account_label:
            return RedirectResponse(
                url="/miniapp/settings?error=Account%20name%20is%20required",
                status_code=303,
            )

        datastore.save_account_info(account_id, account_label)
        response = RedirectResponse(
            url="/miniapp/settings?notice=Account%20saved", status_code=303
        )
        response.set_cookie(
            key="soundcork_account_label",
            value=encode_cookie_value(account_label),
            max_age=86400 * 30,
            httponly=False,
            samesite="strict",
        )
        return response

    @router.post("/miniapp/settings/search")
    async def update_search_settings(request: Request):
        account_id = request.cookies.get("soundcork_account_id", "")
        if not account_id or not datastore.account_exists(account_id):
            return RedirectResponse(url="/miniapp/login", status_code=303)

        form_data = await request.form()
        endpoint = str(form_data.get("tunein_search_endpoint", "")).strip()
        try:
            settings = load_user_settings()
            settings.tunein_search_endpoint = normalize_tunein_search_endpoint(endpoint)
            save_user_settings(settings)
        except ValueError:
            return RedirectResponse(
                url="/miniapp/settings?error=Search%20endpoint%20must%20be%20a%20URL",
                status_code=303,
            )

        return RedirectResponse(
            url="/miniapp/settings?notice=Search%20endpoint%20saved",
            status_code=303,
        )

    @router.post("/miniapp/settings/search/reset")
    async def reset_search_settings(request: Request):
        account_id = request.cookies.get("soundcork_account_id", "")
        if not account_id or not datastore.account_exists(account_id):
            return RedirectResponse(url="/miniapp/login", status_code=303)

        settings = load_user_settings()
        settings.tunein_search_endpoint = DEFAULT_TUNEIN_SEARCH_ENDPOINT
        save_user_settings(settings)
        return RedirectResponse(
            url="/miniapp/settings?notice=Default%20search%20endpoint%20restored",
            status_code=303,
        )

    @router.post("/miniapp/settings/visual")
    async def update_visual_settings(request: Request):
        account_id = require_account(request)
        if not account_id:
            return RedirectResponse(url="/miniapp/login", status_code=303)

        form_data = await request.form()
        settings = load_user_settings()
        settings.global_panel_style = normalize_panel_style(
            {
                "background_color": form_data.get("background_color"),
                "text_color": form_data.get("text_color"),
                "border_color": form_data.get("border_color"),
                "button_background_color": form_data.get("button_background_color"),
            }
        )
        settings.site_style = normalize_site_style(
            {
                "page_background_color": form_data.get("page_background_color"),
                "input_background_color": form_data.get("input_background_color"),
                "topbar_background_color": form_data.get("topbar_background_color"),
                "topbar_text_color": form_data.get("topbar_text_color"),
                "topbar_muted_color": form_data.get("topbar_muted_color"),
                "topbar_accent_color": form_data.get("topbar_accent_color"),
                "topbar_border_color": form_data.get("topbar_border_color"),
            }
        )
        settings.visual_theme = "custom"
        settings.panel_style = {}
        save_user_settings(settings)
        return RedirectResponse(
            url="/miniapp/settings?notice=Panel%20colors%20applied",
            status_code=303,
        )

    @router.post("/miniapp/settings/visual/light")
    async def apply_light_visual_settings(request: Request):
        account_id = require_account(request)
        if not account_id:
            return RedirectResponse(url="/miniapp/login", status_code=303)

        settings = load_user_settings()
        settings.global_panel_style = normalize_panel_style(LIGHT_PANEL_STYLE)
        settings.site_style = normalize_site_style(LIGHT_SITE_STYLE)
        settings.visual_theme = "light"
        settings.panel_style = {}
        save_user_settings(settings)
        return RedirectResponse(
            url="/miniapp/settings?notice=Light%20theme%20applied",
            status_code=303,
        )

    @router.post("/miniapp/settings/visual/reset")
    async def reset_visual_settings(request: Request):
        return await apply_light_visual_settings(request)

    @router.post("/miniapp/settings/visual/dark")
    async def apply_dark_visual_settings(request: Request):
        account_id = require_account(request)
        if not account_id:
            return RedirectResponse(url="/miniapp/login", status_code=303)

        settings = load_user_settings()
        settings.global_panel_style = normalize_panel_style(DARK_PANEL_STYLE)
        settings.site_style = normalize_site_style(DARK_SITE_STYLE)
        settings.visual_theme = "dark"
        settings.panel_style = {}
        save_user_settings(settings)
        return RedirectResponse(
            url="/miniapp/settings?notice=Dark%20theme%20applied",
            status_code=303,
        )

    @router.post("/miniapp/settings/language")
    async def update_language_settings(request: Request):
        account_id = require_account(request)
        if not account_id:
            return RedirectResponse(url="/miniapp/login", status_code=303)

        form_data = await request.form()
        settings = load_user_settings()
        settings.language = normalize_language(form_data.get("language"))
        save_user_settings(settings)
        return RedirectResponse(
            url="/miniapp/settings?notice=Language%20saved",
            status_code=303,
        )

    @router.post("/miniapp/settings/usability")
    async def update_usability_settings(request: Request):
        account_id = require_account(request)
        if not account_id:
            return RedirectResponse(url="/miniapp/login", status_code=303)

        form_data = await request.form()
        settings = load_user_settings()
        try:
            settings.topbar_long_press_delay_ms = int(
                form_data.get("topbar_long_press_delay_ms", 900)
            )
            settings.now_playing_poll_interval_ms = int(
                form_data.get("now_playing_poll_interval_ms", 15000)
            )
            settings.volume_poll_interval_ms = int(
                form_data.get("volume_poll_interval_ms", 15000)
            )
        except (TypeError, ValueError):
            return RedirectResponse(
                url="/miniapp/settings?error=Invalid%20usability%20settings",
                status_code=303,
            )

        save_user_settings(settings)
        return RedirectResponse(
            url="/miniapp/settings?notice=Usability%20settings%20saved",
            status_code=303,
        )

    @router.post("/miniapp/settings/menu")
    async def update_menu_settings(request: Request):
        account_id = require_account(request)
        if not account_id:
            return RedirectResponse(url="/miniapp/login", status_code=303)

        form_data = await request.form()
        settings = load_user_settings()
        settings.menu_settings_label = normalize_menu_label(
            str(form_data.get("settings_label", "Settings"))
        )
        custom_label = str(form_data.get("custom_label", "")).strip()
        custom_url = str(form_data.get("custom_url", "")).strip()

        try:
            if custom_label or custom_url:
                settings.menu_custom_buttons.append(
                    MenuButton(
                        label=custom_label[:30],
                        url=normalize_menu_url(custom_url),
                    )
                )
            save_user_settings(settings)
        except ValueError:
            return RedirectResponse(
                url="/miniapp/settings?error=Menu%20button%20URL%20is%20invalid",
                status_code=303,
            )

        return RedirectResponse(
            url="/miniapp/settings?notice=Menu%20saved",
            status_code=303,
        )

    @router.post("/miniapp/settings/menu/remove")
    async def remove_menu_button(request: Request):
        account_id = require_account(request)
        if not account_id:
            return RedirectResponse(url="/miniapp/login", status_code=303)

        form_data = await request.form()
        try:
            button_index = int(str(form_data.get("button_index", "")))
        except ValueError:
            button_index = -1

        settings = load_user_settings()
        if 0 <= button_index < len(settings.menu_custom_buttons):
            settings.menu_custom_buttons.pop(button_index)
            save_user_settings(settings)

        return RedirectResponse(
            url="/miniapp/settings?notice=Menu%20saved",
            status_code=303,
        )

    @router.post("/miniapp/settings/layout")
    async def update_layout_settings(request: Request):
        account_id = require_account(request)
        if not account_id:
            return JSONResponse(
                {"ok": False, "error": "login required"},
                status_code=401,
            )

        try:
            payload = await request.json()
        except Exception:
            return JSONResponse(
                {"ok": False, "error": "invalid layout"},
                status_code=400,
            )

        settings = load_user_settings()
        panel_order = payload.get("panel_order", {})
        panel_surface = payload.get("panel_surface", {})
        topbar_order = payload.get("topbar_order")
        topbar_row = payload.get("topbar_row")
        if isinstance(panel_order, dict):
            settings.panel_order = {
                str(surface): normalize_panel_list(order)
                for surface, order in panel_order.items()
                if isinstance(order, list)
            }
        if isinstance(panel_surface, dict):
            settings.panel_surface = {
                str(panel_id): str(surface)
                for panel_id, surface in panel_surface.items()
                if str(panel_id).strip() and str(surface).strip()
            }
        if topbar_order is not None:
            settings.topbar_order = normalize_topbar_order(topbar_order)
        if topbar_row is not None:
            settings.topbar_row = normalize_topbar_row(topbar_row)

        save_user_settings(settings)
        return JSONResponse({"ok": True})

    @router.post("/miniapp/settings/preferences")
    async def update_preference_settings(request: Request):
        account_id = require_account(request)
        if not account_id:
            return JSONResponse(
                {"ok": False, "error": "login required"},
                status_code=401,
            )

        try:
            payload = await request.json()
        except Exception:
            return JSONResponse(
                {"ok": False, "error": "invalid preferences"},
                status_code=400,
            )

        settings = load_user_settings()
        global_panel_style_updated = False
        if "preset_drag_opacity" in payload:
            try:
                settings.preset_drag_opacity = float(payload["preset_drag_opacity"])
            except (TypeError, ValueError):
                return JSONResponse(
                    {"ok": False, "error": "invalid opacity"},
                    status_code=400,
                )
        if "preset_thumbnail_size_px" in payload:
            try:
                settings.preset_thumbnail_size_px = int(
                    payload["preset_thumbnail_size_px"]
                )
            except (TypeError, ValueError):
                return JSONResponse(
                    {"ok": False, "error": "invalid preset thumbnail size"},
                    status_code=400,
                )
        if "preset_long_press_delay_ms" in payload:
            try:
                settings.preset_long_press_delay_ms = int(
                    payload["preset_long_press_delay_ms"]
                )
            except (TypeError, ValueError):
                return JSONResponse(
                    {"ok": False, "error": "invalid long press delay"},
                    status_code=400,
                )
        if "preset_search_result_delay_ms" in payload:
            try:
                settings.preset_search_result_delay_ms = int(
                    payload["preset_search_result_delay_ms"]
                )
            except (TypeError, ValueError):
                return JSONResponse(
                    {"ok": False, "error": "invalid search result delay"},
                    status_code=400,
                )
        if "timer_job_visible_count" in payload:
            try:
                settings.timer_job_visible_count = int(
                    payload["timer_job_visible_count"]
                )
            except (TypeError, ValueError):
                return JSONResponse(
                    {"ok": False, "error": "invalid visible scheduled rows"},
                    status_code=400,
                )
        if "topbar_long_press_delay_ms" in payload:
            try:
                settings.topbar_long_press_delay_ms = int(
                    payload["topbar_long_press_delay_ms"]
                )
            except (TypeError, ValueError):
                return JSONResponse(
                    {"ok": False, "error": "invalid topbar long press delay"},
                    status_code=400,
                )
        if "now_playing_poll_interval_ms" in payload:
            try:
                settings.now_playing_poll_interval_ms = int(
                    payload["now_playing_poll_interval_ms"]
                )
            except (TypeError, ValueError):
                return JSONResponse(
                    {"ok": False, "error": "invalid polling interval"},
                    status_code=400,
                )
        if "volume_poll_interval_ms" in payload:
            try:
                settings.volume_poll_interval_ms = int(
                    payload["volume_poll_interval_ms"]
                )
            except (TypeError, ValueError):
                return JSONResponse(
                    {"ok": False, "error": "invalid volume polling interval"},
                    status_code=400,
                )
        if "preset_legacy_ui" in payload:
            settings.preset_legacy_ui = bool(payload["preset_legacy_ui"])
        if "volume_legacy_ui" in payload:
            settings.volume_legacy_ui = bool(payload["volume_legacy_ui"])
        if "language" in payload:
            settings.language = normalize_language(payload["language"])
        if "visual_theme" in payload:
            requested_theme = str(payload["visual_theme"] or "").strip().lower()
            if requested_theme in {"light", "dark", "custom"}:
                settings.visual_theme = requested_theme
        if "global_panel_style" in payload:
            try:
                settings.global_panel_style = normalize_panel_style(
                    payload["global_panel_style"]
                )
                settings.visual_theme = "custom"
                global_panel_style_updated = True
            except Exception:
                return JSONResponse(
                    {"ok": False, "error": "invalid global panel style"},
                    status_code=400,
                )
        if "site_style" in payload:
            try:
                settings.site_style = normalize_site_style(payload["site_style"])
                settings.visual_theme = "custom"
            except Exception:
                return JSONResponse(
                    {"ok": False, "error": "invalid site style"},
                    status_code=400,
                )
        if "panel_style" in payload:
            panel_style = payload["panel_style"]
            if not isinstance(panel_style, dict):
                return JSONResponse(
                    {"ok": False, "error": "invalid panel style"},
                    status_code=400,
                )
            try:
                settings.panel_style = {
                    str(panel_id): normalize_panel_style(style)
                    for panel_id, style in panel_style.items()
                    if str(panel_id).strip()
                }
            except Exception:
                return JSONResponse(
                    {"ok": False, "error": "invalid panel style"},
                    status_code=400,
                )
        elif global_panel_style_updated:
            settings.panel_style = {}
        if "panel_label" in payload:
            panel_label = payload["panel_label"]
            if not isinstance(panel_label, dict):
                return JSONResponse(
                    {"ok": False, "error": "invalid panel label"},
                    status_code=400,
                )
            settings.panel_label = {
                str(panel_id): str(label or "").strip()[:80]
                for panel_id, label in panel_label.items()
                if str(panel_id).strip()
            }
        if "timer_section_label" in payload:
            timer_section_label = payload["timer_section_label"]
            if not isinstance(timer_section_label, dict):
                return JSONResponse(
                    {"ok": False, "error": "invalid timer section label"},
                    status_code=400,
                )
            settings.timer_section_label = {
                str(label_key): str(label or "").strip()[:80]
                for label_key, label in timer_section_label.items()
                if str(label_key).strip()
            }

        save_user_settings(settings)
        normalized_settings = load_user_settings()
        return JSONResponse(
            {
                "ok": True,
                "preset_drag_opacity": normalized_settings.preset_drag_opacity,
                "preset_thumbnail_size_px": (
                    normalized_settings.preset_thumbnail_size_px
                ),
                "preset_long_press_delay_ms": (
                    normalized_settings.preset_long_press_delay_ms
                ),
                "preset_search_result_delay_ms": (
                    normalized_settings.preset_search_result_delay_ms
                ),
                "timer_job_visible_count": (
                    normalized_settings.timer_job_visible_count
                ),
                "topbar_long_press_delay_ms": (
                    normalized_settings.topbar_long_press_delay_ms
                ),
                "now_playing_poll_interval_ms": (
                    normalized_settings.now_playing_poll_interval_ms
                ),
                "volume_poll_interval_ms": normalized_settings.volume_poll_interval_ms,
                "preset_legacy_ui": normalized_settings.preset_legacy_ui,
                "volume_legacy_ui": normalized_settings.volume_legacy_ui,
                "language": normalized_settings.language,
                "visual_theme": normalized_settings.visual_theme,
                "translations": translation_bundle(normalized_settings.language),
                "global_panel_style": normalized_settings.global_panel_style.model_dump(),
                "site_style": normalized_settings.site_style.model_dump(),
                "panel_style": {
                    panel_id: style.model_dump()
                    for panel_id, style in normalized_settings.panel_style.items()
                },
                "panel_label": normalized_settings.panel_label,
                "timer_section_label": normalized_settings.timer_section_label,
            }
        )

    @router.post("/miniapp/settings/device")
    async def update_device_settings(request: Request):
        account_id = request.cookies.get("soundcork_account_id", "")
        if not account_id or not datastore.account_exists(account_id):
            return RedirectResponse(url="/miniapp/login", status_code=303)

        form_data = await request.form()
        device_id = str(form_data.get("device_id", "")).strip()
        if device_id not in datastore.list_devices(account_id):
            return RedirectResponse(
                url="/miniapp/settings?error=Unknown%20device", status_code=303
            )

        device_info = datastore.get_device_info(account_id, device_id)
        device_info.name = str(form_data.get("device_name", "")).strip()
        device_info.ip_address = str(form_data.get("ip_address", "")).strip()
        if not device_info.name or not device_info.ip_address:
            return RedirectResponse(
                url="/miniapp/settings?error=Device%20name%20and%20IP%20are%20required",
                status_code=303,
            )

        datastore.save_device_info(device_info, account_id)
        return RedirectResponse(
            url="/miniapp/settings?notice=Radio%20network%20settings%20saved",
            status_code=303,
        )

    @router.post("/miniapp/settings/wifi")
    async def update_wifi_settings(request: Request):
        account_id = request.cookies.get("soundcork_account_id", "")
        if not account_id or not datastore.account_exists(account_id):
            return RedirectResponse(url="/miniapp/login", status_code=303)

        form_data = await request.form()
        device_id = str(form_data.get("device_id", "")).strip()
        ssid = str(form_data.get("ssid", "")).strip()
        password = str(form_data.get("password", ""))
        security_type = str(form_data.get("security_type", "wpa_or_wpa2"))
        allowed_security_types = {
            "none",
            "wep",
            "wpatkip",
            "wpaaes",
            "wpa2tkip",
            "wpa2aes",
            "wpa_or_wpa2",
        }

        if device_id not in datastore.list_devices(account_id):
            return RedirectResponse(
                url="/miniapp/settings?error=Unknown%20device", status_code=303
            )
        if not ssid or security_type not in allowed_security_types:
            return RedirectResponse(
                url="/miniapp/settings?error=Invalid%20Wi-Fi%20settings",
                status_code=303,
            )

        success = speakers.add_wireless_profile(
            device_id, ssid, password, security_type
        )
        if not success:
            return RedirectResponse(
                url="/miniapp/settings?error=Wi-Fi%20update%20failed",
                status_code=303,
            )

        return RedirectResponse(
            url="/miniapp/settings?notice=Wi-Fi%20profile%20sent",
            status_code=303,
        )

    @router.get("/miniapp/now-playing")
    async def now_playing(request: Request):
        selected_device_id = request.cookies.get("soundcork_selected_device_id")
        if not selected_device_id:
            return JSONResponse(
                {
                    "ok": False,
                    "now_playing": None,
                    "volume_state": None,
                    "jobs": jobs_payload(selected_device_id),
                    "job_list": scheduled_job_list(selected_device_id),
                }
            )

        state = speakers.now_playing_state(selected_device_id)
        volume_state = speakers.volume_state(selected_device_id)
        return JSONResponse(
            {
                "ok": state is not None,
                "now_playing": state,
                "volume_state": volume_state,
                "jobs": jobs_payload(selected_device_id),
                "job_list": scheduled_job_list(selected_device_id),
            }
        )

    @router.get("/miniapp/search-stations")
    async def search_stations(request: Request):
        account_id = require_account(request)
        if not account_id:
            return JSONResponse(
                {"ok": False, "error": "login required", "results": []},
                status_code=401,
            )

        query = (
            request.query_params.get("preset_query")
            or request.query_params.get("q")
            or ""
        ).strip()
        if not query:
            return JSONResponse({"ok": True, "query": "", "results": []})

        try:
            results = tunein_station_search_results(query)
        except Exception as e:
            logger.error("TuneIn preset search failed: %s", e)
            return JSONResponse(
                {
                    "ok": False,
                    "query": query,
                    "error": "Station search failed",
                    "results": [],
                },
                status_code=502,
            )

        return JSONResponse({"ok": True, "query": query, "results": results})

    @router.post("/miniapp/sleep")
    async def schedule_sleep(request: Request):
        selected_device_id = request.cookies.get("soundcork_selected_device_id")
        if not selected_device_id:
            if wants_json(request):
                return JSONResponse(
                    {
                        "ok": False,
                        "error": translate(
                            "select_device_first", load_user_settings().language
                        ),
                        "jobs": jobs_payload(selected_device_id),
                        "job_list": scheduled_job_list(selected_device_id),
                    },
                    status_code=400,
                )
            return RedirectResponse(
                url="/miniapp/dashboard?error=Select%20a%20device%20first",
                status_code=303,
            )

        form_data = await request.form()
        minutes_raw = str(form_data.get("sleep_minutes", "")).strip()
        time_raw = str(form_data.get("sleep_time", "")).strip()
        repeat_days = []

        try:
            repeat_days = repeat_days_from_form(form_data)
            if repeat_days:
                if not time_raw:
                    raise ValueError("Repeating sleep needs a clock time")
                run_at = next_repeating_clock_time(time_raw, repeat_days)
                label = f"Sleep at {run_at.strftime('%H:%M')}"
            elif minutes_raw:
                minutes = max(1, min(360, int(minutes_raw)))
                run_at = datetime.now() + timedelta(minutes=minutes)
                label = f"Sleep in {minutes} min"
            elif time_raw:
                run_at = next_clock_time(time_raw)
                label = f"Sleep at {run_at.strftime('%H:%M')}"
            else:
                raise ValueError("Missing sleep time")
        except ValueError:
            if wants_json(request):
                return JSONResponse(
                    {
                        "ok": False,
                        "error": translate(
                            "invalid_sleep_timer", load_user_settings().language
                        ),
                        "jobs": jobs_payload(selected_device_id),
                        "job_list": scheduled_job_list(selected_device_id),
                    },
                    status_code=400,
                )
            return RedirectResponse(
                url="/miniapp/dashboard?error=Invalid%20sleep%20timer",
                status_code=303,
            )

        job_id = schedule_job(
            "sleep",
            selected_device_id,
            run_at,
            label,
            lambda: speakers.standby_device(selected_device_id),
            repeat_days=repeat_days,
            time_value=time_raw if repeat_days else "",
        )
        if wants_json(request):
            language = load_user_settings().language
            sleep_job = job_summary_by_id(job_id)
            return JSONResponse(
                {
                    "ok": True,
                    "notice": translate_message(label, language),
                    "jobs": jobs_payload(selected_device_id),
                    "job_list": scheduled_job_list(selected_device_id),
                    "job": sleep_job,
                }
            )
        return RedirectResponse(
            url="/miniapp/dashboard",
            status_code=303,
        )

    @router.post("/miniapp/sleep/cancel")
    async def cancel_sleep(request: Request):
        selected_device_id = request.cookies.get("soundcork_selected_device_id")
        form_data = await request.form()
        job_id = str(form_data.get("job_id", "")).strip() or None
        if selected_device_id:
            cancel_job("sleep", selected_device_id, job_id)
        if wants_json(request):
            return JSONResponse(
                {
                    "ok": True,
                    "notice": translate(
                        "sleep_timer_cancelled", load_user_settings().language
                    ),
                    "jobs": jobs_payload(selected_device_id),
                    "job_list": scheduled_job_list(selected_device_id),
                    "job": None,
                }
            )
        return RedirectResponse(
            url="/miniapp/dashboard",
            status_code=303,
        )

    @router.post("/miniapp/sleep/toggle")
    async def toggle_sleep(request: Request):
        selected_device_id = request.cookies.get("soundcork_selected_device_id")
        form_data = await request.form()
        paused = str(form_data.get("paused", "")).lower() in {"1", "true", "on"}
        job_id = str(form_data.get("job_id", "")).strip() or None
        if selected_device_id:
            set_job_paused("sleep", selected_device_id, paused, job_id)
        notice = "Sleep timer paused" if paused else "Sleep timer resumed"
        if wants_json(request):
            language = load_user_settings().language
            sleep_job = job_summary_by_id(job_id) or job_summary("sleep", selected_device_id)
            return JSONResponse(
                {
                    "ok": True,
                    "notice": translate_message(notice, language),
                    "jobs": jobs_payload(selected_device_id),
                    "job_list": scheduled_job_list(selected_device_id),
                    "job": sleep_job,
                }
            )
        return RedirectResponse(
            url="/miniapp/dashboard",
            status_code=303,
        )

    @router.post("/miniapp/alarm")
    async def schedule_alarm(request: Request):
        selected_device_id = request.cookies.get("soundcork_selected_device_id")
        if not selected_device_id:
            if wants_json(request):
                return JSONResponse(
                    {
                        "ok": False,
                        "error": translate(
                            "select_device_first", load_user_settings().language
                        ),
                        "jobs": jobs_payload(selected_device_id),
                        "job_list": scheduled_job_list(selected_device_id),
                    },
                    status_code=400,
                )
            return RedirectResponse(
                url="/miniapp/dashboard?error=Select%20a%20device%20first",
                status_code=303,
            )

        form_data = await request.form()
        alarm_time = str(form_data.get("alarm_time", "")).strip()
        preset_id = str(form_data.get("alarm_preset_id", "")).strip()
        repeat_days = []
        if not preset_id:
            if wants_json(request):
                return JSONResponse(
                    {
                        "ok": False,
                        "error": translate(
                            "choose_alarm_preset", load_user_settings().language
                        ),
                        "jobs": jobs_payload(selected_device_id),
                        "job_list": scheduled_job_list(selected_device_id),
                    },
                    status_code=400,
                )
            return RedirectResponse(
                url="/miniapp/dashboard?error=Choose%20an%20alarm%20preset",
                status_code=303,
            )

        try:
            repeat_days = repeat_days_from_form(form_data)
            run_at = (
                next_repeating_clock_time(alarm_time, repeat_days)
                if repeat_days
                else next_clock_time(alarm_time)
            )
        except ValueError:
            if wants_json(request):
                return JSONResponse(
                    {
                        "ok": False,
                        "error": translate(
                            "invalid_alarm_time", load_user_settings().language
                        ),
                        "jobs": jobs_payload(selected_device_id),
                        "job_list": scheduled_job_list(selected_device_id),
                    },
                    status_code=400,
                )
            return RedirectResponse(
                url="/miniapp/dashboard?error=Invalid%20alarm%20time",
                status_code=303,
            )

        label = f"Alarm {run_at.strftime('%H:%M')} preset {preset_id}"
        job_id = schedule_job(
            "alarm",
            selected_device_id,
            run_at,
            label,
            lambda: speakers.play_content_item(selected_device_id, preset_id),
            repeat_days=repeat_days,
            time_value=alarm_time if repeat_days else "",
        )
        if wants_json(request):
            language = load_user_settings().language
            alarm_job = job_summary_by_id(job_id)
            return JSONResponse(
                {
                    "ok": True,
                    "notice": translate_message(label, language),
                    "jobs": jobs_payload(selected_device_id),
                    "job_list": scheduled_job_list(selected_device_id),
                    "job": alarm_job,
                }
            )
        return RedirectResponse(
            url="/miniapp/dashboard",
            status_code=303,
        )

    @router.post("/miniapp/alarm/test")
    async def test_alarm(request: Request):
        selected_device_id = request.cookies.get("soundcork_selected_device_id")
        if not selected_device_id:
            return RedirectResponse(
                url="/miniapp/dashboard?error=Select%20a%20device%20first",
                status_code=303,
            )

        form_data = await request.form()
        preset_id = str(form_data.get("alarm_preset_id", "")).strip()
        if not preset_id:
            return RedirectResponse(
                url="/miniapp/dashboard?error=Choose%20an%20alarm%20preset",
                status_code=303,
            )

        run_at = datetime.now() + timedelta(seconds=10)
        schedule_job(
            "alarm",
            selected_device_id,
            run_at,
            f"Alarm test preset {preset_id}",
            lambda: speakers.play_content_item(selected_device_id, preset_id),
        )
        return RedirectResponse(
            url="/miniapp/dashboard?notice=Alarm%20test%20scheduled%20in%2010%20seconds",
            status_code=303,
        )

    @router.post("/miniapp/alarm/cancel")
    async def cancel_alarm(request: Request):
        selected_device_id = request.cookies.get("soundcork_selected_device_id")
        form_data = await request.form()
        job_id = str(form_data.get("job_id", "")).strip() or None
        if selected_device_id:
            cancel_job("alarm", selected_device_id, job_id)
        if wants_json(request):
            return JSONResponse(
                {
                    "ok": True,
                    "notice": translate("alarm_cancelled", load_user_settings().language),
                    "jobs": jobs_payload(selected_device_id),
                    "job_list": scheduled_job_list(selected_device_id),
                    "job": None,
                }
            )
        return RedirectResponse(
            url="/miniapp/dashboard",
            status_code=303,
        )

    @router.post("/miniapp/alarm/toggle")
    async def toggle_alarm(request: Request):
        selected_device_id = request.cookies.get("soundcork_selected_device_id")
        form_data = await request.form()
        paused = str(form_data.get("paused", "")).lower() in {"1", "true", "on"}
        job_id = str(form_data.get("job_id", "")).strip() or None
        if selected_device_id:
            set_job_paused("alarm", selected_device_id, paused, job_id)
        notice = "Alarm paused" if paused else "Alarm resumed"
        if wants_json(request):
            language = load_user_settings().language
            alarm_job = job_summary_by_id(job_id) or job_summary("alarm", selected_device_id)
            return JSONResponse(
                {
                    "ok": True,
                    "notice": translate_message(notice, language),
                    "jobs": jobs_payload(selected_device_id),
                    "job_list": scheduled_job_list(selected_device_id),
                    "job": alarm_job,
                }
            )
        return RedirectResponse(
            url="/miniapp/dashboard",
            status_code=303,
        )

    @router.post("/miniapp/custom-stream")
    async def store_custom_stream(request: Request):
        selected_device_id = request.cookies.get("soundcork_selected_device_id")
        if not selected_device_id:
            return RedirectResponse(
                url="/miniapp/dashboard?error=Select%20a%20device%20first",
                status_code=303,
            )

        form_data = await request.form()
        try:
            preset_number = int(str(form_data.get("preset_number", "")))
        except ValueError:
            preset_number = 0

        stream_name = str(form_data.get("stream_name", "")).strip()
        stream_url = str(form_data.get("stream_url", "")).strip()
        image_url = str(form_data.get("image_url", "")).strip()
        success = speakers.store_direct_stream_as_preset(
            selected_device_id,
            preset_number,
            stream_name,
            stream_url,
            image_url,
        )
        if success:
            notice = encode_cookie_value(
                f"Saved {stream_name} to preset {preset_number}"
            )
            return RedirectResponse(
                url=f"/miniapp/dashboard?notice={notice}",
                status_code=303,
            )
        return RedirectResponse(
            url="/miniapp/dashboard?error=Custom%20stream%20save%20failed",
            status_code=303,
        )

    @router.post("/miniapp/resync-presets")
    async def resync_presets(request: Request):
        account_id = request.cookies.get("soundcork_account_id", "")
        selected_device_id = request.cookies.get("soundcork_selected_device_id")
        if not account_id or not datastore.account_exists(account_id):
            return RedirectResponse(url="/miniapp/login", status_code=303)
        if not selected_device_id:
            return RedirectResponse(
                url="/miniapp/dashboard?error=Select%20a%20device%20first",
                status_code=303,
            )

        success = speakers.sync_presets_from_speaker(account_id, selected_device_id)
        if success:
            return RedirectResponse(
                url="/miniapp/dashboard?notice=Presets%20resynced%20from%20radio",
                status_code=303,
            )
        return RedirectResponse(
            url="/miniapp/dashboard?error=Preset%20resync%20failed",
            status_code=303,
        )

    @router.post("/miniapp/backup")
    async def backup_layout(request: Request):
        account_id = request.cookies.get("soundcork_account_id", "")
        selected_device_id = request.cookies.get("soundcork_selected_device_id")
        if not account_id or not datastore.account_exists(account_id):
            return RedirectResponse(url="/miniapp/login", status_code=303)

        form_data = await request.form()
        settings = load_user_settings()
        panel_order_raw = str(form_data.get("panel_order", "")).strip()
        if panel_order_raw:
            try:
                panel_order = json.loads(panel_order_raw)
            except json.JSONDecodeError:
                panel_order = []
            if isinstance(panel_order, list):
                settings.panel_order["dashboard"] = normalize_panel_list(panel_order)
                save_user_settings(settings)

        backup = {
            "version": 1,
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "account_id": account_id,
            "device_id": selected_device_id,
            "presets": [
                preset.model_dump()
                for preset in get_presets_or_resync(account_id, selected_device_id)
            ],
            "panel_order": settings.panel_order.get("dashboard", []),
            "miniapp_settings": settings.model_dump(mode="json"),
        }
        filename = f"soundcork-backup-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
        with open(backup_path(filename), "w", encoding="utf-8") as backup_file:
            json.dump(backup, backup_file, indent=4)

        return RedirectResponse(
            url=f"/miniapp/dashboard?notice={encode_cookie_value(f'Saved backup {filename}')}",
            status_code=303,
        )

    @router.get("/miniapp/backup/{filename}")
    async def download_backup(filename: str):
        try:
            file_path = backup_path(filename)
            if not os.path.exists(file_path):
                raise ValueError("Missing backup")
        except ValueError:
            return RedirectResponse(
                url="/miniapp/dashboard?error=Backup%20not%20found",
                status_code=303,
            )
        return FileResponse(
            file_path,
            media_type="application/json",
            filename=filename,
        )

    @router.post("/miniapp/backup/delete")
    async def delete_backup(request: Request):
        account_id = request.cookies.get("soundcork_account_id", "")
        if not account_id or not datastore.account_exists(account_id):
            return RedirectResponse(url="/miniapp/login", status_code=303)

        form_data = await request.form()
        filename = str(form_data.get("backup_filename", "")).strip()
        return_to = form_data.get("return_to", "/miniapp/dashboard")
        try:
            file_path = backup_path(filename)
            if not os.path.exists(file_path):
                raise ValueError("Missing backup")
            os.remove(file_path)
        except ValueError:
            return RedirectResponse(
                url=backup_return_url(return_to, error="Backup not found"),
                status_code=303,
            )
        except OSError:
            return RedirectResponse(
                url=backup_return_url(return_to, error="Backup delete failed"),
                status_code=303,
            )

        return RedirectResponse(
            url=backup_return_url(return_to, notice="Backup deleted"),
            status_code=303,
        )

    @router.post("/miniapp/restore-backup-file")
    async def restore_backup_file(request: Request):
        account_id = request.cookies.get("soundcork_account_id", "")
        selected_device_id = request.cookies.get("soundcork_selected_device_id")
        if not account_id or not datastore.account_exists(account_id):
            return RedirectResponse(url="/miniapp/login", status_code=303)
        if not selected_device_id:
            return RedirectResponse(
                url="/miniapp/dashboard?error=Select%20a%20device%20first",
                status_code=303,
            )

        form_data = await request.form()
        filename = str(form_data.get("backup_filename", "")).strip()
        try:
            with open(backup_path(filename), "r", encoding="utf-8") as backup_file:
                backup = json.load(backup_file)
            presets = presets_from_backup(backup)
        except Exception:
            return RedirectResponse(
                url="/miniapp/dashboard?error=Invalid%20backup",
                status_code=303,
            )

        success = speakers.apply_presets(account_id, selected_device_id, presets)
        if success:
            apply_settings_from_backup(backup)
            return RedirectResponse(
                url=f"/miniapp/dashboard?notice={encode_cookie_value(f'Restored {filename}')}",
                status_code=303,
            )
        return RedirectResponse(
            url="/miniapp/dashboard?error=Backup%20restore%20failed",
            status_code=303,
        )

    @router.post("/miniapp/restore")
    async def restore_layout(request: Request):
        account_id = request.cookies.get("soundcork_account_id", "")
        selected_device_id = request.cookies.get("soundcork_selected_device_id")
        if not account_id or not datastore.account_exists(account_id):
            return RedirectResponse(url="/miniapp/login", status_code=303)
        if not selected_device_id:
            return RedirectResponse(
                url="/miniapp/dashboard?error=Select%20a%20device%20first",
                status_code=303,
            )

        form_data = await request.form()
        backup_json = str(form_data.get("backup_json", "")).strip()
        try:
            backup = json.loads(backup_json)
            presets = presets_from_backup(backup)
        except Exception:
            return RedirectResponse(
                url="/miniapp/dashboard?error=Invalid%20backup",
                status_code=303,
            )

        success = speakers.apply_presets(account_id, selected_device_id, presets)
        if success:
            apply_settings_from_backup(backup)
            return RedirectResponse(
                url="/miniapp/dashboard?notice=Backup%20restored",
                status_code=303,
            )
        return RedirectResponse(
            url="/miniapp/dashboard?error=Backup%20restore%20failed",
            status_code=303,
        )

    @router.post("/miniapp/select-content-item")
    async def select_content_item(request: Request):
        """Handle content_item selection and set cookie."""
        try:
            form_data = await request.form()
            content_item_id = form_data.get("content_item_id")
            content_item_name = form_data.get("content_item_name")
            content_item_art = form_data.get("content_item_art")

            if (
                not isinstance(content_item_id, str)
                or not isinstance(content_item_name, str)
                or not content_item_id
                or not content_item_name
            ):
                if wants_json(request):
                    return JSONResponse(
                        {"ok": False, "error": "invalid preset request"},
                        status_code=400,
                    )
                return RedirectResponse(url="/miniapp/dashboard", status_code=303)

            response = RedirectResponse(url="/miniapp/dashboard", status_code=303)

            selected_device_id = request.cookies.get("soundcork_selected_device_id")
            success = False
            if selected_device_id:
                success = speakers.play_content_item(
                    selected_device_id, content_item_id
                )
                if success:
                    logger.info(
                        f"Started playback from preset click: content_item {content_item_id} on device {selected_device_id}"
                    )
                else:
                    logger.error("Failed to start playback from preset click")

            if wants_json(request):
                return playback_json_response(
                    content_item_id,
                    content_item_name,
                    success,
                    selected_device_id,
                    content_item_art if isinstance(content_item_art, str) else "",
                )

            set_selected_content_cookies(
                response,
                content_item_id,
                content_item_name,
                success if selected_device_id else None,
            )
            return response

        except Exception as e:
            logger.error(f"Error selecting content_item: {e}")
            if wants_json(request):
                return JSONResponse(
                    {"ok": False, "error": "preset play failed"},
                    status_code=500,
                )
            return RedirectResponse(url="/miniapp/dashboard", status_code=303)

    @router.post("/miniapp/select-device")
    async def select_device(request: Request):
        """Handle device selection and set cookie."""
        try:
            form_data = await request.form()
            device_id = form_data.get("device_id")
            device_name = form_data.get("device_name")

            if (
                not isinstance(device_id, str)
                or not isinstance(device_name, str)
                or not device_id
                or not device_name
            ):
                return RedirectResponse(url="/miniapp/devices", status_code=303)

            account_id = request.cookies.get("soundcork_account_id", "")
            if account_id and datastore.account_exists(account_id):
                matching_device = next(
                    (
                        device
                        for device in account_device_cards(account_id)
                        if device["device_id"] == device_id
                    ),
                    None,
                )
                if not matching_device:
                    return RedirectResponse(
                        url="/miniapp/devices?error=Unknown%20radio",
                        status_code=303,
                    )
                response = selected_device_response(matching_device)
                logger.info(
                    "Device selected: %s (%s)",
                    matching_device["name"],
                    matching_device["device_id"],
                )
                return response

            response = RedirectResponse(url="/miniapp/dashboard", status_code=303)
            response.set_cookie(
                key="soundcork_selected_device",
                value=encode_cookie_value(device_name),
                max_age=86400 * 30,  # 30 days
                httponly=False,
                samesite="strict",
            )
            # Also store device_id for future use
            response.set_cookie(
                key="soundcork_selected_device_id",
                value=device_id,
                max_age=86400 * 30,
                httponly=True,
                samesite="strict",
            )
            logger.info(f"Device selected: {device_name} ({device_id})")
            return response

        except Exception as e:
            logger.error(f"Error selecting device: {e}")
            return RedirectResponse(url="/miniapp/dashboard", status_code=303)

    @router.post("/miniapp/store-tunein-preset")
    async def store_tunein_preset(request: Request):
        """Store a TuneIn search result as a numbered speaker preset."""
        try:
            selected_device_id = request.cookies.get("soundcork_selected_device_id")
            if not selected_device_id:
                if wants_json(request):
                    return JSONResponse(
                        {"ok": False, "error": "select a device first"},
                        status_code=400,
                    )
                return RedirectResponse(
                    url="/miniapp/dashboard?error=Select%20a%20device%20first",
                    status_code=303,
                )

            form_data = await request.form()
            preset_number_raw = form_data.get("preset_number")
            station_id = form_data.get("station_id")
            station_name = form_data.get("station_name")
            image_url = form_data.get("image_url") or ""

            if (
                not isinstance(preset_number_raw, str)
                or not isinstance(station_id, str)
                or not isinstance(station_name, str)
                or not isinstance(image_url, str)
            ):
                if wants_json(request):
                    return JSONResponse(
                        {"ok": False, "error": "invalid preset request"},
                        status_code=400,
                    )
                return RedirectResponse(
                    url="/miniapp/dashboard?error=Invalid%20preset%20request",
                    status_code=303,
                )

            try:
                preset_number = int(preset_number_raw)
            except ValueError:
                preset_number = 0

            success = speakers.store_tunein_station_as_preset(
                selected_device_id,
                preset_number,
                station_id,
                station_name,
                image_url,
            )
            if success:
                notice_text = f"Saved {station_name} to preset {preset_number}"
                if wants_json(request):
                    account_id = require_account(request)
                    return JSONResponse(
                        {
                            "ok": True,
                            "notice": translate_message(
                                notice_text,
                                load_user_settings().language,
                            ),
                            "preset": {
                                "id": str(preset_number),
                                "name": station_name,
                                "container_art": image_url,
                            },
                            "preset_slots": (
                                preset_slots_payload(account_id, selected_device_id)
                                if account_id
                                else []
                            ),
                        }
                    )
                notice = encode_cookie_value(notice_text)
                return RedirectResponse(
                    url=f"/miniapp/dashboard?notice={notice}", status_code=303
                )

            if wants_json(request):
                return JSONResponse(
                    {"ok": False, "error": "Preset save failed"},
                    status_code=502,
                )
            return RedirectResponse(
                url="/miniapp/dashboard?error=Preset%20save%20failed",
                status_code=303,
            )
        except Exception as e:
            logger.error(f"Error storing TuneIn preset: {e}")
            if wants_json(request):
                return JSONResponse(
                    {"ok": False, "error": "Preset save failed"},
                    status_code=500,
                )
            return RedirectResponse(
                url="/miniapp/dashboard?error=Preset%20save%20failed",
                status_code=303,
            )

    @router.post("/miniapp/play-tunein-station")
    async def play_tunein_station(request: Request):
        """Play a TuneIn search result immediately without saving it."""
        try:
            selected_device_id = request.cookies.get("soundcork_selected_device_id")
            if not selected_device_id:
                return RedirectResponse(
                    url="/miniapp/dashboard?error=Select%20a%20device%20first",
                    status_code=303,
                )

            form_data = await request.form()
            station_id = form_data.get("station_id")
            station_name = form_data.get("station_name")
            image_url = form_data.get("image_url")
            if not isinstance(station_id, str) or not isinstance(station_name, str):
                if wants_json(request):
                    return JSONResponse(
                        {"ok": False, "error": "invalid station request"},
                        status_code=400,
                    )
                return RedirectResponse(
                    url="/miniapp/dashboard?error=Invalid%20station%20request",
                    status_code=303,
                )

            success = speakers.play_tunein_station(
                selected_device_id, station_id, station_name
            )
            if not success:
                if wants_json(request):
                    return JSONResponse(
                        {"ok": False, "error": "station play failed"},
                        status_code=502,
                    )
                return RedirectResponse(
                    url="/miniapp/dashboard?error=Station%20play%20failed",
                    status_code=303,
                )

            station_key = station_id.replace("/v1/playback/station/", "").strip()
            content_item_id = f"tunein:{station_key}"
            if wants_json(request):
                return playback_json_response(
                    content_item_id,
                    station_name,
                    True,
                    selected_device_id,
                    image_url if isinstance(image_url, str) else "",
                )

            response = RedirectResponse(url="/miniapp/dashboard", status_code=303)
            set_selected_content_cookies(response, content_item_id, station_name, True)
            return response
        except Exception as e:
            logger.error("Error playing TuneIn station: %s", e)
            if wants_json(request):
                return JSONResponse(
                    {"ok": False, "error": "station play failed"},
                    status_code=500,
                )
            return RedirectResponse(
                url="/miniapp/dashboard?error=Station%20play%20failed",
                status_code=303,
            )

    @router.post("/miniapp/reorder-presets")
    async def reorder_presets(request: Request):
        """Persist drag-and-drop preset slot ordering."""
        wants_json = request.headers.get("x-soundcork-request") == "fetch"
        account_id = request.cookies.get("soundcork_account_id", "")
        selected_device_id = request.cookies.get("soundcork_selected_device_id")
        if not account_id or not datastore.account_exists(account_id):
            if wants_json:
                return JSONResponse({"ok": False, "error": "login required"}, 401)
            return RedirectResponse(url="/miniapp/login", status_code=303)
        if not selected_device_id:
            if wants_json:
                return JSONResponse({"ok": False, "error": "select device first"}, 400)
            return RedirectResponse(
                url="/miniapp/dashboard?error=Select%20a%20device%20first",
                status_code=303,
            )

        form_data = await request.form()
        preset_order = str(form_data.get("preset_order", ""))
        ordered_preset_ids = preset_order.split(",")
        success = speakers.reorder_presets(
            account_id, selected_device_id, ordered_preset_ids
        )
        if success:
            if wants_json:
                return JSONResponse({"ok": True})
            return RedirectResponse(
                url="/miniapp/dashboard?notice=Preset%20order%20saved",
                status_code=303,
            )

        if wants_json:
            return JSONResponse({"ok": False, "error": "preset reorder failed"}, 400)
        return RedirectResponse(
            url="/miniapp/dashboard?error=Preset%20reorder%20failed",
            status_code=303,
        )

    @router.post("/miniapp/volume")
    async def set_volume(request: Request):
        """Set volume on the selected device."""
        selected_device_id = request.cookies.get("soundcork_selected_device_id")
        if not selected_device_id:
            return RedirectResponse(
                url="/miniapp/dashboard?error=Select%20a%20device%20first",
                status_code=303,
            )

        form_data = await request.form()
        try:
            level = int(str(form_data.get("volume_level", "")))
        except ValueError:
            level = -1

        success = speakers.set_volume_level(selected_device_id, level)
        if success:
            return RedirectResponse(url="/miniapp/dashboard", status_code=303)
        return RedirectResponse(
            url="/miniapp/dashboard?error=Volume%20update%20failed",
            status_code=303,
        )

    @router.post("/miniapp/mute")
    async def set_mute(request: Request):
        """Set mute state on the selected device."""
        selected_device_id = request.cookies.get("soundcork_selected_device_id")
        if not selected_device_id:
            if wants_json(request):
                return JSONResponse(
                    {
                        "ok": False,
                        "error": translate(
                            "select_device_first", load_user_settings().language
                        ),
                    },
                    status_code=400,
                )
            return RedirectResponse(
                url="/miniapp/dashboard?error=Select%20a%20device%20first",
                status_code=303,
            )

        form_data = await request.form()
        muted = str(form_data.get("muted", "")).lower() == "true"
        success = speakers.set_mute(selected_device_id, muted)
        if success:
            if wants_json(request):
                return JSONResponse(
                    {
                        "ok": True,
                        "muted": muted,
                        "now_playing": speakers.now_playing_state(selected_device_id),
                        "volume_state": speakers.volume_state(selected_device_id),
                        "jobs": jobs_payload(selected_device_id),
                        "job_list": scheduled_job_list(selected_device_id),
                    }
                )
            return RedirectResponse(url="/miniapp/dashboard", status_code=303)
        if wants_json(request):
            return JSONResponse(
                {
                    "ok": False,
                    "error": translate(
                        "mute_update_failed", load_user_settings().language
                    ),
                },
                status_code=502,
            )
        return RedirectResponse(
            url="/miniapp/dashboard?error=Mute%20update%20failed",
            status_code=303,
        )

    @router.post("/miniapp/source")
    async def set_source(request: Request):
        """Select an input source on the selected device."""
        selected_device_id = request.cookies.get("soundcork_selected_device_id")
        if not selected_device_id:
            return RedirectResponse(
                url="/miniapp/dashboard?error=Select%20a%20device%20first",
                status_code=303,
            )

        form_data = await request.form()
        source = str(form_data.get("source", "")).strip()
        source_account = str(form_data.get("source_account", "")).strip()
        success = speakers.select_source(selected_device_id, source, source_account)
        if success:
            return RedirectResponse(url="/miniapp/dashboard", status_code=303)
        return RedirectResponse(
            url="/miniapp/dashboard?error=Source%20change%20failed",
            status_code=303,
        )

    @router.post("/miniapp/bluetooth")
    async def bluetooth_action(request: Request):
        """Run a bluetooth action on the selected device."""
        selected_device_id = request.cookies.get("soundcork_selected_device_id")
        if not selected_device_id:
            return RedirectResponse(
                url="/miniapp/dashboard?error=Select%20a%20device%20first",
                status_code=303,
            )

        form_data = await request.form()
        action = str(form_data.get("action", "")).strip()
        if action == "pair":
            success = speakers.enter_bluetooth_pairing(selected_device_id)
            notice = "Bluetooth%20pairing%20started"
        elif action == "clear":
            success = speakers.clear_bluetooth_paired(selected_device_id)
            notice = "Bluetooth%20pairings%20cleared"
        else:
            success = False
            notice = ""

        if success:
            return RedirectResponse(
                url=f"/miniapp/dashboard?notice={notice}", status_code=303
            )
        return RedirectResponse(
            url="/miniapp/dashboard?error=Bluetooth%20action%20failed",
            status_code=303,
        )

    @router.post("/miniapp/play")
    async def play(request: Request):
        """Play the selected content_item on the selected device."""
        try:
            # Get content_item and device from cookies
            selected_content_item = decode_cookie_value(
                request.cookies.get("soundcork_selected_content_item_name")
            )
            selected_content_item_id = request.cookies.get(
                "soundcork_selected_content_item_id"
            )
            selected_device_id = request.cookies.get("soundcork_selected_device_id")

            if not selected_content_item or not selected_device_id:
                logger.warning("Cannot play: content_item or device not selected")
                return RedirectResponse(url="/miniapp/dashboard", status_code=303)

            logger.info(
                f"content_item: {selected_content_item}, {selected_content_item_id}"
            )

            selected_content_item_id = str(selected_content_item_id)
            if selected_content_item_id.startswith("tunein:"):
                success = speakers.play_tunein_station(
                    selected_device_id,
                    selected_content_item_id.removeprefix("tunein:"),
                    selected_content_item,
                )
            else:
                success = speakers.play_content_item(
                    selected_device_id, selected_content_item_id
                )

            response = RedirectResponse(url="/miniapp/dashboard", status_code=303)
            if success:
                response.set_cookie(
                    key="soundcork_is_playing",
                    value="true",
                    max_age=86400 * 30,
                    httponly=False,
                    samesite="strict",
                )
                logger.info(
                    f"Started playback: content_item {selected_content_item_id} on device {selected_device_id}"
                )
            else:
                logger.error("Failed to start playback")

            return response

        except Exception as e:
            logger.error(f"Error in play endpoint: {e}")
            return RedirectResponse(url="/miniapp/dashboard", status_code=303)

    @router.post("/miniapp/stop")
    async def stop(request: Request):
        """Stop playback on the selected device."""
        try:
            selected_device_id = request.cookies.get("soundcork_selected_device_id")

            if not selected_device_id:
                logger.warning("Cannot stop: device not selected")
                return RedirectResponse(url="/miniapp/dashboard", status_code=303)

            # Stop playback
            success = speakers.stop_playback(selected_device_id)

            response = RedirectResponse(url="/miniapp/dashboard", status_code=303)
            if success:
                response.set_cookie(
                    key="soundcork_is_playing",
                    value="false",
                    max_age=86400 * 30,
                    httponly=False,
                    samesite="strict",
                )
                logger.info(f"Stopped playback on device {selected_device_id}")
            else:
                logger.error("Failed to stop playback")

            return response

        except Exception as e:
            logger.error(f"Error in stop endpoint: {e}")
            return RedirectResponse(url="/miniapp/dashboard", status_code=303)

    @router.post("/miniapp/logout")
    async def logout(request: Request):
        """Clear session and redirect to login."""
        response = RedirectResponse(url="/miniapp/login", status_code=303)
        response.delete_cookie("soundcork_account_id")
        response.delete_cookie("soundcork_account_label")
        response.delete_cookie("soundcork_selected_content_item_name")
        response.delete_cookie("soundcork_selected_content_item_id")
        response.delete_cookie("soundcork_selected_device")
        response.delete_cookie("soundcork_selected_device_id")
        response.delete_cookie("soundcork_is_playing")
        logger.info("User logged out")
        return response

    return router
