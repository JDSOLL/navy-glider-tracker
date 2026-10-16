"""Fetch IOOS Glider DAC ERDDAP data and publish an interactive recovery map."""
from __future__ import annotations

import html
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import folium
import pandas as pd
import requests
from folium.plugins import Fullscreen
from geopy.distance import geodesic
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ----- USER SETTINGS -----
CONFIG_FILE = Path(__file__).resolve().parent / "config.json"
with CONFIG_FILE.open("r", encoding="utf-8") as f:
    config = json.load(f)
RECOVERY_LAT = float(config["recovery_lat"])
RECOVERY_LON = float(config["recovery_lon"])
RECOVERY_RADIUS_KM = float(config["recovery_radius_km"])
if not (-90 <= RECOVERY_LAT <= 90 and -180 <= RECOVERY_LON <= 180 and RECOVERY_RADIUS_KM > 0):
    raise ValueError("Invalid recovery location or radius in config.json")

# Deployment-start dates are inclusive. No end time: new data appear automatically.
GLIDERS = {
    "ng289": {"dataset": "ng289-20260817T0000", "start": "2026-08-17T16:13:35Z", "color": "#d94343"},
    "ng1050": {"dataset": "ng1050-20260713T0000", "start": "2026-07-17T18:05:32Z", "color": "#267ad2"},
    "ng1239": {"dataset": "ng1239-20260713T0000", "start": "2026-07-17T09:56:08Z", "color": "#189a70"},
}
ERDDAP_BASE = "https://gliders.ioos.us/erddap/tabledap"
OUTPUT_FILE = Path("docs/index.html")
# Set to True to have open browser tabs reload every 30 minutes.
BROWSER_AUTO_RELOAD = True

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")


def session_with_retries() -> requests.Session:
    session = requests.Session()
    retry = Retry(total=3, backoff_factor=2, status_forcelist=[429, 500, 502, 503, 504])
    session.mount("https://", HTTPAdapter(max_retries=retry))
    session.headers.update({"User-Agent": "UGA-glider-recovery-map/1.0"})
    return session


def download_track(session: requests.Session, dataset: str, start: str) -> pd.DataFrame:
    # ERDDAP tabledap supports URL-encoded variable requests and constraints.
    # Using .csvp omits the units row, though .csv is also handled below.
    url = f"{ERDDAP_BASE}/{dataset}.csv"
    params = {
        "time,latitude,longitude": "",
        f"time>={start}": "",
    }
    # Build URL manually so commas and comparator are interpreted correctly.
    from urllib.parse import quote
    erddap_url = f"{url}?time,latitude,longitude&time%3E={quote(start, safe=':-')}"
    logging.info("Requesting %s", erddap_url)
    response = session.get(erddap_url, timeout=120)
    response.raise_for_status()
    from io import StringIO
    # csv has 1 header row, then units row; skip the units row.
    df = pd.read_csv(StringIO(response.text), skiprows=[1], low_memory=False)
    missing = {"time", "latitude", "longitude"} - set(df.columns)
    if missing:
        raise ValueError(f"Dataset {dataset}: missing columns: {sorted(missing)}")
    df["time"] = pd.to_datetime(df["time"], utc=True, errors="coerce")
    df["latitude"] = pd.to_numeric(df["latitude"], errors="coerce")
    df["longitude"] = pd.to_numeric(df["longitude"], errors="coerce")
    df = df.dropna(subset=["time", "latitude", "longitude"])
    df = df[df.latitude.between(-90, 90) & df.longitude.between(-180, 180)]
    df = df.drop_duplicates(subset=["time", "latitude", "longitude"]).sort_values("time")
    if df.empty:
        raise ValueError(f"No valid positions in {dataset}")
    return df


def label(value: str) -> str:
    return html.escape(str(value))


def make_map() -> None:
    now = datetime.now(timezone.utc)
    timestamp = now.strftime("%Y-%m-%d %H:%M UTC")
    recovery = (RECOVERY_LAT, RECOVERY_LON)

    m = folium.Map(location=[31.2, -79.2], zoom_start=6, tiles=None, control_scale=True)
    folium.TileLayer(
        tiles="https://basemap.nationalmap.gov/arcgis/rest/services/USGSTopo/MapServer/tile/{z}/{y}/{x}",
        attr="USGS The National Map",
        name="USGS Topographic Map",
        overlay=False,
        control=True,
        max_zoom=16,
    ).add_to(m)
    Fullscreen(position="topleft").add_to(m)
    bounds = [[RECOVERY_LAT, RECOVERY_LON], [32.0809, -81.0912], [30.3, -81.5]]  # Savannah + N. Florida
    folium.Circle(
        location=recovery, radius=RECOVERY_RADIUS_KM * 1000,
        color="#d99300", weight=2, fill=True, fill_opacity=0.1,
        tooltip=f"Recovery radius: {RECOVERY_RADIUS_KM:g} km",
    ).add_to(m)
    folium.Marker(
        recovery, tooltip="Proposed recovery point",
        popup=f"<b>Recovery point</b><br>{RECOVERY_LAT:.6f}, {RECOVERY_LON:.6f}<br>Radius: {RECOVERY_RADIUS_KM:g} km",
        icon=folium.Icon(color="orange", icon="star"),
    ).add_to(m)
    # Savannah point and label. OSM also shows coastal geography naturally.
    folium.CircleMarker(
        [32.0809, -81.0912], radius=4, color="#222", fill=True, fill_color="#222",
        tooltip="Savannah, GA", popup="Savannah, Georgia",
    ).add_to(m)

    summary = []
    successes = 0
    with session_with_retries() as session:
        for name, settings in GLIDERS.items():
            color = settings["color"]
            try:
                df = download_track(session, settings["dataset"], settings["start"])
                latest = df.iloc[-1]
                lat, lon = float(latest.latitude), float(latest.longitude)
                obs_time = latest.time.to_pydatetime()
                obs_string = obs_time.strftime("%Y-%m-%d %H:%M UTC")
                age_hours = max(0, (now - obs_time).total_seconds() / 3600)
                distance = geodesic((lat, lon), recovery).km
                group = folium.FeatureGroup(name=f"{name} track + latest position", show=True)
                track = df[["latitude", "longitude"]].values.tolist()
                folium.PolyLine(track, color=color, weight=3, opacity=0.85,
                                tooltip=f"{name} track ({len(df):,} positions)").add_to(group)
                popup = (
                    f"<b>{label(name)}</b><br>Latest observation: {obs_string}<br>"
                    f"Latitude: {lat:.6f}<br>Longitude: {lon:.6f}<br>"
                    f"Distance to recovery: <b>{distance:.1f} km</b><br>"
                    f"Observation age: {age_hours:.1f} hours"
                )
                folium.CircleMarker([lat, lon], radius=8, color="white", weight=2,
                                    fill=True, fill_color=color, fill_opacity=1,
                                    tooltip=f"{name}: {distance:.1f} km to recovery",
                                    popup=folium.Popup(popup, max_width=320)).add_to(group)
                group.add_to(m)
                bounds.extend(track)
                stale = " <span style='color:#b45309'>(over 6 h old)</span>" if age_hours > 6 else ""
                summary.append(
                    f"<div class='glider-row'><span style='color:{color};font-size:18px'>●</span> "
                    f"<b>{label(name)}</b>{stale}<br>"
                    f"<span>{lat:.5f}, {lon:.5f}</span><br>"
                    f"<b>{distance:.1f} km</b> from recovery<br>"
                    f"<span class='minor'>Position: {obs_string}</span></div>"
                )
                successes += 1
                logging.info("%s: latest %s, (%.5f, %.5f), %.1f km", name, obs_string, lat, lon, distance)
            except Exception as exc:
                logging.exception("Could not retrieve %s", name)
                summary.append(
                    f"<div class='glider-row'><span style='color:{color}'>●</span> "
                    f"<b>{label(name)}</b><br><span style='color:#a33'>Data unavailable this update</span></div>"
                )
    folium.LayerControl(collapsed=False).add_to(m)
    m.fit_bounds(bounds, padding=(20, 20))
    legend = f"""
    <div style="position:fixed;bottom:22px;left:10px;z-index:9999;background:white;
        max-width:250px;max-height:65vh;overflow:auto;padding:12px 14px;
        border:1px solid #aaa;border-radius:8px;box-shadow:0 2px 8px #7777;
        font:12px/1.45 Arial,sans-serif;color:#222">
      <div style="font-size:15px;font-weight:700;margin-bottom:8px">Navy Glider Recovery</div>
      {''.join(summary)}
      <div style="border-top:1px solid #ddd;padding-top:8px;margin-top:5px">
        <b style="color:#b37800">★</b> Recovery: {RECOVERY_LAT:.5f}, {RECOVERY_LON:.5f}<br>
        Circle radius: {RECOVERY_RADIUS_KM:g} km<br>
        <span class="minor">Map refreshed: {timestamp}<br>Positions include ERDDAP interpolated locations.</span>
      </div>
    </div>
    <style>.glider-row{{margin-bottom:9px}}.minor{{font-size:11px;color:#555}}</style>
    """
    m.get_root().html.add_child(folium.Element(legend))
    if BROWSER_AUTO_RELOAD:
        m.get_root().header.add_child(folium.Element('<meta http-equiv="refresh" content="1800">'))
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    m.save(str(OUTPUT_FILE))
    logging.info("Saved %s (%d/3 gliders loaded)", OUTPUT_FILE, successes)
    if successes == 0:
        raise RuntimeError("No gliders could be downloaded; refusing to publish empty map")


if __name__ == "__main__":
    make_map()
