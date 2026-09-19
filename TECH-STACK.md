# 技术栈

记账 App 的技术选型说明。目录对应关系：`web/` 前端、`api/` 识别服务、`supabase/` 数据库。

整体是一个**双后端**结构 —— 这是理解这个项目最关键的一点：

```
                    ┌──────────────────────────────┐
                    │  web/  React SPA (浏览器)    │
                    └───────┬──────────────┬───────┘
       POST 图片（仅识别）  │              │  增删改查 + 传图（直连）
                    ┌───────▼──────┐  ┌────▼─────────────────┐
                    │ api/ FastAPI │  │ Supabase             │
                    │      ↓       │  │  Postgres + Storage  │
                    │ OpenAI gpt-4o│  │  RLS                 │
                    └──────────────┘  └──────────────────────┘
```

FastAPI 只负责「把一张小票图片变成结构化字段」，**从不碰数据库**。账目数据由前端
用 anon key 直连 Supabase 读写，RLS 是真正的权限边界。

---

## 1. 数据库：Supabase（托管 PostgreSQL）

全部定义在 `supabase/schema.sql`，文件是幂等的，改完整份重跑即可。

### 表结构

| 表 | 说明 |
| --- | --- |
| `public.receipts` | 一条账目。`uuid` 主键、`numeric(12,2)` 金额、`date` 交易日期 |
| `public.receipt_items` | 小票明细行，`on delete cascade` 跟随主表删除 |

几个值得注意的字段：

- **`currency`** —— 本位币是 USD。模型从外币小票识别出的原始币种也照实存，但**不做汇率折算**；汇总时只累加本位币，其余币种单独列出。
- **`image_path`** —— 存的是 Supabase Storage 里的**对象路径**（如 `2026/07/uuid.jpg`），不是 URL。bucket 是私有的、签名 URL 会过期，所以展示时才用 `createSignedUrl()` 现算。手动记账为 `null`。
- **`user_id`** —— 外键指向 `auth.users`，但 MVP 单用户阶段**恒为 null**。接 Auth 后改成 `not null` 并由 RLS 填充，表结构不用动。
- **`category`** —— 存 slug（`food` / `transport` …）。标签、图标、颜色只活在 `web/src/constants/categories.ts`，所以 UI 从中文切英文时一行数据都没改。

### 索引

```sql
receipts (date desc)          -- 列表页永远按日期倒序，最热的查询路径
receipts (category)
receipts (user_id)
receipt_items (receipt_id)
```

### 触发器

`set_updated_at()` + `before update` 触发器自动维护 `receipts.updated_at`。

### Row Level Security

两张表都 `enable row level security`，但当前策略是 `mvp_anon_all` ——
**任何持有 anon key 的人都能读写全部数据**，只适用于本地开发 / 个人自用。

接入 Supabase Auth 时，删掉这两条策略，换成 `schema.sql` 第 4 节里已经写好的
按 `user_id` 隔离的版本即可。

### Storage

私有 bucket `receipts`：

| 配置 | 值 |
| --- | --- |
| public | `false` |
| 大小上限 | 10 MB |
| 允许类型 | `image/jpeg` `image/png` `image/webp` `image/heic` |

---

## 2. 前端：React 19 + TypeScript + Vite

| 类别 | 选型 | 版本 |
| --- | --- | --- |
| 框架 | React + React DOM | ^19.2 |
| 语言 | TypeScript | ~6.0 |
| 构建 | Vite + `@vitejs/plugin-react` | ^8.1 / ^6.0 |
| 样式 | Tailwind CSS（`@tailwindcss/vite` 插件） | ^4.3 |
| 路由 | React Router | ^7.18 |
| 状态 | Zustand | ^5.0 |
| 图表 | Recharts | ^3.10 |
| 图标 | lucide-react | ^1.27 |
| 数据层 | `@supabase/supabase-js` | ^2.110 |
| Lint | oxlint | ^1.71 |

开发用 Node v22。

### 样式系统

Tailwind 4 的 CSS-first 配置，没有 `tailwind.config.js`。`web/src/index.css` 里
`:root` 定义语义令牌（`--bg` / `--surface` / `--fg` / `--muted` / `--line` …），
暗色模式下整体翻转，再用 `@theme inline` 注册成 Tailwind 工具类
（`bg-surface`、`text-muted`、`border-line`）。

组件里只用语义名、不出现裸色值 —— 换配色只动 `index.css` 这一个文件。

### 目录结构

```
web/src/
├── pages/        HomePage · ListPage · InsightsPage · ScanPage · ReceiptFormPage
├── components/
│   ├── layout/   AppShell · BottomNav · Fab · PageHeader
│   ├── form/     AmountField · CategoryGrid · FormRow · ItemsEditor
│   ├── insights/ CategoryDonut · TrendBars · InsightCards
│   └── ui/       Sheet
├── store/        receipts.ts（Zustand）
├── lib/          supabase · receipts · api · analytics · advice · format · image · color
├── constants/    categories.ts
└── types/
```

路由（`App.tsx`）：`/` `/list` `/insights` 三个 tab 走 `AppShell`（带底部导航）；
`/new` `/receipt/:id` `/scan` 是全屏页，不套壳。

### 两个设计决定

**洞察全部是确定性规则算出来的**，不经过模型。`lib/advice.ts` 里每条规则要么给出
一个有数字支撑、用户能照着去账本里核对的结论，要么什么都不给 —— 模型编一个
「你餐饮涨了 43%」出来，比不给建议糟糕得多。一次最多显示 4 条。

**Supabase 客户端不会在配置缺失时崩掉。** `lib/supabase.ts` 里缺 env 就退回占位
URL，由首页「Connection」面板的状态点把问题讲清楚，而不是模块加载时抛错白屏。

---

## 3. 识别服务：Python FastAPI

| 依赖 | 版本 | 用途 |
| --- | --- | --- |
| fastapi | 0.118.0 | HTTP 框架 |
| uvicorn[standard] | 0.37.0 | ASGI 服务器 |
| openai | 2.49.0 | 调 `gpt-4o` 读图 |
| pydantic-settings | 2.11.0 | 从 `api/.env` 读配置 |
| python-multipart | 0.0.20 | 接收上传的图片 |
| pillow | 11.3.0 | 图片缩放 / EXIF 方向校正 |
| pillow-heif | 1.1.0 | 认 iPhone 相册的 HEIC |

开发用 Python 3.13。

### 端点

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/health` | 健康检查，前端的连接状态点靠它 |
| `POST` | `/api/recognize` | 上传一张小票图，返回结构化字段 |

没配 `OPENAI_API_KEY` 时 `/api/recognize` 返回 503，手动记账不受影响。

### 分层

- `vision.py` —— **厂商相关的东西只出现在这里**。换模型改这一个文件就够了。
- `schemas.py` —— 纯契约。注意 `ExtractedReceipt` 的 docstring 和每个
  `Field(description=...)` 都会被折进发给模型的 `response_format`，**它们是 prompt，
  不是注释**；实现备注要写在 `#` 注释里。
- `main.py` —— 只管 HTTP 和 CORS。
- `config.py` —— `OPENAI_API_KEY` 等服务端机密，绝不下发到前端。

送模型前先把图压到长边 1600px、JPEG 质量 85。小票是窄长条，这个尺寸足够看清字，
再大只是多烧 token、多等几秒。

---

## 4. 一次扫描走完的路径

1. 浏览器里选/拍照 → 前端先降采样（`lib/image.ts`）
2. `POST /api/recognize` → FastAPI 转 JPEG、压到 1600px → gpt-4o
3. 返回结构化字段 → **进表单给人看一眼**
4. 用户确认 → 前端把**原图**传进 Storage
5. 前端直连 Supabase 写 `receipts` + `receipt_items`

第 3 步不能省。模型误读一个总金额会悄悄污染整个账本，而扫一眼几乎不花什么成本。
整条链路里后端一次都没有碰过数据库。
