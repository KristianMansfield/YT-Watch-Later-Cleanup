# YouTube Watch Later Cleanup

A Python script that cleans up your YouTube Watch Later playlist. It removes:

- every video from creators you choose
- videos longer than a length you set

Before removing anything, it shows how many videos each creator has in the playlist and lists exactly what it will remove. Then it asks you to confirm.

## Why not the YouTube Data API?

The official YouTube Data API v3 can't read or change Watch Later. Google blocked that in 2016. So this script uses your logged-in browser session instead:

- [yt-dlp](https://github.com/yt-dlp/yt-dlp) reads the playlist using your browser's cookies.
- Videos are removed through YouTube's internal `edit_playlist` endpoint, which the website calls when you click "Remove from Watch later". Requests are signed with the same cookies.

That endpoint isn't documented, so YouTube could change it without warning.

## Setup

Requires Python 3.11 or later.

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp config.template.toml config.toml
```

Then edit `config.toml`:

| Setting       | Meaning                                                                                             |
|---------------|-----------------------------------------------------------------------------------------------------|
| `browser`     | Browser you're logged in to YouTube with: `chrome`, `firefox`, `safari`, `brave`, `edge`, and so on |
| `profile`     | Browser profile name. Only needed if you don't use the default profile                              |
| `creators`    | Creators to remove, by channel name, `@handle` or channel ID. Case doesn't matter                   |
| `max_minutes` | Remove videos longer than this. Comment it out to turn length filtering off                         |

## Usage

```bash
python3 wl_cleanup.py --dry-run   # show the report, remove nothing
python3 wl_cleanup.py             # show the report, then ask before removing
python3 wl_cleanup.py --yes       # remove without asking
```

Anything you pass on the command line is added to what's in `config.toml`:

```bash
python3 wl_cleanup.py --creator "Some Channel" --creator @somehandle --max-minutes 45
python3 wl_cleanup.py --browser firefox --profile "Profile 1"
python3 wl_cleanup.py --config other-config.toml
```

## Notes

- Videos with no known length, such as live streams and premieres, are never removed for length.
- Videos are removed 25 at a time, with a one-second pause between groups.
- On macOS, Chrome asks for Keychain access the first time cookies are read. Safari needs Full Disk Access granted to your terminal app.
- If a removal fails, the script prints YouTube's response and carries on with the next group.
