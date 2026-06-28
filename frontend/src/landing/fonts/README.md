# Landing display font (self-hosted subset)

The landing page headlines (`.display`) use a **subset** of Noto Serif CJK SC,
self-hosted (no external CDN) to match the product's privacy stance and keep the
download tiny (~33 KB vs. multi-MB for the full CJK face).

- Committed source emitted by Vite: `../public/fonts/noto-serif-sc-subset.woff2`
  (Vite `publicDir` copies it to `app/static/landing/fonts/` on build — see
  `frontend/vite.landing.config.js`).
- Glyph set: `headlines.txt` — every character that renders in the serif face
  across the page (hero + section titles + feature/step titles + price digits/¥).

## Regenerate (after changing any serif headline copy)

Add the new characters to `headlines.txt`, then:

```bash
pip install fonttools brotli
pyftsubset /usr/share/fonts/opentype/noto/NotoSerifCJK-Bold.ttc \
  --font-number=0 \
  --text-file=frontend/src/landing/fonts/headlines.txt \
  --layout-features='*' \
  --flavor=woff2 \
  --output-file=frontend/src/landing/public/fonts/noto-serif-sc-subset.woff2
```

`--font-number=0` selects the SC face from the .ttc collection. If a headline
character is missing from the subset, it falls back to the system serif
(`Songti SC, serif`) — readable, just not the intended face.
