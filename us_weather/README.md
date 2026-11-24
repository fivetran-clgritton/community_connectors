---
name: US Weather (National Weather Service)
description: syncs active weather alerts and an hourly forecast from the public weather.gov API
category: SaaS & APIs
---

# US Weather connector

## Overview

this connector reads the public weather.gov API. It writes one row per active alert for a chosen state or area, and one row per forecast hour for one latitude and longitude point.

## Requirements

- the Fivetran Connector SDK and the `requests` library
- network access to https://api.weather.gov

## Configuration

| Key | Meaning |
| --- | --- |
| `user_agent` | an identifying User-Agent string sent with every request, as the service asks |
| `area` | a US state or area code such as a two-letter state code, used to filter active alerts |
| `latitude` | latitude of the forecast point, as text |
| `longitude` | longitude of the forecast point, as text |
| `max_forecast_hours` | how many forecast hours to write (default 48, maximum 156) |

The first sync is small: one alert list and at most 48 forecast hours by default. To widen it, raise `max_forecast_hours` or choose a busier `area`.

## Authentication

no account or key is needed. Only the identifying User-Agent string is required.

## Data handling

- alerts are a snapshot of what is active now; each alert is upserted by its id
- the forecast is upserted by point and start time; the connector remembers the forecast generation time in state and skips the forecast when it has not changed
- nested objects are flattened; lists and irregular objects are stored as JSON text
- state is checkpointed after each table

## Error handling

every request has a 30 second timeout. Responses with status 429 or 5xx are retried up to four times with exponential backoff. Other error statuses raise an error and fail the sync.

## Tables

### alert

Primary key: `id`.

Columns: `id`, `area_desc`, `sent`, `effective`, `onset`, `expires`, `ends`, `status`, `message_type`, `category`, `severity`, `certainty`, `urgency`, `event`, `sender`, `sender_name`, `headline`, `description`, `instruction`, `response`, `geocode` (JSON text), `affected_zones` (JSON text), `references` (JSON text), `parameters` (JSON text).

### hourly_forecast

Primary key: `forecast_point`, `start_time`.

Columns: `forecast_point`, `start_time`, `end_time`, `grid_id`, `grid_x`, `grid_y`, `period_number`, `is_daytime`, `temperature`, `temperature_unit`, `precipitation_probability_percent`, `dewpoint_c`, `relative_humidity_percent`, `wind_speed`, `wind_direction`, `short_forecast`, `detailed_forecast`, `icon`.
