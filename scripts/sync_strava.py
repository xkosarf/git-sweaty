import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import requests

from utils import ensure_dir, load_config, read_json, utc_now, write_json

TOKEN_CACHE = ".strava_token.json"
RAW_DIR = os.path.join("activities", "raw")
SUMMARY_JSON = os.path.join("data", "last_sync_summary.json")
SUMMARY_TXT = os.path.join("data", "last_sync_summary.txt")


class RateLimitExceeded(RuntimeError):
    pass


class RateLimiter:
    def __init__(
        self,
        overall_15_limit: int,
        overall_day_limit: int,
        read_15_limit: int,
        read_day_limit: int,
        safety_buffer: int,
        min_interval_seconds: float,
    ) -> None:
        self.overall_15_limit = overall_15_limit
        self.overall_day_limit = overall_day_limit
        self.read_15_limit = read_15_limit
        self.read_day_limit = read_day_limit
        self.safety_buffer = max(0, safety_buffer)
        self.min_interval_seconds = max(0.0, min_interval_seconds)

        self.window_start = time.time()
        self.day_start = datetime.now(timezone.utc).date()

        self.overall_15 = 0
        self.overall_day = 0
        self.read_15 = 0
        self.read_day = 0
        self.last_request_at = 0.0

    def _reset_if_needed(self) -> None:
        now = time.time()
        if now - self.window_start >= 900:
            self.window_start = now
            self.overall_15 = 0
            self.read_15 = 0

        current_day = datetime.now(timezone.utc).date()
        if current_day != self.day_start:
            self.day_start = current_day
            self.overall_day = 0
            self.read_day = 0

    def _sleep_until_window_reset(self) -> None:
        now = time.time()
        remaining = 900 - (now - self.window_start)
        if remaining > 0:
            time.sleep(remaining)
        self._reset_if_needed()

    def before_request(self, kind: str) -> None:
        self._reset_if_needed()

        if self.min_interval_seconds > 0 and self.last_request_at:
            elapsed = time.time() - self.last_request_at
            if elapsed < self.min_interval_seconds:
                time.sleep(self.min_interval_seconds - elapsed)
                self._reset_if_needed()

        if self.overall_15 >= self.overall_15_limit - self.safety_buffer:
            self._sleep_until_window_reset()

        if kind == "read" and self.read_15 >= self.read_15_limit - self.safety_buffer:
            self._sleep_until_window_reset()

        if self.overall_day >= self.overall_day_limit - self.safety_buffer:
            raise RateLimitExceeded("Overall daily limit reached; try again after UTC midnight.")

        if kind == "read" and self.read_day >= self.read_day_limit - self.safety_buffer:
            raise RateLimitExceeded("Read daily limit reached; try again after UTC midnight.")

    def record_request(self, kind: str) -> None:
        self._reset_if_needed()
        self.overall_15 += 1
        self.overall_day += 1
        if kind == "read":
            self.read_15 += 1
            self.read_day += 1
        self.last_request_at = time.time()

    def apply_headers(self, headers: Dict[str, str]) -> None:
        def _parse_pair(value: Optional[str]) -> Optional[Tuple[int, int]]:
            if not value:
                return None
            parts = [p.strip() for p in value.split(",")]
            if len(parts) < 2:
                return None
            try:
                return int(parts[0]), int(parts[1])
            except ValueError:
                return None

        overall_limit = _parse_pair(headers.get("X-RateLimit-Limit"))
        overall_usage = _parse_pair(headers.get("X-RateLimit-Usage"))
        if overall_limit and overall_usage:
            limit_15, limit_day = overall_limit
            usage_15, usage_day = overall_usage
            self.overall_15_limit = limit_15
            self.overall_day_limit = limit_day
            self.overall_15 = max(self.overall_15, usage_15)
            self.overall_day = max(self.overall_day, usage_day)

        read_limit = _parse_pair(headers.get("X-ReadRateLimit-Limit"))
        read_usage = _parse_pair(headers.get("X-ReadRateLimit-Usage"))
        if read_limit and read_usage:
            limit_15, limit_day = read_limit
            usage_15, usage_day = read_usage
            self.read_15_limit = limit_15
            self.read_day_limit = limit_day
            self.read_15 = max(self.read_15, usage_15)
            self.read_day = max(self.read_day, usage_day)


def _load_token_cache() -> Dict:
    if not os.path.exists(TOKEN_CACHE):
        return {}
    try:
        return read_json(TOKEN_CACHE)
    except Exception:
        return {}


def _save_token_cache(payload: Dict) -> None:
    write_json(TOKEN_CACHE, payload)


def _get_access_token(config: Dict, limiter: Optional[RateLimiter]) -> str:
    strava = config.get("strava", {})
    client_id = strava.get("client_id")
    client_secret = strava.get("client_secret")
    refresh_token = strava.get("refresh_token")
    if not client_id or not client_secret or not refresh_token:
        raise ValueError("Missing Strava credentials in config.yaml/config.local.yaml")

    cache = _load_token_cache()
    now = int(utc_now().timestamp())
    access_token = cache.get("access_token")
    expires_at = cache.get("expires_at", 0)

    if access_token and expires_at - 60 > now:
        return access_token

    if limiter:
        limiter.before_request("overall")
    resp = requests.post(
        "https://www.strava.com/oauth/token",
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        },
        timeout=30,
    )
    if limiter:
        limiter.record_request("overall")
        limiter.apply_headers(resp.headers)
    resp.raise_for_status()
    payload = resp.json()
    _save_token_cache(payload)
    return payload["access_token"]


def _lookback_after_ts(years: int) -> int:
    now = datetime.now(timezone.utc)
    try:
        start = now.replace(year=now.year - years)
    except ValueError:
        # handle Feb 29
        start = now.replace(month=2, day=28, year=now.year - years)
    return int(start.timestamp())


def _fetch_page(
    token: str,
    per_page: int,
    page: int,
    after: int,
    limiter: Optional[RateLimiter],
) -> List[Dict]:
    if limiter:
        limiter.before_request("read")
    resp = requests.get(
        "https://www.strava.com/api/v3/athlete/activities",
        headers={"Authorization": f"Bearer {token}"},
        params={"per_page": per_page, "page": page, "after": after},
        timeout=30,
    )
    if limiter:
        limiter.record_request("read")
        limiter.apply_headers(resp.headers)
    resp.raise_for_status()
    return resp.json()


def _write_activity(activity: Dict) -> bool:
    activity_id = activity.get("id")
    if not activity_id:
        return False
    path = os.path.join(RAW_DIR, f"{activity_id}.json")
    if os.path.exists(path):
        try:
            existing = read_json(path)
            if existing == activity:
                return False
        except Exception:
            pass
    write_json(path, activity)
    return True


def sync_strava(dry_run: bool, prune_deleted: bool) -> Dict:
    config = load_config()
    rate_cfg = config.get("rate_limits", {}) or {}
    limiter = RateLimiter(
        overall_15_limit=int(rate_cfg.get("overall_15_min", 200)),
        overall_day_limit=int(rate_cfg.get("overall_daily", 2000)),
        read_15_limit=int(rate_cfg.get("read_15_min", 100)),
        read_day_limit=int(rate_cfg.get("read_daily", 1000)),
        safety_buffer=int(rate_cfg.get("safety_buffer", 2)),
        min_interval_seconds=float(rate_cfg.get("min_interval_seconds", 10)),
    )
    token = _get_access_token(config, limiter)
    ensure_dir(RAW_DIR)

    per_page = int(config.get("sync", {}).get("per_page", 200))
    lookback_years = int(config.get("sync", {}).get("lookback_years", 5))
    after = _lookback_after_ts(lookback_years)

    page = 1
    total = 0
    new_or_updated = 0
    fetched_ids = set()

    rate_limited = False
    rate_limit_message = ""

    while True:
        try:
            activities = _fetch_page(token, per_page, page, after, limiter)
        except RateLimitExceeded as exc:
            rate_limited = True
            rate_limit_message = str(exc)
            break
        if not activities:
            break
        for activity in activities:
            total += 1
            activity_id = activity.get("id")
            if activity_id:
                fetched_ids.add(str(activity_id))
            if dry_run:
                continue
            if _write_activity(activity):
                new_or_updated += 1
        page += 1

    deleted = 0
    if prune_deleted and not dry_run:
        for filename in os.listdir(RAW_DIR):
            if not filename.endswith(".json"):
                continue
            activity_id = filename[:-5]
            if activity_id not in fetched_ids:
                os.remove(os.path.join(RAW_DIR, filename))
                deleted += 1

    summary = {
        "fetched": total,
        "new_or_updated": new_or_updated,
        "deleted": deleted,
        "lookback_years": lookback_years,
        "timestamp_utc": utc_now().isoformat(),
        "rate_limited": rate_limited,
    }
    if rate_limited:
        summary["rate_limit_message"] = rate_limit_message
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync Strava activities")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--prune-deleted",
        action="store_true",
        help="Remove local raw activities not returned by Strava",
    )
    args = parser.parse_args()

    config = load_config()
    prune_deleted = args.prune_deleted or bool(
        config.get("sync", {}).get("prune_deleted", False)
    )

    summary = sync_strava(args.dry_run, prune_deleted)

    ensure_dir("data")
    if not args.dry_run:
        write_json(SUMMARY_JSON, summary)
        message = (
            f"Sync Strava: {summary['new_or_updated']} new/updated, "
            f"{summary['deleted']} deleted (lookback {summary['lookback_years']}y)"
        )
        if summary.get("rate_limited"):
            message += " [rate limited]"
        with open(SUMMARY_TXT, "w", encoding="utf-8") as f:
            f.write(message + "\n")

    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise
