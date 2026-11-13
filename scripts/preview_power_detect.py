#!/usr/bin/env python3
"""Standalone CLI preview tool for `power_detect` endpoints (no Home Assistant required).

Usage examples:
  python3 scripts/preview_power_detect.py
  python3 scripts/preview_power_detect.py http://power.x.wabb.it:1999
  python3 scripts/preview_power_detect.py power.x.wabb.it workshop-power.x.wabb.it:1999
"""

from __future__ import annotations

from datetime import datetime
import importlib.util
from pathlib import Path
import sys
import time
import types
import urllib.error
import urllib.request


def _load_modules():
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

    return (
        sys.modules["custom_components.power_detect.const"],
        sys.modules["custom_components.power_detect.api"],
    )


def _normalize_arg(arg: str, default_port: int) -> dict[str, object]:
    cleaned = arg.strip()
    if "://" in cleaned or ":" in cleaned or "/" in cleaned:
        return {"url": cleaned}
    return {"host": cleaned, "port": default_port}


def _fetch_text(url: str, timeout: float) -> str:
    req = urllib.request.Request(
        url, headers={"User-Agent": "HomeAssistant-PowerDetect/1.0"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def main(argv: list[str]) -> int:
    const, api = _load_modules()
    targets = argv or ["http://power.x.wabb.it:1999"]

    raw_entries = [_normalize_arg(t, const.DEFAULT_PORT) for t in targets]
    configs = api.resolve_server_configs(raw_entries)
    exit_code = 0

    for cfg in configs:
        tracker = api.PowerTrackerState(cfg)
        start = time.monotonic()
        now = datetime.now().astimezone()
        try:
            raw = _fetch_text(cfg.url, cfg.timeout)
            status = api.parse_status_response(raw)
            snapshot, _ = tracker.record_poll(status, reachable=True, now=now)
            latency_ms = (time.monotonic() - start) * 1000.0
            detail = f"reachable ({latency_ms:.0f} ms)"
        except (urllib.error.URLError, TimeoutError, OSError, api.PowerDetectError) as err:
            snapshot, _ = tracker.record_poll(
                api.PowerStatus.UNKNOWN, reachable=False, now=now, error=str(err)
            )
            detail = f"UNREACHABLE ({err})"
            exit_code = 2

        bin_state = (
            "ON "
            if snapshot.power_present is True
            else ("OFF" if snapshot.power_present is False else "UNK")
        )
        bin_entity = f"binary_sensor.{cfg.entity_slug}"
        sen_entity = f"sensor.{cfg.entity_slug}"
        print(
            f"{bin_entity:32s} = {bin_state}"
            + f" | {sen_entity:30s} = {snapshot.status.value:8s}"
            + f" | {cfg.url} ({detail})"
        )

    return exit_code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
