import logging
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from bosesoundtouchapi.soundtouchclient import (  # type: ignore
    ContentItem as BCContentItem,
    SoundTouchClient,
    SoundTouchDevice,
)
from bosesoundtouchapi.soundtouchdiscovery import SoundTouchDiscovery  # type: ignore
from bosesoundtouchapi.models import (  # type: ignore
    SetupRequest,
    SetupRequestStates,
    WirelessProfile,
)
from bosesoundtouchapi.uri.soundtouchnodes import SoundTouchNodes
from pydantic import BaseModel

from soundcork.config import Settings
from soundcork.datastore import DataStore
from soundcork.model import ContentItem
from soundcork.model import Preset

logger = logging.getLogger(__name__)


def _xml_text(element: ET.Element | None, default: str = "") -> str:
    if element is None or element.text is None:
        return default
    return element.text.strip()


class CombinedDevice(BaseModel):
    """Device: either detected, configured, or both

    A Device that's at least one of:
    - A physical SoundTouch speaker detected on the network
    - A configured DeviceInfo block stored in the datastore.

    Property:
    - id: Bose-issued unique speaker ID from DeviceInfo
    - ip: The speaker's IP address
    - name: Human-readable speaker name
    - online: Discoverable on the network as of last-update to this object. Not updated on disconnect.
    - account: Account ID
    - in_soundcork: In the soundcork datastore
    - marge_server: API this speaker uses for Marge: (ie. Bose, or this Soundcork instance)
    - reachable:  Has been configured (ie. with a USB key) to have shell-access available.
    - st_device: SoundTouchDevice instance as discovered by BoseSoundTouchApi
    """

    id: str
    ip: str
    name: str
    online: bool
    account: str
    in_soundcork: bool
    marge_server: str
    reachable: bool
    st_device: SoundTouchDevice | None

    class Config:
        arbitrary_types_allowed = True


class Speakers:
    """
    This class contains methods used to interact with speakers, primarily through the
    bosesoundtouchapi package (https://github.com/thlucas1/bosesoundtouchapi)
    """

    def __init__(self, datastore: DataStore, settings: Settings) -> None:
        self._st_discovery = SoundTouchDiscovery(areDevicesVerified=True)
        self._st_discovery.DiscoverDevices(timeout=1)
        self._datastore = datastore
        self._settings = settings

    def soundtouch_devices(self) -> dict:
        return self._st_discovery.VerifiedDevices

    def clear_device(self, device_id: str):
        cd = self.all_devices().get(device_id)
        if cd:
            st = cd.st_device
            if st:
                self._st_discovery.VerifiedDevices.pop(f"{st.Host}:8090")
                self._st_discovery.DiscoveredDeviceNames.pop(f"{st.Host}:8090")

    def device_by_id(self, ip_port: str) -> SoundTouchDevice:
        logger.debug(f"Getting device by id: {ip_port}")
        return self._st_discovery.VerifiedDevices.get(ip_port)

    def all_devices(self) -> dict[str, CombinedDevice]:
        """
        Returns a combination of all devices seen on the network and
        all devices configured in soundcork as a dict with the device
        id as the key
        """
        combined_devices = {}
        account_ids = self._datastore.list_accounts()
        for account_id in account_ids:
            if account_id:
                for device_id in self._datastore.list_devices(account_id):
                    if device_id:
                        device_info = self._datastore.get_device_info(
                            account_id, device_id
                        )
                        cd = CombinedDevice(
                            # If the IP changes on a device reboot, it would have made a `/power_on`
                            # call to Soundcork, which will have already updated the datastore.
                            id=device_id,
                            ip=device_info.ip_address,
                            name=device_info.name,
                            online=False,
                            account=account_id,
                            in_soundcork=True,
                            marge_server="Unknown",
                            reachable=False,
                            st_device=None,
                        )
                        combined_devices[device_id] = cd
                        logger.debug(
                            f"cd for {device_id} = {combined_devices[device_id]}"
                        )

        verified = self.soundtouch_devices()
        for key in verified.keys():
            st_device = verified[key]
            id = st_device.DeviceId
            sc_device = combined_devices.get(id, None)

            if sc_device:
                sc_device.online = True
                sc_device.st_device = st_device
            else:
                new_cd = CombinedDevice(
                    id=id,
                    ip=st_device.Host,
                    name=st_device.DeviceName,
                    online=True,
                    account=st_device.StreamingAccountUUID,
                    in_soundcork=False,
                    marge_server=st_device.StreamingUrl,
                    reachable=False,
                    st_device=st_device,
                )
                combined_devices[id] = new_cd
                sc_device = new_cd
            if st_device.StreamingUrl == "https://streaming.bose.com":
                sc_device.marge_server = "Bose"
            elif st_device.StreamingUrl == f"{self._settings.base_url}/marge":
                sc_device.marge_server = "Soundcork"
            else:
                sc_device.marge_server = f"Unknown ({st_device.StreamingUrl})"

        return combined_devices

    def _content_item_to_soundtouchclient(self, ci: ContentItem) -> BCContentItem:
        """Maps our ContentItem to a SoundTouchClient ContentItem."""
        return BCContentItem(
            name=ci.name,
            source=ci.source,
            typeValue=ci.type,
            location=ci.location,
            sourceAccount=ci.source_account,
            isPresetable=ci.is_presetable,
        )

    def _device_client(
        self, device_id: str
    ) -> tuple[CombinedDevice, SoundTouchClient] | None:
        cd = self.all_devices().get(device_id)
        if not cd or not cd.st_device:
            logger.error("Device %s not found or not online", device_id)
            return None
        return cd, SoundTouchClient(cd.st_device)

    def play_content_item(self, device_id: str, content_item_id: str) -> bool:
        """Play a content_item on a specific device.

        Args:
            device_id: The device ID to play on
            content_item: The content item ID to play

        Returns:
            True if successful, False otherwise
        """
        cd = self.all_devices().get(device_id)
        if not cd or not cd.st_device:
            logger.error(f"Device {device_id} not found or not online")
            return False

        accounts_to_try = [cd.account]
        try:
            accounts_to_try.extend(
                account
                for account in self._datastore.list_accounts()
                if account and account not in accounts_to_try
            )
        except Exception as e:
            logger.warning("Could not list accounts for preset fallback: %s", e)

        content_item = None
        for account in accounts_to_try:
            try:
                content_item = self._datastore.get_content_item(
                    account=account,
                    device_id=cd.id,
                    ci_id=content_item_id,
                )
            except Exception as e:
                logger.warning(
                    "Could not resolve content item %s for account %s: %s",
                    content_item_id,
                    account,
                    e,
                )
                continue
            if content_item:
                if account != cd.account:
                    logger.info(
                        "Resolved content item %s through saved account %s "
                        "instead of discovered account %s",
                        content_item_id,
                        account,
                        cd.account,
                    )
                break

        if not content_item:
            logger.error(
                "%s is not a defined ContentItem for device %s; tried accounts %s",
                content_item_id,
                device_id,
                accounts_to_try,
            )
            return False

        logger.info(
            f"Attempting playback of content item {content_item_id} on device {device_id}"
        )
        bose_content_item = self._content_item_to_soundtouchclient(content_item)
        client = SoundTouchClient(cd.st_device)
        client.PlayContentItem(bose_content_item)

        return True

    def play_tunein_station(
        self, device_id: str, station_id: str, station_name: str
    ) -> bool:
        """Play a TuneIn station search result without saving it as a preset."""
        station_id = station_id.replace("/v1/playback/station/", "").strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]+", station_id):
            logger.error("Invalid TuneIn station id %s", station_id)
            return False

        station_name = station_name.strip()
        if not station_name:
            logger.error("Cannot play TuneIn station without a name")
            return False

        cd = self.all_devices().get(device_id)
        if not cd or not cd.st_device:
            logger.error("Device %s not found or not online", device_id)
            return False

        try:
            content_item = ContentItem(
                id=f"tunein:{station_id}",
                name=station_name,
                source="TUNEIN",
                type="stationurl",
                location=f"/v1/playback/station/{station_id}",
                source_account="",
                is_presetable="true",
            )
            client = SoundTouchClient(cd.st_device)
            client.PlayContentItem(self._content_item_to_soundtouchclient(content_item))
            return True
        except Exception as e:
            logger.error("Error playing TuneIn station on %s: %s", device_id, e)
            return False

    def stop_playback(self, device_id: str) -> bool:
        """Stop playback on a specific device.

        Args:
            device_id: The device ID to stop

        Returns:
            True if successful, False otherwise
        """
        cd = self.all_devices().get(device_id)
        if not cd or not cd.st_device:
            logger.error(f"Device {device_id} not found or not online")
            return False

        client = SoundTouchClient(cd.st_device)
        try:
            client.MediaStop()
            logger.info(f"Stopped playback on device {device_id}")
            return True
        except Exception as e:
            logger.error(f"Error stopping playback on device {device_id}: {e}")
            return False

    def standby_device(self, device_id: str) -> bool:
        device_client = self._device_client(device_id)
        if not device_client:
            return False

        _cd, client = device_client
        try:
            client.PowerStandby()
            return True
        except Exception as e:
            logger.error("Error putting device %s in standby: %s", device_id, e)
            return self.stop_playback(device_id)

    def now_playing_state(self, device_id: str) -> dict[str, str] | None:
        cd = self.all_devices().get(device_id)
        if not cd or not cd.st_device:
            logger.error("Device %s not found or not online", device_id)
            return None

        try:
            payload = self._get_speaker_xml(cd.ip, "/now_playing")
            root = ET.fromstring(payload)
            content_item = root.find("ContentItem")
            art = _xml_text(root.find("art"))
            if not art and content_item is not None:
                art = _xml_text(content_item.find("containerArt"))

            item_name = ""
            location = ""
            if content_item is not None:
                item_name = _xml_text(content_item.find("itemName"))
                location = content_item.attrib.get("location", "")

            station_name = _xml_text(root.find("stationName"))
            track = _xml_text(root.find("track"))
            return {
                "source": root.attrib.get("source", ""),
                "source_account": root.attrib.get("sourceAccount", ""),
                "item_name": item_name or station_name or track,
                "station_name": station_name,
                "track": track,
                "artist": _xml_text(root.find("artist")),
                "album": _xml_text(root.find("album")),
                "art": art,
                "play_status": _xml_text(root.find("playStatus")),
                "stream_type": _xml_text(root.find("streamType")),
                "location": location,
            }
        except Exception as e:
            logger.error("Error reading now playing on %s: %s", device_id, e)
            return None

    def volume_state(self, device_id: str) -> dict[str, int | bool] | None:
        device_client = self._device_client(device_id)
        if not device_client:
            return None

        _cd, client = device_client
        try:
            volume = client.GetVolume()
            actual = volume.Actual if volume.Actual is not None else 0
            target = volume.Target if volume.Target is not None else actual
            return {
                "actual": int(actual),
                "target": int(target),
                "muted": bool(volume.IsMuted),
            }
        except Exception as e:
            logger.error("Error reading volume on %s: %s", device_id, e)
            return None

    def set_volume_level(self, device_id: str, level: int) -> bool:
        device_client = self._device_client(device_id)
        if not device_client:
            return False

        _cd, client = device_client
        try:
            client.SetVolumeLevel(max(0, min(100, int(level))))
            return True
        except Exception as e:
            logger.error("Error setting volume on %s: %s", device_id, e)
            return False

    def set_mute(self, device_id: str, muted: bool) -> bool:
        device_client = self._device_client(device_id)
        if not device_client:
            return False

        _cd, client = device_client
        try:
            if muted:
                client.MuteOn()
            else:
                client.MuteOff()
            return True
        except Exception as e:
            logger.error("Error setting mute on %s: %s", device_id, e)
            return False

    def source_list(self, device_id: str) -> list[dict[str, str | bool]]:
        device_client = self._device_client(device_id)
        if not device_client:
            return []

        _cd, client = device_client
        try:
            sources = []
            for source in client.GetSourceList():
                sources.append(
                    {
                        "source": source.Source or "",
                        "source_account": source.SourceAccount or "",
                        "title": source.SourceTitle or source.Source or "",
                        "status": source.Status or "",
                        "is_local": bool(source.IsLocal),
                    }
                )
            return sources
        except Exception as e:
            logger.error("Error reading sources on %s: %s", device_id, e)
            return []

    def select_source(
        self, device_id: str, source: str, source_account: str = ""
    ) -> bool:
        device_client = self._device_client(device_id)
        if not device_client:
            return False

        source = source.strip().upper()
        source_account = source_account.strip() or None
        if not re.fullmatch(r"[A-Z0-9_]+", source):
            logger.error("Invalid source %s", source)
            return False

        _cd, client = device_client
        try:
            client.SelectSource(source, source_account, delay=1)
            return True
        except Exception as e:
            logger.error("Error selecting source %s on %s: %s", source, device_id, e)
            return False

    def enter_bluetooth_pairing(self, device_id: str) -> bool:
        device_client = self._device_client(device_id)
        if not device_client:
            return False

        _cd, client = device_client
        try:
            client.EnterBluetoothPairing()
            return True
        except Exception as e:
            logger.error("Error starting bluetooth pairing on %s: %s", device_id, e)
            return False

    def clear_bluetooth_paired(self, device_id: str) -> bool:
        device_client = self._device_client(device_id)
        if not device_client:
            return False

        _cd, client = device_client
        try:
            client.ClearBluetoothPaired()
            return True
        except Exception as e:
            logger.error("Error clearing bluetooth pairings on %s: %s", device_id, e)
            return False

    def wireless_profile(self, device_id: str) -> dict[str, str] | None:
        device_client = self._device_client(device_id)
        if not device_client:
            return None

        _cd, client = device_client
        try:
            profile = client.GetWirelessProfile()
            return {"ssid": profile.Ssid or ""}
        except Exception as e:
            logger.error("Error reading wireless profile on %s: %s", device_id, e)
            return None

    def network_status(self, device_id: str) -> dict[str, object] | None:
        device_client = self._device_client(device_id)
        if not device_client:
            return None

        _cd, client = device_client
        result: dict[str, object] = {
            "ssid": "",
            "configured_ssid": "",
            "kind": "",
            "name": "",
            "mac_address": "",
            "rssi": "",
            "frequency_khz": "",
            "is_running": False,
            "bindings": [],
            "ip_address": "",
            "interfaces": [],
            "network_info_interfaces": [],
            "wifi_profile_count": None,
        }
        found_status = False

        try:
            status = client.GetNetworkStatus()
            interfaces: list[dict[str, object]] = []
            for interface in status:
                data = interface.ToDictionary()
                bindings = [
                    str(binding) for binding in (data.get("bindings") or []) if binding
                ]
                data["bindings"] = bindings
                interfaces.append(data)

            running = [
                interface
                for interface in interfaces
                if interface.get("is_running") is True
            ]
            wireless = [
                interface
                for interface in interfaces
                if str(interface.get("kind") or "").lower() == "wireless"
            ]
            running_wireless = [
                interface
                for interface in wireless
                if interface.get("is_running") is True
            ]
            active = (
                running_wireless[0]
                if running_wireless
                else (
                    running[0]
                    if running
                    else (
                        wireless[0]
                        if wireless
                        else interfaces[0] if interfaces else None
                    )
                )
            )

            result["interfaces"] = interfaces
            if active:
                result.update(
                    {
                        "ssid": str(active.get("ssid") or ""),
                        "kind": str(active.get("kind") or ""),
                        "name": str(active.get("name") or ""),
                        "mac_address": str(active.get("mac_address") or ""),
                        "rssi": str(active.get("rssi") or ""),
                        "frequency_khz": str(active.get("frequency_khz") or ""),
                        "is_running": bool(active.get("is_running")),
                        "bindings": active.get("bindings") or [],
                    }
                )
                bindings = result["bindings"]
                if isinstance(bindings, list) and bindings:
                    result["ip_address"] = str(bindings[0])
                found_status = True
        except Exception as e:
            logger.error("Error reading network status on %s: %s", device_id, e)

        try:
            network_info = client.GetNetworkInfo()
            data = network_info.ToDictionary()
            info_interfaces = [
                interface
                for interface in (data.get("interfaces") or [])
                if isinstance(interface, dict)
            ]
            result["network_info_interfaces"] = info_interfaces
            result["wifi_profile_count"] = data.get("wifi_profile_count")
            for interface in info_interfaces:
                ssid = str(interface.get("ssid") or "")
                if ssid:
                    result["configured_ssid"] = ssid
                    break
            found_status = found_status or bool(info_interfaces)
        except Exception as e:
            logger.error("Error reading network info on %s: %s", device_id, e)

        try:
            profile = client.GetWirelessProfile()
            profile_ssid = profile.Ssid or ""
            if profile_ssid and not result["ssid"]:
                result["ssid"] = profile_ssid
            found_status = found_status or bool(profile_ssid)
        except Exception as e:
            logger.error("Error reading wireless profile on %s: %s", device_id, e)

        return result if found_status else None

    def add_wireless_profile(
        self, device_id: str, ssid: str, password: str, security_type: str
    ) -> bool:
        device_client = self._device_client(device_id)
        if not device_client:
            return False

        ssid = ssid.strip()
        security_type = security_type.strip() or "wpa_or_wpa2"
        if not ssid:
            logger.error("Cannot add wireless profile without SSID")
            return False

        _cd, client = device_client
        try:
            try:
                setup_request = SetupRequest(SetupRequestStates.SETUP_WIFI)
                client.Put(SoundTouchNodes.setup, setup_request)
                time.sleep(2)
            except Exception as e:
                logger.warning(
                    "Could not enter Wi-Fi setup mode on %s before adding profile: %s",
                    device_id,
                    e,
                )
            profile = WirelessProfile(
                ssid=ssid,
                password=password,
                securityType=security_type,
                timeoutSecs=30,
            )
            client.AddWirelessProfile(profile)
            return True
        except Exception as e:
            logger.error("Error adding wireless profile on %s: %s", device_id, e)
            return False
        finally:
            try:
                leave_request = SetupRequest(SetupRequestStates.SETUP_WIFI_LEAVE)
                client.Put(SoundTouchNodes.setup, leave_request)
            except Exception:
                pass

    def store_tunein_station_as_preset(
        self,
        device_id: str,
        preset_number: int,
        station_id: str,
        station_name: str,
        image_url: str = "",
    ) -> bool:
        """Store a TuneIn station on one of the device preset buttons."""
        if preset_number < 1 or preset_number > 6:
            logger.error("Invalid preset number %s", preset_number)
            return False

        station_id = station_id.replace("/v1/playback/station/", "").strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]+", station_id):
            logger.error("Invalid TuneIn station id %s", station_id)
            return False

        station_name = station_name.strip()
        if not station_name:
            logger.error("Cannot store preset without station name")
            return False

        cd = self.all_devices().get(device_id)
        if not cd or not cd.st_device:
            logger.error("Device %s not found or not online", device_id)
            return False

        try:
            location = f"/v1/playback/station/{station_id}"
            content_item = ET.Element(
                "ContentItem",
                {
                    "source": "TUNEIN",
                    "type": "stationurl",
                    "location": location,
                    "sourceAccount": "",
                    "isPresetable": "true",
                },
            )
            ET.SubElement(content_item, "itemName").text = station_name
            ET.SubElement(content_item, "containerArt").text = image_url

            preset = ET.Element("preset", {"id": str(preset_number)})
            preset.append(content_item)
            self._post_speaker_xml(cd.ip, "/storePreset", preset)
            self._save_tunein_preset_to_datastore(
                cd.account,
                device_id,
                preset_number,
                station_name,
                location,
                image_url,
            )
            return True
        except Exception as e:
            logger.error("Error storing TuneIn preset on %s: %s", device_id, e)
            return False

    def store_direct_stream_as_preset(
        self,
        device_id: str,
        preset_number: int,
        stream_name: str,
        stream_url: str,
        image_url: str = "",
    ) -> bool:
        """Store a direct internet radio stream URL as a preset."""
        if preset_number < 1 or preset_number > 6:
            logger.error("Invalid preset number %s", preset_number)
            return False

        stream_name = stream_name.strip()
        stream_url = stream_url.strip()
        parsed = urllib.parse.urlsplit(stream_url)
        if (
            not stream_name
            or parsed.scheme not in {"http", "https"}
            or not parsed.netloc
        ):
            logger.error("Invalid custom stream preset request")
            return False

        cd = self.all_devices().get(device_id)
        if not cd or not cd.st_device:
            logger.error("Device %s not found or not online", device_id)
            return False

        try:
            preset = Preset(
                id=str(preset_number),
                name=stream_name,
                source="INTERNET_RADIO",
                type="uri",
                location=stream_url,
                source_account="",
                is_presetable="true",
                created_on=str(int(time.time())),
                updated_on=str(int(time.time())),
                container_art=image_url,
            )
            self._store_preset_on_speaker(cd.ip, preset)
            self._save_preset_to_datastore(cd.account, device_id, preset)
            return True
        except Exception as e:
            logger.error("Error storing custom stream on %s: %s", device_id, e)
            return False

    def sync_presets_from_speaker(self, account_id: str, device_id: str) -> bool:
        """Read the speaker's hardware presets and save them in SoundCork."""
        cd = self.all_devices().get(device_id)
        if not cd or not cd.st_device:
            logger.error("Device %s not found or not online", device_id)
            return False

        try:
            payload = self._get_speaker_xml(cd.ip, "/presets")
            root = ET.fromstring(payload)
            presets = [
                self._preset_from_element(preset_element)
                for preset_element in root.findall("preset")
            ]
            self._datastore.save_presets(account_id, device_id, presets)
            return True
        except Exception as e:
            logger.error("Error syncing presets from %s: %s", device_id, e)
            return False

    def _get_presets_or_resync(self, account_id: str, device_id: str) -> list[Preset]:
        try:
            return self._datastore.get_presets(account_id)
        except ET.ParseError as e:
            logger.warning(
                "Preset cache for account %s is invalid (%s); resyncing from %s",
                account_id,
                e,
                device_id,
            )
            if self.sync_presets_from_speaker(account_id, device_id):
                return self._datastore.get_presets(account_id)
            raise

    def apply_presets(
        self, account_id: str, device_id: str, presets: list[Preset]
    ) -> bool:
        """Write a preset backup to SoundCork and the selected speaker."""
        cd = self.all_devices().get(device_id)
        if not cd or not cd.st_device:
            logger.error("Device %s not found or not online", device_id)
            return False

        try:
            preset_ids = {preset.id for preset in presets}
            for slot_number in range(1, 7):
                if str(slot_number) not in preset_ids:
                    remove = ET.Element("preset", {"id": str(slot_number)})
                    self._post_speaker_xml(cd.ip, "/removePreset", remove)

            for preset in presets:
                self._store_preset_on_speaker(cd.ip, preset)

            self._datastore.save_presets(account_id, device_id, presets)
            return True
        except Exception as e:
            logger.error("Error applying preset backup on %s: %s", device_id, e)
            return False

    def _post_speaker_xml(self, host: str, path: str, element: ET.Element) -> bytes:
        payload = ET.tostring(element, encoding="utf-8")
        request = urllib.request.Request(
            f"http://{host}:8090{path}",
            data=payload,
            headers={"Content-Type": "application/xml"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.read()

    def _get_speaker_xml(self, host: str, path: str) -> bytes:
        with urllib.request.urlopen(
            f"http://{host}:8090{path}", timeout=10
        ) as response:
            return response.read()

    def _save_tunein_preset_to_datastore(
        self,
        account_id: str,
        device_id: str,
        preset_number: int,
        name: str,
        location: str,
        image_url: str,
    ) -> None:
        presets = self._get_presets_or_resync(account_id, device_id)
        preset_id = str(preset_number)
        now = str(int(time.time()))
        updated = Preset(
            id=preset_id,
            name=name,
            source="TUNEIN",
            type="stationurl",
            location=location,
            source_account="",
            is_presetable="true",
            created_on=now,
            updated_on=now,
            container_art=image_url,
        )
        presets = [
            preset
            for preset in presets
            if preset.id != preset_id
            and not (
                preset.source == updated.source
                and preset.location == updated.location
                and (preset.source_account or "") == (updated.source_account or "")
            )
        ]
        presets.append(updated)
        self._datastore.save_presets(account_id, device_id, presets)

    def _save_preset_to_datastore(
        self, account_id: str, device_id: str, updated: Preset
    ) -> None:
        presets = self._get_presets_or_resync(account_id, device_id)
        presets = [
            preset
            for preset in presets
            if preset.id != updated.id
            and not (
                preset.source == updated.source
                and preset.location == updated.location
                and (preset.source_account or "") == (updated.source_account or "")
            )
        ]
        presets.append(updated)
        self._datastore.save_presets(account_id, device_id, presets)

    def reorder_presets(
        self, account_id: str, device_id: str, ordered_preset_ids: list[str]
    ) -> bool:
        """Renumber presets in the supplied six-slot order and sync the speaker."""
        if len(ordered_preset_ids) != 6:
            logger.error("Preset reorder requires six slots")
            return False

        cd = self.all_devices().get(device_id)
        if not cd or not cd.st_device:
            logger.error("Device %s not found or not online", device_id)
            return False

        existing_presets = self._get_presets_or_resync(account_id, device_id)
        presets_by_id = {preset.id: preset for preset in existing_presets}
        seen: set[str] = set()
        reordered: list[Preset] = []

        for slot_number, preset_id in enumerate(ordered_preset_ids, start=1):
            preset_id = preset_id.strip()
            if not preset_id:
                continue
            if preset_id in seen or preset_id not in presets_by_id:
                logger.error("Invalid preset reorder item %s", preset_id)
                return False
            seen.add(preset_id)

            original = presets_by_id[preset_id]
            reordered.append(
                original.model_copy(
                    update={
                        "id": str(slot_number),
                        "updated_on": str(int(time.time())),
                    }
                )
            )

        try:
            for slot_number in range(1, 7):
                if str(slot_number) not in {preset.id for preset in reordered}:
                    remove = ET.Element("preset", {"id": str(slot_number)})
                    self._post_speaker_xml(cd.ip, "/removePreset", remove)

            for preset in reordered:
                self._store_preset_on_speaker(cd.ip, preset)

            self._datastore.save_presets(account_id, device_id, reordered)
            return True
        except Exception as e:
            logger.error("Error reordering presets on %s: %s", device_id, e)
            self.sync_presets_from_speaker(account_id, device_id)
            return False

    def _store_preset_on_speaker(self, host: str, preset: Preset) -> None:
        preset_element = ET.Element("preset", {"id": preset.id})
        content_item = ET.SubElement(
            preset_element,
            "ContentItem",
            {
                "source": preset.source or "",
                "type": preset.type,
                "location": preset.location,
                "sourceAccount": preset.source_account or "",
                "isPresetable": preset.is_presetable or "true",
            },
        )
        ET.SubElement(content_item, "itemName").text = preset.name
        ET.SubElement(content_item, "containerArt").text = preset.container_art
        self._post_speaker_xml(host, "/storePreset", preset_element)

    def _preset_from_element(self, preset_element: ET.Element) -> Preset:
        content_item = preset_element.find("ContentItem")
        if content_item is None:
            raise ValueError("Preset is missing ContentItem")

        return Preset(
            id=preset_element.attrib.get("id", ""),
            name=_xml_text(content_item.find("itemName")),
            source=content_item.attrib.get("source", ""),
            type=content_item.attrib.get("type", ""),
            location=content_item.attrib.get("location", ""),
            source_account=content_item.attrib.get("sourceAccount", ""),
            is_presetable=content_item.attrib.get("isPresetable", "true"),
            created_on=preset_element.attrib.get("createdOn", ""),
            updated_on=preset_element.attrib.get("updatedOn", ""),
            container_art=_xml_text(content_item.find("containerArt")),
        )
