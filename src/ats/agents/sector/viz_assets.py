"""CSS + JS for the sector viz dashboard (`viz.py::render_html`).

Kept separate from data assembly (`viz.py::build_bundle`) so style/interaction can be
iterated without touching the data layer, and vice versa. The CSS is ported from the
user-approved mockup (`~/.claude/plans/elegant-petting-nygaard.md`'s prototype step)
essentially verbatim — colors, spacing and the dataviz status/categorical palette were
already reviewed and signed off; this file must not re-litigate them. Extended only
where the mockup's fixture data didn't need a rule that real data does (all four
source tiers, all five witness-stance categories, not just the two/three the sample
happened to touch).

The JS is new: the mockup was a static, single-layer, hand-authored page for a UI
review. This renders EVERY layer from one embedded JSON bundle and re-renders the five
panels when the reader clicks a different layer's die — the mockup never had to do
that.
"""

from __future__ import annotations

CSS = """
:root {
  color-scheme: light;
  --page:        #F3F5F7;
  --surface:     #FFFFFF;
  --surface-2:   #EEF1F4;
  --surface-3:   #E4E8EC;
  --ink-1:       #12161B;
  --ink-2:       #4B5561;
  --ink-3:       #8A929C;
  --line:        #DDE2E7;
  --line-strong: #C4CBD3;
  --accent:      #B86B25;
  --accent-ink:  #FFFFFF;
  --accent-wash: rgba(184,107,37,0.10);

  --good:      #0ca30c;
  --good-text: #006300;
  --warn:      #fab219;
  --warn-ink:  #3A2A00;
  --serious:   #ec835a;
  --serious-ink: #401A0A;
  --critical:  #d03b3b;

  --cat-1: #2a78d6;  /* 供给方 */
  --cat-2: #eb6834;  /* 客户方 */
  --cat-3: #1baf7a;  /* 第三方 */
  --cat-4: #eda100;  /* 同业 */
  --cat-5: #e87ba4;  /* 内部人 */

  --div-pos: #2a78d6;
  --div-neg: #e34948;
  --div-mid: #f0efec;

  --shadow: 0 1px 2px rgba(18,22,27,0.04), 0 8px 24px -12px rgba(18,22,27,0.12);
  --font-mono: ui-monospace, "SF Mono", "Cascadia Mono", "Roboto Mono", "JetBrains Mono", monospace;
  --font-sans: -apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI", Roboto, sans-serif;
}
@media (prefers-color-scheme: dark) {
  :root:where(:not([data-theme="light"])) {
    color-scheme: dark;
    --page:        #0B0E12;
    --surface:     #12161C;
    --surface-2:   #171C23;
    --surface-3:   #1D232B;
    --ink-1:       #EAEEF3;
    --ink-2:       #AAB4BF;
    --ink-3:       #707A86;
    --line:        #232A33;
    --line-strong: #313A45;
    --accent:      #E0A05C;
    --accent-ink:  #1B1207;
    --accent-wash: rgba(224,160,92,0.14);
    --good-text:   #0ca30c;
    --cat-1: #3987e5; --cat-2: #d95926; --cat-3: #199e70; --cat-4: #c98500; --cat-5: #d55181;
    --div-pos: #3987e5; --div-neg: #e66767; --div-mid: #383835;
    --shadow: 0 1px 2px rgba(0,0,0,0.3), 0 8px 28px -12px rgba(0,0,0,0.55);
  }
}
:root[data-theme="dark"] {
  color-scheme: dark;
  --page: #0B0E12; --surface: #12161C; --surface-2: #171C23; --surface-3: #1D232B;
  --ink-1: #EAEEF3; --ink-2: #AAB4BF; --ink-3: #707A86;
  --line: #232A33; --line-strong: #313A45;
  --accent: #E0A05C; --accent-ink: #1B1207; --accent-wash: rgba(224,160,92,0.14);
  --good-text: #0ca30c;
  --cat-1: #3987e5; --cat-2: #d95926; --cat-3: #199e70; --cat-4: #c98500; --cat-5: #d55181;
  --div-pos: #3987e5; --div-neg: #e66767; --div-mid: #383835;
  --shadow: 0 1px 2px rgba(0,0,0,0.3), 0 8px 28px -12px rgba(0,0,0,0.55);
}
:root[data-theme="light"] {
  color-scheme: light;
  --page: #F3F5F7; --surface: #FFFFFF; --surface-2: #EEF1F4; --surface-3: #E4E8EC;
  --ink-1: #12161B; --ink-2: #4B5561; --ink-3: #8A929C;
  --line: #DDE2E7; --line-strong: #C4CBD3;
  --accent: #B86B25; --accent-ink: #FFFFFF; --accent-wash: rgba(184,107,37,0.10);
  --good-text: #006300;
  --cat-1: #2a78d6; --cat-2: #eb6834; --cat-3: #1baf7a; --cat-4: #eda100; --cat-5: #e87ba4;
  --div-pos: #2a78d6; --div-neg: #e34948; --div-mid: #f0efec;
  --shadow: 0 1px 2px rgba(18,22,27,0.04), 0 8px 24px -12px rgba(18,22,27,0.12);
}

* { box-sizing: border-box; }
html, body { margin: 0; padding: 0; }
body {
  background: var(--page);
  color: var(--ink-1);
  font-family: var(--font-sans);
  font-size: 14px;
  line-height: 1.55;
  -webkit-font-smoothing: antialiased;
}
::selection { background: var(--accent-wash); }
:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; border-radius: 3px; }
button { font: inherit; color: inherit; }
@media (prefers-reduced-motion: reduce) {
  * { animation-duration: 0.001ms !important; transition-duration: 0.001ms !important; }
}

.shell { max-width: 1320px; margin: 0 auto; padding: 0 24px 64px; }

.topbar { background: var(--surface); border-bottom: 1px solid var(--line); padding: 22px 24px 20px; }
.topbar-inner { max-width: 1320px; margin: 0 auto; }
.topbar-row1 { display: flex; align-items: baseline; justify-content: space-between; gap: 16px; flex-wrap: wrap; margin-bottom: 14px; }
.topbar-title { display: flex; align-items: baseline; gap: 12px; flex-wrap: wrap; }
.topbar-title h1 { font-size: 21px; font-weight: 700; letter-spacing: -0.015em; margin: 0; text-wrap: balance; }
.topbar-meta { font-family: var(--font-mono); font-size: 12px; color: var(--ink-3); }
.search {
  display: flex; align-items: center; gap: 8px; background: var(--surface-2); border: 1px solid var(--line);
  border-radius: 8px; padding: 7px 12px; min-width: 220px; color: var(--ink-3);
}
.search input { border: none; background: none; outline: none; color: var(--ink-1); font-family: var(--font-sans); font-size: 13px; width: 100%; }
.search input::placeholder { color: var(--ink-3); }
.topbar-signals { display: flex; flex-direction: column; gap: 6px; }
.signal-line { display: flex; gap: 10px; align-items: flex-start; font-size: 13.5px; }
.signal-tag {
  font-family: var(--font-mono); font-size: 10px; font-weight: 700; letter-spacing: 0.06em; color: var(--ink-3);
  text-transform: uppercase; padding-top: 2px; flex: none; width: 52px;
}
.signal-line.is-warn .signal-tag { color: var(--serious); }
.signal-text { color: var(--ink-2); text-wrap: pretty; }
.signal-text strong { color: var(--ink-1); font-weight: 600; }
.signal-line.is-warn .signal-text { color: var(--serious-ink); }

.chainmap-wrap { padding: 18px 0 4px; }
.chainmap-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 10px; flex-wrap: wrap; }
.chainmap-title-group { display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap; }
.rail-label {
  font-family: var(--font-mono); font-size: 10.5px; font-weight: 600; letter-spacing: 0.08em;
  text-transform: uppercase; color: var(--ink-3);
}
.chainmap-sub { font-size: 12px; color: var(--ink-3); }
.toggle-btn {
  background: var(--surface); border: 1px solid var(--line); border-radius: 7px; padding: 5px 11px;
  font-size: 12px; font-weight: 600; color: var(--ink-2); cursor: pointer; display: flex; align-items: center; gap: 5px;
}
.toggle-btn:hover { border-color: var(--line-strong); color: var(--ink-1); }
.toggle-btn .car { transition: transform .15s ease; }
.chainmap-wrap[data-open="false"] .toggle-btn .car { transform: rotate(-90deg); }
.chainmap { position: relative; background: var(--surface); border: 1px solid var(--line); border-radius: 12px; padding: 18px 22px; overflow: hidden; }
.chainmap-wrap[data-open="false"] .chainmap { display: none; }
.cm-lanes { display: flex; flex-direction: column; gap: 4px; position: relative; z-index: 2; }
.cm-lane { display: grid; grid-template-columns: 172px 1fr; align-items: center; gap: 14px; min-height: 40px; }
.cm-lane-tag {
  font-family: var(--font-mono); font-size: 11px; font-weight: 700; color: var(--ink-2);
  display: flex; flex-direction: column; line-height: 1.25;
}
.cm-lane-tag b { color: var(--ink-1); font-size: 12px; font-family: var(--font-sans); font-weight: 650; }
.cm-nodes { display: flex; gap: 8px; flex-wrap: wrap; }
.cm-node {
  font-family: var(--font-mono); font-size: 11.5px; font-weight: 700; padding: 4px 10px; border-radius: 999px;
  background: var(--surface-2); border: 1px solid var(--line-strong); color: var(--ink-1); white-space: nowrap;
}
.cm-svg { position: absolute; inset: 0; width: 100%; height: 100%; z-index: 1; pointer-events: none; }
.cm-svg path { fill: none; stroke: var(--line-strong); stroke-width: 1.4; }
.cm-svg marker path { fill: var(--line-strong); stroke: none; }
.cm-foot { margin-top: 12px; font-size: 11.5px; color: var(--ink-3); }

.rail-wrap { padding: 18px 0 6px; }
.rail-wrap > .rail-label { margin-bottom: 10px; display: block; }
.rail {
  display: grid; grid-auto-flow: column; grid-auto-columns: minmax(148px, 1fr);
  gap: 10px; overflow-x: auto; padding-bottom: 4px; scroll-snap-type: x proximity;
}
.die {
  scroll-snap-align: start; background: var(--surface); border: 1px solid var(--line); border-radius: 10px;
  padding: 13px 13px 12px; text-align: left; cursor: pointer; display: flex; flex-direction: column; gap: 9px;
  transition: border-color .12s ease, transform .12s ease, box-shadow .12s ease;
}
.die:hover { border-color: var(--line-strong); transform: translateY(-1px); box-shadow: var(--shadow); }
.die[aria-pressed="true"] { border-color: var(--accent); box-shadow: 0 0 0 1px var(--accent); }
.die-key { font-family: var(--font-mono); font-size: 10.5px; color: var(--ink-3); }
.die-label { font-size: 13px; font-weight: 650; line-height: 1.3; min-height: 2.6em; }
.chip {
  display: inline-flex; align-items: center; gap: 4px; font-family: var(--font-mono); font-size: 11px; font-weight: 700;
  padding: 2px 7px; border-radius: 999px; width: fit-content; letter-spacing: 0.01em;
}
.chip[data-alloc="over"]   { background: var(--good); color: #fff; }
.chip[data-alloc="flat"]   { background: var(--surface-3); color: var(--ink-2); }
.chip[data-alloc="under"]  { background: var(--warn); color: var(--warn-ink); }
.chip[data-alloc="exit"]   { background: var(--critical); color: #fff; }
.die-conf { font-family: var(--font-mono); font-size: 11px; color: var(--ink-3); }
.budget-track { position: relative; height: 7px; border-radius: 4px; background: var(--surface-3); overflow: hidden; }
.budget-cap { position: absolute; inset: 0; background: var(--surface-3); }
.budget-fill { position: absolute; inset: 0 auto 0 0; border-radius: 4px; background: var(--accent); }
.die-foot { display: flex; justify-content: space-between; align-items: center; font-family: var(--font-mono); font-size: 11px; color: var(--ink-3); }
.die-flag { font-size: 10.5px; color: var(--serious); font-family: var(--font-sans); font-weight: 600; }

.scope-bar { display: flex; flex-direction: column; gap: 6px; padding: 20px 0 0; }
.scope-label { font-size: 13.5px; color: var(--ink-2); }
.scope-label strong { color: var(--ink-1); font-weight: 650; }
.cycle-why { font-size: 12.5px; }
.cycle-why summary {
  cursor: pointer; color: var(--accent); font-weight: 600; list-style: none; display: inline-flex; align-items: center; gap: 4px;
}
.cycle-why summary::-webkit-details-marker { display: none; }
.cycle-why .cw-open { display: none; }
.cycle-why[open] .cw-closed { display: none; }
.cycle-why[open] .cw-open { display: inline; }
.cycle-why-body {
  margin-top: 8px; padding: 10px 14px; background: var(--surface-2); border-radius: 8px; color: var(--ink-2);
  display: flex; flex-direction: column; gap: 5px; max-width: 68ch;
}
.cycle-why-body li { list-style: none; }
.cycle-why-body ul { margin: 0; padding: 0; display: flex; flex-direction: column; gap: 5px; }

.tabs { display: flex; gap: 2px; border-bottom: 1px solid var(--line); margin-top: 14px; }
.tab {
  background: none; border: none; padding: 10px 16px 11px; cursor: pointer; font-size: 13.5px; font-weight: 600;
  color: var(--ink-3); border-bottom: 2px solid transparent; margin-bottom: -1px; display: flex; align-items: center; gap: 6px;
}
.tab:hover { color: var(--ink-1); }
.tab[aria-selected="true"] { color: var(--ink-1); border-bottom-color: var(--accent); }
.tab .n { font-family: var(--font-mono); font-size: 10.5px; color: var(--ink-3); background: var(--surface-2); padding: 1px 5px; border-radius: 999px; }

.filters { display: none; gap: 8px; padding: 14px 0 0; flex-wrap: wrap; }
.filters[data-shown="true"] { display: flex; }
.filter-chip {
  background: var(--surface); border: 1px solid var(--line); border-radius: 999px; padding: 5px 12px;
  font-size: 12.5px; color: var(--ink-2); cursor: pointer; display: flex; align-items: center; gap: 6px;
}
.filter-chip:hover { border-color: var(--line-strong); color: var(--ink-1); }
.filter-chip[aria-pressed="true"] { background: var(--accent-wash); border-color: var(--accent); color: var(--ink-1); }
.filter-chip .dot { width: 6px; height: 6px; border-radius: 50%; background: var(--ink-3); }
.filter-chip[data-kind="supportive"] .dot { background: var(--good); }
.filter-chip[data-kind="contradicted"] .dot { background: var(--critical); }
.filter-chip[data-kind="insufficient"] .dot { background: none; border: 1.5px dashed var(--ink-3); }
.filter-chip[data-kind="cross"] .dot { background: var(--good); }
.filter-chip[data-kind="refute"] .dot { background: var(--critical); }
.filter-chip[data-kind="silent"] .dot { background: none; border: 1.5px dashed var(--ink-3); }

.focus-bar {
  display: none; align-items: center; gap: 10px; margin: 14px 0 -2px; padding: 8px 14px;
  background: var(--accent-wash); border: 1px solid var(--accent); border-radius: 8px; font-size: 12.5px; color: var(--ink-1);
}
.focus-bar[data-shown="true"] { display: flex; }
.focus-bar b { font-weight: 700; }
.focus-clear {
  margin-left: auto; background: none; border: 1px solid var(--line-strong); border-radius: 6px; padding: 2px 9px;
  font-size: 11.5px; color: var(--ink-2); cursor: pointer;
}
.focus-clear:hover { color: var(--ink-1); border-color: var(--ink-3); }

.panel { padding: 20px 0 8px; display: none; }
.panel[data-active="true"] { display: block; animation: fade .15s ease; }
@keyframes fade { from { opacity: 0; transform: translateY(2px);} to { opacity: 1; transform: none;} }

.section-title { font-size: 15px; font-weight: 650; margin: 0 0 12px; display: flex; align-items: center; gap: 8px; }
.section-sub { font-size: 12.5px; color: var(--ink-3); margin: -8px 0 14px; }
.card { background: var(--surface); border: 1px solid var(--line); border-radius: 10px; padding: 16px 18px; }
.stack { display: flex; flex-direction: column; gap: 14px; }
.grid-2 { display: grid; grid-template-columns: 1.15fr 0.85fr; gap: 16px; }
@media (max-width: 860px) { .grid-2 { grid-template-columns: 1fr; } }
.empty-note { font-size: 13px; color: var(--ink-3); padding: 4px 0; }

.formula { font-family: var(--font-mono); font-size: 13px; color: var(--ink-2); display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-bottom: 4px; }
.formula b { color: var(--ink-1); font-size: 15px; }
.formula .op { color: var(--ink-3); }
.note-line { font-size: 12.5px; color: var(--ink-3); margin-top: 8px; display: flex; gap: 6px; }
.note-line .lab { color: var(--ink-1); font-weight: 600; flex: none; }

table { width: 100%; border-collapse: collapse; font-size: 13px; }
.table-wrap { overflow-x: auto; }
th {
  text-align: left; font-family: var(--font-mono); font-size: 10.5px; font-weight: 600; letter-spacing: 0.05em;
  text-transform: uppercase; color: var(--ink-3); padding: 8px 12px; border-bottom: 1px solid var(--line); white-space: nowrap;
}
th.cn { font-family: var(--font-sans); font-size: 11.5px; text-transform: none; letter-spacing: 0; }
td { padding: 9px 12px; border-bottom: 1px solid var(--line); vertical-align: middle; }
tr:last-child td { border-bottom: none; }
td.num, th.num { font-family: var(--font-mono); font-variant-numeric: tabular-nums; text-align: right; }
.tk { font-family: var(--font-mono); font-weight: 650; }
.sub-tag { font-size: 11px; color: var(--ink-3); }
.stance-pill { display: inline-flex; align-items: center; gap: 5px; font-size: 12.5px; font-weight: 600; padding: 2px 9px; border-radius: 999px; }
.stance-pill[data-s="in"]  { background: var(--good); color: #fff; }
.stance-pill[data-s="hold"] { background: var(--surface-3); color: var(--ink-2); }
.stance-pill[data-s="out"] { background: var(--critical); color: #fff; }
.flag-self { color: var(--warn-ink); background: var(--warn); font-size: 10px; font-weight: 700; padding: 1px 5px; border-radius: 4px; margin-left: 6px; }

.trigger-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 8px; }
.trigger-list li { display: flex; gap: 10px; align-items: flex-start; font-size: 13px; color: var(--ink-2); }
.trigger-box { width: 15px; height: 15px; border: 1.5px solid var(--line-strong); border-radius: 4px; flex: none; margin-top: 2px; }

.claim-card { display: flex; flex-direction: column; gap: 10px; cursor: pointer; transition: border-color .12s ease; }
.claim-card:hover { border-color: var(--accent); }
.claim-card .goto { font-size: 11.5px; color: var(--accent); font-weight: 600; display: flex; align-items: center; gap: 4px; }
.claim-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; flex-wrap: wrap; }
.claim-statement { font-size: 14.5px; font-weight: 650; line-height: 1.4; max-width: 62ch; text-wrap: pretty; }
.verdict-badge {
  display: inline-flex; align-items: center; gap: 5px; font-family: var(--font-mono); font-size: 11.5px; font-weight: 700;
  padding: 3px 9px; border-radius: 999px; flex: none;
}
.verdict-badge[data-v="supportive"] { background: var(--good); color: #fff; }
.verdict-badge[data-v="resolved"]   { background: var(--accent); color: var(--accent-ink); }
.verdict-badge[data-v="mixed"]      { background: var(--warn); color: var(--warn-ink); }
.verdict-badge[data-v="contradicted"] { background: var(--critical); color: #fff; }
.verdict-badge[data-v="falsified"]  { background: var(--critical); color: #fff; }
.verdict-badge[data-v="unknown"]    { background: var(--surface-3); color: var(--ink-2); }

.basis-chip { display: inline-flex; align-items: center; gap: 5px; font-size: 11.5px; font-weight: 600; padding: 2px 8px; border-radius: 6px; color: var(--ink-2); border: 1px solid var(--line-strong); }
.basis-chip[data-b="corroborated"] { background: var(--surface-2); }
.basis-chip[data-b="self_reported"] { border-style: solid; }
.basis-chip[data-b="thin"] { border-style: dashed; color: var(--ink-3); }

.metric-row { display: flex; gap: 18px; flex-wrap: wrap; font-family: var(--font-mono); font-size: 12px; color: var(--ink-3); }
.metric-row b { color: var(--ink-1); font-weight: 650; }

.bidir { display: flex; align-items: center; gap: 10px; }
.bidir-track { flex: 1; height: 8px; display: flex; border-radius: 4px; overflow: hidden; background: var(--surface-3); }
.bidir-sup { background: var(--good); height: 100%; }
.bidir-ref { background: var(--critical); height: 100%; }
.bidir-label { font-family: var(--font-mono); font-size: 11px; color: var(--ink-3); white-space: nowrap; }

.silent-line { font-size: 12.5px; color: var(--serious-ink); display: flex; gap: 6px; align-items: baseline; }
.silent-line b { font-weight: 700; }

.entity-reading-list { display: flex; flex-direction: column; gap: 6px; margin-top: 2px; }
.entity-reading-row { display: flex; gap: 8px; align-items: baseline; font-size: 12.5px; color: var(--ink-2); flex-wrap: wrap; }
.entity-reading-row .tk { font-size: 12.5px; }
.entity-reading-row .standing { font-weight: 700; }
.entity-reading-row .standing[data-s="strong"] { color: var(--good-text); }
.entity-reading-row .standing[data-s="weak"] { color: var(--critical); }

.matrix { overflow-x: auto; }
.matrix table { min-width: 640px; }
.matrix td, .matrix th { text-align: center; }
.matrix .row-label { text-align: left; font-size: 12.5px; max-width: 220px; }
.mcell { width: 18px; height: 18px; border-radius: 5px; margin: 0 auto; display: flex; align-items: center; justify-content: center; font-size: 11px; font-weight: 700; }
.mcell[data-m="sup"] { background: var(--good); color: #fff; }
.mcell[data-m="ref"] { background: var(--critical); color: #fff; }
.mcell[data-m="neu"] { background: var(--surface-3); color: var(--ink-3); }
.mcell[data-m="silent"] { border: 1.5px dashed var(--line-strong); }

.stance-cols { display: grid; grid-template-columns: repeat(3, 1fr); gap: 14px; }
@media (max-width: 900px) { .stance-cols { grid-template-columns: 1fr; } }
.stance-col-head { display: flex; align-items: center; gap: 8px; margin-bottom: 10px; }
.stance-swatch { width: 9px; height: 9px; border-radius: 2px; }
.stance-col-title { font-size: 12.5px; font-weight: 700; letter-spacing: 0.01em; }
.stance-col-n { font-family: var(--font-mono); font-size: 11px; color: var(--ink-3); }
.evi-card { border-top: 3px solid transparent; margin-bottom: 10px; cursor: pointer; transition: transform .1s ease, box-shadow .1s ease; }
.evi-card:hover { transform: translateY(-1px); box-shadow: var(--shadow); }
.evi-card.is-dashed { border-top-style: dashed; border-top-color: var(--line-strong) !important; opacity: .8; cursor: default; }
.evi-card.is-dashed:hover { transform: none; box-shadow: none; }
.evi-card.no-trace { cursor: default; }
.evi-head { display: flex; justify-content: space-between; align-items: flex-start; gap: 8px; }
.evi-speaker { font-family: var(--font-mono); font-weight: 700; font-size: 13px; }
.evi-polarity { font-size: 15px; font-weight: 800; }
.evi-polarity[data-p="support"] { color: var(--good-text); }
.evi-polarity[data-p="refute"] { color: var(--critical); }
.evi-polarity[data-p="neutral"] { color: var(--ink-3); }
.evi-concept { font-family: var(--font-mono); font-size: 11px; color: var(--ink-3); margin: 3px 0 8px; }
.evi-reason { font-size: 13px; color: var(--ink-2); line-height: 1.5; }
.evi-foot { display: flex; gap: 12px; margin-top: 9px; font-family: var(--font-mono); font-size: 10.5px; color: var(--ink-3); }
.evi-cross-tag { font-size: 10.5px; font-weight: 700; color: var(--good-text); }

.trace-cluster { margin-bottom: 26px; }
.trace-cluster-head { display: flex; align-items: center; gap: 10px; margin-bottom: 12px; flex-wrap: wrap; }
.trace-cluster-swatch { width: 4px; align-self: stretch; border-radius: 2px; min-height: 20px; }
.trace-cluster-title { font-family: var(--font-mono); font-weight: 700; font-size: 13.5px; }
.trace-cluster-verdict { font-size: 12.5px; color: var(--ink-2); }
.quote-list { display: flex; flex-direction: column; gap: 10px; }
.quote-card { border-left: 3px solid var(--ink-3); padding: 10px 14px; background: var(--surface-2); border-radius: 0 8px 8px 0; }
.quote-card[data-stance="supplier"] { border-left-color: var(--cat-1); }
.quote-card[data-stance="customer"] { border-left-color: var(--cat-2); }
.quote-card[data-stance="regulator"] { border-left-color: var(--cat-3); }
.quote-card[data-stance="competitor"] { border-left-color: var(--cat-4); }
.quote-card[data-stance="incumbent"] { border-left-color: var(--cat-5); }
.quote-top { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; flex-wrap: wrap; }
.quote-stance-tag { font-family: var(--font-mono); font-size: 10px; font-weight: 700; padding: 1px 6px; border-radius: 4px; color: #fff; }
.quote-stance-tag[data-stance="supplier"] { background: var(--cat-1); }
.quote-stance-tag[data-stance="customer"] { background: var(--cat-2); }
.quote-stance-tag[data-stance="regulator"] { background: var(--cat-3); }
.quote-stance-tag[data-stance="competitor"] { background: var(--cat-4); }
.quote-stance-tag[data-stance="incumbent"] { background: var(--cat-5); }
.quote-conf { font-family: var(--font-mono); font-size: 10.5px; color: var(--ink-3); }
.quote-text { font-size: 13.5px; color: var(--ink-1); line-height: 1.6; font-style: italic; }
.quote-src {
  margin-top: 7px; font-family: var(--font-mono); font-size: 10.5px; color: var(--ink-3);
  display: flex; align-items: center; gap: 6px; flex-wrap: wrap; font-style: normal;
}
.quote-src .tier-mini {
  font-weight: 700; padding: 0 5px; border-radius: 3px; letter-spacing: .02em;
}
.quote-src .tier-mini[data-t="manual"] { background: var(--accent); color: var(--accent-ink); }
.quote-src .tier-mini[data-t="keyed"]  { background: var(--good); color: #fff; }
.quote-src .tier-mini[data-t="public"] { background: var(--surface-3); color: var(--ink-2); }
.quote-src .tier-mini[data-t="search"] { background: none; border: 1px solid var(--line-strong); color: var(--ink-3); }
.quote-src a { color: var(--ink-3); text-decoration: none; }
.quote-src a:hover { color: var(--accent); text-decoration: underline; }

.legend-note { font-size: 12.5px; color: var(--ink-3); margin-bottom: 12px; }
.xrow { cursor: pointer; }
.xrow:hover td { background: var(--surface-2); }
.xrow[aria-selected="true"] td { background: var(--accent-wash); }
.xrow[aria-selected="true"] .tk { color: var(--accent); }
.factor-label-row { display: flex; align-items: center; gap: 5px; }
.info-btn {
  width: 15px; height: 15px; border-radius: 50%; border: 1px solid var(--line-strong); background: var(--surface-2);
  color: var(--ink-3); font-family: var(--font-mono); font-size: 10px; font-weight: 700; line-height: 1; cursor: pointer;
  display: inline-flex; align-items: center; justify-content: center; flex: none; padding: 0;
}
.info-btn:hover, .info-btn[aria-expanded="true"] { color: var(--accent-ink); background: var(--accent); border-color: var(--accent); }
.div-bar-row { display: grid; grid-template-columns: 118px 1fr 46px; align-items: center; gap: 10px; margin-bottom: 9px; }
.div-bar-label { font-family: var(--font-mono); font-size: 12px; color: var(--ink-2); }
.div-bar-track { position: relative; height: 14px; background: var(--div-mid); border-radius: 3px; }
.div-bar-mid { position: absolute; left: 50%; top: -2px; bottom: -2px; width: 1px; background: var(--line-strong); }
.div-bar-fill { position: absolute; top: 1px; bottom: 1px; border-radius: 2px; transition: width .18s ease; }
.div-bar-fill.pos { background: var(--div-pos); left: 50%; }
.div-bar-fill.neg { background: var(--div-neg); right: 50%; }
.div-bar-val { font-family: var(--font-mono); font-size: 11.5px; text-align: right; font-variant-numeric: tabular-nums; }

.popover {
  position: fixed; z-index: 50; max-width: 300px; background: var(--surface); border: 1px solid var(--line-strong);
  border-radius: 8px; padding: 11px 13px; box-shadow: var(--shadow); font-size: 12.5px; color: var(--ink-2);
  line-height: 1.55; display: none;
}
.popover[data-shown="true"] { display: block; }
.popover b { color: var(--ink-1); }
.popover .pw { font-family: var(--font-mono); font-size: 11px; color: var(--ink-3); margin-top: 5px; display: block; }

.footnote { margin-top: 40px; padding-top: 16px; border-top: 1px solid var(--line); font-size: 12px; color: var(--ink-3); font-family: var(--font-mono); }
"""

# The embedded bundle is read from a `<script type="application/json" id="bundleData">`
# element rather than a `window.X = {...}` literal — sidesteps ever having to think
# about what a stray `</script>` inside real LLM-written prose would do to a literal.
JS = """
(function () {
  var DATA = JSON.parse(document.getElementById('bundleData').textContent);
  var state = { layerKey: (DATA.layers[0] || {}).key, tab: 'decision',
               filters: { claims: null, evidence: null }, focus: null };

  function esc(s) {
    if (s == null) { return ''; }
    return String(s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }
  function pct1(x) { return x == null ? '—' : (x * 100).toFixed(1) + '%'; }
  function pct0(x) { return x == null ? '—' : Math.round(x * 100) + '%'; }
  function num1(x) { return x == null ? '—' : Number(x).toFixed(1); }
  function conf2(x) { return x == null ? '—' : Number(x).toFixed(2); }
  function sigma(x) { return x == null ? '—' : (x >= 0 ? '+' : '') + Number(x).toFixed(1) + 'σ'; }

  var STANCE_CAT = { supplier: 1, customer: 2, regulator: 3, competitor: 4, incumbent: 5 };
  var STANCE_LABEL = { supplier: '供给方', customer: '客户方', regulator: '第三方',
                       competitor: '同业', incumbent: '当事方' };
  // cross_section.py's own weight tables, mirrored — no build step to share a Python
  // constant with the browser, so this file's docstring is the record that these two
  // must be kept in sync by hand.
  var QUANT_WEIGHTS = { growth: 0.25, quality: 0.20, value: 0.25, momentum: 0.10, revisions: 0.20 };
  var STRUCT_WEIGHTS = { tech_tenor: 0.20, moat_pricing: 0.20 };
  var BLENDED_WEIGHTS = {};
  Object.keys(QUANT_WEIGHTS).forEach(function (k) { BLENDED_WEIGHTS[k] = Math.round(QUANT_WEIGHTS[k] * 0.6 * 10000) / 10000; });
  Object.keys(STRUCT_WEIGHTS).forEach(function (k) { BLENDED_WEIGHTS[k] = STRUCT_WEIGHTS[k]; });
  var FACTOR_LABEL = { growth: '增长 growth', quality: '质量 quality', value: '估值 value',
    momentum: '动量 momentum', revisions: '评级修正', tech_tenor: '技术时间朝向', moat_pricing: '护城河/定价权' };
  var FACTOR_TEXT = {
    growth: '营收同比增速（YoY）。',
    quality: '毛利率与营业利润率的均值，衡量单位收入的留存质量。',
    value: 'PEG = 前瞻市盈率 ÷ 营收增速（增速封顶 60% 防止超高增速失真）。越低越好，记分时取负号。',
    momentum: '60 日价格涨跌幅。权重较低，避免行业集体回调时压低了恰好具备介入价值的标的。',
    revisions: '分析师净评级较最早可得月份（-3/-2/-1 月）的变化，范围 -4..+4。',
    tech_tenor: '结构分析师依据知识库判断的技术时间朝向，-2..+2（如光进铜退等 secular 位置）。',
    moat_pricing: '结构分析师依据知识库判断的护城河/份额/定价权/客户集中度，-2..+2。'
  };
  var FACTOR_ORDER = ['growth', 'quality', 'value', 'momentum', 'revisions', 'tech_tenor', 'moat_pricing'];

  function layerByKey(key) {
    for (var i = 0; i < DATA.layers.length; i++) { if (DATA.layers[i].key === key) { return DATA.layers[i]; } }
    return DATA.layers[0];
  }

  // ---------------------------------------------------------------- topbar
  function renderTopbar() {
    document.getElementById('pageTitle').textContent = DATA.meta.label + ' 产业链 · 层级分析看板';
    document.getElementById('asOfMeta').textContent = 'as_of ' + DATA.meta.as_of_display;
    var lines = [];
    if (DATA.meta.regime) {
      lines.push('<div class="signal-line"><span class="signal-tag">REGIME</span>' +
                 '<span class="signal-text">' + esc(DATA.meta.regime) + '</span></div>');
    }
    if (DATA.meta.rotation_advice) {
      lines.push('<div class="signal-line"><span class="signal-tag">轮动</span>' +
                 '<span class="signal-text">' + esc(DATA.meta.rotation_advice) + '</span></div>');
    }
    if (DATA.meta.missing_layers && DATA.meta.missing_layers.length) {
      lines.push('<div class="signal-line is-warn"><span class="signal-tag">缺口</span>' +
                 '<span class="signal-text">本轮未产出结论：' + esc(DATA.meta.missing_layers.join('、')) + '</span></div>');
    }
    if (DATA.meta.top_risks && DATA.meta.top_risks.length) {
      lines.push('<div class="signal-line is-warn"><span class="signal-tag">风险</span>' +
                 '<span class="signal-text">' + esc(DATA.meta.top_risks.join('；')) + '</span></div>');
    }
    document.getElementById('topbarSignals').innerHTML = lines.join('');
  }

  // ---------------------------------------------------------------- chainmap
  function renderChainmap() {
    if (!DATA.chainmap.lanes.length) {
      document.getElementById('chainmapWrap').style.display = 'none';
      return;
    }
    var html = DATA.chainmap.lanes.map(function (lane) {
      var nodes = lane.nodes.map(function (n) {
        return '<span class="cm-node" data-node="' + esc(n.id) + '">' + esc(n.symbol) + '</span>';
      }).join('');
      return '<div class="cm-lane" data-lane="' + esc(lane.short) + '">' +
             '<div class="cm-lane-tag">' + esc(lane.short) + '<b>' + esc(lane.short_label) + '</b></div>' +
             '<div class="cm-nodes">' + nodes + '</div></div>';
    }).join('');
    document.getElementById('cmLanes').innerHTML = html;
  }

  var CM_EDGES = DATA.chainmap.edges || [];
  function drawChainmapEdges() {
    var svg = document.getElementById('cmSvg');
    var container = document.getElementById('chainmap');
    if (!svg || !container || container.offsetParent === null) { return; }
    var cr = container.getBoundingClientRect();
    Array.prototype.slice.call(svg.querySelectorAll('path.edge')).forEach(function (p) { p.remove(); });
    var byId = {};
    Array.prototype.slice.call(container.querySelectorAll('[data-node]')).forEach(function (el) {
      byId[el.getAttribute('data-node')] = el;
    });
    function centerOf(id) {
      var el = byId[id];
      if (!el) { return null; }
      var r = el.getBoundingClientRect();
      return { x: r.left + r.width / 2 - cr.left, y: r.top + r.height / 2 - cr.top };
    }
    CM_EDGES.forEach(function (edge) {
      var a = centerOf(edge[0]), b = centerOf(edge[1]);
      if (!a || !b) { return; }
      var midY = (a.y + b.y) / 2;
      var d = 'M ' + a.x + ',' + (a.y + 8) + ' C ' + a.x + ',' + midY + ' ' + b.x + ',' + midY + ' ' + b.x + ',' + (b.y - 8);
      var path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
      path.setAttribute('d', d);
      path.setAttribute('class', 'edge');
      path.setAttribute('marker-end', 'url(#cmArrow)');
      svg.appendChild(path);
    });
  }

  // ---------------------------------------------------------------- die rail
  function renderRail() {
    var html = DATA.layers.map(function (ly) {
      var d = ly.die;
      var flags = (d.flags || []).map(function (f) { return '<div class="die-flag">⚠ ' + esc(f) + '</div>'; }).join('');
      return '<button class="die" data-layer="' + esc(ly.key) + '" role="tab" aria-pressed="' +
             (ly.key === state.layerKey ? 'true' : 'false') + '">' +
             '<span class="die-key">' + esc(ly.key) + '</span>' +
             '<span class="die-label">' + esc(d.label) + '</span>' +
             '<span class="chip" data-alloc="' + esc(d.alloc_class) + '">' + esc(d.allocation) + '</span>' +
             '<span class="die-conf">信心 ' + num1(d.confidence) + '</span>' +
             '<div class="budget-track"><div class="budget-cap"></div>' +
             '<div class="budget-fill" style="width:' + d.budget_pct_of_cap + '%"></div></div>' +
             '<div class="die-foot"><span>' + pct1(d.budget) + '</span><span>cap ' + pct0(d.cap) + '</span></div>' +
             flags + '</button>';
    }).join('');
    document.getElementById('rail').innerHTML = html;
    Array.prototype.slice.call(document.querySelectorAll('.die')).forEach(function (die) {
      die.addEventListener('click', function () { selectLayer(die.getAttribute('data-layer')); });
    });
  }
  function syncDieSelection() {
    Array.prototype.slice.call(document.querySelectorAll('.die')).forEach(function (die) {
      die.setAttribute('aria-pressed', die.getAttribute('data-layer') === state.layerKey ? 'true' : 'false');
    });
  }

  // ---------------------------------------------------------------- scope bar
  function renderScopeBar() {
    var ly = layerByKey(state.layerKey);
    document.getElementById('scopeLabel').textContent = ly.label + ' · 周期位置：' + ly.cycle_position;
    var body = ly.cycle_why.length
      ? '<ul>' + ly.cycle_why.map(function (l) { return '<li>' + esc(l) + '</li>'; }).join('') + '</ul>'
      : '<ul><li>本轮无可展示依据</li></ul>';
    document.getElementById('cycleWhyBody').innerHTML = body;
  }

  function updateTabCounts() {
    var ly = layerByKey(state.layerKey);
    var evCount = ly.evidence.stances.reduce(function (n, s) { return n + s.clusters.length; }, 0) + ly.evidence.silent.length;
    document.querySelector('.tab[data-tab="claims"] .n').textContent = ly.claims.length;
    document.querySelector('.tab[data-tab="evidence"] .n').textContent = evCount;
  }

  // ---------------------------------------------------------------- tabs / filters
  var tabs = Array.prototype.slice.call(document.querySelectorAll('.tab'));
  var panels = Array.prototype.slice.call(document.querySelectorAll('.panel'));
  var filterRows = Array.prototype.slice.call(document.querySelectorAll('.filters'));
  function activateTab(name) {
    state.tab = name;
    tabs.forEach(function (t) { t.setAttribute('aria-selected', t.dataset.tab === name ? 'true' : 'false'); });
    panels.forEach(function (p) { p.setAttribute('data-active', p.dataset.tab === name ? 'true' : 'false'); });
    filterRows.forEach(function (f) { f.setAttribute('data-shown', f.dataset.for === name ? 'true' : 'false'); });
  }
  tabs.forEach(function (t) { t.addEventListener('click', function () { activateTab(t.dataset.tab); }); });

  document.querySelectorAll('.filter-chip').forEach(function (chip) {
    chip.addEventListener('click', function () {
      var group = chip.closest('.filters').dataset.for;
      var kind = chip.dataset.kind;
      var siblings = chip.closest('.filters').querySelectorAll('.filter-chip');
      if (state.filters[group] === kind) {
        state.filters[group] = null;
        siblings.forEach(function (c) { c.setAttribute('aria-pressed', 'false'); });
      } else {
        state.filters[group] = kind;
        siblings.forEach(function (c) { c.setAttribute('aria-pressed', c === chip ? 'true' : 'false'); });
      }
      applyClaimFilter(); applyEvidenceFilter();
    });
  });
  function applyClaimFilter() {
    var kind = state.filters.claims;
    document.querySelectorAll('.panel[data-tab="claims"] .claim-card').forEach(function (card) {
      var v = card.dataset.verdict;
      var show = !kind ||
        (kind === 'supportive' && (v === 'supportive' || v === 'resolved')) ||
        (kind === 'contradicted' && (v === 'contradicted' || v === 'falsified')) ||
        (kind === 'insufficient' && (v === 'unknown' || v === 'mixed'));
      card.style.display = show ? '' : 'none';
    });
  }
  function applyEvidenceFilter() {
    var kind = state.filters.evidence;
    document.querySelectorAll('.panel[data-tab="evidence"] .evi-card').forEach(function (card) {
      var flags = (card.dataset.flags || '').split(' ').filter(Boolean);
      var show = !kind || flags.indexOf(kind) !== -1;
      card.style.display = show ? '' : 'none';
    });
  }
  function resetFilters() {
    state.filters = { claims: null, evidence: null };
    document.querySelectorAll('.filter-chip').forEach(function (c) { c.setAttribute('aria-pressed', 'false'); });
  }

  // ---------------------------------------------------------------- focus bar
  var focusBar = document.getElementById('focusBar');
  var focusText = document.getElementById('focusText');
  document.getElementById('focusClear').addEventListener('click', clearFocus);
  function clearFocus() {
    state.focus = null;
    focusBar.setAttribute('data-shown', 'false');
    document.querySelectorAll('.evi-card, .trace-cluster').forEach(function (el) { el.style.display = ''; });
    recomputeStanceCounts();
  }

  // Column headers ("供给方 30 簇") are written once for the WHOLE layer; a claim
  // focus hides most of a column's cards without touching that number, so a reader
  // sees "30 簇" over one visible card. Recompute from what is actually visible —
  // cheaper than re-rendering the panel, and correct in both directions (focus and
  // clear).
  function recomputeStanceCounts() {
    document.querySelectorAll('.stance-cols > div').forEach(function (col) {
      var head = col.querySelector('.stance-col-n');
      if (!head) { return; }
      var visible = Array.prototype.slice.call(col.querySelectorAll('.evi-card'))
        .filter(function (c) { return c.style.display !== 'none'; }).length;
      head.textContent = head.textContent.replace(/^\d+/, String(visible));
    });
  }

  // ---------------------------------------------------------------- ① 决策
  function renderDecision(ly) {
    var html = '<div class="grid-2"><div class="stack">' +
      '<div class="card"><div class="section-title">本层预算</div>' +
      '<div class="formula">' + esc(ly.budget_formula) + '</div>' +
      (ly.confidence != null ? '<div class="note-line"><span class="lab">信心 ' + num1(ly.confidence) +
       '</span>——对结论本身的把握程度，<b>不参与预算计算</b></div>' : '') + '</div>';

    html += '<div class="card"><div class="section-title">层内配置</div>';
    if (ly.name_calls.length) {
      html += '<div class="table-wrap"><table><thead><tr><th class="cn">代码</th><th class="cn">子层</th>' +
        '<th class="cn">观点</th><th class="num">权重</th><th class="num">排名</th></tr></thead><tbody>' +
        ly.name_calls.map(function (c) {
          return '<tr><td class="tk">' + esc(c.symbol) + (c.self_reported_only ? '<span class="flag-self">仅自述</span>' : '') +
                 '</td><td class="sub-tag">' + esc(c.subgroup || '—') + '</td>' +
                 '<td><span class="stance-pill" data-s="' + esc(c.stance_class) + '">' + esc(c.stance) + '</span></td>' +
                 '<td class="num">' + (c.weight != null ? pct1(c.weight) : '—') + '</td>' +
                 '<td class="num">' + (c.rank != null ? c.rank : '—') + '</td></tr>';
        }).join('') + '</tbody></table></div>';
    } else {
      html += '<div class="empty-note">本轮未产出层内配置。</div>';
    }
    html += '</div></div>';

    html += '<div class="card"><div class="section-title">反转触发条件</div>' +
      '<div class="section-sub">下一轮逐条核对</div>';
    if (ly.reversal_triggers.length) {
      html += '<ul class="trigger-list">' + ly.reversal_triggers.map(function (t) {
        return '<li><span class="trigger-box"></span><span>' + esc(t.text) + '</span></li>';
      }).join('') + '</ul>';
    } else {
      html += '<div class="empty-note">本轮未提出反转触发条件。</div>';
    }
    if (ly.candidate_claims.length) {
      html += '<div class="section-title" style="margin-top:18px;">候选追踪议题</div>' +
        '<div class="section-sub">分析师自发提出，尚未预设，不参与本期任何计算</div>' +
        ly.candidate_claims.map(function (c) {
          return '<div style="margin-bottom:10px;"><b>「' + esc(c.statement) + '」</b>' +
                 '<div class="section-sub" style="margin:4px 0 0;">谁能作证：' + esc(c.witnesses.join('、') || '—') +
                 ' · 证伪条件：' + esc(c.falsifier || '—') + '</div></div>';
        }).join('');
    }
    html += '</div></div>';
    document.querySelector('.panel[data-tab="decision"]').innerHTML = html;
  }

  // ---------------------------------------------------------------- ② 命题
  function claimCardHtml(c) {
    var badges = '<span class="basis-chip" data-b="' + esc(c.basis) + '">' + esc(c.basis_cn) + '</span>';
    var body = '';
    if (c.kind === 'common') {
      var total = c.support_score + c.refute_score;
      var supPct = total > 0 ? (c.support_score / total * 100) : 50;
      body += '<div class="metric-row"><span>证人覆盖 <b>' + esc(c.coverage) + '</b></span>' +
        '<span>独立证据簇 <b>' + c.evidence_clusters + '</b></span>' +
        '<span>立场类别 <b>' + c.stance_classes + '</b> 类</span></div>';
      body += '<div class="bidir"><span class="bidir-label">支持 ' + num1(c.support_score) + '</span>' +
        '<div class="bidir-track"><div class="bidir-sup" style="width:' + supPct + '%"></div>' +
        '<div class="bidir-ref" style="width:' + (100 - supPct) + '%"></div></div>' +
        '<span class="bidir-label">反驳 ' + num1(c.refute_score) + '</span></div>';
    } else {
      body += '<div class="entity-reading-list">' + c.entity_readings.map(function (r) {
        return '<div class="entity-reading-row"><span class="tk">' + esc(r.entity) + '</span>' +
          '<span class="standing" data-s="' + esc(r.standing) + '">' + esc(r.standing_cn) + '</span>' +
          '<span class="basis-chip" data-b="' + esc(r.basis) + '" style="padding:1px 6px;">' + esc(r.basis_cn) + '</span></div>';
      }).join('') + '</div>';
    }
    if (c.silent_witnesses.length) {
      body += '<div class="silent-line">⚠ <b>已声明但本期未发声：</b>' + esc(c.silent_witnesses.join('、')) + ' —— 这是缺口，不是中性</div>';
    }
    if (c.dissenters.length) {
      body += '<div class="silent-line">⚠ <b>具名例外：</b>' + esc(c.dissenters.join('、')) + '</div>';
    }
    return '<div class="card claim-card" data-claim-id="' + esc(c.claim_id) + '" data-verdict="' + esc(c.verdict) + '">' +
      '<div class="claim-head"><div class="claim-statement">「' + esc(c.statement) + '」</div>' +
      '<span class="verdict-badge" data-v="' + esc(c.verdict) + '">' + esc(c.verdict_mark) + '</span></div>' +
      '<div style="display:flex; gap:8px; flex-wrap:wrap;">' + badges + '</div>' + body +
      '<div class="goto">查看相关判读簇与观测 →</div></div>';
  }

  function matrixHtml(m) {
    if (!m.rows.length) { return ''; }
    var head = '<th class="row-label">命题</th>' + m.witnesses.map(function (w) { return '<th>' + esc(w) + '</th>'; }).join('');
    var body = m.rows.map(function (row) {
      var cells = m.witnesses.map(function (w) {
        var v = row.cells[w];
        if (!v) { return '<td></td>'; }
        var mark = v === 'sup' ? '＋' : v === 'ref' ? '－' : v === 'neu' ? '・' : '';
        return '<td><div class="mcell" data-m="' + v + '">' + mark + '</div></td>';
      }).join('');
      return '<tr><td class="row-label">' + esc(row.statement) + '</td>' + cells + '</tr>';
    }).join('');
    return '<div class="card"><div class="section-title">证人立场矩阵</div>' +
      '<div class="section-sub">虚线格 = 已声明证人本期未发声（沉默是缺口，不是中性，不留白）</div>' +
      '<div class="matrix"><table><thead><tr>' + head + '</tr></thead><tbody>' + body + '</tbody></table></div></div>';
  }

  function renderClaims(ly) {
    var panel = document.querySelector('.panel[data-tab="claims"]');
    if (!ly.claims.length) {
      panel.innerHTML = '<div class="empty-note">' + (ly.has_claims ?
        '本层有命题，但本期没有一条产出结论——这是证据缺口。' :
        '本层还没有配置命题——这是配置缺口，不是本季没人发声。') + '</div>';
      return;
    }
    panel.innerHTML = '<div class="stack">' + ly.claims.map(claimCardHtml).join('') + matrixHtml(ly.witness_matrix) + '</div>';
    panel.querySelectorAll('.claim-card').forEach(function (card) {
      card.addEventListener('click', function () { focusClaimToEvidence(card); });
    });
    applyClaimFilter();
  }

  function focusClaimToEvidence(card) {
    var claimId = card.getAttribute('data-claim-id');
    var statement = card.querySelector('.claim-statement').textContent;
    activateTab('evidence');
    document.querySelectorAll('.panel[data-tab="evidence"] .evi-card').forEach(function (evi) {
      evi.style.display = (evi.getAttribute('data-claim-id') === claimId) ? '' : 'none';
    });
    recomputeStanceCounts();
    state.focus = { kind: 'claim', id: claimId };
    focusText.innerHTML = '聚焦命题 · <b>' + esc(statement) + '</b> —— 只显示与它相关的判读簇';
    focusBar.setAttribute('data-shown', 'true');
  }

  // ---------------------------------------------------------------- ③ 证据
  function eviCardHtml(c) {
    var flags = [];
    if (c.cross) { flags.push('cross'); }
    if (c.polarity === 'refute') { flags.push('refute'); }
    var crossTag = c.cross ? ' <span class="evi-cross-tag">· 有交叉印证</span>' : '';
    var mark = c.polarity === 'support' ? '＋' : c.polarity === 'refute' ? '－' : '・';
    var clickable = c.has_trace;
    return '<div class="card evi-card' + (clickable ? '' : ' no-trace') + '" style="border-top-color:var(--cat-' +
      STANCE_CAT[c._stance] + ')" data-claim-id="' + esc(c.claim_id) + '" data-flags="' + flags.join(' ') + '"' +
      (clickable ? ' data-cluster-key="' + esc(c.cluster_key) + '"' : '') + '>' +
      '<div class="evi-head"><span class="evi-speaker">' + esc(c.speaker) + '</span>' +
      '<span class="evi-polarity" data-p="' + esc(c.polarity) + '">' + mark + '</span></div>' +
      '<div class="evi-concept">' + esc(c.concept) + crossTag + '</div>' +
      '<div class="evi-reason">' + esc(c.reason || '（无判读理由）') + '</div>' +
      '<div class="evi-foot"><span>' + c.n_observations + ' 条观测</span>' +
      (c.period_span ? '<span>' + esc(c.period_span) + '</span>' : '') + '</div></div>';
  }
  function silentCardHtml(s) {
    return '<div class="card evi-card is-dashed" data-flags="silent" data-claim-id="' +
      esc(s.claim_id) + '">' +
      '<div class="evi-head"><span class="evi-speaker" style="color:var(--ink-3)">' + esc(s.speaker) + '</span>' +
      '<span style="font-size:11px; color:var(--ink-3)">未发声</span></div>' +
      '<div class="evi-concept">「' + esc(s.statement) + '」（已声明证人）</div>' +
      '<div class="evi-reason" style="color:var(--ink-3)">本期未就此维度发声——按纪律计为缺口，不计为中性。</div></div>';
  }
  function renderEvidence(ly) {
    var panel = document.querySelector('.panel[data-tab="evidence"]');
    if (!ly.evidence.stances.length && !ly.evidence.silent.length) {
      panel.innerHTML = '<div class="empty-note">本层本期没有可展示的证据。</div>';
      return;
    }
    var cols = ly.evidence.stances.map(function (s) {
      var cards = s.clusters.map(function (c) { c._stance = s.key; return eviCardHtml(c); }).join('');
      return '<div><div class="stance-col-head"><span class="stance-swatch" style="background:var(--cat-' +
        STANCE_CAT[s.key] + ')"></span><span class="stance-col-title">' + esc(s.label) + '</span>' +
        '<span class="stance-col-n">' + s.clusters.length + ' 簇</span></div>' + cards + '</div>';
    });
    if (ly.evidence.silent.length) {
      cols.push('<div><div class="stance-col-head"><span class="stance-col-title">未发声</span>' +
        '<span class="stance-col-n">' + ly.evidence.silent.length + '</span></div>' +
        ly.evidence.silent.map(silentCardHtml).join('') + '</div>');
    }
    panel.innerHTML = '<div class="section-title">判读簇 · 按立场类别分组</div>' +
      '<div class="section-sub">形状本身即信息：全部簇挤在一栏 = 自说自话；跨栏分布 = 交叉验证。点击任一簇可下钻到原文。</div>' +
      '<div class="stance-cols">' + cols.join('') + '</div>';
    panel.querySelectorAll('.evi-card[data-cluster-key]').forEach(function (card) {
      card.addEventListener('click', function () { focusClusterToTrace(card); });
    });
    applyEvidenceFilter();
  }

  function focusClusterToTrace(card) {
    var clusterKey = card.getAttribute('data-cluster-key');
    if (!clusterKey) { return; }
    var speaker = card.querySelector('.evi-speaker').textContent;
    activateTab('trace');
    document.querySelectorAll('.trace-cluster').forEach(function (tc) {
      tc.style.display = (tc.getAttribute('data-cluster-key') === clusterKey) ? '' : 'none';
    });
    state.focus = { kind: 'cluster', id: clusterKey };
    focusText.innerHTML = '聚焦判读簇 · <b>' + esc(speaker) + '</b> —— 只显示这一簇的观测原文';
    focusBar.setAttribute('data-shown', 'true');
  }

  // ---------------------------------------------------------------- ④ 溯源
  function quoteCardHtml(q) {
    var link = q.source_url ? '<a href="' + esc(q.source_url) + '" target="_blank" rel="noopener">' +
      esc(q.source_url) + ' ↗</a>' : '';
    var localPath = q.local_path ? '<span>· ' + esc(q.local_path) + '</span>' : '';
    return '<div class="quote-card" data-stance="' + esc(q._stance) + '">' +
      '<div class="quote-top"><span class="quote-stance-tag" data-stance="' + esc(q._stance) + '">' +
      esc(STANCE_LABEL[q._stance] || q._stance) + '</span>' +
      '<span class="quote-conf">抽取置信度 ' + conf2(q.confidence) + '</span></div>' +
      '<div class="quote-text">"' + esc(q.text) + '"</div>' +
      '<div class="quote-src"><span class="tier-mini" data-t="' + esc(q.tier) + '">' + esc(q.tier.toUpperCase()) + '</span>' +
      '<span>' + esc(q.entity) + (q.period ? ' · ' + esc(q.period) : '') + (q.source ? ' · ' + esc(q.source) : '') + '</span>' +
      link + localPath + '</div></div>';
  }
  function traceClusterHtml(c) {
    var color = c.verdict_class === 'support' ? 'var(--good-text)' :
               c.verdict_class === 'refute' ? 'var(--critical)' : 'var(--ink-2)';
    var quotes = c.quotes.map(function (q) { q._stance = c.stance; return quoteCardHtml(q); }).join('');
    return '<div class="trace-cluster" data-cluster-key="' + esc(c.cluster_key) + '">' +
      '<div class="trace-cluster-head"><div class="trace-cluster-swatch" style="background:var(--cat-' +
      STANCE_CAT[c.stance] + ')"></div><div><div class="trace-cluster-title">' + esc(c.title) + '</div>' +
      '<div class="trace-cluster-verdict">判读簇结论：<b style="color:' + color + '">' + esc(c.verdict_text) +
      '</b></div></div></div><div class="quote-list">' + quotes + '</div></div>';
  }
  function renderTrace(ly) {
    var panel = document.querySelector('.panel[data-tab="trace"]');
    if (!ly.trace.clusters.length) {
      panel.innerHTML = '<div class="empty-note">本层本期没有可下钻的原文——引用的观测均缺少可展示文本。</div>';
      return;
    }
    panel.innerHTML = '<div class="section-title">从判读到原文</div>' +
      '<div class="section-sub">原文为主，来源文档退到每条观测下方的小字里；颜色 = 立场类别，与「证据」页一致</div>' +
      ly.trace.clusters.map(traceClusterHtml).join('');
  }

  // ---------------------------------------------------------------- ⑤ 截面
  function renderXsection(ly) {
    var panel = document.querySelector('.panel[data-tab="xsection"]');
    var xs = ly.xsection;
    if (!xs.rows.length) {
      panel.innerHTML = '<div class="empty-note">本层本期未产出截面：取数失败或本层无可排序标的。</div>';
      return;
    }
    var rowsHtml = xs.rows.map(function (r, i) {
      return '<tr class="xrow" aria-selected="' + (i === 0 ? 'true' : 'false') + '" data-sym="' + esc(r.symbol) +
        '" data-factors=\\'' + JSON.stringify(r.factors || {}).replace(/'/g, '&#39;') + '\\'>' +
        '<td class="tk">' + esc(r.symbol) + (r.data_ok ? '' : ' ⚠') + '</td>' +
        '<td class="sub-tag">' + esc(r.subgroup || '—') + '</td>' +
        '<td class="num">' + (r.rev_growth != null ? pct1(r.rev_growth) : '—') + '</td>' +
        '<td class="num">' + (r.gross_margin != null ? pct1(r.gross_margin) : '—') + '</td>' +
        '<td class="num">' + (r.peg != null ? num1(r.peg) : '—') + '</td>' +
        '<td class="num">' + (r.mom_60d != null ? num1(r.mom_60d) + '%' : '—') + '</td>' +
        '<td class="num">' + (r.composite >= 0 ? '+' : '') + num1(r.composite) + '</td>' +
        '<td class="num">' + pct1(r.weight) + '</td></tr>';
    }).join('');
    var applicNote = xs.applicable ? '' :
      '<div class="empty-note">⚠ 本层截面不适用：可比样本少于两个，名次不是发现，权重仍按单票限额落地。</div>';
    panel.innerHTML = '<div class="grid-2">' +
      '<div class="card"><div class="section-title">截面排序</div>' +
      '<div class="legend-note">z 分在整层统一计算；<code style="font-family:var(--font-mono)">复合分</code> = 各因子 z 分加权和，换一层不可比。点击某行查看右侧因子分解。</div>' +
      applicNote +
      '<div class="table-wrap"><table><thead><tr><th class="cn">代码</th><th class="cn">子层</th>' +
      '<th class="cn num">营收增速</th><th class="cn num">毛利率</th><th class="cn num">PEG</th>' +
      '<th class="cn num">60日动量</th><th class="cn num">复合分</th><th class="cn num">权重</th></tr></thead>' +
      '<tbody id="xrows">' + rowsHtml + '</tbody></table></div></div>' +
      '<div class="card"><div class="section-title">因子贡献分解 · <span id="xFactorSym">' +
      esc(xs.rows[0].symbol) + '</span></div><div id="xFactorBars"></div></div></div>';

    var xrows = panel.querySelectorAll('.xrow');
    xrows.forEach(function (row) {
      row.addEventListener('click', function () {
        xrows.forEach(function (r) { r.setAttribute('aria-selected', 'false'); });
        row.setAttribute('aria-selected', 'true');
        renderFactorBars(row.getAttribute('data-sym'), JSON.parse(row.getAttribute('data-factors')), xs.structural);
      });
    });
    renderFactorBars(xs.rows[0].symbol, xs.rows[0].factors || {}, xs.structural);
  }

  function renderFactorBars(sym, factors, structural) {
    document.getElementById('xFactorSym').textContent = sym;
    var keys = FACTOR_ORDER.filter(function (k) { return factors.hasOwnProperty(k); });
    var weights = structural ? BLENDED_WEIGHTS : QUANT_WEIGHTS;
    document.getElementById('xFactorBars').innerHTML = keys.length ? keys.map(function (key) {
      var val = factors[key];
      var w = Math.min(45, Math.abs(val) * 16);
      var cls = val >= 0 ? 'pos' : 'neg';
      return '<div class="div-bar-row" data-factor="' + key + '">' +
        '<span class="div-bar-label"><span class="factor-label-row">' + esc(FACTOR_LABEL[key] || key) +
        ' <button class="info-btn" data-factor="' + key + '" aria-expanded="false">!</button></span></span>' +
        '<div class="div-bar-track"><div class="div-bar-mid"></div>' +
        '<div class="div-bar-fill ' + cls + '" style="width:' + w + '%"></div></div>' +
        '<span class="div-bar-val">' + sigma(val) + '</span></div>';
    }).join('') : '<div class="empty-note">该标的数据不足，无因子分解。</div>';
    bindInfoButtons(weights);
  }

  var popover = document.getElementById('popover');
  var openInfoBtn = null;
  function bindInfoButtons(weights) {
    document.querySelectorAll('.info-btn').forEach(function (btn) {
      btn.addEventListener('click', function (e) {
        e.stopPropagation();
        var isOpen = btn.getAttribute('aria-expanded') === 'true';
        document.querySelectorAll('.info-btn').forEach(function (b) { b.setAttribute('aria-expanded', 'false'); });
        popover.setAttribute('data-shown', 'false');
        if (isOpen) { openInfoBtn = null; return; }
        var key = btn.getAttribute('data-factor');
        var w = weights[key];
        var wtxt = w == null ? '' : '权重 ' + Math.round(w * 100) + '%';
        popover.innerHTML = '<b>' + esc(FACTOR_LABEL[key] || key) + '</b> — ' + esc(FACTOR_TEXT[key] || '') +
          '<span class="pw">' + esc(wtxt) + '</span>';
        var r = btn.getBoundingClientRect();
        popover.style.left = Math.min(r.left, window.innerWidth - 316) + 'px';
        popover.style.top = (r.bottom + 6) + 'px';
        popover.setAttribute('data-shown', 'true');
        btn.setAttribute('aria-expanded', 'true');
        openInfoBtn = btn;
      });
    });
  }
  document.addEventListener('click', function () {
    if (openInfoBtn) { openInfoBtn.setAttribute('aria-expanded', 'false'); openInfoBtn = null; }
    popover.setAttribute('data-shown', 'false');
  });
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && openInfoBtn) {
      openInfoBtn.setAttribute('aria-expanded', 'false'); openInfoBtn = null;
      popover.setAttribute('data-shown', 'false');
    }
  });

  // ---------------------------------------------------------------- layer switch
  function selectLayer(key) {
    state.layerKey = key;
    resetFilters();
    clearFocus();
    var ly = layerByKey(key);
    syncDieSelection();
    renderScopeBar();
    updateTabCounts();
    renderDecision(ly);
    renderClaims(ly);
    renderEvidence(ly);
    renderTrace(ly);
    renderXsection(ly);
  }

  // ---------------------------------------------------------------- chain-map toggle
  var cmWrap = document.getElementById('chainmapWrap');
  var cmToggle = document.getElementById('chainmapToggle');
  if (cmToggle) {
    cmToggle.addEventListener('click', function () {
      var open = cmWrap.dataset.open === 'true';
      cmWrap.dataset.open = open ? 'false' : 'true';
      cmToggle.setAttribute('aria-expanded', String(!open));
      cmToggle.lastChild.textContent = open ? ' 展开' : ' 收起';
      if (!open) { requestAnimationFrame(drawChainmapEdges); }
    });
  }
  window.addEventListener('resize', function () { requestAnimationFrame(drawChainmapEdges); });

  // ---------------------------------------------------------------- search
  // Jumps to the first layer with a match and the tab that matched — this is a real
  // implementation of the interaction the design calls for, not the decorative input
  // the UI-review mockup shipped with (that prototype never needed it to work).
  var searchInput = document.getElementById('searchInput');
  if (searchInput) {
    searchInput.addEventListener('keydown', function (e) {
      if (e.key !== 'Enter') { return; }
      var q = searchInput.value.trim().toLowerCase();
      if (!q) { return; }
      for (var i = 0; i < DATA.layers.length; i++) {
        var ly = DATA.layers[i];
        var claimHit = ly.claims.some(function (c) { return c.statement.toLowerCase().indexOf(q) !== -1; });
        var xHit = ly.xsection.rows.some(function (r) { return r.symbol.toLowerCase().indexOf(q) !== -1; });
        var evHit = ly.evidence.stances.some(function (s) { return s.clusters.some(function (c) {
          return (c.speaker || '').toLowerCase().indexOf(q) !== -1 || (c.reason || '').toLowerCase().indexOf(q) !== -1;
        }); });
        if (claimHit || xHit || evHit) {
          selectLayer(ly.key);
          activateTab(claimHit ? 'claims' : xHit ? 'xsection' : 'evidence');
          return;
        }
      }
    });
  }

  // ---------------------------------------------------------------- init
  renderTopbar();
  renderChainmap();
  renderRail();
  activateTab('decision');
  if (DATA.layers.length) { selectLayer(state.layerKey); }
  requestAnimationFrame(function () { requestAnimationFrame(drawChainmapEdges); });
})();
"""
