"""识别接口的契约：让模型填的结构，和返给前端的结构。

两者刻意不是同一个模型 —— 见 ExtractedReceipt 上面那段注释。
这里不出现任何厂商名，换模型只需要动 vision.py。
"""

import re
from datetime import date as date_type
from typing import Literal

from pydantic import BaseModel, Field

# 必须和 web/src/constants/categories.ts 里的 slug 一一对应
CategorySlug = Literal[
    "food", "transport", "shopping", "entertainment", "housing", "medical", "other"
]

PaymentMethod = Literal["cash", "credit_card", "debit_card", "other"]


# ---------------------------------------------------------------------------
# 模型要填的结构（OpenAI structured outputs 的 response_format）
# ---------------------------------------------------------------------------


class ExtractedItem(BaseModel):
    item_name: str = Field(description="Item name, copied verbatim from the receipt")
    unit_price: float = Field(description="Unit price; use 0 if it cannot be read")
    quantity: float = Field(description="Quantity; use 0 if it cannot be read")


# ⚠️ 下面这个类的 docstring 和每个 Field 的 description 都会被 SDK 转进
#    response_format 发给模型 —— 它们是 prompt，不是注释。实现层面的说明
#    一律写成 # 注释，别写进 docstring。
#
#    字段全部必填、且都是非空类型，用 "" / 0 / "unknown" 表达「没识别出来」，
#    而不是 Optional。这既是 OpenAI strict 模式的硬性要求（所有 property 都
#    得在 required 里），也避开了 nullable 带来的 anyOf —— 各家模型对 anyOf
#    的支持参差不齐。真正的 null 在 normalize() 里还原。
class ExtractedReceipt(BaseModel):
    """Information taken from a shopping receipt."""

    merchant: str = Field(description="Merchant name; use an empty string if it cannot be read")
    date: str = Field(description="Transaction date, format YYYY-MM-DD; use an empty string if the receipt has none")
    total_amount: float = Field(description="Total actually paid, including tax and service charge; use 0 if it cannot be read")
    currency: str = Field(description="ISO 4217 code, e.g. GBP / EUR / CNY; use an empty string if it cannot be read")
    category: CategorySlug = Field(description="Spending category; pick one from the list, use other when unsure")
    payment_method: Literal["cash", "credit_card", "debit_card", "other", "unknown"] = Field(
        description="Payment method; use unknown if the receipt does not state one"
    )
    items: list[ExtractedItem] = Field(description="Individual line items; use an empty array if the receipt has none")


# ---------------------------------------------------------------------------
# 给前端的响应
# ---------------------------------------------------------------------------


class RecognizedItem(BaseModel):
    item_name: str
    unit_price: float | None
    quantity: float | None


class RecognizedReceipt(BaseModel):
    """识别结果。null 表示「没认出来」，前端会把对应的表单项留空让人自己填。"""

    merchant: str | None
    date: str | None
    total_amount: float | None
    currency: str | None
    category: CategorySlug
    payment_method: PaymentMethod | None
    items: list[RecognizedItem]


# ---------------------------------------------------------------------------
# 归一化
# ---------------------------------------------------------------------------

_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_CURRENCY_PATTERN = re.compile(r"^[A-Z]{3}$")


def _text(value: str) -> str | None:
    return value.strip() or None


def _positive(value: float) -> float | None:
    """0 和负数都是「没识别出来」。金额列有 check (total_amount > 0)，放过去只会被数据库打回。"""
    return round(value, 2) if value > 0 else None


def _date(value: str) -> str | None:
    """只接受真实存在的 YYYY-MM-DD。模型偶尔会返回 2026-02-30 这种日历上不存在的日期。"""
    value = value.strip()
    if not _DATE_PATTERN.match(value):
        return None
    try:
        date_type.fromisoformat(value)
    except ValueError:
        return None
    return value


def _currency(value: str) -> str | None:
    value = value.strip().upper()
    return value if _CURRENCY_PATTERN.match(value) else None


def normalize(raw: ExtractedReceipt) -> RecognizedReceipt:
    """把模型的哨兵空值还原成 null，顺手挡掉明显不合法的值。"""
    return RecognizedReceipt(
        merchant=_text(raw.merchant),
        date=_date(raw.date),
        total_amount=_positive(raw.total_amount),
        currency=_currency(raw.currency),
        category=raw.category,
        payment_method=None if raw.payment_method == "unknown" else raw.payment_method,
        items=[
            RecognizedItem(
                item_name=name,
                unit_price=_positive(item.unit_price),
                quantity=_positive(item.quantity),
            )
            for item in raw.items
            # 名字都没有的明细行对用户没有任何价值，不如不给
            if (name := item.item_name.strip())
        ],
    )
