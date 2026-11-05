# Rebuild Star Trek playlist — design

**Date:** 2026-11-05
**Status:** implemented
**Repo:** `jellyfin-trek-playlist` (the skill is self-contained; it does not use the old `chrono-trek.py`)

## Intent

Star Trek series are still airing, so the canonical viewing order grows. Jellyfin 12.2.0
cannot reorder a playlist in place: any change to order means recreating it. The audit run
on 2026-11-05 proved the procedure but did it by hand.

This skill makes the procedure repeatable: pull the canonical order, diff it against the
live playlist, report, and — only after explicit approval — rename the old playlist, create
a new one, and verify it. The old playlist is left for the user to delete manually after
they have checked the new one.

## Goals

- Fetch the canonical order from startrekviewingguide.com and parse every position.
- Map every position to exactly one library item, or classify it AMBIGUOUS/MISSING.
- Diff the intended order against the current playlist and say plainly whether a rebuild is needed.
- Report gaps with enough detail (and links) for the user to fix their library.
- After approval: rename the old playlist, create the new one, verify the result.
- Never rebuild silently; never delete anything.

## Non-goals

- Deleting the superseded playlist (manual step, by the user, after checking).
- Fixing or replacing `chrono-trek.py` / `jellyfin-api-client`; the skill does not use them.
- Other franchises, other servers, or a UI.
- Library scans, metadata edits, or any write other than rename + create.

## Anti-goals

- A low-confidence match chosen without telling the user.
- A rebuild when the order is already correct.
- The API key appearing in a report, log, state file, or command output.
- Any mutation before the approval gate.

## Constraints

- Python 3 standard library only; no new dependencies.
- Read-only until the approval gate.
- Jellyfin 12.2.0 quirks observed on this server:
  - no `DELETE /Playlists/{id}`; deletion would be `DELETE /Items/{id}` (not used);
  - `GET /Playlists/{id}` and `GET /Users/Me` return 400; use `/Playlists/{id}/Items` and `/Users`;
  - the 13 classic films are BoxSet children and absent from the Movies listing; `searchTerm=Star Trek`
    returns them.
- The playlist must be recreated for any order change.

## Approaches considered

1. **Scripted pipeline + thin skill (chosen).** One stdlib Python file with subcommands;
   SKILL.md orchestrates the gates. Deterministic and testable offline.
2. Agent-driven skill with ad-hoc API calls. More tolerant of site changes, but two runs can
   disagree and verification depends on the agent's attention.
3. Single interactive script. Rejected: harness commands run unattended, so a prompt stalls.

## Layout

```
.opencode/skills/rebuild-trek-playlist/SKILL.md
.opencode/skills/rebuild-trek-playlist/scripts/trek_playlist.py
.opencode/commands/rebuild-trek-playlist.md      # thin: load the skill
```

## Phases

`plan` → report + state → **gate 1** → `rename` → `create` → `verify` → summary.
`plan` is read-only. `rename` and `create` are the only writes.

State lives in `~/.local/state/trek-playlist/state.json` (per playlist name). Persistent
decisions live beside it in `decisions.json`, keyed `CODE|S|E` or `Film|title`, so a
judgment made once survives later runs.

## Data acquisition

- Guide: `https://www.startrekviewingguide.com/lo-fi-print-ready-listing.html`; parse
  `<section data-chapter>` and `<li class="ck" data-id data-units>` → code, S/E (with range),
  title, anchor.
- Library: discover `tvshows` and `movies` folders via `/Library/MediaFolders` (CollectionType),
  never hardcoded IDs. Series whose name starts `Star Trek`; episodes per series.
- Films via `/Items?ParentId=<movies>&Recursive=true&IncludeItemTypes=Movie&searchTerm=Star Trek`.
- User: `--user`/`JELLYFIN_USER`, else the first administrator from `/Users`.

## Matching

Applied in order; first non-empty tier wins:

1. exact library title;
2. normalized: NFKD, casefold, `&`→`and`, roman/word part numbers, strip leading `the`, punctuation;
3. part-aware (base + part number, then base alone);
4. combined files titled `X (1) / X (2)`;
5. prefix (unique, length ≥ 4);
6. fuzzy: unique candidate ≥ 0.80 with the same season/episode, else unique ≥ 0.80 overall;
   anything else is AMBIGUOUS with its candidates and scores.

Then: `units=2` expands to two library files when the library has parts 1 and 2 separately;
consecutive positions resolving to the same file collapse (site duplicates), recorded in the report.

Films: exact → normalized → unique fuzzy ≥ 0.90, else AMBIGUOUS.
Unknown series code: hard error naming the code.

`decisions.json` overrides are applied before matching and win outright.

## Missing items

Every MISSING position is reported with: position, code, season/episode, canonical title,
guide anchor URL, TVDB series link (from the series' `ProviderIds`), and an IMDb search link;
films also get a TMDB search link. Gate 1 asks the user to either abort or proceed with the
listed positions excluded. Exclusions are recorded in state and echoed in the final report.

## Diff against the live playlist

Find the playlist by exact name (more than one → error; none → verdict `NO_PLAYLIST`).
Compare entry IDs in order with the intended list:

- identical → verdict `IDENTICAL`, stop, nothing written;
- otherwise → `DIFFERENT`, with first divergence, added/removed/moved counts (LCS), and
  any playlist entries that no longer resolve in the library.

## Rename

`POST /Playlists/{id}` `{"Name": "<name> (superseded YYYY-MM-DD HHMM)"}` → 204.
The new name is read back and stored. If creation later fails, `rename --restore` puts the
old name back; the old playlist itself is never modified beyond its name.

## Create

`POST /Playlists` `{Name, Ids, UserId, MediaType: "Video"}`. Only after gate 1.

## Verify

- Read back `/Playlists/{id}/Items` fully; assert count, order, uniqueness, types
  (Episode/Movie), and that every ID is present.
- Re-read the old playlist: still present under the superseded name, entry count unchanged.
- Write results into the report; exit non-zero on any mismatch.
- End by telling the user the old playlist is theirs to delete once satisfied.

## Report

`reports/rebuild-YYYY-MM-DD-HHMM.md`: verdict, counts, diff detail, gaps with links, full
mapping table, API calls executed, verification results. The key never appears.

## Failure and rollback

- `create` fails → old playlist remains renamed; `rename --restore` documented and reported.
- `verify` fails → nothing is deleted; the mismatch is reported with the first divergent position.
- `plan` fails (unknown code, network, auth) → no state change, clear error.

## Testing

- `plan` against the current playlist must print `IDENTICAL` (regression check for this build).
- `--site-file` / `--inventory-file` fixtures allow offline runs of the matcher.
- Matcher output is compared against the 977-position audit mapping.

## Decisions made here

- Skill name `rebuild-trek-playlist`; command of the same name.
- Rename scheme `<name> (superseded YYYY-MM-DD HHMM)`.
- State and decisions under `~/.local/state/trek-playlist/`.
- Report under `reports/`, one file per run, never overwriting the audit report.

## Resolved during builder check

- **Target playlist:** exact name `Chronological Star Trek` by default, `--playlist` to override.
  Other playlists (including the Audio one named `Star Trek`) are ignored, never renamed.
- **Decisions:** `decisions.json` starts empty; `decide` writes entries and the file is
  hand-editable. The guide's duplicate positions (Relativity at E24/E25, Past Tense, In the
  Cards) are collapsed by the matcher, so they need no entry.
- **Connection:** `JELLYFIN_URL` (default `http://localhost:8096`) and `JELLYFIN_API_KEY`
  (env only). `--user`/`JELLYFIN_USER` overrides the first-admin default. `login.json`
  is not read. A user session (`JELLYFIN_USER` + `JELLYFIN_PASSWORD`) is required for
  `rename`: Jellyfin resolves the calling user from the token, and an API key has no user id.
- **Oracle:** the acceptance check is a live `plan` against the current playlist, which must
  print `IDENTICAL` and match all 975 IDs position by position. Tier and confidence are not
  asserted. Offline fixtures are optional and are not committed.
- **Live verification is in scope**; the key is available in the environment.
- **`NO_PLAYLIST`:** gate 1 offers create-from-scratch; `rename` is skipped and `verify` omits
  the old-playlist assertion.

## Left to the builder

- Exact SKILL.md wording and the report's layout.
- Link formats that age well if the guide or TVDB changes URLs.
