"""Unit tests for the `power_detect` client, URL/slug resolution, and state-transition events."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from zoneinfo import ZoneInfo


def _load_api():
    root = Path(__file__).resolve().parent.parent
    pkg_dir = root / "custom_components" / "power_detect"

    for mod_name in ("custom_components", "custom_components.power_detect"):
        if mod_name not in sys.modules:
            pkg = types.ModuleType(mod_name)
            pkg.__path__ = [str(pkg_dir)]
            sys.modules[mod_name] = pkg

    for sub in ("const", "api"):
        full_name = f"custom_components.power_detect.{sub}"
        spec = importlib.util.spec_from_file_location(full_name, pkg_dir / f"{sub}.py")
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        sys.modules[full_name] = module
        spec.loader.exec_module(module)

    return sys.modules["custom_components.power_detect.api"]


api = _load_api()


class _FakeResponse:
    def __init__(self, text: str, status_code: int = 200) -> None:
        self._text = text
        self.status_code = status_code

    async def __aenter__(self) -> _FakeResponse:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise ValueError(f"HTTP {self.status_code}")

    async def text(self) -> str:
        return self._text


class _FakeSession:
    def __init__(self, outcomes: list[object]) -> None:
        self._outcomes = list(outcomes)
        self.calls = 0

    def get(self, url: str, timeout: object = None) -> _FakeResponse:
        self.calls += 1
        item = self._outcomes.pop(0)
        if isinstance(item, Exception):
            raise item
        assert isinstance(item, _FakeResponse)
        return item


class _FakeAiohttp:
    class ClientError(Exception):
        pass

    class ClientTimeout:
        def __init__(self, total: float) -> None:
            self.total = total


class PowerDetectApiTests(unittest.TestCase):
    """Tests for status parsing, server config resolution, and `power_detect` event transitions."""

    def test_parse_status_response_valid_and_invalid(self) -> None:
        self.assertEqual(api.parse_status_response("powered\n"), api.PowerStatus.POWERED)
        self.assertEqual(api.parse_status_response("  BATTERY \r\n"), api.PowerStatus.BATTERY)
        self.assertEqual(api.parse_status_response("shutdown"), api.PowerStatus.SHUTDOWN)
        self.assertEqual(api.parse_status_response("unknown\n"), api.PowerStatus.UNKNOWN)

        for invalid in ("", "ok", "<html>502 Bad Gateway</html>", "power_on"):
            with self.assertRaises(api.PowerDetectError):
                api.parse_status_response(invalid)

    def test_build_server_url_variants(self) -> None:
        self.assertEqual(
            api.build_server_url(url="http://power.x.wabb.it:1999/"),
            "http://power.x.wabb.it:1999",
        )
        self.assertEqual(
            api.build_server_url(url="power.x.wabb.it:1999"),
            "http://power.x.wabb.it:1999",
        )
        self.assertEqual(
            api.build_server_url(host="power.x.wabb.it"),
            "http://power.x.wabb.it:1999",
        )
        self.assertEqual(
            api.build_server_url(host="::1", port=2000),
            "http://[::1]:2000",
        )
        with self.assertRaises(api.PowerDetectError):
            api.build_server_url()

    def test_resolve_server_configs_single_and_multi(self) -> None:
        single = api.resolve_server_configs([{"url": "http://power.x.wabb.it:1999"}])
        self.assertEqual(len(single), 1)
        self.assertEqual(single[0].name, "Power Detect")
        self.assertEqual(single[0].entity_slug, "power_detect")
        self.assertEqual(single[0].url, "http://power.x.wabb.it:1999")

        multi = api.resolve_server_configs(
            [
                {"url": "http://power.x.wabb.it:1999", "name": "House Power"},
                {"host": "workshop.x.wabb.it"},
            ]
        )
        self.assertEqual(multi[0].entity_slug, "house_power")
        self.assertEqual(multi[1].name, "Workshop Power")
        self.assertEqual(multi[1].entity_slug, "workshop_power")

        # Duplicate slugs disambiguate deterministically
        dups = api.resolve_server_configs(
            [
                {"host": "10.0.0.10", "slug": "ups"},
                {"host": "10.0.0.11", "slug": "ups"},
            ]
        )
        self.assertEqual([c.entity_slug for c in dups], ["ups_1", "ups_2"])

    def test_full_outage_lifecycle_and_single_event(self) -> None:
        cfg = api.resolve_server_configs([{"url": "http://power.x.wabb.it:1999"}])[0]
        tracker = api.PowerTrackerState(cfg)
        pacific = ZoneInfo("America/Los_Angeles")
        t0 = datetime(2026, 11, 13, 12, 0, 0, tzinfo=pacific)

        # 1. Startup poll -> NO event fired
        snap0, ev0 = tracker.record_poll(
            api.PowerStatus.POWERED, reachable=True, now=t0
        )
        self.assertIsNone(ev0)
        self.assertTrue(snap0.power_present)
        self.assertEqual(snap0.status, api.PowerStatus.POWERED)
        self.assertIsNone(snap0.outage_started)

        # 2. Steady powered poll -> NO event fired
        snap1, ev1 = tracker.record_poll(
            api.PowerStatus.POWERED, reachable=True, now=t0 + timedelta(seconds=10)
        )
        self.assertIsNone(ev1)
        self.assertEqual(snap1.last_changed, t0)

        # 3. Mains power lost -> switches to `battery`
        t_loss = t0 + timedelta(seconds=20)
        snap2, ev2 = tracker.record_poll(
            api.PowerStatus.BATTERY, reachable=True, now=t_loss
        )
        self.assertFalse(snap2.power_present)
        self.assertEqual(snap2.outage_started, t_loss)
        assert ev2 is not None
        self.assertEqual(ev2.event_type, "power_detect")
        self.assertEqual(ev2.data["entity_id"], "sensor.power_detect")
        self.assertEqual(ev2.data["binary_sensor_id"], "binary_sensor.power_detect")
        self.assertEqual(ev2.data["previous_status"], "powered")
        self.assertEqual(ev2.data["status"], "battery")
        self.assertFalse(ev2.data["was_outage"])
        self.assertEqual(ev2.data["changed_at"], "2026-11-13T12:00:20-08:00")
        self.assertEqual(ev2.data["outage_duration_seconds"], 0.0)

        # 4. Grace period (300s) expires -> switches to `shutdown`
        t_shutdown = t_loss + timedelta(seconds=300)
        snap3, ev3 = tracker.record_poll(
            api.PowerStatus.SHUTDOWN, reachable=True, now=t_shutdown
        )
        self.assertFalse(snap3.power_present)
        self.assertEqual(snap3.outage_started, t_loss)
        assert ev3 is not None
        self.assertEqual(ev3.event_type, "power_detect")
        self.assertEqual(ev3.data["status"], "shutdown")
        self.assertTrue(ev3.data["was_outage"])
        self.assertEqual(ev3.data["outage_duration_seconds"], 300.0)

        # 5. Mains power restored 120s later -> switches back to `powered`
        t_restored = t_shutdown + timedelta(seconds=120)
        snap4, ev4 = tracker.record_poll(
            api.PowerStatus.POWERED, reachable=True, now=t_restored
        )
        self.assertTrue(snap4.power_present)
        self.assertIsNone(snap4.outage_started)
        assert ev4 is not None
        self.assertEqual(ev4.event_type, "power_detect")
        self.assertEqual(ev4.data["status"], "powered")
        self.assertTrue(ev4.data["was_outage"])
        self.assertEqual(ev4.data["last_known_status"], "shutdown")
        self.assertEqual(ev4.data["outage_duration_seconds"], 420.0)
        self.assertEqual(
            ev4.data["outage_started"], t_loss.isoformat(timespec="seconds")
        )

    def test_network_blip_unknown_is_not_treated_as_outage(self) -> None:
        cfg = api.resolve_server_configs([{"url": "http://power.x.wabb.it:1999"}])[0]
        tracker = api.PowerTrackerState(cfg)
        pacific = ZoneInfo("America/Los_Angeles")
        t0 = datetime(2026, 11, 13, 12, 0, 0, tzinfo=pacific)

        tracker.record_poll(api.PowerStatus.POWERED, reachable=True, now=t0)

        # Network unreachable -> `unknown` (`power_present` is None, not False!)
        snap_unk, ev_unk = tracker.record_poll(
            api.PowerStatus.UNKNOWN,
            reachable=False,
            now=t0 + timedelta(seconds=10),
            error="Connection timed out",
        )
        self.assertIsNone(snap_unk.power_present)
        self.assertFalse(snap_unk.reachable)
        self.assertEqual(snap_unk.last_known_status, api.PowerStatus.POWERED)
        assert ev_unk is not None
        self.assertEqual(ev_unk.event_type, "power_detect")
        self.assertEqual(ev_unk.data["status"], "unknown")
        self.assertEqual(ev_unk.data["error"], "Connection timed out")

        # Network recovers while power never failed -> `was_outage` is False!
        snap_rec, ev_rec = tracker.record_poll(
            api.PowerStatus.POWERED,
            reachable=True,
            now=t0 + timedelta(seconds=20),
        )
        self.assertTrue(snap_rec.power_present)
        assert ev_rec is not None
        self.assertEqual(ev_rec.event_type, "power_detect")
        self.assertFalse(ev_rec.data["was_outage"])
        self.assertIsNone(ev_rec.data["outage_duration_seconds"])

        attrs = api.format_snapshot_attributes(cfg, snap_rec)
        self.assertEqual(attrs["status"], "powered")
        self.assertEqual(attrs["previous_status"], "unknown")
        self.assertEqual(attrs["last_checked"], "2026-11-13T12:00:20-08:00")
        self.assertTrue(attrs["reachable"])

    def test_client_retries_once_on_transient_error(self) -> None:
        orig_aiohttp = api.aiohttp
        api.aiohttp = _FakeAiohttp
        try:
            session = _FakeSession(
                [TimeoutError("transient"), _FakeResponse("battery\n")]
            )
            client = api.PowerDetectClient(
                session=session,  # type: ignore[arg-type]
                url="http://power.x.wabb.it:1999",
                request_timeout=3.0,
                retries=1,
                retry_delay=0.0,
            )
            status = asyncio.run(client.async_fetch_status())
            self.assertEqual(status, api.PowerStatus.BATTERY)
            self.assertEqual(session.calls, 2)

            failing_session = _FakeSession(
                [TimeoutError("down 1"), TimeoutError("down 2")]
            )
            failing_client = api.PowerDetectClient(
                session=failing_session,  # type: ignore[arg-type]
                url="http://power.x.wabb.it:1999",
                request_timeout=3.0,
                retries=1,
                retry_delay=0.0,
            )
            with self.assertRaises(api.PowerDetectError):
                asyncio.run(failing_client.async_fetch_status())
            self.assertEqual(failing_session.calls, 2)
        finally:
            api.aiohttp = orig_aiohttp


if __name__ == "__main__":
    unittest.main()
