# Vendor marks

The dashboard embeds monochrome vendor SVG paths in `dashboard_page.py`; it
does not fetch images, fonts, scripts, or styles from a vendor at runtime.
These marks identify the corresponding service, not an affiliation or
endorsement. The trademarks belong to their respective owners.

## OpenAI / Codex

- Official source: <https://developers.openai.com/>, header Blossom SVG.
- Brand guidance: <https://openai.com/brand/>.
- Retrieved 2026-10-24. Original SVG SHA-256:
  `468ebe03b0504e3e40be53e6c1f4b5b80c00d94ea4ad07b913ae41073638ae90`.
- Original path geometry, fill rule, and square viewBox `100 100 411 411`
  are retained. The monochrome fill uses the dashboard's OpenAI green (`#10a37f`).

## Grok

- Official source: <https://grok.com/>, product icon (`path[id="mark"]`),
  not the wordmark. The same product icon identifies the independent Bot quota.
- Brand guidance: <https://x.ai/legal/brand-guidelines>.
- Retrieved 2026-10-24. Original source SVG SHA-256:
  `d2800b32682a5494ce2c9d5b5d203ed16774cfa81d98c28cb8cda79100dbea18`.
- The official combined mark/wordmark SVG supplies the two product-icon paths.
  Their geometry is unchanged; viewBox `0 0 34 33` isolates the icon, with
  preserved aspect ratio, and the monochrome fill uses the dashboard text colour.

Only inert SVG/path elements and fixed attributes are embedded. No scripts,
event handlers, external references, raster images, or arbitrary downloaded
markup are included. Both marks have an accessible name on a bare 24px span
in their service heading, without a background or border. The nested 24px SVG
is decorative to avoid duplicate announcements. Aspect ratio is preserved.
