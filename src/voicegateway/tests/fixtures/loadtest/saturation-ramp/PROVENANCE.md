# saturation-ramp: SYNTHETIC, a sizing run that fails on purpose

**Nothing here was measured.** Every number is computed by `../make_synthetic.py`.
Do not read any figure here as evidence about any deployment.

Four steps, 100 / 150 / 200 / 250 concurrent, each 60 seconds, run back to back
so their windows do not overlap and each correlates to its own node samples.
Named and shaped like a real capture: `gossipper_<pid>_stats.log`, no
`summary.json`.

## Peak concurrency rises at every step, on purpose

`detect_plateau` refuses a ramp where a step asked for more and got the same:
something stopped scaling, and until it is known whether that was the node or
the generator, the ceiling is not attributable. A fixture that plateaued would
prove the refusal rather than the derivation.

## The last step exceeds the CPU ceiling, also on purpose

That is what makes the figure derivable, and it is the same step that fails the
node CPU gate. **So this run's verdict is FAIL by construction.** Both are
correct: measuring capacity means pushing past 70%, and the acceptance criterion
says stay under it. They are two different runs and the report is right to
describe them differently. `../acceptance-500/` is the other one.

## It needs a plan file

`target_concurrency` is not in any artifact: it lives in the generator's
scenario file. Without it `derive_calls_per_node` refuses across a multi-step
ramp, because "no plateau detected" from a ramp that declared no targets means
the check never ran rather than that the ramp kept climbing. `plan.json` beside
this file is the operator declaring what each step asked for, and it is passed
with `--plan`. It is a declaration, not a measurement: the peak concurrency each
step REACHED still comes from the stat file.
