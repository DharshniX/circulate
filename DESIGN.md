# CIRCULATE design system

The interface of an industrial-technology product: calm, precise, trustworthy.
A first-time visitor should understand any screen within five seconds.

## Principles

1. **One idea per screen.** Every screen opens with a plain title, one sentence saying what you can do here,
   then ONE hero number or ONE main action. Everything else sits below, and secondary detail is folded away
   ("Show details", tabs or the side drawer).
2. **Plain words.** Technical terms live only in a small tooltip (dotted underline):
   blade gap (*kerf*), scrap value (*salvage*), cutting plan (*nesting*),
   what would happen otherwise (*counterfactual*), combined from several sellers (*pooling*).
3. **Readable names, not codes.** "Gussets for Selvam Fabrication", with the code (`G3`) only as small grey text.
4. **At most four numbers per card.** Rupees are rounded and use the Indian format (₹1,23,456). Every number has a short label.
5. **Honesty is visible.** Sample, Simulated, Assumption, Measured, Verified, Illustrative photo, Planned and
   "Posted by a buyer in this demo" are always shown as small pills with a tooltip.
6. **One amber thing per screen.** Amber marks only "value created" and the single main button.

## Colour tokens

All colours are CSS variables on `:root` in `app/static/app.css`, redefined for dark mode
(`prefers-color-scheme: dark` unless `data-theme="light"`, and `data-theme="dark"`). Text pairs meet WCAG AA (4.5:1).

| Token | Light | Dark | Use |
|---|---|---|---|
| `--bg` | `#f6f4ef` warm off-white | `#121417` deep graphite | page |
| `--panel` | `#ffffff` | `#1b1e23` | cards (one step lighter than the page in dark) |
| `--panel-2` | `#faf8f4` | `#16191d` | sunken areas, drawing backgrounds |
| `--line` | `#e5e1d8` | `#2d3239` | 1px borders |
| `--fg` | `#1b2026` | `#e9ebee` | text |
| `--mut` | `#5a636e` | `#a0a8b2` | secondary text (AA on panel and bg) |
| `--rail` | `#16191d` | `#0c0e10` | left rail (deep graphite in both themes) |
| `--np` | `#2c5c88` steel blue | `#7aaedf` | Durable lane |
| `--pe` | `#2d7a4b` leaf green | `#6cc28b` | Fresh lane |
| `--acc` | `#a85a00` amber ink | `#f4a640` | value created (text, bars) |
| `--acc-fill` | `#f2a33a` | `#f2a33a` | the single main button (dark text `#1f1400`) |
| `--ok` / `--okbg` | `#1d6b40` / `#e3f2e8` | `#8fd8a8` / `#16301f` | success |
| `--warn` / `--warnbg` | `#865400` / `#fcefd4` | `#f1c87a` / `#33270f` | warning, Sample |
| `--bad` / `--badbg` | `#b0281c` / `#fde7e4` | `#f3a197` / `#3b1c18` | refused, errors |
| `--c1`…`--c6` | muted order colours | same | neighbours' parts in cutting plans |

## Type

Open-licensed, bundled as woff2 in `app/static/fonts` (works offline):
IBM Plex Sans (body), IBM Plex Sans Condensed SemiBold (headings, labels), IBM Plex Mono (numbers, `tabular-nums`).

| Token | Size / line height | Use |
|---|---|---|
| `--fs-12` | 12 / 16 | labels (uppercase, +0.08em), notes, pills |
| `--fs-14` | 14 / 20 | body, tables, buttons |
| `--fs-16` | 16 / 24 | lead sentence, inputs on phones |
| `--fs-20` | 20 / 28 | card titles |
| `--fs-28` | 28 / 36 | page titles, card hero numbers |
| `--fs-40` | 40 / 48 | screen hero number, welcome headline |

## Space, radius, shadow, motion

- Spacing: 8px grid: `--s1` 8, `--s2` 16, `--s3` 24, `--s4` 32, `--s5` 48.
- Radius: `--r-card` 12px (cards, drawer, photos), `--r-ctl` 8px (buttons, inputs, chips use 999px).
- Shadow: `--shadow` `0 1px 2px rgba(20,24,28,.04), 0 1px 1px rgba(20,24,28,.03)`; `--shadow-lg` for the drawer and toasts.
- Motion: `--ease` 160ms; drawings drop in over about 1.5 s. Everything is switched off under `prefers-reduced-motion: reduce`.
- Control height 40px (44px+ on phones).

## Components

| Component | Class / function | Notes |
|---|---|---|
| Icon | `ic(name)` → `<svg class="ic"><use href="/static/icons/sprite.svg#i-name">` | Lucide (ISC), `app/static/icons` |
| Logo | `app/static/brand/mark.svg`, `favicon.svg` | a sheet with one piece moving into a second sheet |
| Rail | `.rail`, sections per role | Seller: My workshop, Logistics. Buyer: Marketplace, My orders, Logistics. Admin: Platform |
| Top bar | `.topbar` | lane switch, role switch, Sample data badge |
| Page head | `<header>` h1 + one lead sentence | |
| Hero | `.hero` + `.hero-num` | one number, amber only when it is value created |
| Value ladder | `ladder()` → `.ladder` | scrap value → transport → value created, reused wherever money is shown |
| Card | `.card` | white, 1px border, 12px radius, 24px padding |
| Product card | `.pcard` | 4:3 media, title, three spec chips, price line, seller and distance, two buttons |
| Drawer | `drawer.open()` | side panel with large photo, specs, drawing, "What can this become?", passport QR |
| Fold | `<details class="fold">` | "Show details", "Change the job" |
| Badge | `badge(kind)` | honesty pills with tooltips |
| Term | `term(plain, technical)` | plain word, technical word in a tooltip |
| Toast | `toast(text)` | bottom right, 4 s, `aria-live` |
| Skeleton | `.skel` | while a screen loads |
| Empty state | `emptyState(icon, sentence, action)` | icon, one sentence, one next action |
| Error | `errBox()` | says what went wrong and how to fix it |
| Photo | `.media` 4:3, `object-fit: cover`, 12px radius | "Illustrative photo" badge, credit on hover and a link on phones; seller photos say "Photo uploaded by the seller" |

## Photos

`app/static/illustrative/credits.json` records every photo: file, category, what it shows, source URL, author, licence.
Only Wikimedia Commons (public domain, CC0, CC BY, CC BY-SA, with credit) or Unsplash (Unsplash Licence).
At most 1600 px wide. A missing or failed photo shows no photo, never an unlicensed one.
