from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from typing import Any, cast
from urllib.parse import unquote

from fastapi import FastAPI
from fastapi.testclient import TestClient

from soundcork.miniapp import get_miniapp_router
from soundcork.model import Preset
from soundcork.user_settings import load_user_settings

ACCOUNT_ID = "8208423"
DEVICE_ID = "device-1"
SECOND_DEVICE_ID = "device-2"


class FakeDatastore:
    def __init__(self, device_ids: list[str] | None = None) -> None:
        self.account_label = "Účet ložnice"
        self.saved_device = None
        self.device_ids = device_ids or [DEVICE_ID]
        self._temp_dir = TemporaryDirectory()
        self.data_dir = self._temp_dir.name

    def account_exists(self, account_id: str) -> bool:
        return account_id == ACCOUNT_ID

    def list_accounts(self) -> list[str]:
        return [ACCOUNT_ID]

    def get_account_info(self, account_id: str) -> str:
        assert account_id == ACCOUNT_ID
        return self.account_label

    def save_account_info(self, account_id: str, label: str) -> None:
        assert account_id == ACCOUNT_ID
        self.account_label = label

    def list_devices(self, account_id: str) -> list[str]:
        assert account_id == ACCOUNT_ID
        return self.device_ids

    def get_device_info(self, account_id: str, device_id: str):
        assert account_id == ACCOUNT_ID
        assert device_id in self.device_ids
        index = self.device_ids.index(device_id)
        return SimpleNamespace(
            name="ložnice" if device_id == DEVICE_ID else f"radio {index + 1}",
            product_code="SoundTouch10",
            device_id=device_id,
            ip_address=f"192.0.2.{10 + index}",
        )

    def save_device_info(self, device_info, account_id: str):
        assert account_id == ACCOUNT_ID
        self.saved_device = device_info
        return device_info

    def get_presets(self, account_id: str) -> list[Preset]:
        assert account_id == ACCOUNT_ID
        return [
            Preset(
                id="4",
                name="Rádio Proglas",
                source="LOCAL_INTERNET_RADIO",
                type="STORED_MUSIC",
                location="proglas",
                container_art="",
            )
        ]


class FakeSpeakers:
    def __init__(
        self, play_result: bool = True, device_ids: list[str] | None = None
    ) -> None:
        self.play_result = play_result
        self.device_ids = device_ids or [DEVICE_ID]
        self.play_calls: list[tuple[str, str]] = []
        self.play_tunein_calls: list[tuple[str, str, str]] = []
        self.store_calls: list[tuple[str, int, str, str, str]] = []
        self.custom_stream_calls: list[tuple[str, int, str, str, str]] = []
        self.reorder_calls: list[tuple[str, str, list[str]]] = []
        self.volume_calls: list[tuple[str, int]] = []
        self.mute_calls: list[tuple[str, bool]] = []
        self.sync_calls: list[tuple[str, str]] = []
        self.apply_calls: list[tuple[str, str, list[Preset]]] = []

    def all_devices(self):
        return {
            device_id: SimpleNamespace(
                account=ACCOUNT_ID,
                ip=f"192.0.2.{10 + index}",
                online=True,
                in_soundcork=True,
                marge_server="Soundcork",
            )
            for index, device_id in enumerate(self.device_ids)
        }

    def play_content_item(self, device_id: str, content_item_id: str) -> bool:
        self.play_calls.append((device_id, content_item_id))
        return self.play_result

    def play_tunein_station(
        self, device_id: str, station_id: str, station_name: str
    ) -> bool:
        self.play_tunein_calls.append((device_id, station_id, station_name))
        return self.play_result

    def store_tunein_station_as_preset(
        self,
        device_id: str,
        preset_number: int,
        station_id: str,
        station_name: str,
        image_url: str = "",
    ) -> bool:
        self.store_calls.append(
            (device_id, preset_number, station_id, station_name, image_url)
        )
        return self.play_result

    def volume_state(self, device_id: str):
        assert device_id == DEVICE_ID
        return {"actual": 22, "target": 22, "muted": False}

    def now_playing_state(self, device_id: str):
        assert device_id == DEVICE_ID
        return {
            "item_name": "Radio Test",
            "track": "Track Test",
            "artist": "Artist Test",
            "source": "TUNEIN",
            "art": "",
            "play_status": "PLAY_STATE",
        }

    def set_volume_level(self, device_id: str, level: int) -> bool:
        self.volume_calls.append((device_id, level))
        return self.play_result

    def set_mute(self, device_id: str, muted: bool) -> bool:
        self.mute_calls.append((device_id, muted))
        return self.play_result

    def source_list(self, device_id: str):
        assert device_id == DEVICE_ID
        return [
            {
                "source": "TUNEIN",
                "source_account": "",
                "title": "TuneIn",
                "status": "READY",
                "is_local": False,
            },
            {
                "source": "BLUETOOTH",
                "source_account": "",
                "title": "Bluetooth",
                "status": "READY",
                "is_local": True,
            },
        ]

    def select_source(self, device_id: str, source: str, source_account: str = ""):
        return self.play_result

    def enter_bluetooth_pairing(self, device_id: str) -> bool:
        return self.play_result

    def clear_bluetooth_paired(self, device_id: str) -> bool:
        return self.play_result

    def wireless_profile(self, device_id: str):
        assert device_id == DEVICE_ID
        return {"ssid": "Kitchen WiFi"}

    def network_status(self, device_id: str):
        assert device_id in self.device_ids
        index = self.device_ids.index(device_id)
        return {
            "ssid": "Kitchen WiFi",
            "configured_ssid": "Saved Kitchen WiFi",
            "kind": "Wireless",
            "name": "wlan0",
            "mac_address": "00:11:22:33:44:55",
            "rssi": "good",
            "frequency_khz": "2452000",
            "is_running": True,
            "bindings": [f"192.0.2.{10 + index}"],
            "ip_address": f"192.0.2.{10 + index}",
            "interfaces": [],
            "wifi_profile_count": 2,
            "network_info_interfaces": [
                {
                    "name": "eth0",
                    "type_value": "ETHERNET_INTERFACE",
                    "state_value": "NETWORK_ETHERNET_CONNECTED",
                    "ip_address": f"192.0.2.{10 + index}",
                    "mac_address": "00:11:22:33:44:55",
                },
                {
                    "name": "wlan0",
                    "type_value": "WIFI_INTERFACE",
                    "state_value": "NETWORK_WIFI_DISCONNECTED",
                    "ssid": "Saved Kitchen WiFi",
                    "mac_address": "00:11:22:33:44:56",
                },
            ],
        }

    def add_wireless_profile(
        self, device_id: str, ssid: str, password: str, security_type: str
    ) -> bool:
        return self.play_result

    def standby_device(self, device_id: str) -> bool:
        return self.play_result

    def store_direct_stream_as_preset(
        self,
        device_id: str,
        preset_number: int,
        stream_name: str,
        stream_url: str,
        image_url: str = "",
    ) -> bool:
        self.custom_stream_calls.append(
            (device_id, preset_number, stream_name, stream_url, image_url)
        )
        return self.play_result

    def sync_presets_from_speaker(self, account_id: str, device_id: str) -> bool:
        self.sync_calls.append((account_id, device_id))
        return self.play_result

    def apply_presets(
        self, account_id: str, device_id: str, presets: list[Preset]
    ) -> bool:
        self.apply_calls.append((account_id, device_id, presets))
        return self.play_result

    def reorder_presets(
        self, account_id: str, device_id: str, ordered_preset_ids: list[str]
    ) -> bool:
        self.reorder_calls.append((account_id, device_id, ordered_preset_ids))
        return self.play_result


def make_client(
    monkeypatch,
    speakers: FakeSpeakers | None = None,
    device_ids: list[str] | None = None,
):
    monkeypatch.chdir(Path(__file__).resolve().parents[1])
    app = FastAPI()
    datastore = FakeDatastore(device_ids)
    monkeypatch.setenv("data_dir", datastore.data_dir)
    fake_speakers = speakers or FakeSpeakers(device_ids=device_ids)
    app.include_router(
        get_miniapp_router(cast(Any, datastore), cast(Any, fake_speakers))
    )
    return TestClient(app), fake_speakers


def set_cookie_headers(response) -> list[str]:
    return response.headers.get_list("set-cookie")


def test_login_auto_selects_only_radio(monkeypatch):
    client, _speakers = make_client(monkeypatch)

    response = client.post(
        "/miniapp/login",
        data={"account_id": ACCOUNT_ID},
        follow_redirects=False,
    )

    cookies = "\n".join(set_cookie_headers(response))
    assert response.status_code == 303
    assert response.headers["location"] == "/miniapp/dashboard"
    assert "soundcork_selected_device=lo%C5%BEnice" in cookies
    assert f"soundcork_selected_device_id={DEVICE_ID}" in cookies


def test_login_with_multiple_radios_shows_device_picker(monkeypatch):
    client, _speakers = make_client(
        monkeypatch, device_ids=[DEVICE_ID, SECOND_DEVICE_ID]
    )

    response = client.post(
        "/miniapp/login",
        data={"account_id": ACCOUNT_ID},
        follow_redirects=False,
    )

    cookies = "\n".join(set_cookie_headers(response))
    assert response.status_code == 303
    assert response.headers["location"] == "/miniapp/devices"
    assert "soundcork_selected_device=lo%C5%BEnice" not in cookies
    assert f"soundcork_selected_device_id={DEVICE_ID}" not in cookies

    picker = client.get("/miniapp/devices")
    assert picker.status_code == 200
    assert "Choose Radio" in picker.text
    assert "radio 2" in picker.text
    assert 'data-panel-stack="dashboard"' not in picker.text


def test_dashboard_without_selected_device_redirects_to_picker(monkeypatch):
    client, _speakers = make_client(
        monkeypatch, device_ids=[DEVICE_ID, SECOND_DEVICE_ID]
    )

    response = client.get(
        "/miniapp/dashboard",
        headers={"Cookie": f"soundcork_account_id={ACCOUNT_ID}"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/miniapp/devices"


def test_open_device_shortcut_selects_radio_and_account(monkeypatch):
    client, _speakers = make_client(
        monkeypatch, device_ids=[DEVICE_ID, SECOND_DEVICE_ID]
    )

    response = client.get(
        f"/miniapp/open/{SECOND_DEVICE_ID}",
        follow_redirects=False,
    )

    cookies = "\n".join(set_cookie_headers(response))
    assert response.status_code == 303
    assert response.headers["location"] == "/miniapp/dashboard"
    assert f"soundcork_account_id={ACCOUNT_ID}" in cookies
    assert "soundcork_account_label=%C3%9A%C4%8Det%20lo%C5%BEnice" in cookies
    assert "soundcork_selected_device=radio%202" in cookies
    assert f"soundcork_selected_device_id={SECOND_DEVICE_ID}" in cookies


def test_open_device_shortcut_rejects_unknown_radio(monkeypatch):
    client, _speakers = make_client(monkeypatch)

    response = client.get("/miniapp/open/missing-radio", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/miniapp/login?error=Radio%20not%20found"


def test_select_device_percent_encodes_unicode_cookie(monkeypatch):
    client, _speakers = make_client(monkeypatch)

    response = client.post(
        "/miniapp/select-device",
        data={"device_id": DEVICE_ID, "device_name": "ložnice"},
        follow_redirects=False,
    )

    cookies = "\n".join(set_cookie_headers(response))
    assert response.status_code == 303
    assert "soundcork_selected_device=lo%C5%BEnice" in cookies
    assert "ložnice" not in cookies


def test_dashboard_decodes_display_cookies(monkeypatch):
    client, _speakers = make_client(monkeypatch)

    response = client.get(
        "/miniapp/dashboard",
        headers={
            "Cookie": (
                f"soundcork_account_id={ACCOUNT_ID}; "
                "soundcork_account_label=%C3%9A%C4%8Det%20lo%C5%BEnice; "
                "soundcork_selected_device=lo%C5%BEnice; "
                "soundcork_selected_content_item_name=R%C3%A1dio%20Proglas; "
                f"soundcork_selected_device_id={DEVICE_ID}"
            )
        },
    )

    assert response.status_code == 200
    assert "Účet ložnice" in response.text
    assert "ložnice" in response.text
    assert "Rádio Proglas" in response.text


def test_select_content_item_plays_when_device_is_selected(monkeypatch):
    client, speakers = make_client(monkeypatch)

    response = client.post(
        "/miniapp/select-content-item",
        data={"content_item_id": "4", "content_item_name": "Rádio Proglas"},
        headers={"Cookie": f"soundcork_selected_device_id={DEVICE_ID}"},
        follow_redirects=False,
    )

    cookies = "\n".join(set_cookie_headers(response))
    assert response.status_code == 303
    assert speakers.play_calls == [(DEVICE_ID, "4")]
    assert "soundcork_selected_content_item_name=R%C3%A1dio%20Proglas" in cookies
    assert "soundcork_is_playing=true" in cookies


def test_select_content_item_without_device_only_selects(monkeypatch):
    client, speakers = make_client(monkeypatch)

    response = client.post(
        "/miniapp/select-content-item",
        data={"content_item_id": "4", "content_item_name": "Rádio Proglas"},
        follow_redirects=False,
    )

    cookies = "\n".join(set_cookie_headers(response))
    assert response.status_code == 303
    assert speakers.play_calls == []
    assert "soundcork_selected_content_item_name=R%C3%A1dio%20Proglas" in cookies
    assert "soundcork_is_playing" not in cookies


def test_select_content_item_records_failed_playback(monkeypatch):
    client, speakers = make_client(monkeypatch, FakeSpeakers(play_result=False))

    response = client.post(
        "/miniapp/select-content-item",
        data={"content_item_id": "4", "content_item_name": "Rádio Proglas"},
        headers={"Cookie": f"soundcork_selected_device_id={DEVICE_ID}"},
        follow_redirects=False,
    )

    cookies = "\n".join(set_cookie_headers(response))
    assert response.status_code == 303
    assert speakers.play_calls == [(DEVICE_ID, "4")]
    assert "soundcork_is_playing=false" in cookies


def test_dashboard_shows_tunein_preset_search_results(monkeypatch):
    def fake_search(query: str):
        assert query == "fm4"
        return [
            {
                "station_id": "s8007",
                "name": "FM4",
                "subtitle": "Alternative",
                "image_url": "http://example.test/fm4.png",
            }
        ]

    monkeypatch.setattr("soundcork.miniapp.tunein_station_search_results", fake_search)
    client, _speakers = make_client(monkeypatch)

    response = client.get(
        "/miniapp/dashboard?preset_query=fm4&preset_number=1",
        headers={
            "Cookie": (
                f"soundcork_account_id={ACCOUNT_ID}; "
                f"soundcork_selected_device_id={DEVICE_ID}"
            )
        },
    )

    assert response.status_code == 200
    assert "FM4" in response.text
    assert "s8007" in response.text
    assert "/miniapp/play-tunein-station" in response.text
    assert "Save 1" not in response.text


def test_station_search_endpoint_returns_json(monkeypatch):
    def fake_search(query: str):
        assert query == "fm4"
        return [
            {
                "station_id": "s8007",
                "name": "FM4",
                "subtitle": "Alternative",
                "image_url": "http://example.test/fm4.png",
            }
        ]

    monkeypatch.setattr("soundcork.miniapp.tunein_station_search_results", fake_search)
    client, _speakers = make_client(monkeypatch)

    response = client.get(
        "/miniapp/search-stations?preset_query=fm4",
        headers={"Cookie": f"soundcork_account_id={ACCOUNT_ID}"},
    )

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert response.json()["results"][0]["name"] == "FM4"


def test_dashboard_topbar_shows_station_and_track_without_now_panel(monkeypatch):
    client, _speakers = make_client(monkeypatch)

    response = client.get(
        "/miniapp/dashboard",
        headers={
            "Cookie": (
                f"soundcork_account_id={ACCOUNT_ID}; "
                f"soundcork_selected_device_id={DEVICE_ID}; "
                "soundcork_selected_device=Kitchen"
            )
        },
    )

    assert response.status_code == 200
    assert 'data-live-station data-live-marquee title="Radio Test"' in response.text
    assert 'data-live-station-text>Radio Test</span>' in response.text
    assert 'data-live-title title="Track Test"' in response.text
    assert "home-nav-link" in response.text
    assert 'href="/miniapp/home"' in response.text
    assert 'data-panel-drop-target="home"' in response.text
    assert 'data-panel-drop-target="dashboard"' in response.text
    assert 'class="nav-link dashboard-nav-link is-active"' in response.text
    assert 'data-panel-id="playing"' not in response.text


def test_home_page_uses_separate_home_surface(monkeypatch):
    client, _speakers = make_client(monkeypatch)

    response = client.get(
        "/miniapp/home",
        headers={
            "Cookie": (
                f"soundcork_account_id={ACCOUNT_ID}; "
                f"soundcork_selected_device_id={DEVICE_ID}; "
                "soundcork_selected_device=Kitchen"
            )
        },
    )

    assert response.status_code == 200
    assert 'data-panel-stack="home"' in response.text
    assert 'data-panel-stack="dashboard"' not in response.text
    assert 'data-topbar-item="home"' in response.text
    assert '"home": []' in response.text


def test_select_content_item_fetch_updates_playback_json(monkeypatch):
    client, speakers = make_client(monkeypatch)

    response = client.post(
        "/miniapp/select-content-item",
        data={
            "content_item_id": "4",
            "content_item_name": "RÃ¡dio Proglas",
            "content_item_art": "http://example.test/art.png",
        },
        headers={
            "X-SoundCork-Request": "fetch",
            "Cookie": f"soundcork_selected_device_id={DEVICE_ID}",
        },
    )

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert response.json()["now_playing"]["item_name"] == "RÃ¡dio Proglas"
    assert response.json()["now_playing"]["art"] == "http://example.test/art.png"
    assert speakers.play_calls == [(DEVICE_ID, "4")]
    cookies = "\n".join(response.headers.get_list("set-cookie"))
    assert "soundcork_selected_content_item_name=" in cookies
    assert "soundcork_is_playing=true" in cookies


def test_play_tunein_station_calls_speaker_and_sets_play_cookie(monkeypatch):
    client, speakers = make_client(monkeypatch)

    response = client.post(
        "/miniapp/play-tunein-station",
        data={
            "station_id": "s8007",
            "station_name": "FM4",
        },
        headers={"Cookie": f"soundcork_selected_device_id={DEVICE_ID}"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert speakers.play_tunein_calls == [(DEVICE_ID, "s8007", "FM4")]
    cookies = "\n".join(response.headers.get_list("set-cookie"))
    assert "soundcork_selected_content_item_id=tunein:s8007" in cookies
    assert "soundcork_is_playing=true" in cookies


def test_play_cookie_supports_tunein_search_result(monkeypatch):
    client, speakers = make_client(monkeypatch)

    response = client.post(
        "/miniapp/play",
        headers={
            "Cookie": (
                f"soundcork_selected_device_id={DEVICE_ID}; "
                "soundcork_selected_content_item_id=tunein:s8007; "
                "soundcork_selected_content_item_name=FM4"
            )
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert speakers.play_tunein_calls == [(DEVICE_ID, "s8007", "FM4")]


def test_store_tunein_preset_calls_speaker(monkeypatch):
    client, speakers = make_client(monkeypatch)

    response = client.post(
        "/miniapp/store-tunein-preset",
        data={
            "preset_number": "5",
            "station_id": "s8007",
            "station_name": "FM4",
            "image_url": "http://example.test/fm4.png",
        },
        headers={"Cookie": f"soundcork_selected_device_id={DEVICE_ID}"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert speakers.store_calls == [
        (DEVICE_ID, 5, "s8007", "FM4", "http://example.test/fm4.png")
    ]
    assert "notice=Saved%20FM4%20to%20preset%205" in response.headers["location"]


def test_store_tunein_preset_fetch_returns_updated_slot(monkeypatch):
    client, speakers = make_client(monkeypatch)

    response = client.post(
        "/miniapp/store-tunein-preset",
        data={
            "preset_number": "5",
            "station_id": "s8007",
            "station_name": "FM4",
            "image_url": "http://example.test/fm4.png",
        },
        headers={
            "X-SoundCork-Request": "fetch",
            "Cookie": f"soundcork_selected_device_id={DEVICE_ID}",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["preset"] == {
        "id": "5",
        "name": "FM4",
        "container_art": "http://example.test/fm4.png",
    }
    assert speakers.store_calls == [
        (DEVICE_ID, 5, "s8007", "FM4", "http://example.test/fm4.png")
    ]


def test_reorder_presets_calls_speaker(monkeypatch):
    client, speakers = make_client(monkeypatch)

    response = client.post(
        "/miniapp/reorder-presets",
        data={"preset_order": "4,,,,,"},
        headers={
            "Cookie": (
                f"soundcork_account_id={ACCOUNT_ID}; "
                f"soundcork_selected_device_id={DEVICE_ID}"
            )
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert speakers.reorder_calls == [
        (ACCOUNT_ID, DEVICE_ID, ["4", "", "", "", "", ""])
    ]
    assert "notice=Preset%20order%20saved" in response.headers["location"]


def test_reorder_presets_fetch_returns_json(monkeypatch):
    client, speakers = make_client(monkeypatch)

    response = client.post(
        "/miniapp/reorder-presets",
        data={"preset_order": "4,,,,,"},
        headers={
            "X-SoundCork-Request": "fetch",
            "Cookie": (
                f"soundcork_account_id={ACCOUNT_ID}; "
                f"soundcork_selected_device_id={DEVICE_ID}"
            ),
        },
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert speakers.reorder_calls == [
        (ACCOUNT_ID, DEVICE_ID, ["4", "", "", "", "", ""])
    ]


def test_set_volume_calls_speaker(monkeypatch):
    client, speakers = make_client(monkeypatch)

    response = client.post(
        "/miniapp/volume",
        data={"volume_level": "27"},
        headers={"Cookie": f"soundcork_selected_device_id={DEVICE_ID}"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert speakers.volume_calls == [(DEVICE_ID, 27)]


def test_set_mute_fetch_returns_json_without_redirect(monkeypatch):
    client, speakers = make_client(monkeypatch)

    response = client.post(
        "/miniapp/mute",
        data={"muted": "true"},
        headers={
            "Cookie": f"soundcork_selected_device_id={DEVICE_ID}",
            "X-SoundCork-Request": "fetch",
        },
        follow_redirects=False,
    )

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert response.json()["muted"] is True
    assert response.json()["now_playing"]["item_name"] == "Radio Test"
    assert response.json()["volume_state"]["target"] == 22
    assert speakers.mute_calls == [(DEVICE_ID, True)]


def test_settings_page_shows_default_search_endpoint(monkeypatch):
    client, _speakers = make_client(monkeypatch)

    response = client.get(
        "/miniapp/settings",
        headers={"Cookie": f"soundcork_account_id={ACCOUNT_ID}"},
    )

    assert response.status_code == 200
    assert "https://api.radiotime.com/profiles" in response.text
    assert "Reset Default Values" in response.text
    assert "Connection" in response.text
    assert "Wireless" in response.text
    assert "Current IP" in response.text
    assert "192.0.2.10" in response.text
    assert "Kitchen WiFi" in response.text
    assert "Configured Wi-Fi" in response.text
    assert "Saved Kitchen WiFi" in response.text
    assert "Wi-Fi profiles" in response.text
    assert "Radio-reported interfaces" in response.text
    assert "Ethernet" in response.text
    assert "Wi-Fi Disconnected" in response.text
    assert "Wi-Fi setup without the app" in response.text
    assert "Preset 2 + Volume -" in response.text
    assert "http://192.168.1.1" in response.text
    assert "http://192.0.2.1" in response.text
    assert "Gateway, DNS and subnet are not reported" in response.text
    assert (
        "Saved Wi-Fi passwords and security settings are not exposed by the radio."
        in response.text
    )
    assert 'data-panel-id="settings-menu"' not in response.text
    assert "settings-nav-icon" in response.text
    assert "data-password-input" in response.text
    assert "data-password-toggle" in response.text
    assert "Show password" in response.text
    assert "Input Background" in response.text
    assert 'name="input_background_color"' in response.text
    assert 'data-panel-id="settings-usability"' in response.text
    assert 'name="topbar_long_press_delay_ms"' in response.text
    assert "Top Bar Hold Before Drag" in response.text
    assert 'name="now_playing_poll_interval_ms"' in response.text
    assert "Indicator Update Interval" in response.text
    assert 'name="volume_poll_interval_ms"' in response.text
    assert "Volume Update Interval" in response.text
    assert "Scan Wi-Fi Networks" not in response.text
    assert "data-wifi-network-choice" not in response.text


def test_visual_settings_page_shows_theme_buttons(monkeypatch):
    client, _speakers = make_client(monkeypatch)

    response = client.get(
        "/miniapp/settings",
        headers={"Cookie": f"soundcork_account_id={ACCOUNT_ID}"},
    )

    assert response.status_code == 200
    assert 'action="/miniapp/settings/visual/light"' in response.text
    assert 'action="/miniapp/settings/visual/dark"' in response.text
    assert "Light Theme" in response.text
    assert "Dark Theme" in response.text
    assert 'name="button_background_color"' in response.text


def test_dashboard_does_not_show_invalid_source_in_topbar(monkeypatch):
    speakers = FakeSpeakers()
    speakers.now_playing_state = lambda device_id: {
        "item_name": "",
        "station_name": "",
        "track": "",
        "artist": "",
        "album": "",
        "source": "INVALID_SOURCE",
        "art": "",
        "play_status": "STOP_STATE",
    }
    client, _speakers = make_client(monkeypatch, speakers=speakers)

    response = client.get(
        "/miniapp/dashboard",
        headers={
            "Cookie": (
                f"soundcork_account_id={ACCOUNT_ID}; "
                f"soundcork_selected_device_id={DEVICE_ID}; "
                "soundcork_selected_device=lo%C5%BEnice"
            )
        },
    )

    assert response.status_code == 200
    assert "INVALID_SOURCE" not in response.text
    assert "Ready" in response.text
    assert "Select a preset or search for a station" in response.text


def test_dashboard_treats_reported_station_as_active_playback(monkeypatch):
    speakers = FakeSpeakers()
    speakers.now_playing_state = lambda device_id: {
        "item_name": "Radio Austria 1",
        "station_name": "Radio Austria 1",
        "track": "",
        "artist": "",
        "album": "",
        "source": "TUNEIN",
        "art": "",
        "play_status": "UNKNOWN",
    }
    client, _speakers = make_client(monkeypatch, speakers=speakers)

    response = client.get(
        "/miniapp/dashboard",
        headers={
            "Cookie": (
                f"soundcork_account_id={ACCOUNT_ID}; "
                f"soundcork_selected_device_id={DEVICE_ID}; "
                "soundcork_selected_device=Kitchen"
            )
        },
    )

    assert response.status_code == 200
    assert "Radio Austria 1" in response.text
    assert "Playing" in response.text
    assert "is-playing" in response.text


def test_layout_settings_are_saved_server_side(monkeypatch):
    client, _speakers = make_client(monkeypatch)

    response = client.post(
        "/miniapp/settings/layout",
        json={
            "panel_order": {"dashboard": ["presets", "backup"]},
            "panel_surface": {"backup": "settings"},
            "topbar_order": ["home", "status", "settings", "dashboard", "logout"],
            "topbar_row": {"status": "secondary", "home": "primary"},
        },
        headers={"Cookie": f"soundcork_account_id={ACCOUNT_ID}"},
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    settings = load_user_settings()
    assert settings.panel_order["dashboard"] == ["presets", "backup"]
    assert settings.panel_surface["backup"] == "settings"
    assert settings.topbar_order == [
        "home",
        "status",
        "settings",
        "dashboard",
        "logout",
    ]
    assert settings.topbar_row["status"] == "secondary"
    assert settings.topbar_row["home"] == "primary"
    assert settings.topbar_row["settings"] == "primary"


def test_legacy_menu_settings_are_not_rendered_in_topbar(monkeypatch):
    client, _speakers = make_client(monkeypatch)

    response = client.post(
        "/miniapp/settings/menu",
        data={
            "settings_label": "Prefs",
            "custom_label": "Docs",
            "custom_url": "/docs",
        },
        headers={"Cookie": f"soundcork_account_id={ACCOUNT_ID}"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    settings = load_user_settings()
    assert settings.menu_settings_label == "Prefs"
    assert settings.menu_custom_buttons[0].label == "Docs"

    dashboard = client.get(
        "/miniapp/dashboard",
        headers={
            "Cookie": (
                f"soundcork_account_id={ACCOUNT_ID}; "
                f"soundcork_selected_device_id={DEVICE_ID}; "
                "soundcork_selected_device=lo%C5%BEnice"
            )
        },
    )
    assert "Prefs" not in dashboard.text
    assert 'href="/docs"' not in dashboard.text
    assert "settings-nav-icon" in dashboard.text


def test_preference_settings_are_saved_server_side(monkeypatch):
    client, _speakers = make_client(monkeypatch)

    response = client.post(
        "/miniapp/settings/preferences",
        json={
            "preset_drag_opacity": 0.42,
            "preset_thumbnail_size_px": 132,
            "preset_long_press_delay_ms": 1300,
            "preset_search_result_delay_ms": 85,
            "timer_job_visible_count": 7,
            "topbar_long_press_delay_ms": 900,
            "now_playing_poll_interval_ms": 7000,
            "volume_poll_interval_ms": 11000,
            "panel_label": {"volume": "", "presets": "My presets"},
            "timer_section_label": {
                "sleep": "Nap",
                "alarm": "Wake",
                "jobs": "Planned stuff",
            },
            "global_panel_style": {
                "background_color": "#eeeeee",
                "text_color": "#222222",
                "border_color": "#333333",
                "button_background_color": "#444444",
            },
            "site_style": {
                "page_background_color": "#010203",
                "input_background_color": "#0a0b0c",
                "topbar_background_color": "#111213",
                "topbar_text_color": "#f1f2f3",
                "topbar_muted_color": "#a1a2a3",
                "topbar_accent_color": "#444555",
                "topbar_border_color": "#666777",
            },
        },
        headers={"Cookie": f"soundcork_account_id={ACCOUNT_ID}"},
    )

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert load_user_settings().preset_drag_opacity == 0.42
    assert load_user_settings().preset_thumbnail_size_px == 132
    assert response.json()["preset_thumbnail_size_px"] == 132
    assert load_user_settings().preset_long_press_delay_ms == 1300
    assert load_user_settings().preset_search_result_delay_ms == 85
    assert response.json()["preset_search_result_delay_ms"] == 85
    assert load_user_settings().timer_job_visible_count == 7
    assert response.json()["timer_job_visible_count"] == 7
    assert load_user_settings().topbar_long_press_delay_ms == 900
    assert load_user_settings().now_playing_poll_interval_ms == 7000
    assert response.json()["now_playing_poll_interval_ms"] == 7000
    assert load_user_settings().volume_poll_interval_ms == 11000
    assert response.json()["volume_poll_interval_ms"] == 11000
    assert load_user_settings().panel_label == {
        "volume": "",
        "presets": "My presets",
    }
    assert response.json()["panel_label"] == {
        "volume": "",
        "presets": "My presets",
    }
    assert load_user_settings().timer_section_label == {
        "sleep": "Nap",
        "alarm": "Wake",
        "jobs": "Planned stuff",
    }
    assert response.json()["timer_section_label"] == {
        "sleep": "Nap",
        "alarm": "Wake",
        "jobs": "Planned stuff",
    }
    assert load_user_settings().visual_theme == "custom"
    assert load_user_settings().global_panel_style.background_color == "#eeeeee"
    assert load_user_settings().global_panel_style.text_color == "#222222"
    assert load_user_settings().global_panel_style.border_color == "#333333"
    assert load_user_settings().global_panel_style.button_background_color == "#444444"
    assert load_user_settings().site_style.page_background_color == "#010203"
    assert load_user_settings().site_style.input_background_color == "#0a0b0c"
    assert load_user_settings().site_style.topbar_background_color == "#111213"
    assert load_user_settings().site_style.topbar_text_color == "#f1f2f3"
    assert load_user_settings().site_style.topbar_muted_color == "#a1a2a3"
    assert load_user_settings().site_style.topbar_accent_color == "#444555"
    assert load_user_settings().site_style.topbar_border_color == "#666777"


def test_timer_section_labels_render_from_preferences(monkeypatch):
    client, _speakers = make_client(monkeypatch)

    client.post(
        "/miniapp/settings/preferences",
        json={
            "timer_section_label": {
                "sleep": "Nap time",
                "alarm": "Wake me",
                "jobs": "Automation list",
            },
        },
        headers={"Cookie": f"soundcork_account_id={ACCOUNT_ID}"},
    )

    dashboard = client.get(
        "/miniapp/dashboard",
        headers={
            "Cookie": (
                f"soundcork_account_id={ACCOUNT_ID}; "
                f"soundcork_selected_device_id={DEVICE_ID}"
            )
        },
    )

    assert dashboard.status_code == 200
    assert 'data-timer-section-label="sleep"' in dashboard.text
    assert "Nap time" in dashboard.text
    assert 'data-timer-section-label="alarm"' in dashboard.text
    assert "Wake me" in dashboard.text
    assert 'data-timer-section-label="jobs"' in dashboard.text
    assert "Automation list" in dashboard.text


def test_volume_legacy_controls_are_not_rendered(monkeypatch):
    client, _speakers = make_client(monkeypatch)
    cookie = (
        f"soundcork_account_id={ACCOUNT_ID}; "
        f"soundcork_selected_device_id={DEVICE_ID}"
    )

    client.post(
        "/miniapp/settings/preferences",
        json={"volume_legacy_ui": True},
        headers={"Cookie": f"soundcork_account_id={ACCOUNT_ID}"},
    )
    dashboard = client.get("/miniapp/dashboard", headers={"Cookie": cookie})

    assert dashboard.status_code == 200
    assert 'data-panel-id="volume"' in dashboard.text
    assert "is-volume-legacy-ui" not in dashboard.text
    assert "volume-legacy-control" not in dashboard.text
    assert "data-volume-submit" not in dashboard.text
    assert "Show Legacy Volume Controls" not in dashboard.text


def test_panel_style_preferences_keep_requested_text_color(monkeypatch):
    client, _speakers = make_client(monkeypatch)

    response = client.post(
        "/miniapp/settings/preferences",
        json={
            "panel_style": {
                "presets": {
                    "background_color": "#ffffff",
                    "text_color": "#fffffe",
                    "border_color": "#123456",
                    "button_background_color": "#654321",
                }
            }
        },
        headers={"Cookie": f"soundcork_account_id={ACCOUNT_ID}"},
    )

    assert response.status_code == 200
    panel_style = response.json()["panel_style"]["presets"]
    assert panel_style["background_color"] == "#ffffff"
    assert panel_style["text_color"] == "#fffffe"
    assert panel_style["border_color"] == "#123456"
    assert panel_style["button_background_color"] == "#654321"
    assert load_user_settings().panel_style["presets"].text_color == "#fffffe"
    assert (
        load_user_settings().panel_style["presets"].button_background_color == "#654321"
    )


def test_visual_settings_form_updates_global_panel_colors(monkeypatch):
    client, _speakers = make_client(monkeypatch)

    client.post(
        "/miniapp/settings/preferences",
        json={
            "panel_style": {
                "presets": {
                    "background_color": "#ffffff",
                    "text_color": "#fffffe",
                    "border_color": "#123456",
                    "button_background_color": "#654321",
                }
            }
        },
        headers={"Cookie": f"soundcork_account_id={ACCOUNT_ID}"},
    )

    response = client.post(
        "/miniapp/settings/visual",
        data={
            "background_color": "#101010",
            "text_color": "#fefefe",
            "border_color": "#555555",
            "button_background_color": "#333333",
            "page_background_color": "#202020",
            "input_background_color": "#252a31",
            "topbar_background_color": "#303030",
            "topbar_text_color": "#fafafa",
            "topbar_muted_color": "#b0b0b0",
            "topbar_accent_color": "#4488ff",
            "topbar_border_color": "#777777",
        },
        headers={"Cookie": f"soundcork_account_id={ACCOUNT_ID}"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    settings = load_user_settings()
    assert settings.global_panel_style.background_color == "#101010"
    assert settings.global_panel_style.text_color == "#fefefe"
    assert settings.global_panel_style.border_color == "#555555"
    assert settings.global_panel_style.button_background_color == "#333333"
    assert settings.visual_theme == "custom"
    assert settings.site_style.page_background_color == "#202020"
    assert settings.site_style.input_background_color == "#252a31"
    assert settings.site_style.topbar_background_color == "#303030"
    assert settings.site_style.topbar_text_color == "#fafafa"
    assert settings.site_style.topbar_muted_color == "#b0b0b0"
    assert settings.site_style.topbar_accent_color == "#4488ff"
    assert settings.site_style.topbar_border_color == "#777777"
    assert settings.panel_style == {}


def test_visual_settings_light_theme_restores_page_and_topbar_colors(monkeypatch):
    client, _speakers = make_client(monkeypatch)

    client.post(
        "/miniapp/settings/visual",
        data={
            "background_color": "#101010",
            "text_color": "#fefefe",
            "border_color": "#555555",
            "button_background_color": "#333333",
            "page_background_color": "#202020",
            "input_background_color": "#252a31",
            "topbar_background_color": "#303030",
            "topbar_text_color": "#fafafa",
            "topbar_muted_color": "#b0b0b0",
            "topbar_accent_color": "#4488ff",
            "topbar_border_color": "#777777",
        },
        headers={"Cookie": f"soundcork_account_id={ACCOUNT_ID}"},
    )

    response = client.post(
        "/miniapp/settings/visual/light",
        headers={"Cookie": f"soundcork_account_id={ACCOUNT_ID}"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert "notice=Light%20theme%20applied" in response.headers["location"]
    settings = load_user_settings()
    assert settings.global_panel_style.background_color == "#fbf7ef"
    assert settings.global_panel_style.button_background_color == "#f6efe4"
    assert settings.visual_theme == "light"
    assert settings.site_style.page_background_color == "#f3ecdf"
    assert settings.site_style.input_background_color == "#faf4ea"
    assert settings.site_style.topbar_background_color == "#f3ecdf"
    assert settings.site_style.topbar_text_color == "#1d2329"
    assert settings.site_style.topbar_muted_color == "#6f665a"
    assert settings.site_style.topbar_accent_color == "#1868db"
    assert settings.site_style.topbar_border_color == "#ded3c3"
    assert settings.panel_style == {}


def test_visual_settings_dark_theme_updates_global_colors(monkeypatch):
    client, _speakers = make_client(monkeypatch)

    client.post(
        "/miniapp/settings/preferences",
        json={
            "panel_style": {
                "presets": {
                    "background_color": "#ffffff",
                    "text_color": "#fffffe",
                    "border_color": "#123456",
                    "button_background_color": "#654321",
                }
            }
        },
        headers={"Cookie": f"soundcork_account_id={ACCOUNT_ID}"},
    )

    response = client.post(
        "/miniapp/settings/visual/dark",
        headers={"Cookie": f"soundcork_account_id={ACCOUNT_ID}"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert "notice=Dark%20theme%20applied" in response.headers["location"]
    settings = load_user_settings()
    assert settings.global_panel_style.background_color == "#1d2024"
    assert settings.global_panel_style.text_color == "#f5f7f2"
    assert settings.global_panel_style.border_color == "#3d4652"
    assert settings.global_panel_style.button_background_color == "#2b3139"
    assert settings.visual_theme == "dark"
    assert settings.site_style.page_background_color == "#0f1012"
    assert settings.site_style.input_background_color == "#252b33"
    assert settings.site_style.topbar_background_color == "#161719"
    assert settings.site_style.topbar_text_color == "#f5f7f2"
    assert settings.site_style.topbar_muted_color == "#abb1b8"
    assert settings.site_style.topbar_accent_color == "#4cc9a7"
    assert settings.site_style.topbar_border_color == "#2f343a"
    assert settings.panel_style == {}


def test_preset_sync_icon_follows_selected_theme(monkeypatch):
    client, _speakers = make_client(monkeypatch)
    cookie = (
        f"soundcork_account_id={ACCOUNT_ID}; "
        f"soundcork_selected_device_id={DEVICE_ID}"
    )

    light_dashboard = client.get("/miniapp/dashboard", headers={"Cookie": cookie})

    assert light_dashboard.status_code == 200
    assert "/static/images/resync_black.png" in light_dashboard.text
    assert "/static/images/resync_white.png" not in light_dashboard.text

    client.post(
        "/miniapp/settings/visual/dark",
        headers={"Cookie": f"soundcork_account_id={ACCOUNT_ID}"},
    )
    dark_dashboard = client.get("/miniapp/dashboard", headers={"Cookie": cookie})

    assert "/static/images/resync_white.png" in dark_dashboard.text
    assert "/static/images/resync_black.png" not in dark_dashboard.text


def test_preset_sync_icon_follows_dark_button_background(monkeypatch):
    client, _speakers = make_client(monkeypatch)
    cookie = (
        f"soundcork_account_id={ACCOUNT_ID}; "
        f"soundcork_selected_device_id={DEVICE_ID}"
    )

    client.post(
        "/miniapp/settings/preferences",
        json={
            "global_panel_style": {
                "background_color": "#1d2024",
                "text_color": "#f5f7f2",
                "border_color": "#3d4652",
                "button_background_color": "#2b3139",
            },
        },
        headers={"Cookie": f"soundcork_account_id={ACCOUNT_ID}"},
    )
    dashboard = client.get("/miniapp/dashboard", headers={"Cookie": cookie})

    assert "/static/images/resync_white.png" in dashboard.text
    assert "/static/images/resync_black.png" not in dashboard.text


def test_language_setting_is_saved_server_side(monkeypatch):
    client, _speakers = make_client(monkeypatch)

    response = client.post(
        "/miniapp/settings/language",
        data={"language": "de"},
        headers={"Cookie": f"soundcork_account_id={ACCOUNT_ID}"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert load_user_settings().language == "de"

    dashboard = client.get(
        "/miniapp/dashboard",
        headers={
            "Cookie": (
                f"soundcork_account_id={ACCOUNT_ID}; "
                f"soundcork_selected_device_id={DEVICE_ID}"
            )
        },
    )

    assert 'html lang="de"' in dashboard.text
    assert "Lautstärke" in dashboard.text
    assert "Favoriten" in dashboard.text
    assert '"language": "de"' in dashboard.text
    assert '"translations"' in dashboard.text


def test_now_playing_returns_selected_speaker_state(monkeypatch):
    client, _speakers = make_client(monkeypatch)

    response = client.get(
        "/miniapp/now-playing",
        headers={"Cookie": f"soundcork_selected_device_id={DEVICE_ID}"},
    )

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert response.json()["now_playing"]["item_name"] == "Radio Test"
    payload = response.json()
    assert payload["jobs"]["sleep"] is None
    assert payload["jobs"]["alarm"] is None
    assert payload["jobs"]["items"] == []


def test_sleep_form_fetch_schedules_without_redirect(monkeypatch):
    client, _speakers = make_client(monkeypatch)

    response = client.post(
        "/miniapp/sleep",
        data={"sleep_minutes": "3"},
        headers={
            "Cookie": f"soundcork_selected_device_id={DEVICE_ID}",
            "X-SoundCork-Request": "fetch",
        },
        follow_redirects=False,
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["job"]["label"] == "Sleep in 3 min"
    assert payload["jobs"]["sleep"]["label"] == "Sleep in 3 min"
    assert len(payload["job_list"]) == 1


def test_multiple_sleep_jobs_stay_visible(monkeypatch):
    client, _speakers = make_client(monkeypatch)
    headers = {
        "Cookie": f"soundcork_selected_device_id={DEVICE_ID}",
        "X-SoundCork-Request": "fetch",
    }

    first = client.post("/miniapp/sleep", data={"sleep_minutes": "13"}, headers=headers)
    second = client.post("/miniapp/sleep", data={"sleep_minutes": "30"}, headers=headers)

    assert first.status_code == 200
    assert second.status_code == 200
    assert len(second.json()["job_list"]) == 2

    now_playing = client.get(
        "/miniapp/now-playing",
        headers={"Cookie": f"soundcork_selected_device_id={DEVICE_ID}"},
    )

    jobs = now_playing.json()["job_list"]
    assert [job["kind"] for job in jobs] == ["sleep", "sleep"]
    assert {job["label"] for job in jobs} == {"Sleep in 13 min", "Sleep in 30 min"}


def test_sleep_form_fetch_validation_stays_json(monkeypatch):
    client, _speakers = make_client(monkeypatch)

    response = client.post(
        "/miniapp/sleep",
        data={"sleep_minutes": "3", "repeat_daily": "1"},
        headers={
            "Cookie": f"soundcork_selected_device_id={DEVICE_ID}",
            "X-SoundCork-Request": "fetch",
        },
        follow_redirects=False,
    )

    assert response.status_code == 400
    assert response.json()["ok"] is False
    assert response.json()["error"] == "Invalid sleep timer"


def test_weekday_checks_without_weekly_repeat_do_not_repeat(monkeypatch):
    client, _speakers = make_client(monkeypatch)

    response = client.post(
        "/miniapp/alarm",
        data={
            "alarm_time": "23:59",
            "alarm_preset_id": "4",
            "repeat_days": ["1", "2"],
        },
        headers={
            "Cookie": f"soundcork_selected_device_id={DEVICE_ID}",
            "X-SoundCork-Request": "fetch",
        },
        follow_redirects=False,
    )

    assert response.status_code == 200
    assert response.json()["job"]["repeat_label"] == ""


def test_weekly_repeat_requires_selected_weekday(monkeypatch):
    client, _speakers = make_client(monkeypatch)

    response = client.post(
        "/miniapp/alarm",
        data={
            "alarm_time": "23:59",
            "alarm_preset_id": "4",
            "repeat_weekly": "1",
        },
        headers={
            "Cookie": f"soundcork_selected_device_id={DEVICE_ID}",
            "X-SoundCork-Request": "fetch",
        },
        follow_redirects=False,
    )

    assert response.status_code == 400
    assert response.json()["ok"] is False
    assert response.json()["error"] == "Invalid alarm time"


def test_weekly_repeat_uses_selected_weekdays(monkeypatch):
    client, _speakers = make_client(monkeypatch)

    response = client.post(
        "/miniapp/alarm",
        data={
            "alarm_time": "23:59",
            "alarm_preset_id": "4",
            "repeat_weekly": "1",
            "repeat_days": ["1", "2"],
        },
        headers={
            "Cookie": f"soundcork_selected_device_id={DEVICE_ID}",
            "X-SoundCork-Request": "fetch",
        },
        follow_redirects=False,
    )

    assert response.status_code == 200
    assert response.json()["job"]["repeat_label"] == "Repeat on Tuesday, Wednesday"


def test_repeating_alarm_stays_in_job_summary(monkeypatch):
    client, _speakers = make_client(monkeypatch)

    response = client.post(
        "/miniapp/alarm",
        data={
            "alarm_time": "23:59",
            "alarm_preset_id": "4",
            "repeat_daily": "1",
        },
        headers={"Cookie": f"soundcork_selected_device_id={DEVICE_ID}"},
        follow_redirects=False,
    )

    assert response.status_code == 303

    now_playing = client.get(
        "/miniapp/now-playing",
        headers={"Cookie": f"soundcork_selected_device_id={DEVICE_ID}"},
    )

    alarm_job = now_playing.json()["jobs"]["alarm"]
    assert alarm_job["repeat_label"] == "Repeat every day"
    assert alarm_job["paused"] is False


def test_alarm_form_fetch_schedules_without_redirect(monkeypatch):
    client, _speakers = make_client(monkeypatch)

    response = client.post(
        "/miniapp/alarm",
        data={
            "alarm_time": "23:59",
            "alarm_preset_id": "4",
        },
        headers={
            "Cookie": f"soundcork_selected_device_id={DEVICE_ID}",
            "X-SoundCork-Request": "fetch",
        },
        follow_redirects=False,
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["job"]["label"] == "Alarm 23:59 preset 4"
    assert payload["jobs"]["alarm"]["label"] == "Alarm 23:59 preset 4"


def test_scheduled_jobs_are_only_rendered_inside_timer_panel(monkeypatch):
    client, _speakers = make_client(monkeypatch)
    cookie = f"soundcork_selected_device_id={DEVICE_ID}"

    client.post(
        "/miniapp/alarm",
        data={"alarm_time": "23:59", "alarm_preset_id": "4"},
        headers={"Cookie": cookie},
        follow_redirects=False,
    )

    dashboard = client.get(
        "/miniapp/dashboard",
        headers={
            "Cookie": (
                f"soundcork_account_id={ACCOUNT_ID}; "
                f"soundcork_selected_device_id={DEVICE_ID}"
            )
        },
    )

    assert "job-banner-list" not in dashboard.text
    assert "timer-job-list" in dashboard.text
    assert "Alarm 23:59 preset 4" in dashboard.text


def test_alarm_toggle_and_cancel_fetch_update_jobs(monkeypatch):
    client, _speakers = make_client(monkeypatch)
    headers = {
        "Cookie": f"soundcork_selected_device_id={DEVICE_ID}",
        "X-SoundCork-Request": "fetch",
    }

    client.post(
        "/miniapp/alarm",
        data={"alarm_time": "23:59", "alarm_preset_id": "4"},
        headers=headers,
    )

    pause = client.post(
        "/miniapp/alarm/toggle",
        data={"paused": "true"},
        headers=headers,
    )

    assert pause.status_code == 200
    assert pause.json()["jobs"]["alarm"]["paused"] is True

    cancel = client.post("/miniapp/alarm/cancel", headers=headers)

    assert cancel.status_code == 200
    assert cancel.json()["jobs"]["alarm"] is None


def test_custom_stream_calls_speaker(monkeypatch):
    client, speakers = make_client(monkeypatch)

    response = client.post(
        "/miniapp/custom-stream",
        data={
            "preset_number": "2",
            "stream_name": "Direct FM",
            "stream_url": "https://example.test/live.mp3",
            "image_url": "https://example.test/logo.png",
        },
        headers={"Cookie": f"soundcork_selected_device_id={DEVICE_ID}"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert speakers.custom_stream_calls == [
        (
            DEVICE_ID,
            2,
            "Direct FM",
            "https://example.test/live.mp3",
            "https://example.test/logo.png",
        )
    ]


def test_resync_presets_calls_speaker(monkeypatch):
    client, speakers = make_client(monkeypatch)

    response = client.post(
        "/miniapp/resync-presets",
        headers={
            "Cookie": (
                f"soundcork_account_id={ACCOUNT_ID}; "
                f"soundcork_selected_device_id={DEVICE_ID}"
            )
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert speakers.sync_calls == [(ACCOUNT_ID, DEVICE_ID)]


def test_backup_includes_presets_and_panel_order(monkeypatch):
    client, _speakers = make_client(monkeypatch)

    response = client.post(
        "/miniapp/backup",
        data={"panel_order": '["volume","presets"]'},
        headers={
            "Cookie": (
                f"soundcork_account_id={ACCOUNT_ID}; "
                f"soundcork_selected_device_id={DEVICE_ID}"
            )
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    location = unquote(response.headers["location"])
    assert "Saved backup soundcork-backup-" in location
    filename = location.rsplit("Saved backup ", 1)[1]

    download = client.get(f"/miniapp/backup/{filename}")
    assert download.status_code == 200
    assert download.json()["panel_order"] == ["volume", "presets"]
    assert download.json()["presets"][0]["id"] == "4"


def test_saved_backup_can_be_deleted(monkeypatch):
    client, _speakers = make_client(monkeypatch)
    cookie = (
        f"soundcork_account_id={ACCOUNT_ID}; "
        f"soundcork_selected_device_id={DEVICE_ID}"
    )

    backup_response = client.post(
        "/miniapp/backup",
        headers={"Cookie": cookie},
        follow_redirects=False,
    )
    filename = unquote(backup_response.headers["location"]).rsplit("Saved backup ", 1)[
        1
    ]

    dashboard = client.get("/miniapp/dashboard", headers={"Cookie": cookie})
    assert f'value="{filename}"' in dashboard.text
    assert 'action="/miniapp/backup/delete"' in dashboard.text
    assert "data-confirm-form" in dashboard.text
    assert f"Delete {filename}?" in dashboard.text

    delete_response = client.post(
        "/miniapp/backup/delete",
        data={"backup_filename": filename, "return_to": "/miniapp/settings"},
        headers={"Cookie": cookie},
        follow_redirects=False,
    )

    assert delete_response.status_code == 303
    assert (
        delete_response.headers["location"]
        == "/miniapp/settings?notice=Backup%20deleted"
    )
    missing_download = client.get(f"/miniapp/backup/{filename}", follow_redirects=False)
    assert missing_download.status_code == 303
    assert (
        missing_download.headers["location"]
        == "/miniapp/dashboard?error=Backup%20not%20found"
    )


def test_restore_applies_presets(monkeypatch):
    client, speakers = make_client(monkeypatch)
    backup_json = {
        "presets": [
            {
                "id": "4",
                "name": "Direct FM",
                "source": "INTERNET_RADIO",
                "type": "uri",
                "location": "https://example.test/live.mp3",
                "source_account": "",
                "is_presetable": "true",
                "created_on": "",
                "updated_on": "",
                "container_art": "",
            }
        ],
        "panel_order": ["backup", "presets"],
    }

    response = client.post(
        "/miniapp/restore",
        data={"backup_json": __import__("json").dumps(backup_json)},
        headers={
            "Cookie": (
                f"soundcork_account_id={ACCOUNT_ID}; "
                f"soundcork_selected_device_id={DEVICE_ID}"
            )
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert speakers.apply_calls[0][0:2] == (ACCOUNT_ID, DEVICE_ID)
    assert speakers.apply_calls[0][2][0].name == "Direct FM"


def test_restore_saved_backup_file_applies_presets(monkeypatch):
    client, speakers = make_client(monkeypatch)

    backup_response = client.post(
        "/miniapp/backup",
        data={"panel_order": '["backup","presets"]'},
        headers={
            "Cookie": (
                f"soundcork_account_id={ACCOUNT_ID}; "
                f"soundcork_selected_device_id={DEVICE_ID}"
            )
        },
        follow_redirects=False,
    )
    filename = unquote(backup_response.headers["location"]).rsplit("Saved backup ", 1)[
        1
    ]

    response = client.post(
        "/miniapp/restore-backup-file",
        data={"backup_filename": filename},
        headers={
            "Cookie": (
                f"soundcork_account_id={ACCOUNT_ID}; "
                f"soundcork_selected_device_id={DEVICE_ID}"
            )
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert speakers.apply_calls[0][0:2] == (ACCOUNT_ID, DEVICE_ID)
    assert speakers.apply_calls[0][2][0].id == "4"
