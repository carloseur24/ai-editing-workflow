# Storytelling framework

The storyboard stage does not invent structure ad hoc. Pick a framework in
`brief.json → story.framework` (or `fenix storyboard <project> --framework NAME`)
and the stage produces that beat structure with proportional timing and a prompt
for each beat. `story.decisions` records the human choices.

## Which framework for which piece

| Piece | Default | Why |
|---|---|---|
| 15–60 s short / reel / performance ad | **hook_retain_reward** | Built on attention decay, not narrative; every beat has a job and a payoff. |
| 1–3 min brand / product film | **storybrand** | Customer is the hero, brand is the guide; keeps a CTA slot narrative models lose. |
| Explainer / tutorial | **explainer** | Optimised for comprehension: context → story → connections → steps → next. |
| Long-form narrative (5 min+) | **save_the_cat** | Percentage pacing is checkable; midpoint/loss beats keep a long cut honest. |
| Music video / mood film | **kishotenketsu** | No conflict required; the `ten` recontextualisation is the memorable turn. |
| 30 s TV / classic ad | **three_act** | Camera-native setup → confrontation → resolution. |
| Direct response, pain-first | **pas** | Problem → agitate → solve; high hook rate, use sparingly. |
| Transformation / testimonial | **bab** | Before → after → bridge; the delta is the proof. |
| Direct-response ad with a clear offer | **aida** | Attention → interest → desire → action; a clean four-beat funnel. |
| Short character arc (15–60 s) | **story_circle** | Comfort → want → unfamiliar → adapt → find → take → return → change. |
| Simple cause-and-effect story | **pixar_spine** | Once → every day → one day → because → until → ever since; the most teachable spine. |
| Character-led documentary | **hero** | Condensed Hero's Journey (Vogler); only with a real protagonist. |

`fenix storyboard` picks the default from `project_type`; override it explicitly
when the piece wants a different shape.

## Hook–Retain–Reward — 28 s, five scenes

This is the default for short-form. Hook ≤10 % of runtime; the mechanism is the
longest scene because retention lives there.

| # | Beat | Time | Job |
|---|---|---|---|
| 1 | hook | 0.0–2.5 | Pattern interrupt; signal who this is for. No logo, no setup. |
| 2 | problem | 2.5–6.5 | Name the status-quo cost in the viewer's words. |
| 3 | mechanism | 6.5–16.5 | Show the product doing the job; one demonstrable proof. |
| 4 | payoff | 16.5–24.0 | The after-state; emotional reward; optional social proof. |
| 5 | cta | 24.0–28.0 | One action, one line, no new information. |

The same logic scales to 15 s (compress problem/payoff) and 60 s (extend
mechanism with a second proof, never with features).

## How it binds to the harness

```json
"story": {
  "framework": "hook_retain_reward",
  "hook": "...",
  "beats": [],
  "cta": "...",
  "decisions": ["chose HRR over AIDA: retention matters more than desire"]
}
```

- Empty `beats` → the framework's beats are used, with weights and prompts.
- Custom `beats` (`[{purpose, weight, text, visual, audio, motion}]`) override
  the template while keeping the framework label.
- `output/<project>/storyboard.md` shows the framework and the timed table.
- The edit planner reads narration lines, not storyboard scenes; the framework
  shapes the copy and the order of proof, not the cut count.

## Attribution and limits

- **Story Spine** — created by **Kenn Adams (1991)**; popularised by Pixar.
  Credit Adams, not Pixar.
- **Hero's Journey** — Campbell (1949); the 12-stage version in circulation is
  **Vogler (1992)**. Do not present Campbell's 17 stages as the standard.
- **Save the Cat!** — a registered brand; cite “after Blake Snyder, *Save the
  Cat!* (2005)”. Do not reproduce book text or the BS2 template.
- **Story Circle** — Dan Harmon, Channel 101; paraphrase, link, never paste.
- **AIDA** — commonly attributed to E. St. Elmo Lewis (1898); the 1898 source is
  contested. Write “commonly attributed”.
- **Hook–Retain–Reward, PAS, BAB, three-act ad** — folk practice, no canonical
  author. Present as patterns, not citable methods.
- No framework here has published outcome data for vertical short-form. The
  short-form default rests on platform retention mechanics, not measured ad
  performance; treat it as a strong prior.

## Sources

- Hero's Journey / Vogler: <https://en.wikipedia.org/wiki/Hero%27s_journey>
- Harmon Story Circle: <https://channel101.fandom.com/wiki/Story_Structure_101:_Super_Basic_Shit>
- Save the Cat: <https://savethecat.com/get-started>
- Story Spine (Adams): <https://improvencyclopedia.org/games/Story_Spine.html>
- Explainer method (Common Craft): <https://commoncraft.com/explainer-tip-writing-explainer-video-script>
- Kishōtenketsu: <https://en.wikipedia.org/wiki/Kish%C5%8Dtenketsu>
- AIDA origin critique: <https://ojs.library.carleton.ca/index.php/pcharm/article/view/4340/3310>
- StoryBrand: <https://storybrand.com>
