# Chronological Star Trek for Jellyfin

An [OpenCode](https://opencode.ai) skill that builds and maintains a Jellyfin playlist of Star Trek in the
order of [The Ultimate Chronological Star Trek Viewing Guide](https://www.startrekviewingguide.com/).

It fetches the guide, matches every position against your library, reports anything it cannot resolve, and
only then rebuilds the playlist — renaming the old one instead of deleting it.

Built because Star Trek is still airing and Jellyfin cannot reorder a playlist in place. Every new season
means a new playlist, and doing that by hand is how playlists drift.

## How it works

```
plan ──► report + gate ──► rename old ──► create new ──► verify
(read-only)   (you decide)                  (all items)   (read-only)
```

- **`plan`** fetches the guide, reads your library, matches all 977 positions, compares the result with the
  playlist you have, and writes a report. It changes nothing.
- **The gate** is yours: the skill shows the verdict, the counts, the gaps and the first divergence, and
  waits for a clear yes.
- **`rename`** moves the current playlist to `Chronological Star Trek (superseded YYYY-MM-DD HHMM)`. The
  playlist and its entries are untouched.
- **`create`** posts the planned item ids under the original name.
- **`verify`** reads the new playlist back and asserts count, order, uniqueness, item types, and that the
  old playlist is still intact.

Nothing is ever deleted. When verification passes, deleting the superseded playlist is your manual step.

## Requirements

- Python 3.8+ (standard library only — no packages to install)
- A reachable Jellyfin server. Tested against Jellyfin **12.2.0**.
- A library that already contains the episodes and films, named closely enough to be matched (see
  [Matching](#matching)).
- An administrator account for the first run. `rename` needs a user session; see [Credentials](#credentials).

## Install

Clone the repo and link the skill into your OpenCode project:

```bash
git clone https://github.com/7hr08ik/jellyfin-trek-playlist.git ~/src/jellyfin-trek-playlist
cd /path/to/your/project
mkdir -p .opencode/skills .opencode/commands
ln -s ~/src/jellyfin-trek-playlist/skills/rebuild-trek-playlist .opencode/skills/rebuild-trek-playlist
cp ~/src/jellyfin-trek-playlist/commands/rebuild-trek-playlist.md .opencode/commands/
```

Then either run `/rebuild-trek-playlist` in OpenCode, or ask the agent to "rebuild the Star Trek playlist".
The command loads the skill and starts with `plan`.

You can also run the script directly, without OpenCode, from a checkout:

```bash
python3 ~/src/jellyfin-trek-playlist/skills/rebuild-trek-playlist/scripts/trek_playlist.py plan
```

## Credentials

Everything comes from the environment. Nothing is written to disk, echoed, or logged.

| Variable | Use |
|---|---|
| `JELLYFIN_URL` | Base URL, default `http://localhost:8096` |
| `JELLYFIN_API_KEY` | API key. Enough for `plan`, `create`, `verify`; `decide` needs none |
| `JELLYFIN_USER` | Username or 32-character user id |
| `JELLYFIN_PASSWORD` | Password for that user; required for `rename` |
| `TREK_STATE_DIR` | Where decisions and plan state live; defaults beside the skill |

**Why two kinds of credential?** Jellyfin resolves the calling user from the token when renaming a playlist.
An API key authenticates as an administrator but carries no user id, so `POST /Playlists/{id}` returns
`400` (`Guid can't be empty` in the server log). The same reason `GET /Playlists/{id}` and `GET /Users/Me`
return `400` to an API key. With `JELLYFIN_USER` + `JELLYFIN_PASSWORD` the script logs in through
`/Users/AuthenticateByName`, uses the returned session for the run, and keeps it in memory.

`create` works with either credential because the user id travels in the request body.

## Usage

### 1. Plan

```bash
export JELLYFIN_URL="http://jellyfin:8096"
export JELLYFIN_USER="your-admin"
export JELLYFIN_PASSWORD="..."          # or JELLYFIN_API_KEY for a read-only check
TREK=".opencode/skills/rebuild-trek-playlist/scripts/trek_playlist.py"

python3 "$TREK" plan
```

The verdict is one of:

| Verdict | Meaning | What to do |
|---|---|---|
| `IDENTICAL` | The playlist already matches the guide | Nothing |
| `DIFFERENT` | A rebuild is needed | Review the report, then approve |
| `NO_PLAYLIST` | No playlist with that name | Review, then approve (rename is skipped) |
| `BLOCKED` | Some positions are missing or ambiguous | Resolve them, then run `plan` again |

A plan run prints:

```
verdict: DIFFERENT
positions: 977  playlist entries: 970  excluded: 0  gaps: 0
playlist: Chronological Star Trek (…), 970 entries
report: reports/rebuild-2026-11-05-2206.md
```

### 2. Resolve gaps

`BLOCKED` means at least one canonical position has no confident match. The report lists each one with the
canonical title, season and episode, the guide link, the series' TVDB link and an IMDb search link.

Map a position to an item id, or exclude it:

```bash
# map: SNW S2E10 to this library item
python3 "$TREK" decide --key 'SNW|2|10' --item <item-id> --note "1080p copy"

# exclude: not in the library yet
python3 "$TREK" decide --key 'SFA|1|3' --exclude --note "airs next week"
```

Keys are `CODE|SEASON|EPISODE`, or `Film|<title>` for films. Decisions persist in
`<state-dir>/decisions.json` and are re-applied on every later run, so a judgment is made
once. Re-run `plan` after each decision.

### 3. Rebuild

Only after you approve:

```bash
python3 "$TREK" rename     # old → "… (superseded 2026-11-05 2206)"
python3 "$TREK" create     # new playlist under the original name
python3 "$TREK" verify     # exit 0 on success
```

If `create` fails, the old playlist is still there under its superseded name; `rename --restore` puts the
original name back.

### 4. Check, then delete the old playlist yourself

`verify` prints one line per check:

```
verify: PASS
  count       PASS
  order       PASS
  unique      PASS
  types       PASS
  named       PASS
  old_intact  PASS
```

Open the new playlist, scrub a few positions, then delete the superseded one. The skill will not do it for
you.

## Configuration

| Flag | Default | Purpose |
|---|---|---|
| `--url` | `JELLYFIN_URL` or `http://localhost:8096` | Server base URL |
| `--playlist` | `Chronological Star Trek` (`TREK_PLAYLIST`) | Playlist name |
| `--user` | `JELLYFIN_USER` | Username or user id |
| `--password` | `JELLYFIN_PASSWORD` | Prefer the environment variable |
| `--state-dir` | `TREK_STATE_DIR`, else the skill's `refs/`, else the platform directory | State and decisions |
| `--report-dir` | `./reports` | Where reports are written |
| `--site-file` | — | Parse a saved copy of the guide instead of fetching it |
| `--inventory-file` | — | Read a saved inventory instead of calling the API |
| `--guide-url` | the viewing guide | Override the source |
| `rename --force` | — | Rehearse a rename even when the plan is `IDENTICAL` |

Decisions and plan state live beside the skill, in `refs/`. `TREK_STATE_DIR` overrides that; `--state-dir` overrides
both; a read-only install falls back to `$XDG_STATE_HOME/trek-playlist` (`%LOCALAPPDATA%` on Windows,
`~/Library/Application Support` on macOS). The first run copies an existing `~/.local/state/trek-playlist` across and
prints what it copied.

## Matching

Each canonical position is matched in this order, and the first non-empty tier wins:

1. exact library title;
2. normalized title — case, punctuation, accents, `&`/`and`, roman numerals, leading `the`;
3. part-aware — `Unification I` finds `Unification (1)`, and a missing part 2 falls back to the base title;
4. combined files — `Flesh and Blood (1) / Flesh and Blood (2)` counts as one entry for a two-part position;
5. unique prefix;
6. fuzzy — a unique candidate scoring at least 0.80 in the same season and episode, or 0.80 overall.

Anything else is `AMBIGUOUS`, with its candidates and scores in the report. Films are matched exactly, then
normalized, then uniquely at 0.90.

Two structural rules keep the count honest:

- a `units=2` position becomes two entries when the library stores the parts separately (e.g. *Broken Bow*);
- consecutive positions resolving to the same file collapse into one entry, because the guide repeats some
  titles (Relativity, Past Tense, In the Cards).

The current guide yields **977 positions**. Two rules then set the entry count: a `units=2` position can
become two entries, and repeated positions collapse into one. The report states the resulting count.

## The report

`reports/rebuild-YYYY-MM-DD-HHMM.md` contains the verdict, server facts, the full mapping table
(position, code, season, episode, canonical title, library title, item id, status, tier, confidence, notes), every
gap with links, the diff against the current playlist, the exact API calls a rebuild would make, and the
verification results.

Reports describe your library, so `reports/` is gitignored.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `POST /Playlists/{id} -> 400` on `rename` | API key has no user id | Set `JELLYFIN_USER` + `JELLYFIN_PASSWORD` |
| `login failed … HTTP 400` | Legacy `X-Emby-Authorization` header rejected by 12.2 | Already handled; the script sends `Authorization` |
| `GET /Playlists/{id} -> 400` | Same missing-user problem | Not used; the script reads `/Playlists/{id}/Items` |
| Films never match | Jellyfin hides BoxSet children from library listings | The script searches `searchTerm=Star Trek` instead |
| A title is `AMBIGUOUS` | Two library items share it | `decide --item <id>` |
| Seasons named "Series 1" | Cosmetic naming | No effect; matching uses `ParentIndexNumber` |
| `BLOCKED` with a whole series missing | Not in the library | `decide --exclude`, or add the files and re-plan |
| `series read returned 400 rows` | A library read filled its page limit | Raise the limit in the script; a full page may hide more rows |

## Limitations

- Star Trek only. Series codes come from the guide; an unknown code stops the run with an error rather than
  dropping rows.
- Title matching is heuristic. The report states every non-exact match and its confidence; nothing is
  guessed silently.
- The playlist name is exact. Other playlists — including an audio playlist called `Star Trek` — are ignored.
- Decisions are per server. A `decisions.json` from one library means nothing in another.
- The guide is a living page. If its markup changes, `plan` fails loudly (`fewer than 500 positions parsed`)
  instead of producing a short playlist.

## Credits

- The viewing order and canonical titles come from
  [The Ultimate Chronological Star Trek Viewing Guide](https://www.startrekviewingguide.com/) by
  John Kenneth Fisher.
- This project began as [wkelton/jellytrek](https://github.com/wkelton/jellytrek), a Python CLI that built the
  same playlist and has since been superseded by this skill.
- Star Trek and all related marks are trademarks of Paramount Skydance. This project ships no media and is
  not affiliated with Paramount or the Jellyfin project.

## Licence

MIT. See [LICENSE](LICENSE) — use it, change it, ship it, keep the copyright notice.
