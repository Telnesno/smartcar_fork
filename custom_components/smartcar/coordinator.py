from __future__ import annotations

import copy
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
import datetime as dt
from datetime import timedelta
from http import HTTPStatus
import logging
from typing import Any

from aiohttp import ClientResponseError
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
    UpdateFailed,
)
from homeassistant.util import dt as dt_util

from .auth import AbstractAuth
from .const import CONF_APPLICATION_MANAGEMENT_TOKEN, DOMAIN, EntityDescriptionKey
from .util import async_request_with_retry, key_path_update

_LOGGER = logging.getLogger(__name__)

VEHICLE_FRONT_ROW = 0
VEHICLE_BACK_ROW = 1
VEHICLE_LEFT_COLUMN = 0
VEHICLE_RIGHT_COLUMN = 1

UPDATE_INTERVAL = timedelta(hours=6)

# Units that indicate an imperial measurement (used for unit_system tagging)
IMPERIAL_MEASUREMENTS = {"miles", "psi", "gallons", "mph"}


@dataclass
class DatapointConfig:
    """Datapoint config for a single Smartcar v3 signal."""

    code: str | None  # v3 signal code; None means no v3 equivalent / meta-only
    required_scopes: list[str]

    @property
    def storage_key(self) -> str:
        assert self.code
        return self.code


DATAPOINT_ENTITY_KEY_MAP: dict[EntityDescriptionKey, DatapointConfig] = {
    EntityDescriptionKey.BATTERY_CAPACITY: DatapointConfig(
        "tractionbattery-nominalcapacity",
        ["read_battery"],
    ),
    EntityDescriptionKey.BATTERY_LEVEL: DatapointConfig(
        "tractionbattery-stateofcharge",
        ["read_battery"],
    ),
    EntityDescriptionKey.BATTERY_HEATER_ACTIVE: DatapointConfig(
        "tractionbattery-isheateractive",
        [],
    ),
    EntityDescriptionKey.CHARGE_LIMIT: DatapointConfig(
        "charge-chargelimits",
        ["read_charge", "control_charge"],
    ),
    EntityDescriptionKey.CHARGE_CHARGERATE: DatapointConfig(
        "charge-chargerate",
        [],
    ),
    EntityDescriptionKey.CHARGE_ENERGYADDED: DatapointConfig(
        "charge-energyadded",
        [],
    ),
    EntityDescriptionKey.CHARGE_TIMETOCOMPLETE: DatapointConfig(
        "charge-timetocomplete",
        [],
    ),
    EntityDescriptionKey.CHARGING: DatapointConfig(
        "charge-ischarging",
        ["read_charge", "control_charge"],
    ),
    EntityDescriptionKey.CHARGING_STATE: DatapointConfig(
        "charge-detailedchargingstatus",
        ["read_charge", "control_charge"],
    ),
    EntityDescriptionKey.PLUG_STATUS: DatapointConfig(
        "charge-ischargingcableconnected",
        ["read_charge"],
    ),
    EntityDescriptionKey.CHARGE_VOLTAGE: DatapointConfig(
        "charge-voltage",
        [],
    ),
    EntityDescriptionKey.CHARGE_AMPERAGE: DatapointConfig(
        "charge-amperage",
        [],
    ),
    EntityDescriptionKey.CHARGE_WATTAGE: DatapointConfig(
        "charge-wattage",
        [],
    ),
    EntityDescriptionKey.CHARGE_TIME_TO_COMPLETE: DatapointConfig(
        "charge-timetocomplete",
        [],
    ),
    EntityDescriptionKey.CHARGE_AMPERAGE_MAX: DatapointConfig(
        "charge-amperagemax",
        [],
    ),
    EntityDescriptionKey.CHARGE_FAST_CHARGER_PRESENT: DatapointConfig(
        "charge-isfastchargerpresent",
        [],
    ),
    EntityDescriptionKey.DOOR_LOCK: DatapointConfig(
        "closure-islocked",
        ["read_security", "control_security"],
    ),
    EntityDescriptionKey.DOOR_BACK_LEFT_LOCK: DatapointConfig(
        "closure-doors",
        [],
    ),
    EntityDescriptionKey.DOOR_BACK_RIGHT_LOCK: DatapointConfig(
        "closure-doors",
        [],
    ),
    EntityDescriptionKey.DOOR_FRONT_LEFT_LOCK: DatapointConfig(
        "closure-doors",
        [],
    ),
    EntityDescriptionKey.DOOR_FRONT_RIGHT_LOCK: DatapointConfig(
        "closure-doors",
        [],
    ),
    EntityDescriptionKey.DOOR_BACK_LEFT: DatapointConfig(
        "closure-doors",
        [],
    ),
    EntityDescriptionKey.DOOR_BACK_RIGHT: DatapointConfig(
        "closure-doors",
        [],
    ),
    EntityDescriptionKey.DOOR_FRONT_LEFT: DatapointConfig(
        "closure-doors",
        [],
    ),
    EntityDescriptionKey.DOOR_FRONT_RIGHT: DatapointConfig(
        "closure-doors",
        [],
    ),
    EntityDescriptionKey.ENGINE_COVER: DatapointConfig(
        "closure-enginecover",
        [],
    ),
    EntityDescriptionKey.FRONT_TRUNK: DatapointConfig(
        "closure-fronttrunk",
        [],
    ),
    EntityDescriptionKey.FRONT_TRUNK_LOCK: DatapointConfig(
        "closure-fronttrunk",
        [],
    ),
    EntityDescriptionKey.REAR_TRUNK: DatapointConfig(
        "closure-reartrunk",
        [],
    ),
    EntityDescriptionKey.REAR_TRUNK_LOCK: DatapointConfig(
        "closure-reartrunk",
        [],
    ),
    EntityDescriptionKey.SUNROOF: DatapointConfig(
        "closure-sunroof",
        [],
    ),
    EntityDescriptionKey.WINDOW_BACK_LEFT: DatapointConfig(
        "closure-windows",
        [],
    ),
    EntityDescriptionKey.WINDOW_BACK_RIGHT: DatapointConfig(
        "closure-windows",
        [],
    ),
    EntityDescriptionKey.WINDOW_FRONT_LEFT: DatapointConfig(
        "closure-windows",
        [],
    ),
    EntityDescriptionKey.WINDOW_FRONT_RIGHT: DatapointConfig(
        "closure-windows",
        [],
    ),
    EntityDescriptionKey.ENGINE_OIL: DatapointConfig(
        "internalcombustionengine-oillife",
        ["read_engine_oil"],
    ),
    EntityDescriptionKey.FUEL: DatapointConfig(
        "internalcombustionengine-amountremaining",
        ["read_fuel"],
    ),
    EntityDescriptionKey.FUEL_PERCENT: DatapointConfig(
        "internalcombustionengine-fuellevel",
        ["read_fuel"],
    ),
    EntityDescriptionKey.FUEL_RANGE: DatapointConfig(
        "internalcombustionengine-range",
        ["read_fuel"],
    ),
    EntityDescriptionKey.LOCATION: DatapointConfig(
        "location-preciselocation",
        ["read_location"],
    ),
    EntityDescriptionKey.LOW_VOLTAGE_BATTERY_LEVEL: DatapointConfig(
        "lowvoltagebattery-stateofcharge",
        [],
    ),
    EntityDescriptionKey.ODOMETER: DatapointConfig(
        "odometer-traveleddistance",
        ["read_odometer"],
    ),
    EntityDescriptionKey.RANGE: DatapointConfig(
        "tractionbattery-range",
        ["read_battery"],
    ),
    EntityDescriptionKey.GEAR_STATE: DatapointConfig(
        "transmission-gearstate",
        [],
    ),
    EntityDescriptionKey.TIRE_PRESSURE_BACK_LEFT: DatapointConfig(
        "wheel-tires",
        ["read_tires"],
    ),
    EntityDescriptionKey.TIRE_PRESSURE_BACK_RIGHT: DatapointConfig(
        "wheel-tires",
        ["read_tires"],
    ),
    EntityDescriptionKey.TIRE_PRESSURE_FRONT_LEFT: DatapointConfig(
        "wheel-tires",
        ["read_tires"],
    ),
    EntityDescriptionKey.TIRE_PRESSURE_FRONT_RIGHT: DatapointConfig(
        "wheel-tires",
        ["read_tires"],
    ),
    EntityDescriptionKey.ONLINE: DatapointConfig(
        "connectivitystatus-isonline",
        [],
    ),
    EntityDescriptionKey.ASLEEP: DatapointConfig(
        "connectivitystatus-isasleep",
        [],
    ),
    EntityDescriptionKey.DIGITAL_KEY_PAIRED: DatapointConfig(
        "connectivitystatus-isdigitalkeypaired",
        [],
    ),
    EntityDescriptionKey.SURVEILLANCE_ENABLED: DatapointConfig(
        "surveillance-isenabled",
        [],
    ),
    EntityDescriptionKey.FIRMWARE_VERSION: DatapointConfig(
        "connectivitysoftware-currentfirmwareversion",
        [],
    ),
}

# Map from signal code → tuple of DatapointConfigs that use it
DATAPOINT_CODE_MAP: dict[str | None, tuple[DatapointConfig, ...]] = {
    code: tuple(
        datapoint
        for datapoint in DATAPOINT_ENTITY_KEY_MAP.values()
        if datapoint.code == code
    )
    for code in {datapoint.code for datapoint in DATAPOINT_ENTITY_KEY_MAP.values()}
}


def normalize_signal_body(code: str | None, body: dict) -> tuple[dict, str | None]:
    """Normalize a v3 signal body: convert percent values and extract unit_system.

    Returns:
        (normalized_body, unit_system)
    """
    body = copy.deepcopy(body)
    unit = body.pop("unit", None)

    if unit == "percent":
        _handle_percent_conversion(code, body)
        unit_system = None
    elif unit in IMPERIAL_MEASUREMENTS:
        unit_system = "imperial"
    elif unit is not None:
        unit_system = "metric"
    else:
        unit_system = None

    return body, unit_system


def _handle_percent_conversion(code: str | None, body: dict) -> None:
    """Convert percent values from 0-100 range to 0-1 range in-place."""
    _MULTIVALUE_ITEM_KEY: dict[str | None, str] = {
        "charge-chargelimits": "limit",
    }

    if "values" in body:
        item_key = _MULTIVALUE_ITEM_KEY.get(code) or "value"
        body["values"] = [
            value | {item_key: value[item_key] / 100}
            for value in body["values"]
        ]
    elif "value" in body:
        body["value"] /= 100


class SmartcarVehicleCoordinator(DataUpdateCoordinator):
    """Coordinates updates via the Smartcar v3 GET /signals endpoint."""

    def __init__(
        self,
        hass: HomeAssistant,
        auth: AbstractAuth,
        vehicle_id: str,
        vin: str,
        entry: ConfigEntry,
    ) -> None:
        """Initialize coordinator."""
        self.auth = auth
        self.vehicle_id = vehicle_id
        self.vin = vin
        self.entry = entry
        # batch_requests acts as a "poll requested" flag set
        self.batch_requests: set[EntityDescriptionKey] = set()
        self.data: dict[str, Any] = {}

        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{vin}",
            update_interval=UPDATE_INTERVAL
            if CONF_APPLICATION_MANAGEMENT_TOKEN not in entry.data
            else None,
        )

    def is_scope_enabled(
        self, sensor_key: EntityDescriptionKey, *, verbose: bool = False
    ) -> bool:
        token_scopes = self.config_entry.data.get("token", {}).get("scopes", [])
        required_scopes = DATAPOINT_ENTITY_KEY_MAP[sensor_key].required_scopes
        missing = [scope for scope in required_scopes if scope not in token_scopes]
        enabled = len(missing) == 0

        if not enabled and verbose:
            _LOGGER.warning(
                "Skipping `%s` which requires %r, but "
                "user is missing %r with enabled scopes of %r.",
                sensor_key,
                required_scopes,
                missing,
                token_scopes,
            )

        return enabled

    def batch_sensor(self, sensor: CoordinatorEntity) -> None:
        """Mark a sensor to be included in the next update batch."""
        self._batch_add(sensor.entity_description.key)

    def _batch_add(self, key: EntityDescriptionKey) -> None:
        """Mark data as needing to be fetched in the next update."""
        assert self.is_scope_enabled(key)
        self.batch_requests.add(key)

    def _batch_add_defaults(self) -> None:
        """Add default batch keys when none were explicitly requested.

        In v3, GET /signals returns all available signals at once, so we only
        need to know whether to trigger a poll at all — not which paths to
        batch. This method sets the flag if polling is appropriate.
        """
        if self.batch_requests:
            return
        if (
            self.config_entry.pref_disable_polling
            or CONF_APPLICATION_MANAGEMENT_TOKEN in self.config_entry.data
        ):
            return

        entities: list[er.RegistryEntry] = er.async_entries_for_config_entry(
            er.async_get(self.hass), self.config_entry.entry_id
        )

        for entity in entities:
            _, key = entity.unique_id.split("_", 1)
            if key not in DATAPOINT_ENTITY_KEY_MAP:
                continue
            config = DATAPOINT_ENTITY_KEY_MAP[key]

            if config.code is not None and not entity.disabled:
                self._batch_add(key)
                return  # only need one to trigger; v3 fetches all signals anyway

    def _batch_process(self) -> bool:
        """Return True if a poll should occur, and clear the batch set."""
        self._batch_add_defaults()
        should_poll = bool(self.batch_requests)
        self.batch_requests.clear()
        return should_poll

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch all vehicle signals from the v3 GET /signals endpoint.

        Returns:
            The updated coordinator data dict.

        Raises:
            ConfigEntryAuthFailed: If an authentication failure occurs.
            UpdateFailed: If the update fails for any reason.
        """
        should_poll = self._batch_process()

        if not should_poll:
            _LOGGER.warning(
                "Coordinator %s: No updates to request based on granted scopes and context.",
                self.name,
            )
            return self.data

        _LOGGER.debug(
            "Coordinator %s: Requesting v3 signals update (interval: %s)",
            self.name,
            self.update_interval,
        )

        all_signals: list[dict] = []
        path = f"vehicles/{self.vehicle_id}/signals"

        # v3 GET /signals is paginated
        while path:
            try:
                response = await async_request_with_retry(
                    lambda p=path: self.auth.request("get", p),
                    logger=_LOGGER,
                    context=f"Coordinator {self.name}",
                )
            except ClientResponseError as exception:
                if exception.status in {
                    HTTPStatus.BAD_REQUEST,
                    HTTPStatus.UNAUTHORIZED,
                    HTTPStatus.FORBIDDEN,
                }:
                    raise ConfigEntryAuthFailed from exception
                raise

            if response.status in {
                HTTPStatus.TOO_MANY_REQUESTS,
                HTTPStatus.INTERNAL_SERVER_ERROR,
            }:
                msg = f"API returned {response.status} after retries"
                raise UpdateFailed(msg)

            response.raise_for_status()
            response_data = await response.json()

            if "data" not in response_data:
                msg = "Invalid v3 signals response: missing 'data' key"
                raise UpdateFailed(msg)

            all_signals.extend(response_data["data"])

            # Follow pagination
            next_link: str | None = response_data.get("links", {}).get("next")
            if next_link:
                # Strip leading slash/host — keep only path portion
                path = next_link.lstrip("/")
            else:
                path = None

        _LOGGER.debug(
            "Coordinator %s: Received %d signals from v3 API",
            self.name,
            len(all_signals),
        )

        return self._merge_signals_data(all_signals)

    def _merge_signals_data(self, signals: list[dict]) -> dict[str, Any]:
        """Merge v3 signal list into coordinator data store.

        Mirrors the webhook signal handler so both paths produce identical
        storage format.

        Returns:
            The newly merged data dict.
        """
        with self.create_updated_data() as (add, updated_data):
            for signal in signals:
                attrs = signal.get("attributes", {})
                code: str | None = attrs.get("code")
                status = attrs.get("status", {})
                body: dict = copy.deepcopy(attrs.get("body") or {})
                meta = signal.get("meta", {})

                is_error = status.get("value") != "SUCCESS"

                if is_error:
                    error = status.get("error", {})
                    error_code = error.get("code", "")
                    if code in DATAPOINT_CODE_MAP:
                        _LOGGER.debug(
                            "Coordinator %s: Signal %s has status %s (%s)",
                            self.name,
                            code,
                            status.get("value"),
                            error_code,
                        )

                if code not in DATAPOINT_CODE_MAP:
                    continue

                data_age: dt.datetime | None = None
                fetched_at: dt.datetime | None = None
                unit_system: str | None = None

                if not is_error:
                    body, unit_system = normalize_signal_body(code, body)

                    if oem_ts := meta.get("oemUpdatedAt"):
                        data_age = dt_util.parse_datetime(oem_ts)
                    if ret_ts := meta.get("retrievedAt"):
                        fetched_at = dt_util.parse_datetime(ret_ts)

                add.from_response_body(
                    code,
                    body=body if not is_error else None,
                    unit_system=unit_system,
                    data_age=data_age,
                    fetched_at=fetched_at,
                )

            _LOGGER.debug("Coordinator %s: Signals update processed", self.name)
            return updated_data

    @contextmanager
    def create_updated_data(
        self,
    ) -> Generator[tuple[_DataAdder, dict[str, Any]]]:
        updated_data = dict(self.data or {})
        yield _DataAdder(updated_data), updated_data


class _DataAdder:
    def __init__(self, data: dict[str, Any]) -> None:
        super().__init__()
        self.data = data

    def from_response_body(
        self,
        code: str,
        *,
        body: dict[str, Any] | None,
        data_age: dt.datetime | None = None,
        fetched_at: dt.datetime | None = None,
        unit_system: str | None = None,
        can_clear_meta: bool = True,
    ) -> None:
        """Store a v3 signal body (used by both polling and webhook paths)."""
        for datapoint in DATAPOINT_CODE_MAP[code]:
            assert code == datapoint.code

            self.data[datapoint.storage_key] = (
                ((self.data.get(datapoint.storage_key) or {}) | body)
                if body is not None
                else None
            )

        self._update_meta(
            DATAPOINT_CODE_MAP[code],
            data_age=data_age,
            fetched_at=fetched_at,
            unit_system=unit_system,
            can_clear=can_clear_meta,
        )

    def from_storage_raw_value(
        self,
        entity_description_key: EntityDescriptionKey,
        value_key_path: str,
        *,
        value: Any,  # noqa: ANN401
        data_age: dt.datetime | None = None,
        fetched_at: dt.datetime | None = None,
        unit_system: str | None = None,
        can_clear_meta: bool = True,
    ) -> None:
        """Store an optimistic/restored value by key path (v3 storage format)."""
        datapoint = DATAPOINT_ENTITY_KEY_MAP[entity_description_key]
        key_path_update(self.data, value_key_path, value)
        self._update_meta(
            (datapoint,),
            data_age=data_age,
            fetched_at=fetched_at,
            unit_system=unit_system,
            can_clear=can_clear_meta,
        )

    def _update_meta(
        self,
        datapoints: tuple[DatapointConfig, ...],
        *,
        data_age: dt.datetime | None,
        fetched_at: dt.datetime | None,
        unit_system: str | None,
        can_clear: bool,
    ) -> None:
        for datapoint in datapoints:
            storage_key = datapoint.storage_key

            if unit_system:
                self.data[f"{storage_key}:unit_system"] = unit_system
            elif can_clear:
                self.data.pop(f"{storage_key}:unit_system", None)

            if data_age:
                self.data[f"{storage_key}:data_age"] = data_age
            elif can_clear:
                self.data.pop(f"{storage_key}:data_age", None)

            if fetched_at:
                self.data[f"{storage_key}:fetched_at"] = fetched_at
            elif can_clear:
                self.data.pop(f"{storage_key}:fetched_at", None)
