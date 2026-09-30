# SuperFoundry design tokens

`tokens.css` is copied unchanged from the SuperFoundry Design System artifact
(https://claude.ai/artifact/BbojWrkvrf65oxomiaZzjq, file `project/tokens.css`, version 1790638418-654e,
copied 2026-09-30). Replace it with a newer copy from the same place rather than editing it here.

`style.css` uses its azure scale (`--sf-azure-*`) for every blue on the site. Nothing else from the
system is applied yet: the site keeps its own neutrals, type (Georgia / system sans) and ember is unused.
The `@font-face` rules in `tokens.css` point at font files that are not copied here; browsers only
fetch a font when a rule uses it, and none does.
