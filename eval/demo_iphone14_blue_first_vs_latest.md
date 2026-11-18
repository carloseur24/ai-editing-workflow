# demo_iphone14_blue — first output vs. latest output

Measured 2026-11-15. Source of truth: the two run directories and
`eval/demo_iphone14_blue_20261115-190436.json` (the report produced by
`eval_run.py --baseline output/demo_iphone14_blue`, i.e. against the flat v1
render).

| | first output (v1) | latest output (v3) |
|---|---|---|
| File | `output/demo_iphone14_blue/demo_iphone14_blue_final.mp4` | `output/demo_iphone14_blue/demo_iphone14_blue_20261115-185938/demo_iphone14_blue_20261115-185938_final.mp4` |
| Plan | auto-planned by the renderer | `plan_edit.py` from `narration_lines.json` + `candidates.json` |
| Duration | 28.023 s | 28.023 s |
| Cuts | 16 | 16 |
| Size | 13.459 MB | 13.535 MB |
| Loudness / true peak | −14.35 LUFS / −1.35 dBTP | −14.34 LUFS / −1.32 dBTP |
| Caption CPS (mean / max) | 19.19 / 28.4 | 19.19 / 28.4 |
| Beat offset (mean / max) | 2.0 / 3.3 ms | 0.2 / 0.5 ms |
| In-point sharpness (min / mean × clip median) | 1.000 / 1.658 | 0.932 / 1.628 |
| **Whole-shot sharpness (min / mean)** | **0.836 / 1.139** | **0.957 / 1.423** |
| **Share of each shot under the floor (mean)** | **7.0 %** | **0.9 %** |
| QA checks | 27 (1 warning: legacy flat path) | 29 (0 errors, 0 warnings) |
| Clip identity mismatches | 0 / 16 | 0 / 16 |
| SSIM vs. first output | 1.0 | 0.836 (different shot selection, so a full-frame diff *must* move) |

## What actually changed on screen

* **The shots hold their focus.** v1 scored only the first frame of each shot, so
  several cuts opened sharp and went soft for the rest of the shot (worst shot
  averaged 0.84× its clip median, 7 % of all sampled shot frames below the 0.60
  floor). v3 scores every window over its whole duration, and the worst shot now
  averages 0.96× with 0.9 % soft frames.
* **Every cut lands on the beat.** Boundaries are chosen by beat *index* on the
  measured 118.97 BPM grid instead of snapping a proportional time and then
  clamping it, so the maximum offset fell from 3.3 ms to 0.5 ms.
* **The edit follows the narration line by line.** Each of the 15 script lines
  owns one or two cuts (`narration_lines.json` → `plan_edit.py`), instead of the
  renderer distributing shots by a generic scoring pass.
* **No shot repeats more than twice**, decided in the planner rather than by a
  rotation counter.

## The trade-off that is visible in the numbers

The in-point-only ratio is slightly lower (1.000 → 0.932, still far above the
0.60 floor). That is the intended trade: v1 optimised the single frame at the
cut, v3 optimises the whole shot and accepts a marginally less peak-sharp first
frame in exchange for a shot that stays sharp. This is why `eval_run.py` gates
sharpness with **absolute floors** (`in_point_min_ratio`, `window_min_ratio`)
and compares sustained quality directionally (`window_mean_ratio`,
`window_soft_frac`).

## Intermediate run (kept, never overwritten)

`output/demo_iphone14_blue/demo_iphone14_blue_20261115-185022/` was the first
versioned render (beat grid fixed, shot-window scoring not yet in place). It
passed QA 27/27 but its shot mean was 1.057 and its soft share 8.7 % — worse than
v1 in sustained sharpness, which is what motivated the window scoring that v3
ships. It stays in `runs.json` as history.
