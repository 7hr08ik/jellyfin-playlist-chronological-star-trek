---
description: Rebuild the chronological Star Trek playlist — plan and diff, approve, rename the old playlist, create and verify the new one
---

Load the `rebuild-trek-playlist` skill and follow it exactly.

Start with the read-only plan:

```bash
python3 .opencode/skills/rebuild-trek-playlist/scripts/trek_playlist.py plan
```

Report the verdict and the report path. Stop if the verdict is `IDENTICAL`; resolve gaps if it is `BLOCKED`; ask for explicit approval before running `rename` or `create`. Never delete a playlist.
