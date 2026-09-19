# Tech stack

Implementation notes for the expense tracker. Directories map to tiers: `web/` frontend,
`api/` extraction service, `supabase/` database.

The structure is a **split backend**, and that is the thing to understand first:

```
                    ┌──────────────────────────────┐
                    │   web/  React SPA (browser)  │
                    └───────┬──────────────┬───────┘
    POST image (recognize)  │              │  CRUD + uploads (direct)
                    ┌───────▼──────┐  ┌────▼─────────────────┐
                    │ api/ FastAPI │  │ Supabase             │
                    │      ↓       │  │  Postgres + Storage  │
                    │ OpenAI gpt-4o│  │  RLS                 │
                    └──────────────┘  └──────────────────────┘
```

FastAPI does one job — turn a receipt photo into structured fields — and **never touches
the database**. Ledger data goes straight from the browser to Supabase on the anon key;
RLS is the real boundary.

---

## 1. Database: Supabase (hosted PostgreSQL)

Everything is defined in `supabase/schema.sql`. The file is idempotent — after any
change, re-run the whole thing.

### Tables

| Table | Purpose |
| --- | --- |
| `public.receipts` | One expense. `uuid` primary key, `numeric(12,2)` amount, `date` transaction date |
| `public.receipt_items` | Line items, removed with the parent via `on delete cascade` |

Four columns carry a decision worth knowing about:

- **`currency`** — the base currency is USD. A foreign-currency receipt records its
  original currency faithfully, but **no FX conversion is applied**; totals sum the base
  currency only, and other currencies are listed separately.
- **`image_path`** — stores the Supabase Storage **object path** (`2026/07/uuid.jpg`),
  not a URL. The bucket is private and signed URLs expire, so `createSignedUrl()` mints
  one at display time. `null` for manual entries.
- **`user_id`** — a foreign key to `auth.users`, but always `null` in the single-user
  MVP. Wiring up Auth makes it `not null` and has RLS populate it; no table change.
- **`category`** — stores a slug (`food`, `transport`, …). Labels, icons and colours
  live only in `web/src/constants/categories.ts`, which is why the UI switched from
  Chinese to English without a single stored row changing.

### Indexes

```sql
receipts (date desc)          -- the list view is always date-descending; hottest path
receipts (category)
receipts (user_id)
receipt_items (receipt_id)
```

### Trigger

`set_updated_at()` on a `before update` trigger maintains `receipts.updated_at`.

### Row Level Security

Both tables have `enable row level security`, but the current policy is `mvp_anon_all` —
**anyone holding the anon key can read and write everything.** That is fine for local
development and personal use, and nothing else.

When wiring up Supabase Auth, drop those two policies and swap in the per-`user_id`
versions already written out in section 4 of `schema.sql`.

### Storage

Private bucket `receipts`:

| Setting | Value |
| --- | --- |
| public | `false` |
| Size limit | 10 MB |
| Allowed types | `image/jpeg` `image/png` `image/webp` `image/heic` |

---

## 2. Frontend: React 19 + TypeScript + Vite

| Role | Choice | Version |
| --- | --- | --- |
| Framework | React + React DOM | ^19.2 |
| Language | TypeScript | ~6.0 |
| Build | Vite + `@vitejs/plugin-react` | ^8.1 / ^6.0 |
| Styling | Tailwind CSS (`@tailwindcss/vite` plugin) | ^4.3 |
| Routing | React Router | ^7.18 |
| State | Zustand | ^5.0 |
| Charts | Recharts | ^3.10 |
| Icons | lucide-react | ^1.27 |
| Data | `@supabase/supabase-js` | ^2.110 |
| Lint | oxlint | ^1.71 |

Developed against Node v22.

### Styling

Tailwind 4's CSS-first configuration — there is no `tailwind.config.js`. Semantic tokens
are declared on `:root` in `web/src/index.css` (`--bg`, `--surface`, `--fg`, `--muted`,
`--line`, …), flipped wholesale in dark mode, then registered as Tailwind utilities via
`@theme inline` (`bg-surface`, `text-muted`, `border-line`).

Components use only the semantic names; no raw colour values appear outside that file,
so a re-skin touches one file.

### Layout

```
web/src/
├── pages/        HomePage · ListPage · InsightsPage · ScanPage · ReceiptFormPage
├── components/
│   ├── layout/   AppShell · BottomNav · Fab · PageHeader
│   ├── form/     AmountField · CategoryGrid · FormRow · ItemsEditor
│   ├── insights/ CategoryDonut · TrendBars · InsightCards
│   └── ui/       Sheet
├── store/        receipts.ts (Zustand)
├── lib/          supabase · receipts · api · analytics · advice · format · image · color
├── constants/    categories.ts
└── types/
```

Routes (`App.tsx`): `/`, `/list` and `/insights` are tabs rendered inside `AppShell`
with the bottom navigation; `/new`, `/receipt/:id` and `/scan` are full-screen and skip
the shell.

### Two decisions

**The analysis is computed in the browser, from pure functions.** `lib/analytics.ts` and
`lib/advice.ts` take the array of receipts already in the store — no extra queries. A few
hundred rows a year is faster to aggregate in memory than to round-trip, and it removes a
cache-invalidation problem. No LLM is involved in any conclusion: every insight is a
deterministic rule with a stated threshold, so a user can check it against their own
ledger. At most four are shown at once.

**The Supabase client does not crash on missing configuration.** `lib/supabase.ts` falls
back to a placeholder URL when the environment variables are absent, and the "Connection"
panel on the home screen explains the problem — rather than `createClient` throwing at
module load and blanking the app.

---

## 3. Extraction service: Python FastAPI

| Dependency | Version | Role |
| --- | --- | --- |
| fastapi | 0.118.0 | HTTP framework |
| uvicorn[standard] | 0.37.0 | ASGI server |
| openai | 2.49.0 | Calls `gpt-4o` to read the image |
| pydantic-settings | 2.11.0 | Reads config from `api/.env` |
| python-multipart | 0.0.20 | Receives the uploaded image |
| pillow | 11.3.0 | Resizing and EXIF orientation |
| pillow-heif | 1.1.0 | Reads the HEIC files an iPhone produces |

Developed against Python 3.13.

### Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/health` | Health check, behind the frontend's connection indicator |
| `POST` | `/api/recognize` | Upload one receipt image, get structured fields back |

Without `OPENAI_API_KEY`, `/api/recognize` returns 503. Manual entry is unaffected.

### Layers

- **`vision.py`** — the only file that knows which vendor is in use. Changing models
  means changing this one file.
- **`schemas.py`** — the contract. Note that the `ExtractedReceipt` docstring and every
  `Field(description=...)` are folded into the `response_format` sent to the model:
  **they are prompt, not comments.** Implementation notes belong in `#` comments.
  `normalize()` lives here too, turning the model's sentinel values back into real nulls
  and rejecting impossible ones (non-positive amounts, dates like `2026-02-30`, currency
  codes that aren't three letters).
- **`main.py`** — HTTP and CORS only.
- **`config.py`** — server-side secrets such as `OPENAI_API_KEY`, never shipped to the
  browser.

Images are resized to a 1600px long edge at JPEG quality 85 before being sent. Receipts
are narrow strips; that is enough to read the print, and anything larger only burns
tokens and adds seconds.

---

## 4. One scan, end to end

1. Photo picked or taken in the browser → downscaled client-side (`lib/image.ts`)
2. `POST /api/recognize` → FastAPI converts to JPEG, fixes orientation, resizes to
   1600px → `gpt-4o`
3. Structured fields come back → **shown in a form for a human to check**
4. On confirmation, the frontend uploads the **original** image to Storage
5. The frontend writes `receipts` + `receipt_items` directly to Supabase

Step 3 is not optional. A misread total silently corrupts the ledger and every metric
downstream inherits it, whereas glancing at four fields costs almost nothing. Note that
across the whole chain, the backend never touches the database.
