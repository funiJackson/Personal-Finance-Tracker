This repository holds two things:

- **A receipt image dataset** — `receipt-image-dataset-1` to `-4`, 188 photos of real receipts
- **An expense tracker app** — `web/` + `api/` + `supabase/`. Photograph a receipt,
  GPT-4o reads it, you confirm, it's saved.

The dataset came first; the app was added later. The app's extraction accuracy was
validated against this dataset.

---

# 1. The receipt dataset

188 JPEGs across four directories, named `<number>-receipt.jpg`:

| Directory | Images | Number range |
| --- | --- | --- |
| `receipt-image-dataset-1` | 34 | 1000–1032 |
| `receipt-image-dataset-2` | 51 | 1033–1093 |
| `receipt-image-dataset-3` | 53 | 1094–1145 |
| `receipt-image-dataset-4` | 54 | 1146–1199 |

Numbering runs continuously across the directories, 1000–1199. Twelve numbers were
deleted along the way (see the run of `Delete xxxx-receipt.jpg` commits in the history),
which is why there are 188 images rather than 200. No number appears twice.

Each directory also holds a 1-byte `temp` file. Git won't track an empty directory, so
these keep the directories alive as images get removed.

The images are real-world receipt photos and scans. Sizes and orientations vary — a
sample ranged from 338×450 to 1000×903 — and plenty are shot at an angle, glare across
the paper, or creased. That is exactly what makes them useful for testing extraction.

> The repository does not record where these images came from or under what licence.
> Check with the repository owner before using them commercially or redistributing them.

## Testing extraction against it

With the backend running, point the recognition endpoint at any image:

```bash
curl -s -X POST http://localhost:8000/api/recognize \
  -F "file=@receipt-image-dataset-1/1012-receipt.jpg" | python3 -m json.tool
```

The current prompt was tuned against five of these (1012 / 1040 / 1093 / 1112 / 1178).
On those five, every core field — date, total, currency, category, payment method and
line-item count — matched a hand-checked answer key.

That is **five images, and all five happen to be US restaurant receipts** (USD, food).
UK supermarkets, petrol stations and foreign currencies have never actually been put to
the test. The other 183 images are right here if you want to widen the sample.

---

# 2. The expense tracker

Photograph a receipt, GPT-4o reads it, you confirm, it's saved. Manual entry works too.

## Stack

There are two backends, and that split is the thing to understand first:

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

FastAPI turns one receipt photo into structured fields and **never touches the
database**. Ledger data goes straight from the browser to Supabase on the anon key;
RLS is the real boundary.

| | |
| --- | --- |
| **Frontend** (`web/`) | React 19 · TypeScript · Vite 8 · Tailwind CSS 4 · React Router 7 · Zustand · Recharts · lucide-react · `@supabase/supabase-js` · oxlint |
| **Recognition API** (`api/`) | Python 3.13 · FastAPI · Uvicorn · OpenAI `gpt-4o` · Pydantic Settings · Pillow + pillow-heif |
| **Database** (`supabase/`) | Supabase — Postgres (`receipts`, `receipt_items`), RLS, private Storage bucket |

Field-by-field schema notes, the Tailwind token setup and the frontend directory
layout are in [`TECH-STACK.md`](TECH-STACK.md) (written in Chinese).

## First-time setup

### 1. Database

Paste the whole of `supabase/schema.sql` into the Supabase dashboard → SQL Editor and
run it. The file is idempotent, so just re-run it after any change.

### 2. Frontend environment

```bash
cp web/.env.example web/.env.local
```

Fill in the Project URL and anon public key from the Supabase dashboard →
Project Settings → API.

### 3. Backend environment

```bash
cp api/.env.example api/.env
```

Fill in an [OpenAI](https://platform.openai.com/api-keys) API key. Extraction reads
images, so the model has to support vision input — the default is `gpt-4o`.

You can run without a key; `/scan` will just return 503. Manual entry is unaffected.

> Both `.env` and `.env.local` are gitignored. The anon key is fine in the frontend
> bundle — RLS is the real boundary — but `OPENAI_API_KEY` and the `service_role` key
> must never leave `api/.env`.

## Running

Two terminals:

```bash
# backend → http://localhost:8000
cd api && .venv/bin/uvicorn app.main:app --reload --port 8000

# frontend → http://localhost:5173
cd web && npm run dev
```

Both dots green in the "Connection" panel on the home screen means you're set up.

After a fresh clone, install dependencies first:

```bash
cd web && npm install
cd ../api && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

## Testing on a real phone

The frontend already sets `host: true`, so `npm run dev` prints a Network address like
`http://192.168.x.x:5173`. Open that on a phone on the same WiFi. Two more changes are
needed:

1. Append `http://192.168.x.x:5173` to `CORS_ORIGINS` in `api/.env`
2. Point `VITE_API_BASE_URL` in `web/.env.local` at `http://192.168.x.x:8000`

The backend also has to be started with `--host 0.0.0.0` to be reachable from the phone.

## Design decisions

**Categories are stored as slugs** (`food`, `transport`, …). Labels, icons and colours
live only in `web/src/constants/categories.ts`. The UI was switched from Chinese to
English without touching a single stored row — precisely the case this split was meant
to absorb.

**`image_path` stores a Storage object path, not a URL.** The bucket is private and
signed URLs expire, so `createSignedUrl()` mints one at display time instead.

**Extraction results always get a human look before they're written.** A model that
misreads a total quietly corrupts the ledger, whereas glancing at it costs almost
nothing. `/scan` goes: browser downscales the photo → `POST /api/recognize` → user
confirms → original image uploaded to Storage → row written. The backend never touches
the database.

**Every field in the model's `response_format` is required and non-nullable**, using
`""` / `0` / `"unknown"` to mean "couldn't read it" rather than `Optional`. That is both
what OpenAI's strict mode demands (every property must appear in `required`) and a way
to avoid the `anyOf` that nullable fields produce. Real nulls are restored by
`normalize()` in `api/app/schemas.py`.

> Note that the `ExtractedReceipt` docstring and every `Field(description=...)` get
> folded into the `response_format` sent to the model — **they are prompt, not
> comments**. Keep implementation notes in `#` comments.

**RLS is enabled, but the MVP policy lets any anon key holder read and write
everything.** When wiring up Supabase Auth, swap in the per-`user_id` policies —
section 4 of `supabase/schema.sql` already has them written out. No table changes
required.
