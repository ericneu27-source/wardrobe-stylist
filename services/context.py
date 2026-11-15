import os
import requests

def fetch_today_weather() -> dict:
    # Uses the coordinates you set in .env
    lat = float(os.getenv("LATITUDE", "43.6532"))
    lon = float(os.getenv("LONGITUDE", "-79.3832"))

    resp = requests.get(
        "https://api.open-meteo.com/v1/forecast",
        params={
            "latitude": lat,
            "longitude": lon,
            "hourly": "temperature_2m,apparent_temperature,precipitation_probability,wind_gusts_10m",
            "forecast_days": 1,
            "timezone": "auto",
        },
        timeout=10,
    )
    resp.raise_for_status()
    hourly = resp.json()["hourly"]

    def summarize_window(start_hr: int, end_hr: int) -> dict:
        feels = hourly["apparent_temperature"][start_hr:end_hr]
        precip = hourly["precipitation_probability"][start_hr:end_hr]
        gusts = hourly["wind_gusts_10m"][start_hr:end_hr]
        return {
            "feels_like_c": f"{min(feels):.0f}°C to {max(feels):.0f}°C",
            "max_precip_prob_pct": max(precip),
            "max_wind_gust_kmh": round(max(gusts)),
        }

    # Grouping the day into chunks helps the AI understand temperature swings
    return {
        "morning_8am_12pm": summarize_window(8, 12),
        "afternoon_12pm_5pm": summarize_window(12, 17),
        "evening_5pm_10pm": summarize_window(17, 22),
    }