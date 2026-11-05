# Rebuild Trek Playlist Skill — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a repo-local skill that rebuilds the chronological Star Trek Jellyfin playlist through two approval gates, never deleting anything.

**Architecture:** One stdlib Python file with subcommands (`plan`, `decide`, `rename`, `create`, `verify`) plus a SKILL.md that owns the gates. `plan` is read-only and writes a report + state; `rename` and `create` are the only writes.

**Tech Stack:** Python 3 stdlib (`urllib`, `json`, `re`, `difflib`, `unittest`); Jellyfin REST 12.2.0.

**Spec:** `docs/superpowers/specs/2026-11-05-rebuild-trek-playlist-design.md`

## Global Constraints

- Stdlib only; no new dependencies.
- `JELLYFIN_API_KEY` from env only; never written or printed.
- No delete call of any kind.
- Exact default playlist name `Chronological Star Trek`.
- Acceptance: live `plan` prints `IDENTICAL` against the current 975-entry playlist.

## Review Focus

- Guide gains a new series code → must fail loudly, not drop rows.
- Library has a duplicate title → must be AMBIGUOUS, never a silent pick.
- Site duplicates a position (Relativity E24/E25) → collapse, don't duplicate.
- Library stores a two-parter as one file → one entry, not two.
- `plan` run twice → identical output, no state churn.

---

### Task 1: Matcher core

**Files:** Create `.opencode/skills/rebuild-trek-playlist/scripts/trek_playlist.py`, `.../scripts/test_trek_playlist.py`

**Interfaces:**
- Produces: `norm(s) -> str`, `partkey(s) -> tuple[str, int|None]`, `combined(name) -> tuple|None`, `match_rows(rows, inventory, decisions) -> list[dict]`.

- [ ] Write tests for `norm` (`The 37's` == `The 37s`, `E²` == `E2`, leading `the` stripped), `partkey` (`Unification I` → `('unification', 1)`), `combined` (`Flesh and Blood (1) / Flesh and Blood (2)`).
- [ ] Run: `python3 -m unittest discover -s .opencode/skills/rebuild-trek-playlist/scripts` → FAIL (no module).
- [ ] Implement the three helpers plus the tiered matcher ported from `/tmp/opencode/jt/match3.py`, with decisions applied first and consecutive-duplicate collapse.
- [ ] Add tests for exact, normalized, combined, units-expansion, duplicate-title → AMBIGUOUS, empty inventory → MISSING.
- [ ] Run tests → PASS.

### Task 2: Guide parser

**Files:** same script; tests.

**Interfaces:** `parse_guide(text) -> list[dict]` with keys `chap, code, season, ep, title, sid, units`.

- [ ] Test a two-chapter snippet yields ordered rows and a `S1 · E1–2` range.
- [ ] Run → FAIL.
- [ ] Implement section/`li` parsing with `html.unescape`.
- [ ] Run → PASS.

### Task 3: Jellyfin client and discovery

**Files:** same script; tests with an injected fake `get`.

**Interfaces:** `Api(base, key).get(path, **params)`, `.post(path, body) -> (status, json|None)`; `discover(api) -> dict`; `find_playlist(api, name, user_id)`; `playlist_items(api, pid, user_id)`.

- [ ] Test discovery uses CollectionType, not hardcoded IDs; films use `searchTerm=Star Trek`; series filtered by `Star Trek` prefix.
- [ ] Test playlist lookup: 0 → None, 2 → error.
- [ ] Implement; run → PASS.

### Task 4: `plan`

**Files:** same script.

**Interfaces:** `cmd_plan(args) -> int`; state file `~/.local/state/trek-playlist/state.json`; report `reports/rebuild-YYYY-MM-DD-HHMM.md`.

- [ ] Test diff: identical, added, removed, moved (LCS via `difflib.SequenceMatcher`).
- [ ] Test missing-item links (TVDB dereferrer, IMDb search, TMDB for films).
- [ ] Implement: fetch → match → diff → report + state; verdicts `IDENTICAL`, `DIFFERENT`, `NO_PLAYLIST`, `BLOCKED`.
- [ ] Run tests → PASS.

### Task 5: Writes and verification

**Files:** same script.

- [ ] Test `rename` payload and `create` payload against the fake transport; assert no DELETE path exists.
- [ ] Implement `decide`, `rename` (+`--restore`), `create`, `verify` (count, order, uniqueness, types, old playlist intact).
- [ ] Run tests → PASS.

### Task 6: Skill and command docs

**Files:** Create `.opencode/skills/rebuild-trek-playlist/SKILL.md`, `.opencode/commands/rebuild-trek-playlist.md`.

- [ ] SKILL.md: frontmatter, the five phases, the two gates, the missing-item prompt, the manual-delete reminder, failure/rollback.
- [ ] Command: loads the skill and starts at `plan`.

### Task 7: Live acceptance

- [ ] `plan` against the live server → `IDENTICAL`, 975 entries, no writes.
- [ ] `python3 -m unittest` → PASS.
- [ ] Confirm no state changed except the report/state files.
