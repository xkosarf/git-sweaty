import argparse
import os
from datetime import date, timedelta
from typing import Dict, List

from utils import (
    ensure_dir,
    format_distance,
    format_duration,
    format_elevation,
    load_config,
    read_json,
    utc_now,
    write_json,
)

AGG_PATH = os.path.join("data", "daily_aggregates.json")
NORM_PATH = os.path.join("data", "activities_normalized.json")
README_PATH = "README.md"
SITE_DATA_PATH = os.path.join("site", "data.json")

CELL = 12
GAP = 2
PADDING = 16

COLORS = ["#ebedf0", "#c6e48b", "#7bc96f", "#239a3b", "#196127"]


def _year_range(lookback_years: int) -> List[int]:
    current_year = utc_now().year
    start = current_year - lookback_years + 1
    return list(range(start, current_year + 1))


def _monday_on_or_before(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _sunday_on_or_after(d: date) -> date:
    return d + timedelta(days=(6 - d.weekday()))


def _level(count: int, max_count: int) -> int:
    if count <= 0 or max_count <= 0:
        return 0
    if max_count == 1:
        return 1
    ratio = count / max_count
    level = int(ratio * 3) + 1
    if level > 4:
        level = 4
    return level


def _build_title(date_str: str, entry: Dict, units: Dict[str, str]) -> str:
    count = entry.get("count", 0)
    distance = format_distance(entry.get("distance", 0.0), units["distance"])
    duration = format_duration(entry.get("moving_time", 0.0))
    elevation = format_elevation(entry.get("elevation_gain", 0.0), units["elevation"])

    return (
        f"{date_str}\n"
        f"{count} workout{'s' if count != 1 else ''}\n"
        f"Distance: {distance}\n"
        f"Duration: {duration}\n"
        f"Elevation: {elevation}"
    )


def _svg_for_year(activity_type: str, year: int, entries: Dict[str, Dict], id_to_url: Dict[int, str], units: Dict[str, str]) -> str:
    start = _monday_on_or_before(date(year, 1, 1))
    end = _sunday_on_or_after(date(year, 12, 31))

    weeks = ((end - start).days // 7) + 1
    width = weeks * (CELL + GAP) + PADDING * 2
    height = 7 * (CELL + GAP) + PADDING * 2

    max_count = 0
    for entry in entries.values():
        max_count = max(max_count, int(entry.get("count", 0)))

    lines = []
    lines.append('<?xml version="1.0" encoding="UTF-8"?>')
    lines.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">'  # noqa: E501
    )
    lines.append(
        f'<rect width="{width}" height="{height}" fill="white"/>'
    )
    lines.append(f'<g transform="translate({PADDING},{PADDING})">')

    current = start
    while current <= end:
        week_index = (current - start).days // 7
        row = current.weekday()  # Monday=0
        x = week_index * (CELL + GAP)
        y = row * (CELL + GAP)

        in_year = current.year == year
        date_str = current.isoformat()

        if in_year:
            entry = entries.get(date_str, {
                "count": 0,
                "distance": 0.0,
                "moving_time": 0.0,
                "elevation_gain": 0.0,
                "activity_ids": [],
            })
            count = int(entry.get("count", 0))
            level = _level(count, max_count)
            color = COLORS[level]
            title = _build_title(date_str, entry, units)
            activity_ids = entry.get("activity_ids", [])
            link = None
            if count == 1 and activity_ids:
                link = id_to_url.get(activity_ids[0])
        else:
            color = "#ffffff"
            title = None
            link = None

        rect = (
            f'<rect x="{x}" y="{y}" width="{CELL}" height="{CELL}" '
            f'fill="{color}" stroke="#ffffff" stroke-width="1"/>'
        )

        if title:
            rect = rect[:-2] + f' data-date="{date_str}"><title>{title}</title></rect>'

        if link:
            rect = f'<a href="{link}" target="_blank">{rect}</a>'

        lines.append(rect)
        current += timedelta(days=1)

    lines.append("</g>")
    lines.append("</svg>")
    return "\n".join(lines) + "\n"


def _readme_section(types: List[str], years_desc: List[int]) -> str:
    lines = []
    lines.append("## Heatmaps")
    lines.append("")
    for activity_type in types:
        lines.append(f"### {activity_type}")
        lines.append("")
        for year in years_desc:
            path = f"heatmaps/{activity_type}/{year}.svg"
            lines.append(f"![{activity_type} {year}]({path})")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _update_readme(types: List[str], years_desc: List[int]) -> None:
    if not os.path.exists(README_PATH):
        return
    with open(README_PATH, "r", encoding="utf-8") as f:
        content = f.read()

    start_tag = "<!-- HEATMAPS:START -->"
    end_tag = "<!-- HEATMAPS:END -->"
    section = _readme_section(types, years_desc)

    if start_tag in content and end_tag in content:
        before, rest = content.split(start_tag, 1)
        _, after = rest.split(end_tag, 1)
        new_content = before + start_tag + "\n" + section + end_tag + after
    else:
        new_content = content.rstrip() + "\n\n" + start_tag + "\n" + section + end_tag + "\n"

    with open(README_PATH, "w", encoding="utf-8") as f:
        f.write(new_content)


def _write_site_data(payload: Dict) -> None:
    ensure_dir("site")
    write_json(SITE_DATA_PATH, payload)


def generate():
    config = load_config()
    lookback_years = int(config.get("sync", {}).get("lookback_years", 5))
    types = config.get("activities", {}).get("types", []) or []

    units = config.get("units", {})
    units = {
        "distance": units.get("distance", "mi"),
        "elevation": units.get("elevation", "ft"),
    }

    aggregates = read_json(AGG_PATH)
    years = _year_range(lookback_years)

    normalized = read_json(NORM_PATH) if os.path.exists(NORM_PATH) else []
    id_to_url = {int(item["id"]): item.get("strava_url") for item in normalized}

    for activity_type in types:
        type_dir = os.path.join("heatmaps", activity_type)
        ensure_dir(type_dir)
        for year in years:
            year_entries = (
                aggregates.get("years", {})
                .get(str(year), {})
                .get(activity_type, {})
            )
            svg = _svg_for_year(activity_type, year, year_entries, id_to_url, units)
            path = os.path.join(type_dir, f"{year}.svg")
            with open(path, "w", encoding="utf-8") as f:
                f.write(svg)

    years_desc = list(reversed(years))
    _update_readme(types, years_desc)

    site_payload = {
        "generated_at": utc_now().isoformat(),
        "years": years,
        "types": types,
        "aggregates": aggregates.get("years", {}),
        "units": units,
    }
    _write_site_data(site_payload)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate SVG heatmaps and README section")
    args = parser.parse_args()
    generate()
    print("Generated heatmaps and README section")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
