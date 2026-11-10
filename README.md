# Chronological Star Trek for Jellyfin

An agent skill that builds and maintains a Jellyfin playlist of Star Trek in the order of
[The Ultimate Chronological Star Trek Viewing Guide](https://startrekviewingguide.com/).

Ask for a rebuild in plain language and the agent does the work: it fetches the guide, matches every position
against your library, puts anything it is unsure about to you as a proposal, then renames the old playlist and
creates the new one. It never deletes anything.

Built because Star Trek is still airing and Jellyfin cannot reorder a playlist in place. Every new season means
a new playlist, and doing that by hand is how playlists drift.

## What a rebuild does

```
plan ──► review + gate ──► rename old ──► create new ──► verify
(read-only)  (you decide)                   (all items)   (read-only)
```

- **Plan** reads the guide and your library and compares the result with the playlist you have. Nothing on the
  server changes.
- **Review** shows each gap with a proposed library item, its score and the runners-up. You accept, correct or
  exclude it, and the answer is saved for next time.
- **The gate** is yours: the agent shows the verdict, the counts, the gaps and the first divergence, and waits
  for a clear yes.
- **Rebuild** renames the old playlist to `Chronological Star Trek (superseded YYYY-MM-DD HHMM)` and creates the
  new one under the original name.
- **Verify** checks count, order, uniqueness, item types, and that the superseded playlist is still intact.
  Deleting the superseded playlist afterwards is your manual step.

## Requirements

- Python 3.8+ (standard library only — nothing to install) and a coding harness that can run shell commands.
- A reachable Jellyfin server. Tested against Jellyfin **12.2.0**.
- A library that already contains the episodes and films, named closely enough to be matched.
- Credentials: an API key, or a user name and password. The rename needs the second form.

## Install

Clone the repository once, somewhere outside your project:

```bash
git clone https://github.com/7hr08ik/jellyfin-playlist-chronological-star-trek.git ~/src/jellyfin-trek-playlist
```

### OpenCode

```bash
cd /path/to/your/project
mkdir -p .opencode/skills .opencode/commands
ln -s ~/src/jellyfin-trek-playlist/skills/rebuild-trek-playlist .opencode/skills/rebuild-trek-playlist
cp ~/src/jellyfin-trek-playlist/commands/rebuild-trek-playlist.md .opencode/commands/
```

### Claude Code

```bash
cd /path/to/your/project
mkdir -p .claude/skills
ln -s ~/src/jellyfin-trek-playlist/skills/rebuild-trek-playlist .claude/skills/rebuild-trek-playlist
```

### Other agents (Codex, Cursor, Copilot, Gemini CLI, Aider, ...)

These harnesses read a project instruction file rather than a skill directory. Add one line to the file yours
uses — `AGENTS.md` for most, `CLAUDE.md`, `GEMINI.md` or `.cursor/rules/` for the rest:

```markdown
To rebuild the Jellyfin Star Trek playlist, follow `~/src/jellyfin-trek-playlist/skills/rebuild-trek-playlist/SKILL.md`.
```

If your harness will not follow a symlink, copy the folder instead of linking it. On Windows use a directory
junction (`mklink /J`) or a copy, and `py` in place of `python3`.

## Credentials

Set these in the environment of the harness you run the skill from. Nothing is written to disk, echoed or
logged.

| Variable | Use |
|---|---|
| `JELLYFIN_URL` | Base URL. Default `http://localhost:8096` |
| `JELLYFIN_API_KEY` | API key. Enough for planning, creating and verifying |
| `JELLYFIN_USER` | User name or 32-character user id |
| `JELLYFIN_PASSWORD` | Password for that user. Required for the rename |
| `TREK_STATE_DIR` | Where your answers and plan state live. Default: the skill's own `refs/` folder |

You do not have to prepare anything before the first run. The skill opens with a credential checklist: if
neither mode is set it names the variables, asks which mode you want, and waits for the answer. A password you
give it is used for that one command and never repeated back.

**Why two kinds of credential?** Jellyfin resolves the calling user from the token when renaming a playlist. An
API key authenticates as an administrator but carries no user id, so the rename is answered `400`. With
`JELLYFIN_USER` + `JELLYFIN_PASSWORD` the script logs in through `/Users/AuthenticateByName` and keeps the token
in memory for that run. An API key covers planning, creating and verifying, because the user id travels in the
request body.

## Run it

- **OpenCode:** run `/rebuild-trek-playlist`, or ask the agent to "rebuild the Star Trek playlist".
- **Claude Code and other agents:** ask for a rebuild; the skill's description is what selects it.

The agent plans first and reports one of four verdicts:

| Verdict | Meaning | What you do |
|---|---|---|
| `IDENTICAL` | The playlist already matches the guide | Nothing |
| `DIFFERENT` | A rebuild is needed | Review the gaps, then approve |
| `NO_PLAYLIST` | No playlist with that name | Review, then approve (no rename) |
| `BLOCKED` | Some positions are missing or ambiguous | Answer the review queue, then let it plan again |

For each gap the agent shows the proposal and asks what to do: accept it, name a different library item, or
exclude the position. Answer in the conversation — you do not type commands. Once the queue is empty the agent
plans again and asks for approval before renaming and creating anything.

## Where your answers live

Everything the skill learns about your library is saved beside the skill, in
`skills/rebuild-trek-playlist/refs/`:

- `decisions.json` — every gap you answered, keyed by position. Re-applied before every automatic match, so the
  same question is never asked twice. Back it up or copy it to another machine freely.
- `<playlist-slug>.json` — the last plan: verdict, intended items, the live playlist it saw, the guide order it
  used, and the report path.
- `decisions.example.json` — the empty template that ships with the repository; the live files are gitignored.

The folder is found in this order: `--state-dir`, then `TREK_STATE_DIR`, then the skill's own `refs/`, then a
per-user directory when the install is read-only (`~/.local/state/trek-playlist`, `%LOCALAPPDATA%` on Windows,
`~/Library/Application Support` on macOS). A symlinked install shares one folder across projects; a copied
install keeps its own, so set `TREK_STATE_DIR` if you want several harnesses to share the answers. An existing
`~/.local/state/trek-playlist` is copied in on the first run.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `JELLYFIN_API_KEY is not set …` | No credential | Let the skill ask, or export one |
| `POST /Playlists/{id} -> 400` on rename | An API key has no user id | Give a user name and password |
| Films never match | Jellyfin hides BoxSet children from library listings | Handled; the script searches `searchTerm=Star Trek` |
| A title is ambiguous | Two library items share it | Pick the right one during the review |
| `BLOCKED` with a whole series missing | Not in the library | Exclude those positions, or add the files and plan again |
| Questions asked again in another checkout | Each install has its own `refs/` | Set `TREK_STATE_DIR` to one shared path |

## Script reference

The skill runs `skills/rebuild-trek-playlist/scripts/trek_playlist.py`; it is also usable directly, though the
agent adds the review and the gate around it.

| Command | Writes | Purpose |
|---|---|---|
| `plan` | report, working copy, state | Fetch, match, diff, classify |
| `decide` | `decisions.json` | Accept, correct or exclude one position |
| `rename` | playlist name | Supersede the live playlist |
| `create` | new playlist | Create it from the planned items |
| `verify` | report | Assert the new playlist, read-only on the server |

Global options: `--url`, `--playlist`, `--user`, `--password` (prefer the environment variable), `--state-dir`,
`--report-dir` (default `./reports`), `--site-file` and `--inventory-file` for offline runs, `--guide-url`.

Per command: `plan --review-json` prints the queue as JSON; `rename --restore` puts the original name back after
a failed create; `rename --force` rehearses the rename on an `IDENTICAL` plan.

Lists of positions are keyed `CODE|SEASON|EPISODE`, or `Film|<title>`. The plan writes
`reports/rebuild-YYYY-MM-DD-HHMM.md` (the full mapping table, every gap, the diff and the verification results)
and `reports/playlist.local.md` (the mapping table with a review column, overwritten each run). Both are
gitignored because they describe your library.

## Limitations

- Star Trek only. An unknown series code stops the run instead of dropping rows.
- Matching is heuristic. Every non-exact match is reported with its tier and confidence; nothing is guessed
  silently.
- The playlist name is exact and must be unique. Other playlists, including an audio playlist called `Star
  Trek`, are ignored.
- Answers are per server and carry no server identity; point `--state-dir` elsewhere before rebuilding a second
  server.
- The guide is a living page. If its markup changes, the plan fails loudly instead of producing a short
  playlist.

## Credits

- The viewing order and canonical titles come from
  [The Ultimate Chronological Star Trek Viewing Guide](https://startrekviewingguide.com/) by John Kenneth
  Fisher.
- This project began as [wkelton/jellytrek](https://github.com/wkelton/jellytrek), a Python CLI that built the
  same playlist and has since been superseded by this skill.
- Star Trek and all related marks are trademarks of Paramount Skydance. This project ships no media and is not
  affiliated with Paramount or the Jellyfin project.

## Licence

MIT. See [LICENSE](LICENSE) — use it, change it, ship it, keep the copyright notice.
