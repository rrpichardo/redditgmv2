# Pipeline Hardening Findings

- Current source checkout: `codex/pipeline-hardening` from `c0ecddd1d9a4772949cb619f679de625453c5369`.
- Live app checkout: `/Users/ricopichardo/Claude/redditgmv2/.claude/worktrees/angry-volhard-f4b947`, same baseline commit.
- `/api/analyze` returns a generated run ID before the coordinator calls `RunStore.create_run`, creating a reproducible first-poll 404 race.
- `loadStatus()` renders recovered status but does not clear the previous notice.
- Retry 409s observed in the server log were caused by overlapping successful retry launches, not a stale lock.
- Trend PDF uses Helvetica core fonts with em dashes and check marks, which fpdf2 cannot encode as Latin-1.
- Matplotlib is a declared dependency and provides DejaVu Sans regular, bold, and italic TTF files suitable for fpdf2 embedding.
- KMeans currently caps by row count but not by the number of distinct embedding vectors.
- The running virtual environment has now been synchronized from `requirements.txt` and contains OpenAI, FAISS, scikit-learn, matplotlib, and fpdf2.
- `main` and `claude/angry-volhard-f4b947` both point to baseline `c0ecddd`, but the live app still runs from the latter checkout.
- The `main` runtime ledger has zero runs; the live worktree ledger has nine, including `c477e905fced4163aebdc1af89db2d10`. Runtime databases must not be copied or merged for rollout.
- The backend already returns `active_run_id` for retry conflicts. Generic `Request failed with 409` is caused by the pipeline UI discarding that response body.
- `start_job` currently generates its own job ID while the analyze endpoint also passes `--job_id`, producing duplicate CLI flags. An explicit optional job ID will make the lock owner and worker identity identical.
