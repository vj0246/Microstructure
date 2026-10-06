# Wrap-up checklist: contain and end the project

Written 2026-10-06 from the state of branch `claude/task-i2c5vs` (HEAD `bcaba85`, clean tree, no commits ahead of `main`).
Everything below is something you do. Nothing here needs new analysis.

## Decision: stop condition

The project ends when the confirmatory results are final, the record is reproducible, and the limitations are stated.
No new hypotheses. Three pre-registered tests are done (SPEC v1, v2, v3). Any further test needs a new spec and a new tag, which is a new project.

## A. Fix the pre-registration record (must do, blocking)

The README says SPEC v1, v2 and v3 are frozen under tags `spec-v1`, `spec-v2`, `spec-v3`, and that each report refuses to run without its tag.
In this clone, `git tag` is empty and `git ls-remote --tags origin` returns nothing. The tags do not exist locally or on the remote.
Without them the "frozen before outcomes" claim cannot be checked by anyone.

1. Find the commit where each spec was frozen, before its results commit. Candidates from `git log`:
   - SPEC v1: `ea1903b` (initial study commit). Check that `docs/SPEC.md` at that commit is the frozen text.
   - SPEC v2: `9fbc538` (pre-register sell-side test), before `d459d00` (results).
   - SPEC v3: `7504888` (pre-register mechanism tests), before `bcaba85` (results).
2. Confirm each by `git show <commit> --stat` and `git diff <commit> HEAD -- docs/SPEC*.md` (spec text unchanged since freeze).
3. If the tags exist on your own machine, push them: `git push origin spec-v1 spec-v2 spec-v3`.
4. If they do not exist anywhere, create them at the commits above and say so plainly in the README, with the date you created them. Do not backdate or imply they were created at freeze time.

## B. Close the open items (decide each: fix or accept as a stated limitation)

| Item | Recommendation |
|---|---|
| Delivery file missing for 2022-08-08 (NSE served Excel as CSV) | Accept. One day, handled in code, affects only the exploratory delivery split. Already in README. |
| Live-snapshot revision check ran on 1 of 5 trading days | Accept, or run `uv run impact fetch` on 4 more trading days and rerun `impact check m0`. Only needed if you want M0 fully green. |
| Typing audit 94.5% vs 95% gate | Accept. README already explains the error direction makes rejections conservative. Do not re-audit: new verdicts after seeing results would taint it. |
| 26 unverified dictionary entries | Accept. README reports their measured effect as negligible. |
| PIT data lacks some promoters | Accept. Stated. |

## C. Freeze the code and data

1. Run the full test suite: `uv run pytest`. Expect 108 passing. Fix nothing unless something fails.
2. Run `uv run impact check m0` through `m4` and keep the output.
3. Record the manifest hash from the README (`d7873714...34e2`) next to the raw store. Back up `IMPACT_DATA_DIR` (14,940 raw files plus `manifest.jsonl`). The repo does not hold the data, so losing that directory ends reproducibility.
4. Write down the exact run order in the README or `scripts/run_pipeline.cmd`: fetch, build, simulate, report v1, v2, v3.

## D. Merge and archive

1. Open a PR from `claude/task-i2c5vs` to `main` and merge it. The branch has no unmerged work today, so this is only needed if you add the commits from A, C or E.
2. Tag the final state, for example `v1.0-final`.
3. Archive the GitHub repo (Settings, Archive) so it becomes read-only.

## E. Decide the deliverable

Pick one. Each is a separate piece of work.

1. **Repo as record.** README, specs, figures and tags are the deliverable. Nothing more to write.
2. **Short write-up.** Convert the README headline and the two powered results into a 2 to 4 page note.
3. **Stop silently.** Archive and move on. Acceptable, but then keep section A done, because the tags are what make the results credible.

## F. Things not to do

- Do not edit `SPEC.md`, `SPEC_v2.md` or `SPEC_v3.md` after tagging. You said earlier edits are allowed, but any edit voids the pre-registration.
- Do not rerun a spec with changed thresholds or samples and report it as confirmatory.
- Do not add a fourth exploratory spec unless you are willing to treat it as a new project.

## Done when

- [ ] Tags `spec-v1`, `spec-v2`, `spec-v3` exist on the remote at the freeze commits (A)
- [ ] Each open item in B is marked fix or accept
- [ ] Tests and gates rerun, output saved (C)
- [ ] Raw data backed up and manifest hash recorded (C)
- [ ] Branch merged, final tag created, repo archived (D)
- [ ] Deliverable choice made (E)
