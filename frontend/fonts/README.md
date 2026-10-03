# Local font assets

Source: https://github.com/google/fonts/tree/9710da1eacb3be272583c3224dcb70f9da6eadbb/ofl

All five families retain their upstream OFL.txt. IBM Plex assets are **unmodified
upstream TTFs**, including their original names and reserved-name notices.
Inter, JetBrains Mono and Source Serif 4 are WOFF2 conversions made with
fontTools 4.66.1 / Brotli 1.2.0. No character subsetting was performed.
Inter optical size is pinned to 14; Source Serif 4 optical size to 20 (upstream
defaults), matching the old Google request without an optical-size axis.
Weight remains variable. Conversion verification compared cmap and horizontal
metrics and checked Cyrillic including Ё/ё. These tools are not app dependencies.

Original SHA-256 (before WOFF2 conversion):
- Inter: 29160a80ff49ddcab2c97711247e08b1fab27a484a329ce8b813d820dc559031
- JetBrains Mono: 48715a42ec242c21e9f02692891e147d022299a52e48d5e413e1a942193ffeda
- Source Serif 4: 97b2d4da6e3cb494b5a1e66ae176914d852ccabef49e0c02c0df25f3e39aca0b

Final file hashes are pinned by `tests/localFonts.test.ts`.
The root and ledger CSS variable names and route ownership are unchanged.
Ledger fonts remain scoped to store/import screens. `display: swap` is retained.
Full-character assets cost more bytes than a Latin-only subset, but include
Cyrillic and remove build-time requests to Google. No runtime CDN is added.

Rollback: revert the dedicated local-font commit, restoring next/font/google;
this also restores the external build-time dependency. Do not mix rollback
with changes to account/MFA behavior or the approved palette.
