# Bundled typefaces

Both families are self-hosted (no external font CDN: the Content-Security-Policy keeps
`font-src 'self'`) and are the **latin** subsets, taken from the Fontsource packages.

| Family | Files | Licence | Upstream |
|---|---|---|---|
| Public Sans | `public-sans-latin-{400,500,600,700}-normal.woff2` | SIL Open Font License 1.1 | `@fontsource/public-sans` (USWDS / Public Sans) |
| IBM Plex Mono | `ibm-plex-mono-latin-{400,500}-normal.woff2` | SIL Open Font License 1.1 | `@fontsource/ibm-plex-mono` (IBM Plex) |

The OFL permits redistribution of the font files with this notice. See `docs/design.md` for
which family is used where — in short, IBM Plex Mono is only ever used for evidence quoted
from a business's website, URLs, scores and IDs.
