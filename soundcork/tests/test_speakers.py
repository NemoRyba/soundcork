import xml.etree.ElementTree as ET
from types import SimpleNamespace
from typing import Any, cast

from soundcork.model import Preset
from soundcork.ui.speakers import Speakers


class FakeDatastore:
    def __init__(self) -> None:
        self.saved: tuple[str, str, list[Preset]] | None = None

    def get_presets(self, account_id: str) -> list[Preset]:
        assert account_id == "account-1"
        return []

    def save_presets(
        self, account_id: str, device_id: str, presets: list[Preset]
    ) -> None:
        self.saved = (account_id, device_id, presets)


class BrokenPresetDatastore(FakeDatastore):
    def __init__(self) -> None:
        super().__init__()
        self.broken = True

    def get_presets(self, account_id: str) -> list[Preset]:
        if self.broken:
            raise ET.ParseError("junk after document element")
        return self.saved[2] if self.saved else []

    def save_presets(
        self, account_id: str, device_id: str, presets: list[Preset]
    ) -> None:
        self.broken = False
        super().save_presets(account_id, device_id, presets)


class AccountFallbackDatastore:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    def list_accounts(self) -> list[str]:
        return ["saved-account"]

    def get_content_item(self, account: str, device_id: str, ci_id: str):
        self.calls.append((account, device_id, ci_id))
        if account != "saved-account":
            return None
        return Preset(
            id=ci_id,
            name="Saved Station",
            source="TUNEIN",
            type="stationurl",
            location="/v1/playback/station/s11109",
            source_account="",
            is_presetable="true",
            container_art="",
        )


def test_play_content_item_falls_back_to_saved_account(monkeypatch):
    speakers = cast(Any, Speakers.__new__(Speakers))
    datastore = AccountFallbackDatastore()
    played = []

    class FakeSoundTouchClient:
        def __init__(self, st_device) -> None:
            self.st_device = st_device

        def PlayContentItem(self, content_item) -> None:
            played.append(content_item)

    monkeypatch.setattr(
        "soundcork.ui.speakers.SoundTouchClient", FakeSoundTouchClient
    )
    speakers._datastore = datastore
    speakers.all_devices = lambda: {
        "device-1": SimpleNamespace(
            id="device-1",
            ip="192.0.2.10",
            account="stale-discovered-account",
            st_device=object(),
        )
    }

    success = speakers.play_content_item("device-1", "5")

    assert success is True
    assert datastore.calls == [
        ("stale-discovered-account", "device-1", "5"),
        ("saved-account", "device-1", "5"),
    ]
    assert len(played) == 1


def test_store_tunein_station_as_preset_uses_store_preset_endpoint():
    speakers = cast(Any, Speakers.__new__(Speakers))
    datastore = FakeDatastore()
    posted: list[tuple[str, str, str]] = []

    speakers._datastore = datastore
    speakers.all_devices = lambda: {
        "device-1": SimpleNamespace(
            ip="192.0.2.10", account="account-1", st_device=object()
        )
    }
    speakers._post_speaker_xml = lambda host, path, element: posted.append(
        (host, path, ET.tostring(element, encoding="unicode"))
    )

    success = speakers.store_tunein_station_as_preset(
        "device-1",
        5,
        "/v1/playback/station/s11109",
        "radio klassik Stephansdom",
        "http://cdn-radiotime-logos.tunein.com/s11109q.png",
    )

    assert success is True
    assert len(posted) == 1
    host, path, payload = posted[0]
    assert host == "192.0.2.10"
    assert path == "/storePreset"

    preset = ET.fromstring(payload)
    content_item = preset.find("ContentItem")
    assert preset.attrib["id"] == "5"
    assert content_item is not None
    assert content_item.attrib["source"] == "TUNEIN"
    assert content_item.attrib["location"] == "/v1/playback/station/s11109"
    assert content_item.findtext("itemName") == "radio klassik Stephansdom"

    assert datastore.saved is not None
    account_id, device_id, presets = datastore.saved
    assert account_id == "account-1"
    assert device_id == "device-1"
    assert presets[0].id == "5"
    assert presets[0].location == "/v1/playback/station/s11109"


def test_broken_preset_cache_resyncs_from_speaker():
    speakers = cast(Any, Speakers.__new__(Speakers))
    datastore = BrokenPresetDatastore()

    speakers._datastore = datastore
    speakers.all_devices = lambda: {
        "device-1": SimpleNamespace(
            ip="192.0.2.10", account="account-1", st_device=object()
        )
    }
    speakers._get_speaker_xml = lambda host, path: b"""
        <presets>
          <preset id="1">
            <ContentItem source="TUNEIN" type="stationurl" location="/v1/playback/station/s1" sourceAccount="" isPresetable="true">
              <itemName>Station One</itemName>
              <containerArt>http://example.test/s1.png</containerArt>
            </ContentItem>
          </preset>
        </presets>
    """

    presets = speakers._get_presets_or_resync("account-1", "device-1")

    assert datastore.broken is False
    assert datastore.saved is not None
    assert presets[0].id == "1"
    assert presets[0].name == "Station One"
