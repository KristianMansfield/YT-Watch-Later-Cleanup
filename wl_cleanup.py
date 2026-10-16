#!/usr/bin/env python3
"""Clean up your YouTube Watch Later playlist.

Removes videos from chosen creators and videos longer than a set length.
Before it removes anything, it prints how many videos each creator has in
the playlist.

The official YouTube Data API v3 can't read or change Watch Later (Google
blocked that in 2016). This script uses your logged-in browser session
instead:
  * yt-dlp reads the playlist using your browser's cookies
  * removal goes through YouTube's internal "edit_playlist" endpoint (the
    one the website calls when you click "Remove from Watch later"),
    signed with those same cookies

Usage:
    python wl_cleanup.py                 # report, then ask before removing
    python wl_cleanup.py --dry-run       # report only, never remove
    python wl_cleanup.py --yes           # remove without asking
    python wl_cleanup.py --creator "Some Channel" --max-minutes 45
    python wl_cleanup.py --browser firefox

Options given on the command line are added to those in config.toml.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import tomllib
import urllib.error
import urllib.request
from collections import Counter
from http.cookiejar import CookieJar
from pathlib import Path

try:
    import yt_dlp
except ImportError:
    sys.exit("yt-dlp is not installed. Run: pip install -r requirements.txt")

WL_URL = "https://www.youtube.com/playlist?list=WL"
ORIGIN = "https://www.youtube.com"
EDIT_URL = f"{ORIGIN}/youtubei/v1/browse/edit_playlist?prettyPrint=false"
CLIENT_VERSION = "2.20250101.00.00"
BATCH_SIZE = 25
DEFAULT_CONFIG = Path(__file__).with_name("config.toml")


def load_config(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open("rb") as f:
        return tomllib.load(f)


def fetch_watch_later(browser: str, profile: str | None) -> tuple[list[dict], CookieJar]:
    opts = {
        "extract_flat": "in_playlist",
        "cookiesfrombrowser": (browser, profile, None, None),
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(WL_URL, download=False)
        entries = [e for e in (info.get("entries") or []) if e and e.get("id")]
        return entries, ydl.cookiejar


def creator_name(entry: dict) -> str:
    return entry.get("channel") or entry.get("uploader") or "<unknown>"


def creator_keys(entry: dict) -> set[str]:
    """Every identifier we might match a creator against: name, ID, @handle."""
    keys = set()
    for field in ("channel", "uploader", "channel_id", "uploader_id"):
        value = entry.get(field)
        if value:
            keys.add(value.strip().lower())
            keys.add(value.strip().lower().lstrip("@"))
    return keys


def fmt_duration(seconds: float | None) -> str:
    if seconds is None:
        return "?:??"
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def print_creator_counts(entries: list[dict], targets: set[str]) -> None:
    counts = Counter(creator_name(e) for e in entries)
    print(f"\nWatch Later contains {len(entries)} videos from {len(counts)} creators:\n")
    width = max((len(name) for name in counts), default=10)
    for name, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0].lower())):
        marker = ""
        if targets:
            sample = next(e for e in entries if creator_name(e) == name)
            if creator_keys(sample) & targets:
                marker = "  <- targeted"
        print(f"  {name:<{width}}  {count:>4}{marker}")


def plan_removals(entries: list[dict], targets: set[str], max_seconds: float | None):
    removals = []
    for e in entries:
        reasons = []
        if targets and creator_keys(e) & targets:
            reasons.append("creator")
        duration = e.get("duration")
        if max_seconds is not None and duration is not None and duration > max_seconds:
            reasons.append(f"length {fmt_duration(duration)}")
        if reasons:
            removals.append((e, reasons))
    return removals


def sapisid_auth_header(jar: CookieJar) -> str:
    cookies = {c.name: c.value for c in jar if c.domain.endswith("youtube.com")}
    ts = str(int(time.time()))
    parts = []
    for cookie_name, scheme in (
        ("SAPISID", "SAPISIDHASH"),
        ("__Secure-1PAPISID", "SAPISID1PHASH"),
        ("__Secure-3PAPISID", "SAPISID3PHASH"),
    ):
        value = cookies.get(cookie_name)
        if value:
            digest = hashlib.sha1(f"{ts} {value} {ORIGIN}".encode()).hexdigest()
            parts.append(f"{scheme} {ts}_{digest}")
    if not parts:
        sys.exit("No SAPISID cookie found. Are you logged in to YouTube in that browser?")
    return " ".join(parts)


def remove_videos(video_ids: list[str], jar: CookieJar) -> int:
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    removed = 0
    for i in range(0, len(video_ids), BATCH_SIZE):
        batch = video_ids[i : i + BATCH_SIZE]
        body = {
            "context": {"client": {"clientName": "WEB", "clientVersion": CLIENT_VERSION, "hl": "en"}},
            "playlistId": "WL",
            "actions": [{"action": "ACTION_REMOVE_VIDEO_BY_VIDEO_ID", "removedVideoId": vid} for vid in batch],
        }
        req = urllib.request.Request(
            EDIT_URL,
            data=json.dumps(body).encode(),
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": sapisid_auth_header(jar),
                "Origin": ORIGIN,
                "X-Origin": ORIGIN,
                "X-Goog-AuthUser": "0",
                "X-Youtube-Client-Name": "1",
                "X-Youtube-Client-Version": CLIENT_VERSION,
            },
        )
        try:
            with opener.open(req) as resp:
                result = json.load(resp)
        except urllib.error.HTTPError as err:
            print(f"  Batch {i // BATCH_SIZE + 1} failed: HTTP {err.code} {err.read()[:300]!r}")
            continue
        if result.get("status") == "STATUS_SUCCEEDED":
            removed += len(batch)
            print(f"  Removed {removed}/{len(video_ids)}")
        else:
            print(f"  Batch {i // BATCH_SIZE + 1} not confirmed: {json.dumps(result)[:300]}")
        time.sleep(1)
    return removed


def main() -> None:
    parser = argparse.ArgumentParser(description="Clean up your YouTube Watch Later playlist.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--creator", action="append", default=[], help="creator name, @handle or channel ID (repeatable)")
    parser.add_argument("--max-minutes", type=float, help="remove videos longer than this")
    parser.add_argument("--browser", help="browser to read cookies from (chrome, firefox, safari, brave, edge, ...)")
    parser.add_argument("--profile", help="browser profile name, if not the default")
    parser.add_argument("--dry-run", action="store_true", help="show the report, never remove anything")
    parser.add_argument("--yes", action="store_true", help="remove without asking")
    args = parser.parse_args()

    config = load_config(args.config)
    browser = args.browser or config.get("browser", "chrome")
    profile = args.profile or config.get("profile")
    creators = list(config.get("creators", [])) + args.creator
    targets = {c.strip().lower().lstrip("@") for c in creators} | {c.strip().lower() for c in creators}
    max_minutes = args.max_minutes if args.max_minutes is not None else config.get("max_minutes")
    max_seconds = max_minutes * 60 if max_minutes else None

    print(f"Reading Watch Later using {browser} cookies...")
    entries, jar = fetch_watch_later(browser, profile)
    if not entries:
        sys.exit("Watch Later came back empty. Check you're logged in to YouTube in that browser.")

    print_creator_counts(entries, targets)

    unmatched = [c for c in creators if not any(creator_keys(e) & {c.strip().lower(), c.strip().lower().lstrip("@")} for e in entries)]
    if unmatched:
        print(f"\nNo videos in Watch Later from: {', '.join(unmatched)}")

    removals = plan_removals(entries, targets, max_seconds)
    unknown = sum(1 for e in entries if e.get("duration") is None)
    if max_seconds is not None and unknown:
        print(f"\n{unknown} videos have no known length (live/premieres) and won't be removed for length.")

    if not removals:
        print("\nNothing matches the removal rules.")
        return

    print(f"\n{len(removals)} videos match the removal rules:\n")
    for e, reasons in removals:
        print(f"  [{fmt_duration(e.get('duration')):>8}] {creator_name(e)} - {e.get('title')}  ({', '.join(reasons)})")

    if args.dry_run:
        print("\nDry run: nothing was removed.")
        return
    if not args.yes and input(f"\nRemove these {len(removals)} videos? [y/N] ").strip().lower() != "y":
        print("Cancelled.")
        return

    removed = remove_videos([e["id"] for e, _ in removals], jar)
    print(f"\nDone. Removed {removed} of {len(removals)} videos.")


if __name__ == "__main__":
    main()
