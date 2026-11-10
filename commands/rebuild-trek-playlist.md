---
description: Rebuild the chronological Star Trek playlist — plan and diff, approve, rename the old playlist, create and verify the new one
---

Load the `rebuild-trek-playlist` skill and follow it exactly.

Start with the read-only plan:

```bash
python3 .opencode/skills/rebuild-trek-playlist/scripts/trek_playlist.py plan
```

Report the verdict and the report path. Stop if the verdict is `IDENTICAL`.

If the verdict is `BLOCKED`, run the review: put each gap and its proposal to the user, record the answer with the
`decide` command the plan printed, then run `plan` again. Ask for explicit approval before running `rename` or
`create`, and only when that re-run is not `BLOCKED`. Never delete a playlist.
