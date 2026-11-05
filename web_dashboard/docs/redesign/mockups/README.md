# Redesign mockups

These are the source files of the design canvas the redesign was drawn on.

They are **reference markup, not app code**. Each file is a self-contained
"Design Component" page that needs the canvas runtime (`support.js`) to render,
so opening one directly in a browser will not show the design. Read them for:

- exact colors, spacing, font sizes and radii (all inline `style="…"`)
- panel structure and the order of sections on each page
- the sample data shape each panel expects (the `renderVals()` block at the bottom)

| File | Page in the app | Spec section |
| --- | --- | --- |
| `Main.dc.html` | Live → Overview | REDESIGN.md § Live |
| `Phone.dc.html` | Live, phone width | REDESIGN.md § Mobile |
| `Decisions.dc.html` | Decisions (Ledger + Autopsy merged) | REDESIGN.md § Decisions |
| `ResearchExpectancy.dc.html` | Research → Expectancy | REDESIGN.md § Research |
| `ResearchNewsLag.dc.html` | Research → News lag | REDESIGN.md § Research |
| `ResearchBacktests.dc.html` | Research → Backtests | REDESIGN.md § Research |
| `ResearchFollowers.dc.html` | Research → Followers | REDESIGN.md § Research |
| `ResearchRollout.dc.html` | Research → V2 rollout | REDESIGN.md § Research |
| `Palette.dc.html` | Color tokens (A applied, B alternative) | REDESIGN.md § Tokens |

Values in square brackets in the mockups (`[n]`, `[price]`, `[verdict]`) are
placeholders for data that was not visible when the mockups were drawn. The
spec says which API field fills each one.
