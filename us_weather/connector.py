# Built using Connector SDK AI Plugin
# Plugin: fivetran-connector-sdk (datavore-claude-agent) | Version: 0.0.0
"""Connector that syncs active alerts and the hourly forecast from the US weather service."""

import json
import time
from datetime import datetime, timezone

import requests

from fivetran_connector_sdk import Connector
from fivetran_connector_sdk import Logging as log
from fivetran_connector_sdk import Operations as op

__BASE_URL = "https://api.weather.gov"
__TIMEOUT_SECONDS = 30  # Maximum wait for one HTTP call
__MAX_RETRIES = 4  # Maximum number of attempts per request
__RETRYABLE_STATUS = (429, 500, 502, 503, 504)  # Rate limit and server errors
__DEFAULT_FORECAST_HOURS = 48  # Forecast hours written when the setting is not given
__MAX_FORECAST_HOURS = 156  # The API returns at most this many hourly periods
__ALERT_TABLE = "alert"
__FORECAST_TABLE = "hourly_forecast"


def validate_configuration(configuration: dict):
    """Check that the required configuration values are present and usable.

    Args:
        configuration: The connector configuration values.

    Raises:
        ValueError: If a required value is missing or a number cannot be read.
    """
    for key in ("user_agent", "area", "latitude", "longitude"):
        if not configuration.get(key):
            raise ValueError(f"missing required configuration value: {key}")
    try:
        float(configuration["latitude"])
        float(configuration["longitude"])
        read_forecast_hours(configuration)
    except ValueError as error:
        raise ValueError("latitude, longitude and max_forecast_hours must be numbers") from error


def schema(configuration: dict):
    """Declare the tables and their primary keys.

    Args:
        configuration: The connector configuration values.

    Returns:
        The list of table definitions for Fivetran.
    """
    return [
        {"table": __ALERT_TABLE, "primary_key": ["id"]},
        {"table": __FORECAST_TABLE, "primary_key": ["forecast_point", "start_time"]},
    ]


def read_forecast_hours(configuration: dict):
    """Read how many forecast hours to write.

    Args:
        configuration: The connector configuration values.

    Returns:
        The number of hours, between 1 and the API maximum.
    """
    value = int(configuration.get("max_forecast_hours") or __DEFAULT_FORECAST_HOURS)
    return max(1, min(value, __MAX_FORECAST_HOURS))


def fetch_json(configuration: dict, url: str, params=None):
    """Fetch one JSON document, retrying 429 and 5xx responses with exponential backoff.

    Args:
        configuration: The connector configuration values.
        url: The full address to request.
        params: Optional query string parameters.

    Returns:
        The decoded JSON body.

    Raises:
        RuntimeError: If the API keeps failing after all retries.
    """
    headers = {
        "User-Agent": configuration["user_agent"],
        "Accept": "application/geo+json",
    }
    for attempt in range(__MAX_RETRIES):
        response = requests.get(url, headers=headers, params=params, timeout=__TIMEOUT_SECONDS)
        if response.status_code in __RETRYABLE_STATUS:
            log.warning(f"received status {response.status_code}, retrying")
            time.sleep(2**attempt)
            continue
        response.raise_for_status()
        return response.json()
    raise RuntimeError("the weather service kept failing after retries")


def to_json_text(value):
    """Turn a list or object into JSON text, keeping empty values empty.

    Args:
        value: A list, dict or None.

    Returns:
        JSON text, or None when there is no value.
    """
    if value is None:
        return None
    return json.dumps(value)


def build_alert_row(properties: dict):
    """Flatten one alert into a table row.

    Args:
        properties: The properties object of one alert feature.

    Returns:
        A dict of column values.
    """
    row = {
        key: properties.get(key)
        for key in (
            "id",
            "area_desc",
            "sent",
            "effective",
            "onset",
            "expires",
            "ends",
            "status",
            "message_type",
            "category",
            "severity",
            "certainty",
            "urgency",
            "event",
            "sender",
            "sender_name",
            "headline",
            "description",
            "instruction",
            "response",
        )
    }
    row["area_desc"] = properties.get("areaDesc")
    row["message_type"] = properties.get("messageType")
    row["sender_name"] = properties.get("senderName")
    row["geocode"] = to_json_text(properties.get("geocode"))
    row["affected_zones"] = to_json_text(properties.get("affectedZones"))
    row["references"] = to_json_text(properties.get("references"))
    row["parameters"] = to_json_text(properties.get("parameters"))
    return row


def sync_alerts(configuration: dict, state: dict):
    """Write every active alert for the configured area.

    Args:
        configuration: The connector configuration values.
        state: The saved state, updated with the time of this alert sync.
    """
    url = f"{__BASE_URL}/alerts/active"
    body = fetch_json(configuration, url, {"area": configuration["area"]})
    features = body.get("features") or []
    for feature in features:
        op.upsert(table=__ALERT_TABLE, data=build_alert_row(feature.get("properties") or {}))
    state["alerts_synced_at"] = datetime.now(timezone.utc).isoformat()
    op.checkpoint(state)
    log.info(f"synced {len(features)} active alerts")


def read_measure(period: dict, key: str):
    """Read the value of a measurement object such as dewpoint.

    Args:
        period: One forecast period.
        key: The name of the measurement object.

    Returns:
        The numeric value, or None when it is missing.
    """
    return (period.get(key) or {}).get("value")


def build_forecast_row(period: dict, point: str, grid: dict):
    """Flatten one forecast hour into a table row.

    Args:
        period: One hourly forecast period.
        point: The configured latitude and longitude as text.
        grid: The grid details of the point.

    Returns:
        A dict of column values.
    """
    return {
        "forecast_point": point,
        "start_time": period.get("startTime"),
        "end_time": period.get("endTime"),
        "grid_id": grid.get("gridId"),
        "grid_x": grid.get("gridX"),
        "grid_y": grid.get("gridY"),
        "period_number": period.get("number"),
        "is_daytime": period.get("isDaytime"),
        "temperature": period.get("temperature"),
        "temperature_unit": period.get("temperatureUnit"),
        "precipitation_probability_percent": read_measure(period, "probabilityOfPrecipitation"),
        "dewpoint_c": read_measure(period, "dewpoint"),
        "relative_humidity_percent": read_measure(period, "relativeHumidity"),
        "wind_speed": period.get("windSpeed"),
        "wind_direction": period.get("windDirection"),
        "short_forecast": period.get("shortForecast"),
        "detailed_forecast": period.get("detailedForecast"),
        "icon": period.get("icon"),
    }


def sync_forecast(configuration: dict, state: dict):
    """Write the hourly forecast for the configured point.

    Args:
        configuration: The connector configuration values.
        state: The saved state, updated with the forecast generation time.

    Raises:
        RuntimeError: If the forecast URL cannot be read for the configured point.
    """
    point = f"{float(configuration['latitude']):.4f},{float(configuration['longitude']):.4f}"
    grid = fetch_json(configuration, f"{__BASE_URL}/points/{point}").get("properties") or {}
    forecast_url = grid.get("forecastHourly")
    if not forecast_url:
        raise RuntimeError("could not read the forecast URL for the configured point")
    properties = fetch_json(configuration, forecast_url).get("properties") or {}
    generated_at = properties.get("generatedAt")
    if generated_at and generated_at == state.get("forecast_generated_at"):
        log.info("skipped the forecast because it has not changed")
        return
    periods = (properties.get("periods") or [])[: read_forecast_hours(configuration)]
    for period in periods:
        op.upsert(table=__FORECAST_TABLE, data=build_forecast_row(period, point, grid))
    state["forecast_generated_at"] = generated_at
    op.checkpoint(state)
    log.info(f"synced {len(periods)} forecast hours")


def update(configuration: dict, state: dict):
    """Sync active alerts and the hourly forecast, checkpointing after each table.

    Args:
        configuration: The connector configuration values.
        state: The saved state from the previous sync.
    """
    validate_configuration(configuration=configuration)
    sync_alerts(configuration, state)
    sync_forecast(configuration, state)


connector = Connector(update=update, schema=schema)
