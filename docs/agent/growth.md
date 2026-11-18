# Growing the project

The rule is simple: **new behaviour goes into a package layer, never into a new
root script.** The root has one executable (`fenix`) and one policy file
(`config/pipeline.json`).

## Decide what you are adding

| You are adding | It belongs in | Also update |
|---|---|---|
| A number/threshold/layout constant | `config/pipeline.json` | `docs/reference/config.md` |
| A pure measurement or model helper | `src/fenixsoft/domain/` | tests for the helper |
| A pipeline stage (new analysis/planning step) | `src/fenixsoft/stages/` | `cli.py`, `docs/pipeline.md`, `docs/agent/workflow.md`, tests |
| A rendering/compositing change | `src/fenixsoft/render/` | `docs/pipeline.md`, `docs/reference/qa-gates.md` if a gate changes |
| A delivery/regression gate | `src/fenixsoft/gates/` | `docs/reference/qa-gates.md`, tests |
| A multi-SKU or hand-off operation | `src/fenixsoft/ops/` | `docs/reference/commands.md` |
| A filesystem location | `src/fenixsoft/context.py` | `docs/agent/artifacts.md`, `.gitignore` |
| Documentation only | `docs/` | link it from the nearest guide |

If the answer is “a script at the repo root”, the answer is wrong.

## Layer rules

```text
cli → ops | gates | stages | render
stages → domain + context
render → domain + stages.beat_grid + context
gates → domain + context
ops → domain + context
domain → context
context → stdlib
```

- No reverse imports (a stage must not import a gate or the CLI).
- No module except `context.py` computes the repo root or an `input/`/`output/`
  path.
- No stage writes outside the artifact it owns (see
  [artifacts.md](artifacts.md)).
- No new dependency without adding it to `requirements.txt` **and** explaining
  whether it is runtime or tooling-only in the comment block. The renderer must
  keep working without librosa; beat measurement is a separate command.

## Recipe: add a config key

1. Add the key and a default to `config/pipeline.json` under the right block.
2. Read it in code with a safe default: `cfg.get("block", {}).get("key", DEFAULT)`.
3. Document it in `docs/reference/config.md` (block, meaning, units, who reads it).
4. If it changes a gate, update `docs/reference/qa-gates.md` and the `fix` string.
5. Run `./fenix test` and one dry path (`plan --dry-run` or `validate`).

Never read a hard-coded constant that duplicates a config value.

## Recipe: add a stage

1. Create `src/fenixsoft/stages/<name>.py` with `def main(argv=None)` and an
   argparse parser (the CLI forwards the tail verbatim).
2. Read inputs through `context.resolve_sku_dir()` / `context.load_config()`;
   write outputs through `context.artifact()` or `context.output_dir()`.
3. Add the command to `COMMANDS` and `ORDER` in `src/fenixsoft/cli.py`.
4. Declare reads/writes in `docs/pipeline.md` and ownership in
   `docs/agent/artifacts.md`.
5. Add a fast unit test for the pure part and a CLI import test (the existing
   `test_every_command_target_imports` covers the wiring automatically).
6. If it produces a number the agent should trust, emit
   `{id, measured, threshold, pass, fix}` — not free-form log text.

## Recipe: add a QA gate

1. Add a `check(...)` row in `src/fenixsoft/gates/qa_check.py` with:
   - a stable `id` (`group_thing`, e.g. `c_caption_text`),
   - `measured`, `threshold`, `pass`,
   - `severity` (`error` or `warn`),
   - a `fix` that names a concrete action and the artifact to read.
2. Choose `error` only when the piece must not ship. Everything else is `warn`
   and becomes an error under `--strict`.
3. Update `docs/reference/qa-gates.md` (row, threshold, fix).
4. Update `output/<sku>/qa_report.json` expectations in any baseline or test.
5. A gate without a `fix` is not finished.

## Recipe: add a derived artifact

1. Declare the name in `context.py` if it is reused, or write it through
   `context.artifact(sku, "name.json")`.
2. Producer stage writes it atomically (`.write_text` after the full payload is
   built).
3. Add it to the derived-artifact table in `docs/agent/artifacts.md`.
4. Add it to `.gitignore` only if it is reproducible and should not be tracked.
5. Name it after the stage, not the model: `plan_validation.json`, not
   `result2.json`.

## Versioning and compatibility

- `config/pipeline.json → version` tracks the config schema. A breaking key
  rename bumps it and is documented in `docs/reference/config.md`.
- `edit_plan.json` is a contract: `cuts[].clip`, `in`, `dur`, optional
  `candidate`. Do not silently repurpose a field; add a new optional one.
- `runs.json` and `latest.json` are append-only indexes. Never rewrite history.
- Old flat renders stay readable; do not migrate or delete them by hand.

## Definition of done for a code change

- [ ] `./fenix test` green.
- [ ] `./fenix doctor` clean.
- [ ] The affected command still runs on the demo SKU (or a `--dry-run` path).
- [ ] `./fenix qa --latest demo_iphone14_blue --strict` still PASSes.
- [ ] `docs/` updated where the change is visible to an agent.
- [ ] No new root-level script, config or output directory.
- [ ] No hard-coded path outside `context.py`.

## Anti-patterns (reject on sight)

- Patching `render_video.py` for one video’s framing or timing bug.
- Typing a timestamp from vision instead of choosing a candidate ID.
- Writing a `qa_report.json` or `runs.json` by hand.
- Creating `output/<sku>/<sku>_final.mp4` directly.
- Adding a second config file, or reading `session3_config.json` (the old name).
- A check that prints “FAIL” without a `fix`.
- A new dependency imported at render time “just in case”.
