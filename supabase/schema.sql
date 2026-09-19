-- ============================================================================
-- 记账 App — 数据库 Schema
--
-- 这个文件是幂等的，可以安全地重复执行。
-- 执行方式：Supabase 控制台 → SQL Editor → 粘贴全文 → Run
-- ============================================================================


-- ----------------------------------------------------------------------------
-- 1. 表结构
-- ----------------------------------------------------------------------------

create table if not exists public.receipts (
  id             uuid primary key default gen_random_uuid(),

  -- MVP 单用户阶段恒为 null。接入 Supabase Auth 后改为 not null 并由 RLS 填充。
  user_id        uuid references auth.users(id) on delete cascade,

  merchant       text,
  date           date        not null default current_date,
  total_amount   numeric(12,2) not null check (total_amount > 0),

  -- 本位币 USD。模型从外币小票识别出的原始币种也存这里，但不做汇率折算 ——
  -- 汇总时只累加本位币，其余币种单独列出。
  currency       text        not null default 'USD',

  category       text        not null,
  payment_method text,

  -- Supabase Storage 中的对象路径（不是 URL —— 签名 URL 会过期，展示时现算）。
  -- 手动记账为 null。
  image_path     text,

  is_manual      boolean     not null default false,
  note           text,

  created_at     timestamptz not null default now(),
  updated_at     timestamptz not null default now()
);

comment on column public.receipts.image_path is
  'Supabase Storage 中 receipts bucket 内的对象路径，例如 2026/07/uuid.jpg。手动记账为 null。';


create table if not exists public.receipt_items (
  id         uuid primary key default gen_random_uuid(),
  receipt_id uuid not null references public.receipts(id) on delete cascade,
  item_name  text not null,
  unit_price numeric(12,2),
  quantity   numeric(10,2)
);


-- ----------------------------------------------------------------------------
-- 2. 索引
-- ----------------------------------------------------------------------------

-- 列表页永远按日期倒序，这是最热的查询路径
create index if not exists receipts_date_idx        on public.receipts (date desc);
create index if not exists receipts_category_idx    on public.receipts (category);
create index if not exists receipts_user_id_idx     on public.receipts (user_id);
create index if not exists receipt_items_receipt_id_idx
                                                    on public.receipt_items (receipt_id);


-- ----------------------------------------------------------------------------
-- 3. updated_at 自动维护
-- ----------------------------------------------------------------------------

create or replace function public.set_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

drop trigger if exists receipts_set_updated_at on public.receipts;
create trigger receipts_set_updated_at
  before update on public.receipts
  for each row execute function public.set_updated_at();


-- ----------------------------------------------------------------------------
-- 4. Row Level Security
--
-- ⚠️  MVP 单用户阶段：下面的 "mvp_anon_all" 策略允许任何持有 anon key 的人
--     读写全部数据。这只适用于本地开发 / 个人自用。
--
--     接入 Supabase Auth 时，执行本节末尾注释掉的那段：删掉 mvp 策略，
--     换成按 user_id 隔离的策略。表结构不需要任何改动。
-- ----------------------------------------------------------------------------

alter table public.receipts      enable row level security;
alter table public.receipt_items enable row level security;

drop policy if exists mvp_anon_all on public.receipts;
create policy mvp_anon_all on public.receipts
  for all to anon, authenticated
  using (true) with check (true);

drop policy if exists mvp_anon_all on public.receipt_items;
create policy mvp_anon_all on public.receipt_items
  for all to anon, authenticated
  using (true) with check (true);

-- 接入 Auth 后启用以下内容（并删除上面两条 mvp_anon_all）：
--
--   drop policy if exists mvp_anon_all on public.receipts;
--   create policy own_receipts on public.receipts
--     for all to authenticated
--     using (auth.uid() = user_id) with check (auth.uid() = user_id);
--
--   drop policy if exists mvp_anon_all on public.receipt_items;
--   create policy own_receipt_items on public.receipt_items
--     for all to authenticated
--     using (exists (select 1 from public.receipts r
--                    where r.id = receipt_id and r.user_id = auth.uid()))
--     with check (exists (select 1 from public.receipts r
--                    where r.id = receipt_id and r.user_id = auth.uid()));


-- ----------------------------------------------------------------------------
-- 5. Storage：小票原图
--
-- 私有 bucket。前端展示时用 createSignedUrl() 现算临时 URL。
-- ----------------------------------------------------------------------------

insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values (
  'receipts',
  'receipts',
  false,
  10485760,                                          -- 10 MB
  array['image/jpeg', 'image/png', 'image/webp', 'image/heic']
)
on conflict (id) do update
  set public             = excluded.public,
      file_size_limit    = excluded.file_size_limit,
      allowed_mime_types = excluded.allowed_mime_types;

-- 同样是 MVP 期的临时策略，接 Auth 时收紧为 "只能操作 user_id 前缀下的对象"
drop policy if exists mvp_anon_receipts_all on storage.objects;
create policy mvp_anon_receipts_all on storage.objects
  for all to anon, authenticated
  using (bucket_id = 'receipts') with check (bucket_id = 'receipts');
