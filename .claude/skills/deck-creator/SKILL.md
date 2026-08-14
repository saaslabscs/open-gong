---
name: "Deck Creator"
description: "Build quick pitch / proof-of-work slide decks as self-contained HTML Claude Artifacts, using this project's established scroll-snap deck pattern (numbered rail nav, paper/ink/accent palette, hero/receipt/beat/credits slide blocks). Use when asked to create a slide deck, add a slide to an existing deck, or restyle a pitch/demo/team-credits deck for this project."
---

# Deck Creator

## What this skill does

Open Gong's pitch decks (e.g. "Proof of Work") are single-file HTML pages published
as Claude Artifacts — not Google Slides, not a slide library. This skill captures the
structural pattern already proven in that deck so a new one (or a new slide in an
existing one) is fast to build and looks like it belongs next to the others.

This skill is about **structure and mechanics** — the scroll-snap shell, the rail nav,
the reusable slide blocks. It is **not** a substitute for the creative pass: always load
the `artifact-design` skill first for palette/typography/copy decisions, then come back
here for how to assemble the deck. Publish with the `Artifact` tool.

## Quick start

1. Load `artifact-design` and decide the deck's token system (color, type, one signature
   element) for *this* brief — don't reuse Proof of Work's blue/mono palette by default,
   pick deliberately.
2. Copy `resources/example-deck.html` as your starting point — it's a complete, working
   4-slide reference (hero → receipt → beats → credits) with the shell and script intact.
3. Swap the CSS custom properties in `:root` for your chosen palette, replace slide
   content, delete any of the four slide-block patterns you don't need.
4. Adding a slide is *only* two edits — the nav-highlight script is generic:
   - add `<a href="#five"><span class="dot"></span>05</a>` to `.rail`
   - add `<section class="slide" id="five">…</section>` before `</div>` closing `.deck`
5. Publish via the `Artifact` tool. Pick a favicon emoji and **do not change it on
   later redeploys of the same deck** — the tab icon is how the reader finds it again.

## The shell

Every deck is one `.deck` scroll-snap container of full-viewport `.slide` sections, plus
a fixed rail nav down the left edge with a numbered dot per slide:

```html
<nav class="rail" aria-label="Slides">
  <a href="#one" aria-current="true"><span class="dot"></span>01</a>
  <a href="#two"><span class="dot"></span>02</a>
</nav>

<div class="deck" id="deck">
  <section class="slide" id="one">
    <div class="slide-inner"> ... </div>
  </section>
  <section class="slide" id="two">
    <div class="slide-inner"> ... </div>
  </section>
</div>
```

```css
.deck { height: 100vh; overflow-y: auto; overflow-x: hidden; scroll-snap-type: y mandatory; }
.slide {
  min-height: 100vh; width: 100%; display: flex; flex-direction: column; justify-content: center;
  scroll-snap-align: start; position: relative; border-bottom: 1px solid var(--paper-line);
  padding: clamp(28px, 7vw, 96px);
}
.slide-inner { max-width: 1040px; width: 100%; margin: 0 auto; }

.rail {
  position: fixed; left: clamp(10px, 2vw, 28px); top: 50%; transform: translateY(-50%);
  display: flex; flex-direction: column; gap: 18px; z-index: 30;
  font-family: var(--font-mono); font-size: 0.72rem; color: var(--ink-faint);
}
.rail a { color: inherit; text-decoration: none; display: flex; align-items: center; gap: 8px; }
.rail .dot { width: 6px; height: 6px; border-radius: 50%; background: var(--paper-line); transition: background 0.2s, transform 0.2s; }
.rail a[aria-current="true"] { color: var(--accent); }
.rail a[aria-current="true"] .dot { background: var(--accent); transform: scale(1.6); }
@media (max-width: 720px) {
  .rail { left: 50%; top: auto; bottom: 14px; transform: translateX(-50%); flex-direction: row; gap: 22px; }
}
```

The active-slide highlight is generic — it reads whatever `.rail a` / `.slide` pairs
exist at load time, so new slides never need a script change:

```html
<script>
  (function () {
    var links = Array.prototype.slice.call(document.querySelectorAll('.rail a'));
    var sections = links.map(function (a) { return document.querySelector(a.getAttribute('href')); });
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (!entry.isIntersecting) return;
        var idx = sections.indexOf(entry.target);
        links.forEach(function (a, i) { a.setAttribute('aria-current', i === idx ? 'true' : 'false'); });
      });
    }, { root: document.getElementById('deck'), threshold: 0.6 });
    sections.forEach(function (s) { if (s) io.observe(s); });
  })();
</script>
```

## Token system

A CSS custom-property palette with paper/ink/accent roles, defined once on bare `:root`
(the light values), then overridden under `prefers-color-scheme: dark` and again under
an explicit `[data-theme="dark"]` so a manual toggle wins either way — this is the same
contract the `artifact-design` skill requires for any published Artifact:

```css
:root {
  --paper: #EDEEF3; --paper-raised: #E2E4EB; --paper-line: #C7CBD6;
  --ink: #10131A; --ink-soft: #4A5164; --ink-faint: #848AA0;
  --accent: #2E3EE8; --accent-deep: #1B2699; --accent-soft: rgba(46, 62, 232, 0.09);
  --font-mono: ui-monospace, "SF Mono", "Cascadia Code", "JetBrains Mono", "Roboto Mono", Menlo, Consolas, monospace;
  --font-sans: -apple-system, "Segoe UI", "Helvetica Neue", Roboto, Ubuntu, Arial, sans-serif;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --paper: #12141B; --paper-raised: #191C24; --paper-line: #2B2F3D;
    --ink: #ECEDF2; --ink-soft: #ABB0C0; --ink-faint: #676D80;
    --accent: #7482FF; --accent-deep: #B4BCFF; --accent-soft: rgba(116, 130, 255, 0.14);
  }
}
:root[data-theme="dark"] {
  --paper: #12141B; --paper-raised: #191C24; --paper-line: #2B2F3D;
  --ink: #ECEDF2; --ink-soft: #ABB0C0; --ink-faint: #676D80;
  --accent: #7482FF; --accent-deep: #B4BCFF; --accent-soft: rgba(116, 130, 255, 0.14);
}
```

Mono carries headings/labels/data (`h1`, `h2`, `.kicker`, receipt rows); system sans
carries body copy (`p.lede`, `.beat-body p`). Swap the palette values for a new deck's
brief — keep the *roles* (paper/paper-raised/paper-line/ink/ink-soft/ink-faint/accent),
not necessarily these exact hexes.

## Slide-content blocks

Four reusable patterns, used as needed — not every deck needs all four, and don't force
a block where it doesn't fit the content.

**Hero** — kicker + big statement + supporting line + a decorative stamp:
```html
<p class="kicker">Project Name — Deck Title</p>
<div class="hero-row">
  <h1>The big claim,<br>set across<br>a few short lines.</h1>
  <div class="seal" aria-hidden="true"><div class="seal-text">Verified<br>Every<br>Claim</div></div>
</div>
<p class="lede">One or two sentences of supporting context, longer-form than the h1.</p>
```
The `.seal` is a rotated circle-in-circle stamp (see `resources/example-deck.html` for
the full CSS incl. its `seal-in` entrance keyframe) — reuse it whenever a claim wants a
"this has been checked" visual; it's the deck's own signature move, don't reuse it as
generic decoration.

**Receipt** — dotted-fill label/value rows for factual, checkable claims (a tech stack,
a pricing breakdown, a spec sheet):
```html
<div class="receipt">
  <div class="receipt-head"><span>PROJECT NAME</span><span>RECEIPT No. 0001</span></div>
  <hr>
  <div class="receipt-group">
    <div class="receipt-group-label">Category label</div>
    <div class="receipt-row"><span>Item name</span><span class="fill"></span><span class="value">detail<span class="verified-tag">checked ✓</span></span></div>
    <p class="receipt-note">One line of context nobody would otherwise know to ask.</p>
  </div>
  <hr>
  <div class="receipt-total"><span>Total</span><span>the one-line takeaway</span></div>
</div>
```
`.fill` is a `flex: 1 1 auto` element with a dotted bottom border — it's what makes the
label and value look typeset like an actual receipt regardless of how long either is.

**Beats** — numbered narrative steps, **only when the order is real information** (a
before/now/what-changed story, a three-act pitch). Don't number a list that isn't
actually sequential — that's decoration pretending to be structure:
```html
<div class="beat">
  <div class="beat-num">01</div>
  <div class="beat-body">
    <h3>The problem in one line</h3>
    <p>A paragraph of supporting detail.</p>
  </div>
</div>
```

**Credits** — a team/contributors slide, human and non-human entries side by side:
```html
<div class="team-grid">
  <div class="team-card">
    <div class="team-avatar is-mark" aria-hidden="true">◆</div>
    <div class="team-name">Non-human contributor</div>
    <div class="team-role">Role</div>
  </div>
  <div class="team-card">
    <img class="team-avatar" src="data:image/jpeg;base64,…" alt="Person's name">
    <div class="team-name">Person's name</div>
    <div class="team-role">Role</div>
    <p class="team-note">Optional italic aside.</p>
  </div>
</div>
```
`.team-avatar` is a 96px circle, `object-fit: cover` for real photos; `.is-mark` swaps in
a flat accent-bordered circle with a centered glyph for anything without a photo (an AI
collaborator, a placeholder role) — never fabricate or guess a stand-in photo for a real
person who doesn't have one available.

## Embedding real photos

Artifacts run under a strict CSP that blocks external image requests — every photo must
be inlined as a `data:` URI, and the whole published file has a 16MB budget. Source
photos (phone camera photos, Slack avatars) are almost always bigger than a slide needs.
Downscale and compress before inlining, e.g. with macOS `sips` (no extra install needed):

```bash
sips -Z 320 -s format jpeg -s formatOptions 82 source.png --out photo.jpg
base64 -i photo.jpg -o photo.b64   # inline this string as data:image/jpeg;base64,<contents>
```

320px at JPEG quality ~80 is plenty for a 96px circular avatar and typically lands well
under 50KB — two or three team photos at that size add negligible weight to the deck.

Only use a real person's actual photo if it's been supplied directly by the user or
comes from an internal, authorized source they have access to (e.g. company Slack via
an already-connected integration) for their own team's deck — never source a photo of a
private individual from open-web search.

## Reference file

`resources/example-deck.html` is a complete, working deck built from this project's
real data rather than placeholder content: the Atlas Freight voice-agent quote (hero →
receipt → close), using the exact line items an agent already produced for that call —

- Voice agent usage: 10,000 min/mo × $0.45/min = $4,500.00/mo
- Platform licenses: 10 users × $69/user/mo = $690.00/mo
- Total: $5,190.00/month

— each row noted with the transcript line it came from, the same evidence-linking this
project's guaranteed-baseline pipeline does everywhere else. This is what a "deck
creator" skill dispatch (see an `AgentRun.output` with a `*-quote-deck` step name, e.g.
`GET /api/calls/541d9553f06548638349f06cecf4a223`) is meant to turn into an actual
artifact. Copy this file wholesale as a starting point for the next one, and carry the
same discipline forward: every number on the slide should trace back to a real quote,
call, or dataset — never invented to fill space.
