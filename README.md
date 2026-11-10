# Chronological Star Trek for Jellyfin

Builds and maintains a Jellyfin playlist of Star Trek in the order of
[The Ultimate Chronological Star Trek Viewing Guide](https://startrekviewingguide.com/).

A small Python 3 script (standard library only) does the work: it fetches the guide, matches every position
against your library, proposes a match for anything it is unsure about, diffs the result against the playlist
you have, and only then rebuilds it. Rebuilding means renaming the old playlist and creating a new one — the
script never deletes anything.

It ships as an agent skill, so in OpenCode, Claude Code or any other coding harness you can ask for it in
plain language ("rebuild the Star Trek playlist") and let the agent run the phases, ask you the questions and
respect the approval gate. You can also run the script yourself, with no agent at all.

Built because Star Trek is still airing and Jellyfin cannot reorder a playlist in place. Every new season
means a new playlist, and doing that by hand is how playlists drift.

## How it works

```
plan ──► review + gate ──► rename old ──► create new ──► verify
(read-only)  (you decide)                   (all items)   (read-only)
```

- **`plan`** fetches the guide, reads your library, matches every position, compares the result with the
  playlist you have, and writes a report and a working copy. It changes nothing on the server.
- **Review** puts every gap to you as a proposal — the likely library item, its score, and the runners-up —
  and records your answer, so the next run does not ask again.
- **The gate** is yours: the agent reports the verdict, the counts, the gaps and the first divergence, and
  waits for a clear yes.
- **`rename`** moves the current playlist to `Chronological Star Trek (superseded YYYY-MM-DD HHMM)`.
- **`create`** posts the planned item ids under the original name.
- **`verify`** reads the new playlist back and checks count, order, uniqueness, item types, and that the
  superseded playlist is still intact.

Nothing is ever deleted. After a passing verification, deleting the superseded playlist is your manual step.

## Requirements

- Python 3.8+ (standard library only — nothing to install).
- A reachable Jellyfin server. Tested against Jellyfin **12.2.0**.
- A library that already contains the episodes and films, named closely enough to be matched (see
  [Matching](#matching)).
- Credentials: an API key, or a user name and password. `rename` requires the second form; see
  [Credentials](#credentials).

## Install

### Any harness, or none

Clone the repository once:

```bash
git clone https://github.com/7hr08ik/jellyfin-playlist-chronological-star-trek.git ~/src/jellyfin-trek-playlist
```

The script is the whole tool, and it runs on its own:

```bash
python3 ~/src/jellyfin-trek-playlist/skills/rebuild-trek-playlist/scripts/trek_playlist.py plan
```

Then jump to [Credentials](#credentials) and [Usage](#usage).

### OpenCode

Link the skill and copy the command into your project:

```bash
cd /path/to/your/project
mkdir -p .opencode/skills .opencode/commands
ln -s ~/src/jellyfin-trek-playlist/skills/rebuild-trek-playlist .opencode/skills/rebuild-trek-playlist
cp ~/src/jellyfin-trek-playlist/commands/rebuild-trek-playlist.md .opencode/commands/
```

Run `/rebuild-trek-playlist`, or ask the agent to "rebuild the Star Trek playlist". The command loads
`SKILL.md` and starts at `plan`.

### Claude Code

Claude Code reads agent skills from a skill directory, so link it the same way:

```bash
cd /path/to/your/project
mkdir -p .claude/skills
ln -s ~/src/jellyfin-trek-playlist/skills/rebuild-trek-playlist .claude/skills/rebuild-trek-playlist
```

Then ask for a rebuild. There is no command file to copy: describe the task and the skill's `description`
takes over.

### Other agents (Codex, Cursor, Copilot, Gemini CLI, Aider, ...)

These harnesses have no skill directory, but they all read a project instruction file. Add one line to the
file yours uses — `AGENTS.md` for most, `CLAUDE.md`, `GEMINI.md` or `.cursor/rules/` for the rest:

```markdown
To rebuild the Jellyfin Star Trek playlist, follow `~/src/jellyfin-trek-playlist/skills/rebuild-trek-playlist/SKILL.md`.
```

The skill is plain Markdown and the commands are plain shell, so nothing else changes. If a harness cannot
link a symlink, copy the folder instead: `cp -R ~/src/jellyfin-trek-playlist/skills/rebuild-trek-playlist
.claude/skills/`. On Windows, use a directory junction (`mklink /J`) or a copy, and replace `python3` with
`py`.

## Credentials

Everything comes from the environment. The script never writes a credential to disk, echoes it, or logs it.

| Variable | Use |
|---|---|
| `JELLYFIN_URL` | Base URL. Default `http://localhost:8096` |
| `JELLYFIN_API_KEY` | API key. Enough for `plan`, `create` and `verify`, and for `decide` (which needs no credential at all) |
| `JELLYFIN_USER` | User name or 32-character user id |
| `JELLYFIN_PASSWORD` | Password for that user. Required for `rename` |
| `TREK_STATE_DIR` | Where decisions and plan state live. Default: the skill's own `refs/` folder |

Export them for the shell you are working in:

```bash
export JELLYFIN_URL="http://jellyfin:8096"
export JELLYFIN_API_KEY="..."        # read-only check, create, verify
```

or, for a single rebuild, supply a password on that one command:

```bash
JELLYFIN_USER="rob" JELLYFIN_PASSWORD="..." \
  python3 ~/src/jellyfin-trek-playlist/skills/rebuild-trek-playlist/scripts/trek_playlist.py rename
```

Keep credentials out of files: no `.env` in the repository, no password typed as `--password` (it lands in
your shell history and the process list).

### The skill asks you for them

When you run it through an agent, you do not need to prepare anything in advance. `SKILL.md` opens with a
credential checklist, so before the first server read the agent:

- checks `JELLYFIN_API_KEY` and `JELLYFIN_USER` + `JELLYFIN_PASSWORD`;
- if neither is set, tells you which variables exist, asks which mode you want, and waits for the answer;
- passes a password you give it inline on that single command, rather than exporting it for the session;
- never repeats the value back in the conversation or the report.

Run the script directly and there is no one to ask, so it stops with a plain message instead:

```
JELLYFIN_API_KEY is not set (or set JELLYFIN_USER and JELLYFIN_PASSWORD)
```

### Why two kinds of credential?

Jellyfin resolves the calling user from the token when renaming a playlist. An API key authenticates as an
administrator but carries no user id, so `POST /Playlists/{id}` answers `400` (`Guid can't be empty` in the
server log). The same applies to `GET /Playlists/{id}` and `GET /Users/Me`. With `JELLYFIN_USER` +
`JELLYFIN_PASSWORD` the script logs in through `/Users/AuthenticateByName`, uses the returned session for
that run, and keeps it in memory.

`create` works with either kind, because the user id travels in the request body. So an API key is enough to
plan, create and verify — only the rename needs a session.

## The `refs/` folder

Your answers and in-flight plan live beside the skill, in `skills/rebuild-trek-playlist/refs/`:

| File | Purpose |
|---|---|
| `refs/decisions.json` | Every gap you answered — mapped item or exclusion — keyed by position. Re-applied on every plan, so the same question is never asked twice. Safe to back up, copy to another machine, or edit by hand. |
| `refs/<playlist-slug>.json` | The last plan: verdict, intended ids, the live playlist it saw, the recorded guide order, and the report path. Written by `plan`, updated by `rename` and `create`. |
| `refs/decisions.example.json` | The empty template that ships with the repository. The live files are gitignored. |

Where the folder is, in order of precedence:

1. `--state-dir <path>` on the command line;
2. `TREK_STATE_DIR` in the environment;
3. the skill's own `refs/` directory, found from the script's real path, so a symlinked install shares one
   folder across every project that links it;
4. a per-user directory, when the install is read-only: `$XDG_STATE_HOME/trek-playlist` (usually
   `~/.local/state/trek-playlist`), `%LOCALAPPDATA%\trek-playlist` on Windows,
   `~/Library/Application Support/trek-playlist` on macOS.

Two consequences worth knowing:

- **A copied install keeps its own `refs/`.** Copy the folder into two harnesses and each asks the same
  questions once. Set `TREK_STATE_DIR` to one path to share the answers instead.
- **Moving from an older version is automatic.** On the first run the script copies
  `~/.local/state/trek-playlist` into the resolved folder, decisions and plan state together, and prints
  what it copied. Nothing is removed from the old location.

`decide` writes here and touches nothing else, so it works with no credential and no network.

## Usage

Set a shortcut for the rest of this section:

```bash
TREK="python3 ~/src/jellyfin-trek-playlist/skills/rebuild-trek-playlist/scripts/trek_playlist.py"
```

### 1. Plan

```bash
$TREK plan
```

The verdict is one of:

| Verdict | Meaning | What to do |
|---|---|---|
| `IDENTICAL` | The playlist already matches the guide | Nothing |
| `DIFFERENT` | A rebuild is needed | Review, then approve |
| `NO_PLAYLIST` | No playlist with that name | Review, then approve (rename is skipped) |
| `BLOCKED` | Some positions are missing or ambiguous | Answer the review queue, then plan again |

```
state: /home/you/src/jellyfin-trek-playlist/skills/rebuild-trek-playlist/refs
verdict: DIFFERENT
positions: 977  playlist entries: 970  excluded: 0  gaps: 0
playlist: Chronological Star Trek (1f0c…), 970 entries
changes: 5 added, 2 removed, 0 positions changed; first divergence 412
working copy: reports/playlist.local.md
report: reports/rebuild-2026-11-05-2206.md
```

`plan` also writes the **working copy**, `reports/playlist.local.md`: the mapping table in canonical order
with a `Review` column. It is overwritten on every run and is easier to read than the report while you work
through gaps.

### 2. Review the gaps

Each gap is printed with a proposal: the best candidate, its series, season, episode, score and id, plus up
to two runners-up, and the exact command that records the answer.

```
GAP 812 SNW S2E10 Hegemony [MISSING] low confidence
  proposal: 'Hegemony' S2E10 fuzzy 0.74 id=9b6b…
  proposal: 'Hegemony (2)' S2E10 fuzzy 0.71 id=…
  decide --key 'SNW|2|10' --item 9b6b…
  or exclude: decide --key 'SNW|2|10' --exclude --note 'why'
```

Three answers, all through `decide`:

```bash
# accept the proposal
$TREK decide --key 'SNW|2|10' --item <proposed id> --note "accepted proposal"

# correct it with a different item
$TREK decide --key 'SNW|2|10' --item <other id> --note "the 1080p copy"

# exclude it: not in the library yet
$TREK decide --key 'SFA|1|3' --exclude --note "airs next week"
```

Keys are `CODE|SEASON|EPISODE`, or `Film|<title>` for films. Answers persist in `refs/decisions.json` and are
applied before every automatic tier, so a decision is made once. When the queue is empty, run `plan` again —
the rebuild is approved on that run's verdict, not the `BLOCKED` one you started from.

For scripted review, `plan --review-json` prints the same queue as JSON on stdout and writes nothing extra.

### 3. Rebuild

Only after you approve:

```bash
$TREK rename     # old → "… (superseded 2026-11-05 2206)"
$TREK create     # new playlist under the original name
$TREK verify     # exit 0 on success
```

A `NO_PLAYLIST` plan skips `rename` and goes straight to `create`. If `create` fails, the old playlist is
still there under its superseded name; `rename --restore` puts the original name back. `rename --force`
rehearses the two-step path on an `IDENTICAL` plan without changing the order.

### 4. Check, then delete the old playlist yourself

`verify` prints one line per check, then an observation about the guide:

```
verify: PASS
  count       PASS
  order       PASS
  unique      PASS
  types       PASS
  named       PASS
  old_intact  PASS
  guide check: the guide still lists the recorded order
```

Open the new playlist, scrub a few positions, then delete the superseded one. The script will not do it for
you. If the guide has changed since the plan ran, the last line says how many positions moved and which; that
is news, not a failure — it does not change the exit status, and the next plan picks it up.

## Configuration

| Flag | Default | Purpose |
|---|---|---|
| `--url` | `JELLYFIN_URL` or `http://localhost:8096` | Server base URL |
| `--playlist` | `Chronological Star Trek` (`TREK_PLAYLIST`) | Playlist name; exact and unique |
| `--user` | `JELLYFIN_USER` | User name or user id |
| `--password` | `JELLYFIN_PASSWORD` | Prefer the environment variable |
| `--state-dir` | resolved as described under [`refs/`](#the-refs-folder) | Decisions and plan state |
| `--report-dir` | `./reports` (current directory) | Reports and the working copy |
| `--site-file` | — | Parse a saved copy of the guide instead of fetching it |
| `--inventory-file` | — | Read a saved inventory instead of calling the API |
| `--guide-url` | the viewing guide | Point at a mirror |
| `plan --review-json` | — | Print the review queue as JSON on stdout |
| `rename --restore` | — | Undo the rename after a failed create |
| `rename --force` | — | Rehearse the rename on an `IDENTICAL` plan |

## Matching

Each canonical position is matched in this order, and the first non-empty tier wins:

1. a saved `decide` answer;
2. film tiers — exact, normalized, then a unique 0.90 candidate;
3. a `parts N and M` combined title;
4. exact library title;
5. normalized title — case, punctuation, accents, `&`/`and`, roman numerals, leading `the`;
6. part-aware — `Unification I` finds `Unification (1)`, and a missing part 2 falls back to the base title;
7. combined files — `Flesh and Blood (1) / Flesh and Blood (2)` counts as one entry for a two-part position;
8. unique prefix;
9. fuzzy — a unique candidate at least 0.80 in the same season and episode, or 0.80 overall.

Films need a unique candidate at 0.90, strictly above every other. Anything without a single clear answer is
`AMBIGUOUS`, with its candidates and scores; anything with no candidate at or above 0.55 is `MISSING`, with
search links. Nothing is guessed silently, and every non-exact match appears in the report with its tier and
confidence.

Proposals come from that same matcher — a gap reuses the candidates the tier already produced, capped at the
best three — so a suggestion can never contradict the order the rules decide.

Two structural rules keep the count honest:

- a `units=2` position becomes two entries when the library stores the parts separately (e.g. *Broken Bow*);
- consecutive positions resolving to the same file collapse into one entry, because the guide repeats some
  titles (*Relativity*, *Past Tense*, *In the Cards*).

The guide currently yields 977 positions; the two rules then set the entry count, and the report states the
result.

## The report

`reports/rebuild-YYYY-MM-DD-HHMM.md` holds the verdict, server facts, the full mapping table (position, code,
season, episode, canonical title, library title, item id, status, tier, confidence, notes), every gap with its
links and proposal, the diff against the current playlist, the exact API calls a rebuild would make, and the
verification results with the guide observation.

Reports and the working copy describe your library, so `reports/` is gitignored.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `JELLYFIN_API_KEY is not set …` | No credential | Export one, or let the skill ask for it |
| `POST /Playlists/{id} -> 400` on `rename` | An API key has no user id | Set `JELLYFIN_USER` + `JELLYFIN_PASSWORD` |
| `login failed … HTTP 400` | Legacy `X-Emby-Authorization` header rejected by 12.2 | Already handled; the script sends `Authorization` |
| `GET /Playlists/{id} -> 400` | Same missing-user problem | Not used; the script reads `/Playlists/{id}/Items` |
| Films never match | Jellyfin hides BoxSet children from library listings | The script searches `searchTerm=Star Trek` instead |
| A title is `AMBIGUOUS` | Two library items share the title | `decide --item <id>` |
| Seasons named "Series 1" | Cosmetic naming | No effect; matching uses `ParentIndexNumber` |
| `BLOCKED` with a whole series missing | Not in the library | `decide --exclude`, or add the files and plan again |
| `rename not valid at phase created` | The create already succeeded | Repair the new playlist; `--restore` is refused after a create |
| `series read returned 400 rows` | A library read filled its page limit | Raise the limit in the script; a full page may hide more rows |
| Questions asked again in another checkout | Each install has its own `refs/` | Set `TREK_STATE_DIR` to one shared path |

## Limitations

- Star Trek only. Series codes come from the guide; an unknown code stops the run with an error rather than
  dropping rows.
- Title matching is heuristic. The report states every non-exact match with its tier and confidence.
- The playlist name is exact and must be unique. Other playlists — including an audio playlist called
  `Star Trek` — are ignored.
- Decisions are per server and carry no server identity. A `decisions.json` from one library means nothing in
  another; delete it or point `--state-dir` elsewhere before rebuilding a second server.
- The guide is a living page. If its markup changes, `plan` fails loudly (fewer than 500 positions parsed)
  instead of producing a short playlist.
- The guide observation in `verify` is reported, not enforced: a guide that moved on after the plan does not
  fail the run.
- Nothing leaves your machine except the requests to your Jellyfin server and the read of the guide.

## Credits

- The viewing order and canonical titles come from
  [The Ultimate Chronological Star Trek Viewing Guide](https://startrekviewingguide.com/) by John Kenneth
  Fisher.
- This project began as [wkelton/jellytrek](https://github.com/wkelton/jellytrek), a Python CLI that built the
  same playlist and has since been superseded by this skill.
- Star Trek and all related marks are trademarks of Paramount Skydance. This project ships no media and is
  not affiliated with Paramount or the Jellyfin project.

## Licence

MIT. See [LICENSE](LICENSE) — use it, change it, ship it, keep the copyright notice.
