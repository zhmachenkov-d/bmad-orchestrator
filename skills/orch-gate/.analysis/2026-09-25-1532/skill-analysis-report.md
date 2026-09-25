# Analysis Report: skills/orch-gate

Generated: 2026-09-25 · Schema: 2

**Grade: Fair**

> The 24 findings from the last run are all fixed. What is left is mostly script-level: a local verdict can still differ from CI because of machine state (git quotePath, stale origin refs), and several registry and archive shapes still fail open.

The prompt side is close to done. Leanness, customization and architecture confirmed every earlier finding fixed and raised only small coherence gaps, and SKILL.md is 1917 tokens with a clean script/prompt split. The biggest remaining opportunity is the gate's core promise of the same verdict locally and in CI. Git listings follow the user's core.quotePath, and a local run reads whatever origin/main was last fetched. Next come the registry, archive and sprint-status paths where malformed or unusual input still passes, crashes, or gets a fix that cannot clear its own finding.

| Severity | Count |
| --- | --- |
| Critical | 0 |
| High | 2 |
| Medium | 9 |
| Low | 5 |

## Themes

### 1. The local verdict depends on the machine, not only on the refs

- Root cause: The gate promises the same verdict locally and in CI, but some inputs still come from the environment. These are the user's git config (quotePath changes ls-tree output), how fresh the local origin refs are (never fetched, while registry repos are), and a narrow CI detection that switches on local-only checks. The text log also cannot show which inputs a run used.
- Fix: Pin git's output-affecting config in gitio.git (-c core.quotePath=false) and use -z listings. In local non-offline runs, fetch the base and coordination refs before reading them, and add a notice when the fetch fails. Detect CI from the common provider variables and record ci_source. Print a compact inputs line (shas, sources, repos_read, detector versions) in the text and markdown output.
- Findings:
  - `determinism-1` Directory listings depend on core.quotePath, so non-ASCII registry files and epics are silently dropped — `scripts/orchlib/gitio.py:159-163 (Tree.list); registry.py:99, stories.py:126, gitio.py:138, markers.py:92/204`
  - `enhancement-1` A local run gates against stale origin refs and can PASS what CI fails — `scripts/orchlib/gitio.py:41-45 default_base; scripts/orch.py:215-226 _gate_base; SKILL.md:21`
  - `determinism-7` CI detection reads only $CI — `scripts/orchlib/gate.py:381-385, 395-396; scripts/orch.py:237-239`
  - `enhancement-3` Text and markdown output omit the verdict inputs that SKILL.md compares — `scripts/orchlib/gate.py:407-414 render_text, 428-437 render_markdown`

### 2. Registry and archive inputs that still fail open or crash

- Root cause: The setup-repair work made missing and invalid registries fail closed, but validation is still shape-incomplete. Empty allowed_write lists pass as present, and a non-mapping contracts field crashes load, which blocks setup-repair. Overlap is compared on raw repo URLs, archived markers are not protected from edits or deletes, and the setup row promises epic validity that only covers file existence.
- Fix: Make registry.load total and type-checked: bad shapes and empty required values become registry-invalid issues, never exceptions or permissive defaults. Normalize repos in the overlap check. Protect .orch/archive against modify and delete in every mode. Surface story-plan issues: fail those that concern the PR's own story, and warn about the rest under setup. Add a test for each.
- Findings:
  - `determinism-2` Markerless PRs can modify or delete archived markers — `scripts/orchlib/gate.py:229-243; scripts/orchlib/markers.py:183-199 (is_archive_move)`
  - `determinism-3` allowed_write: [] passes the required-field check and leaves the subproject unprotected — `scripts/orchlib/registry.py:117`
  - `determinism-4` Some registry shapes crash the loader, which also blocks the setup-repair PR — `scripts/orchlib/registry.py:122-124; scripts/orch.py:242`
  - `determinism-5` The write-overlap check compares raw repo strings — `scripts/orchlib/registry.py:177`
  - `architecture-2` The setup row promises structurally valid epics, but only a missing epics file fails — `SKILL.md:33; scripts/orchlib/gate.py:133-135; scripts/orchlib/stories.py:146-176`

### 3. sprint-status derive cannot clear the gate's merged-not-done warning

- Root cause: In a coordination PR the gate counts markers at the PR head as merged, while derive and epic close-check count them on coordination main. So the PR's own story is warned as merged-not-done with a fix that changes nothing, and the SKILL.md fix loop never converges.
- Fix: Use one merged-set rule. Exempt the PR's own story from the reverse-lag warning, or give derive and close-check a --head ref that the fix.command passes. Test that applying the gate's own fix.command clears the finding on re-run.
- Findings:
  - `architecture-1` merged-not-done is raised on the PR's own story in the normal bmad-build flow — `scripts/orchlib/gate.py:302-309, 364-371; scripts/orchlib/sprint_status.py:143-145`
  - `determinism-6` The derive fix reads a different merged set than the gate, so it cannot clear its own warning — `scripts/orchlib/gate.py:304, 369-370; scripts/orch.py:191, 209`

### 4. Prompt residue the script already knows

- Root cause: A few SKILL.md sentences do lookups or restate defaults that the script resolves and reports itself, and one resolves the language in a different order than stock BMad. The prompt also never tells the agent which PR to gate, so an empty diff reports PASS, and it never mentions the untracked cache.
- Fix: Expose communication_language through orch.py config in stock layer order and point SKILL.md at it. Shrink the Defaults paragraph to the trusted-read-point sentence. Tell the agent to gate a named PR with --head and --base, and emit an empty-diff notice. Move the fetch cache under the git dir.
- Findings:
  - `leanness-1` The Defaults paragraph repeats input resolution that the script records and reports — `SKILL.md:23`
  - `customization-1` communication_language precedence differs from stock BMad and from USER_LAYERS — `SKILL.md:16; scripts/orchlib/config.py:20`
  - `determinism-8` The model resolves {communication_language} by reading four TOML layers — `SKILL.md:16 (Resolution rules)`
  - `enhancement-2` Nothing says which PR to gate, and an empty diff reports PASS — `SKILL.md:21; scripts/orchlib/gate.py:142-156, 218`
  - `architecture-3` The fetch cache is untracked in the working tree and hidden from the dirty notice — `SKILL.md:25; scripts/orchlib/markers.py:19; scripts/orch.py:52; scripts/orchlib/gate.py:161`

## Strengths

- Every one of the 24 findings from 2026-09-25-1326 was verified fixed in code, and the 62 tests pass.
- The setup check now fails closed on an empty or invalid registry, missing epics and an unregistered repo, and the setup-repair escape prevents a bootstrap deadlock.
- The script owns the verdict and the prompt only explains it. Stable finding codes with a runnable fix.command make the fix loop mechanical.
- The SKILL.md body is 1917 tokens. It uses a single-file layout, with the calling convention in --help and the module plan, and the leanness lens found only one low cut.
- It is fully headless at the script level: JSON or markdown output, with exit codes 0, 1 and 2 separating a pass, a failing verdict and an environment error.

## Recommendations

1. Pin git output config (-c core.quotePath=false, -z listings) in gitio so the same commit gives the same verdict on every machine. (resolves: determinism-1)
2. Make registry.load total and shape-checked, treat empty required values as missing, normalize repos in the overlap check, and protect .orch/archive against edits and deletes. (resolves: determinism-2, determinism-3, determinism-4, determinism-5)
3. Unify the merged-set rule between the gate's sprint-status check and derive or close-check, so the attached fix clears its own finding. (resolves: architecture-1, determinism-6)
4. Fetch the base and coordination refs in local runs, and print an inputs line in the text and markdown output. (resolves: enhancement-1, enhancement-3)
5. Move communication_language into orch.py config, trim the Defaults paragraph, add gate-a-named-PR guidance and an empty-diff notice, and move the cache under the git dir. (resolves: customization-1, determinism-8, leanness-1, enhancement-2, architecture-3)
6. Surface story-plan issues from the gate, and broaden CI detection with a recorded ci_source. (resolves: architecture-2, determinism-7)

## Experience

- **Developer checks a story branch locally** — Commit, run gate --format text -o tmp.json, apply fix.command when mechanical, commit, re-run. The loop works. The gap is that without a fetch, pins and scope run against a stale origin/main and can pass a branch that CI fails.
- **Developer asks why CI failed** — With the JSON artifact, findings are explained by code and the inputs are compared. With only the job log, there are no shas to compare. A local re-run gates the current checkout, which may not be the PR being asked about, and an empty diff reports PASS.
- **First-time adopter wires CI before setup** — Now fails closed. registry-empty, no-epics and repo-unregistered each name the fix, and a repair PR passes as setup-repair. Some malformed registry shapes still crash before the repair can run.
- **Contract author ships a breaking change** — The gate fails with the expand, migrate, contract hint. A narrow change passes only when every consumer's dependencies are merged, and an unreadable consumer repo fails as unverifiable.
- Headless: Fully headless at the script level (JSON or markdown output, exit codes 0, 1 and 2). The SKILL.md layer's only confirmation is the offer to run fix.command, and no v1 caller needs a headless variant.

## Findings

### High (2)

#### determinism-1 — Directory listings depend on core.quotePath, so non-ASCII registry files and epics are silently dropped

- Lens: determinism
- Location: `scripts/orchlib/gitio.py:159-163 (Tree.list); registry.py:99, stories.py:126, gitio.py:138, markers.py:92/204`
- Evidence: ls-tree runs without -z and inherits the user's git config. With the default quotePath=true, a registry file named zahlung-ü.yaml or an epics file under Überblick/ is C-quoted and filtered out, so the same commit gets needs-story or no-epics under one setting and passes under the other. Tree.files crashes with exit 2 on such directories.
- Recommendation: Use ls-tree -r -z --name-only split on NUL, and pass -c core.quotePath=false in gitio.git. Add a regression test with a non-ASCII registry file and epics subdirectory under both settings.

#### enhancement-1 — A local run gates against stale origin refs and can PASS what CI fails

- Lens: enhancement
- Location: `scripts/orchlib/gitio.py:41-45 default_base; scripts/orch.py:215-226 _gate_base; SKILL.md:21`
- Evidence: Nothing fetches the base or coordination ref before a local run, while registry repos are fetched fresh. Reproduced: after a contract change lands on remote main, a story branch gets PASS pins locally, and gets FAIL pin-stale after a plain git fetch.
- Recommendation: In local non-CI, non-offline runs, fetch the base branch and coordination ref first, recorded as refs_fetched. On failure, add a refs-not-refreshed notice without changing the verdict. Add a test reproducing the stale-ref case.

### Medium (9)

#### determinism-2 — Markerless PRs can modify or delete archived markers

- Lens: determinism
- Location: `scripts/orchlib/gate.py:229-243; scripts/orchlib/markers.py:183-199 (is_archive_move)`
- Evidence: Only deleted story markers and added archive files are examined, so an M or D under .orch/archive/epic-N/ passes. Reproduced: editing or deleting .orch/archive/epic-1/1-2.yaml in a markerless PR returns pass. That archive is the merge record that close_check and sprint-status read.
- Recommendation: Protect .orch/ (except cache) in every mode. Allow only pure archive moves, marker adds and closed records, and fail archived-marker-modified or archived-marker-removed. Add tests for both cases.

#### determinism-3 — allowed_write: [] passes the required-field check and leaves the subproject unprotected

- Lens: determinism
- Location: `scripts/orchlib/registry.py:117`
- Evidence: An empty list is not in (None, ""), so the entry loads, validate is silent and setup passes. Reproduced: a markerless PR editing services/user/app.py passes. This contradicts the decision that required fields never default permissively.
- Recommendation: Treat an empty or whitespace-only allowed_write, repo or path as missing-field. Add a test that a markerless PR into sub.path then fails setup.

#### determinism-4 — Some registry shapes crash the loader, which also blocks the setup-repair PR

- Lens: determinism
- Location: `scripts/orchlib/registry.py:122-124; scripts/orch.py:242`
- Evidence: contracts.get("exports") assumes a mapping. With contracts: [payment-service] on main, both orch.py registry and the gate on the PR that fixes it exit 2 with AttributeError, so setup-repair never runs.
- Recommendation: Make load total. Type-check contracts, exports and scalar fields, record a bad-field issue and drop the entry, so a malformed main becomes a registry-invalid failure that a repair PR can clear. Add a test.

#### determinism-5 — The write-overlap check compares raw repo strings

- Lens: determinism
- Location: `scripts/orchlib/registry.py:177`
- Evidence: Two subprojects with the same allowed_write, one at git@github.com:o/code.git and one at https://github.com/o/code, validate clean. The rest of the library normalizes these to the same repo, so one path gets two owners.
- Recommendation: Compare normalize_repo values, and add a validate test with both URL spellings.

#### determinism-6 — The derive fix reads a different merged set than the gate, so it cannot clear its own warning

- Lens: determinism
- Location: `scripts/orchlib/gate.py:304, 369-370; scripts/orch.py:191, 209`
- Evidence: In coordination PRs the gate counts markers at head, while derive and epic close-check count them on coordination main. Reproduced: story 1-2 set to review gets merged-not-done with fix sprint-status derive --write, and running that fix changes nothing. The standalone close-check also misses archives added in the close PR.
- Recommendation: Use one merged-set rule. Either give derive and close-check a --head ref that fix.command passes, or do not attach the derive fix to the PR's own story. Test that applying fix.command clears the finding.

#### architecture-1 — merged-not-done is raised on the PR's own story in the normal bmad-build flow

- Lens: architecture
- Location: `scripts/orchlib/gate.py:302-309, 364-371; scripts/orchlib/sprint_status.py:143-145`
- Evidence: The PR's own marker counts as merged at head, so a coordination story PR that sets its story to review (as stock bmad-build does) is warned merged-not-done. This contradicts the decision that in-progress and review are free, and the attached fix is a no-op (see determinism-6).
- Recommendation: Exclude result['story'] from the reverse-lag warning, and keep the head-based set for the forward invariant. Add a test that a story PR setting its own story to review produces no merged-not-done finding.

#### enhancement-2 — Nothing says which PR to gate, and an empty diff reports PASS

- Lens: enhancement
- Location: `SKILL.md:21; scripts/orchlib/gate.py:142-156, 218`
- Evidence: The skill always gates HEAD. Reproduced: asking about a PR while on main gives PASS non-story change with empty changes, so a user asking why a gate failed is told it passes.
- Recommendation: Add one SKILL.md sentence: gate a named PR or branch with --head and --base. Emit an empty-diff notice when changes is empty.

#### enhancement-3 — Text and markdown output omit the verdict inputs that SKILL.md compares

- Lens: enhancement
- Location: `scripts/orchlib/gate.py:407-414 render_text, 428-437 render_markdown`
- Evidence: SKILL.md explains a CI run from the job log, but render_text prints only ref names and render_markdown only base and head shas. A log-only user cannot compare coord_sha, sources or repos_read.
- Recommendation: Add a compact inputs line to both renderers: base@sha (source), head sha, coord_ref@sha (source), repos_read, detectors_used. Have the orch-setup CI templates upload the -o JSON.

#### architecture-2 — The setup row promises structurally valid epics, but only a missing epics file fails

- Lens: architecture
- Location: `SKILL.md:33; scripts/orchlib/gate.py:133-135; scripts/orchlib/stories.py:146-176`
- Evidence: setup_problems keeps only no-epics. Duplicate stories, forward or unknown dependencies and unknown subprojects never surface, and a duplicate silently keeps its first definition, so the enforced subproject can come from a heading the author did not edit.
- Recommendation: Fail the story issues that concern the PR's own story (stable codes) and warn about the rest under setup, or narrow the SKILL.md row to 'epic files exist'.

### Low (5)

#### determinism-7 — CI detection reads only $CI

- Lens: determinism
- Location: `scripts/orchlib/gate.py:381-385, 395-396; scripts/orch.py:237-239`
- Evidence: Jenkins, Azure (TF_BUILD) and TeamCity do not set CI. On them, --offline is accepted, the dirty notice runs, and the working-tree merge-driver check can fail every PR.
- Recommendation: Recognize the common CI variables, or have the CI templates pass --ci, and record ci_source in the result.

#### customization-1 — communication_language precedence differs from stock BMad and from USER_LAYERS

- Lens: customization
- Location: `SKILL.md:16; scripts/orchlib/config.py:20`
- Evidence: SKILL.md ranks config.user.toml above custom/config.toml. Stock BMad (last layer wins) and orch-gate's own USER_LAYERS rank the team custom layer above the installer's user layer, so a team-pinned language is ignored in the explanations only.
- Recommendation: Resolve it through orch.py config in stock order (see determinism-8), or rewrite the line as custom user > custom team > user > team.

#### determinism-8 — The model resolves {communication_language} by reading four TOML layers

- Lens: determinism
- Location: `SKILL.md:16 (Resolution rules)`
- Evidence: The rule is a 'first found in' layered lookup with one correct answer. config.user_settings already resolves these layers, and SKILL.md already reads orch.py config for the artifact paths.
- Recommendation: Add communication_language to config.USER_KEYS and make the rule 'the value orch.py config prints, else the user's language'.

#### leanness-1 — The Defaults paragraph repeats input resolution that the script records and reports

- Lens: leanness
- Location: `SKILL.md:23`
- Evidence: Default bases, locator order and the --repo-id rule are in --help, recorded in base_source and coord_ref_source, and named in every failing error. Only the 'registry read at base or coordination main' clause carries a why the model cannot get elsewhere.
- Recommendation: Cut the paragraph to the trusted-read-point sentence plus a pointer to the source fields and error hints.
- Proposed smallest: The script resolves base, coordination repo and coordination ref itself and records how in `base_source` and `coord_ref_source`; when it cannot, it exits 2 or fails `setup` naming the flag to pass (`--base`, `--coord`, `--coord-ref`, `--repo-id`). The registry and epics are read at the base in a monorepo and at coordination main otherwise, so a PR cannot move its own boundaries.
- Predicted delta: None expected. Every failure case names the same flag the paragraph teaches. Confirm with a polyrepo input whose registry URL differs from origin.

#### architecture-3 — The fetch cache is untracked in the working tree and hidden from the dirty notice

- Lens: architecture
- Location: `SKILL.md:25; scripts/orchlib/markers.py:19; scripts/orch.py:52; scripts/orchlib/gate.py:161`
- Evidence: Polyrepo runs write .orch/cache, which only orch-setup gitignores. The dirty notice skips it, so 'commit what it changed' with a broad add can commit the cache and cause out-of-scope on the next run.
- Recommendation: Move the cache under git rev-parse --git-common-dir, or say in SKILL.md to commit only the files the fix wrote.
