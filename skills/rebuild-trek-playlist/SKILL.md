---
name: rebuild-trek-playlist
description: Rebuild the chronological Star Trek Jellyfin playlist from the live viewing guide — plan and diff first, then rename the old playlist, create the new one, and verify. Use when new episodes have aired, when the playlist order may be stale, or when asked to rebuild/refresh the Star Trek playlist.
---

# Rebuild the chronological Star Trek playlist

Jellyfin cannot reorder a playlist in place, so any order change means creating a new one. This skill never deletes: it renames the old playlist, creates the new one, verifies, and leaves the old one for you to delete after you have checked the result.

The work is done by one stdlib script. Read its output; do not re-implement the matching by hand.

## Safety rules

- Read-only until the approval gate. `plan` is the only phase that runs before approval.
- `rename` and `create` are the only writes. Never call `DELETE` on anything.
- Credentials come from the environment (`JELLYFIN_API_KEY`, or `JELLYFIN_USER` + `JELLYFIN_PASSWORD`). Never echo them, paste them into a command line, or write them to a file. If none is set, stop and ask the user to export one.
- If a step fails, report the error and the state; do not improvise a fix against the server.

## Prerequisites

Two ways in. A rebuild needs the first; the second is enough for `plan`, `decide` and `verify`.

- **User session (required for `rename`):** set `JELLYFIN_USER` (username or id) and `JELLYFIN_PASSWORD`. The script logs in through `/Users/AuthenticateByName`, keeps the token in memory for that run, and never writes or prints it. An API key cannot rename: it has no user id, so Jellyfin answers `POST /Playlists/{id}` with 400.
- **API key:** `JELLYFIN_API_KEY`. Works for every read and for `create`, which takes the user id in the body.

```bash
test -n "$JELLYFIN_PASSWORD" -o -n "$JELLYFIN_API_KEY" || echo "no credential is set"
```

`JELLYFIN_URL` defaults to `http://localhost:8096`.

## Phase 1 — plan (read-only)

```bash
python3 .opencode/skills/rebuild-trek-playlist/scripts/trek_playlist.py plan
```

It fetches the guide, reads the library, matches every position, compares the result with the current playlist, and writes `reports/rebuild-YYYY-MM-DD-HHMM.md`.

Read the verdict:

| Verdict | Meaning | Next |
|---|---|---|
| `IDENTICAL` | Playlist already matches the guide | Stop. Nothing to do. |
| `DIFFERENT` | A rebuild is needed | Show the summary, then gate 1. |
| `NO_PLAYLIST` | No playlist with that name | Show the summary, then gate 1 (skip rename). |
| `BLOCKED` | Missing or ambiguous library items | Resolve the gaps, then re-run `plan`. |

For `BLOCKED`, show the gap table from the report, including the TVDB/IMDb/TMDB links, then either map a gap or exclude it:

```bash
python3 .opencode/skills/rebuild-trek-playlist/scripts/trek_playlist.py decide \
  --key 'SNW|2|10' --item <item-id> --note "why"
python3 .opencode/skills/rebuild-trek-playlist/scripts/trek_playlist.py decide \
  --key 'SFA|1|3' --exclude --note "not in library yet"
```

Decisions persist in `~/.local/state/trek-playlist/decisions.json`. Re-run `plan` after each change. Never proceed to a write while the verdict is `BLOCKED`.

## Gate 1 — approval

Report: verdict, canonical positions, intended entries, exclusions, gaps, current playlist id and count, first divergence, report path. Ask explicitly whether to rebuild. Do not run `rename` or `create` without a clear yes.

## Phase 2 — rename the old playlist

```bash
python3 .opencode/skills/rebuild-trek-playlist/scripts/trek_playlist.py rename
```

It renames `Chronological Star Trek` to `Chronological Star Trek (superseded YYYY-MM-DD HHMM)` and prints the name read back from the server. The playlist and its entries are otherwise untouched.

## Phase 3 — create the new playlist

```bash
python3 .opencode/skills/rebuild-trek-playlist/scripts/trek_playlist.py create
```

It posts the planned item ids under the original name. If it fails, the old playlist is still there under its superseded name: run `rename --restore` to put the original name back, and report.

## Phase 4 — verify

```bash
python3 .opencode/skills/rebuild-trek-playlist/scripts/trek_playlist.py verify
```

It re-reads the new playlist and asserts count, order, uniqueness, Episode/Movie types, and that the old playlist still exists with the same entry count. It appends a verification section to the report and exits non-zero on any failure.

A failure is not a reason to touch the old playlist. Report the first divergent position and stop.

## After a pass

Tell the user: the new playlist is verified, the old one is renamed and intact, and deleting it is their manual step once they have checked it. Do not delete it for them.

## Reference

| Command | Writes? | Purpose |
|---|---|---|
| `plan` | report + local state | Fetch, match, diff, classify |
| `decide` | local decisions file | Map or exclude one position |
| `rename` | playlist name only | Supersede the old playlist |
| `rename --restore` | playlist name only | Undo the rename after a failed create |
| `create` | new playlist | Create from the planned ids |
| `verify` | report | Assert the new playlist |

State: `~/.local/state/trek-playlist/<playlist-slug>.json`, decisions in `decisions.json` beside it. Reports: `reports/rebuild-*.md`.

Offline runs: `--site-file` and `--inventory-file` replace the network calls. Tests: `python3 -m unittest discover -s .opencode/skills/rebuild-trek-playlist/scripts`.
