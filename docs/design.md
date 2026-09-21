# Design system — Lead Discovery Radar

Spec: `specs/v0.9.0.md`. This document is the source of truth for *how the app looks*, the way
the blueprint is the source of truth for *why it exists*. It records the tokens, the type
scale, the component inventory, the reasoning behind the review layout, and what was
rejected. Nothing here changes behaviour: v0.9.0 is a presentation-only spec.

## Who this is for

An internal evidence tool used by the team at **Flex The Brand Studio**. Its whole job is to
let a reviewer decide *approve or reject* in under two minutes, with the evidence in front of
them. The personality is **calm instrument**, not marketing dashboard: quiet surfaces, strong
hierarchy, evidence given typographic weight.

> **Client brand assets.** The spec's human prerequisite — "may we use the brand colours and
> typefaces from flextbs.com?" — had not been answered when this was built, so the app uses
> the internal palette and open typefaces below, which deliberately do **not** imitate
> flextbs.com. If the client later sends a brand guide, change the values in
> `frontend/src/app/globals.css` and the table in this file; nothing else should need to move,
> because no component names a colour of its own.

## Principles

1. **Evidence gets the distinct typeface.** Anything quoted from a business's website — the
   evidence text, URLs, scores, IDs — is set in the mono face. Our own wording is the sans.
   A reviewer can tell at a glance what came from the site and what is the tool talking.
2. **One accent colour.** Severity and status are the only other things allowed to carry
   meaning in colour, and each also carries a word, never colour alone.
3. **Quiet by default.** Nothing animates unless it answers a click.
4. **Every screen answers "what do I do next"** in its empty state.

## Colour tokens

Defined once as CSS custom properties on `:root` in `frontend/src/app/globals.css` and exposed
to Tailwind in `frontend/tailwind.config.ts`. The Tailwind colour palette is **replaced**, not
extended: `bg-slate-50` and friends no longer exist, so a component cannot quietly reach past
the system. `frontend/src/test/tokens.test.ts` fails the build if a component names a raw hex
colour, a raw font size or a raw radius.

| Token | Value | Use |
|---|---|---|
| `--ink` | `#101826` | primary text, top bar |
| `--ink-soft` | `#475467` | secondary text, labels, table headers |
| `--paper` | `#FBFAF8` | app background |
| `--surface` | `#FFFFFF` | cards, table rows, inputs |
| `--line` | `#E4E7EC` | borders, rules, dividers |
| `--accent` | `#0F766E` | primary actions, active nav, focus ring, links |
| `--accent-strong` | `#0B5C55` | hover/active of a primary action |
| `--accent-tint` | `#E9F3F2` | the one accent-tinted surface (selected row) |
| `--warn` | `#B54708` | medium severity, "held", attention |
| `--warn-tint` | `#FEF0E6` | 10 % tint behind a warning |
| `--risk` | `#B42318` | high severity, errors, destructive |
| `--risk-tint` | `#FEF0EF` | 10 % tint behind an error |
| `--ok` | `#027A48` | success, "in CRM", healthy |
| `--ok-tint` | `#E8F7EF` | 10 % tint behind a success |

No gradients. No coloured card backgrounds except the severity and status tints above.

### Contrast audit

Every pair below is a pair the UI actually puts text on, measured with the WCAG 2.1 relative
luminance formula. `frontend/src/lib/contrast.ts` implements the formula and
`frontend/src/lib/contrast.test.ts` asserts every row, so a later token change cannot silently
drop below the floor. Floor: **4.5:1** for body text, **3:1** for text ≥ 21 px.

| Foreground | Background | Ratio | Floor | Pass |
|---|---|---|---|---|
| `--ink` | `--paper` | 17.05 | 4.5 | ✅ |
| `--ink` | `--surface` | 17.79 | 4.5 | ✅ |
| `--ink-soft` | `--paper` | 7.37 | 4.5 | ✅ |
| `--ink-soft` | `--surface` | 7.69 | 4.5 | ✅ |
| `--accent` | `--surface` | 5.47 | 4.5 | ✅ |
| `--accent` | `--paper` | 5.25 | 4.5 | ✅ |
| `--surface` | `--accent` | 5.47 | 4.5 | ✅ |
| `--surface` | `--accent-strong` | 7.85 | 4.5 | ✅ |
| `--surface` | `--ink` | 17.79 | 4.5 | ✅ |
| `--ink` | `--accent-tint` | 15.16 | 4.5 | ✅ |
| `--warn` | `--surface` | 5.43 | 4.5 | ✅ |
| `--warn` | `--warn-tint` | 4.86 | 4.5 | ✅ |
| `--ink` | `--warn-tint` | 15.94 | 4.5 | ✅ |
| `--risk` | `--surface` | 6.57 | 4.5 | ✅ |
| `--risk` | `--risk-tint` | 5.92 | 4.5 | ✅ |
| `--ink` | `--risk-tint` | 16.03 | 4.5 | ✅ |
| `--ok` | `--surface` | 5.41 | 4.5 | ✅ |
| `--ok` | `--ok-tint` | 4.89 | 4.5 | ✅ |
| `--ink` | `--ok-tint` | 16.08 | 4.5 | ✅ |

`--line` is never a text colour; it only draws 1 px rules, so it is not in the table.

## Typography

Two families, clearly distinct, both open-licensed and **self-hosted** from
`frontend/public/fonts/` (latin subset, `font-display: swap`). No external font CDN: the
Content-Security-Policy keeps `font-src 'self'`.

| Role | Family | Where |
|---|---|---|
| Display / UI | **Public Sans** (OFL) | everything the tool says in its own voice |
| Evidence / data | **IBM Plex Mono** (OFL) | quoted evidence, URLs, scores, IDs, counts, raw codes |

Scale — six sizes, no others:

| Token | Size | Line height | Use |
|---|---|---|---|
| `text-xs` | 12 px | 1.5 | table meta, timestamps, help text |
| `text-sm` | 13 px | 1.5 | labels, secondary text, dense table cells |
| `text-base` | 15 px | 1.5 | body, table cells, inputs, buttons |
| `text-md` | 17 px | 1.4 | card and section headings (`h2`/`h3`) |
| `text-lg` | 21 px | 1.2 | page headings (`h1`) |
| `text-xl` | 27 px | 1.2 | the sign-in wordmark, the only place it is used |

Rules that follow from the scale:

- **Sentence case everywhere.** No tracked-out all-caps labels, no `LABEL —` eyebrows above a
  heading, no `·`-joined meta strings. Meta goes on its own line or in its own table cell.
- **Measure ≤ 72 characters** for any running prose (`max-w-measure`).
- Numbers that line up in a column use `font-mono` with `tabular-nums`.

## Space, radius, elevation, motion

- **Space:** a 4 px grid. Tailwind's default spacing scale is already 4 px-based and is kept;
  components use `gap-*`/`p-*` steps only.
- **Radius:** `rounded` = 6 px for controls (buttons, inputs, chips), `rounded-lg` = 10 px for
  surfaces (cards, dialogs, tables). `rounded-full` for a dot or a pill. Nothing else.
- **Elevation:** exactly one shadow, `shadow-overlay`, and only overlays use it (dialog,
  toast). Cards are separated by `--line`, never by a shadow.
- **Motion:** one duration (120 ms) and one easing, only on colour and opacity, only in answer
  to a click or hover. Under `prefers-reduced-motion: reduce` every transition and animation is
  switched off globally in `globals.css` (including the skeleton shimmer, which becomes a flat
  tint).

## Component inventory

One implementation each, in `frontend/src/components/ui/`, each with a test in the same folder.

| Component | Notes |
|---|---|
| `Button` | variants `primary` / `secondary` / `danger` / `ghost`; sizes `sm` / `md`; `loading` shows a busy label and sets `aria-busy`, and disables |
| `Input`, `Textarea`, `Select`, `Checkbox` | always label-bound (`Field` wraps label + control + hint + error); invalid sets `aria-invalid` |
| `Field` | the label/hint/error wrapper every form control uses |
| `Table` | `Table`/`THead`/`Th`/`Td`/`Tr`; `Th numeric` gives right-aligned tabular figures; `Th sortable` renders a real button with `aria-sort`; the wrapper scrolls horizontally, the page never does |
| `Card` | a surface with `--line` and 10 px radius |
| `Badge` | status word in a tint; tones `neutral` / `accent` / `ok` / `warn` / `risk` |
| `SeverityDot` | the coloured dot in a findings list; always next to the severity word, never alone |
| `Chip` | a small selectable/among-many token (services, findings) |
| `Dialog` | focus trap, Esc closes, focus restored to the opener, `aria-modal`, labelled by its heading |
| `Toast` | `role="status"`, polite live region, one optional action, dismissable |
| `Tabs` | roving focus, arrow keys, `aria-selected`, `role="tablist"` |
| `Disclosure` | `aria-expanded`, `aria-controls`, animates only opacity |
| `Tooltip` | hover **and** focus, Esc dismisses, `aria-describedby`; never the only place a fact lives |
| `Skeleton` | fixed-height blocks matching the real content, so nothing shifts when data lands |
| `EmptyState` | heading, one sentence, and the next action as a link or button |
| `PageHeader` | `h1`, one sentence of description, and an actions slot |
| `Pagination` | the "load more" / count row under a table |

## The review detail layout

**Before:** three columns of equal weight — business facts, website audit, and a stack of
opportunity cards — each a boxed panel. Everything looked equally important, the decision
buttons were the third thing your eye reached, and six findings meant six stacked cards.

**After:** a two-column argument.

```
┌──────────────────────────────────────────────┬──────────────────────┐
│ Business name              Austin, TX   [DNC]│  Opportunities       │
│ phone · website · industry                   │  ┌────────────────┐  │
├──────────────────────────────────────────────┤  │ Website redesign│ │
│ What the audit found                         │  │ score 78        │ │
│  ● No HTTPS            evidence…             │  │ [Approve][Reject]│ │
│  ● Not mobile-friendly evidence…             │  │ why · evidence  │ │
│  ● No online booking   evidence…             │  └────────────────┘  │
│ Speed  55/100 · 3.6 s                        │  ┌────────────────┐  │
│ AI summary (labelled, muted)                 │  │ SEO …          │  │
└──────────────────────────────────────────────┴──────────────────────┘
```

The reasoning:

- The left column is **the argument**: who the business is, then what is wrong with their site,
  in one continuous read. The findings are a **list with severity markers**, not cards — six
  findings should read as six lines, because their weight is in the severity and the quoted
  evidence, not in a box each.
- The right column is **the decision**, and it is `sticky` so Approve stays reachable however
  long the evidence runs. It is narrower than the left column on purpose: a decision needs less
  room than the case for it.
- Below 1024 px the two columns stack, the decision column first, because on a narrow screen
  the reviewer scrolls to the evidence rather than the other way round.

## Vocabulary

The UI keeps the blueprint's two states and does **not** collapse them into one word:

- **Opportunity** — a claim about a business that a human has not yet decided on. Everything on
  the review queue and the review detail page is an opportunity.
- **Lead** — an opportunity a reviewer has **approved**. Everything on `/leads` and in the CRM
  is a lead.

So "Review queue" holds opportunities, "My leads" holds leads, and the word changes exactly at
the approval. This is deliberate: a rep who is told they have 4 leads must not find 4 undecided
guesses. Reviewers are the only people who see both words.

## Writing

- A button says what happens and the flow keeps the word: **Approve** → the toast says the
  opportunity was **approved**.
- Empty states name the next action:
  - Review queue (nothing at all): *"Nothing to review. Run a search to find businesses."* with
    a link to Searches. With filters applied it stays *"Nothing to review with these filters."*
    — the reviewer's next action there is to widen the filters, not to run a search.
  - My leads: *"No leads yet. A reviewer assigns leads to you."*
  - CRM: *"Nothing waiting. Approved leads appear here 30 minutes after approval."*
- Errors state the cause **and** the fix, e.g. *"Couldn't reach the API. Check that the api
  container is running (`make prod-ps`)."*

## What was rejected, and why

| Rejected | Why |
|---|---|
| A component library (shadcn/Radix/Headless UI) | Out of scope by the spec, and the set here is 18 small components. A dependency would bring its own token system to fight with, and its own upgrade surface for an internal tool. |
| Dark mode | Explicitly out of scope. The tokens are already CSS variables on `:root`, so a later spec adds one `@media (prefers-color-scheme: dark)` block; no component changes. |
| Keeping the navy/teal palette from v0.1.0 | Navy `#0f1f3a` with a bright `#2dd4bf` teal read as a generic SaaS dashboard, and the bright teal failed 4.5:1 on white (2.0:1) wherever it was used as text. |
| Colour-only severity | Fails for colour-blind reviewers and in print. Every severity dot is followed by its word. |
| Spinners on tables | A spinner says "wait"; a skeleton says "this is the shape of what is coming" and keeps the layout from jumping. Spinners survive only inside a button that is mid-submit. |
| An eyebrow above each page heading | It is decoration that costs a line of vertical space on a tool used all day. |
| `·`-joined meta strings ("email · Role · time") | They read as one run-on sentence, cannot wrap sensibly, and are unreadable to a screen reader. Meta is a definition list or its own line now. |
| Six stacked cards for six findings | Boxes imply equal, separate importance. A list with severity markers ranks them and fits on one screen. |
| A "Print" button on the lead page | The page is read-only for a sales rep, and the e2e asserts that. `Ctrl/⌘+P` is the platform's own affordance; the print stylesheet does the work. |

## Responsive

| Width | Behaviour |
|---|---|
| 1440 px + | content capped at 1440 px, centred, 24 px gutters |
| 1280 px | the design target; two-column review detail |
| 1024 px | review detail stacks to one column; tables scroll inside their own container |
| 768 px | nav wraps under the wordmark; forms go one field per row |

No page ever scrolls horizontally: a table that does not fit scrolls inside its own wrapper.

## Accessibility floor

- Every interactive element has a **visible focus ring** (2 px `--accent`, 2 px offset) via a
  single global `:focus-visible` rule plus a `focus-ring` utility for custom controls.
- A **skip-to-content** link is the first focusable thing on every page.
- Dialogs trap focus, close on `Esc`, and restore focus to whatever opened them.
- Landmarks: one `<header>`, one `<nav aria-label="Main">`, one `<main id="main">`, and exactly
  one `<h1>` per page; headings never skip a level.
- `axe-core` runs over login, review queue, review detail, leads, lead detail, CRM, searches
  and health in `frontend/src/test/axe.test.tsx`; **zero** serious or critical violations.
- Colour never carries meaning alone; every severity and status also carries its word.

## Screenshots

`docs/design/before/` and `docs/design/after/`, one PNG per screen at 1280 px, captured with
`frontend/scripts/screenshots.mjs` (`make screenshots OUT=…`) against the demo stack.
