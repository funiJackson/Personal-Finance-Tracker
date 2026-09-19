"""调多模态模型读小票。目前是 OpenAI gpt-4o。

这个模块是整个后端存在的唯一理由 —— 记账数据的增删改查前端直连 Supabase，
不经过这里。

厂商相关的东西只出现在这个文件里：schemas.py 是纯契约，main.py 只管 HTTP。
换模型改这里就够了。
"""

import base64
import logging
from io import BytesIO

from fastapi import HTTPException
from openai import OpenAI, OpenAIError
from PIL import Image, ImageOps, UnidentifiedImageError
from pillow_heif import register_heif_opener

from app.config import settings
from app.schemas import ExtractedReceipt, RecognizedReceipt, normalize

# iPhone 相册里的照片默认是 HEIC，Pillow 本身不认。注册一次之后
# Image.open() 就能像普通格式一样打开它。
register_heif_opener()

logger = logging.getLogger(__name__)

# 送模型之前先压到这个尺寸。小票是窄长条，1600px 边长足够看清字，
# 再大只是多烧 token、多等几秒。
MAX_EDGE = 1600
JPEG_QUALITY = 85

PROMPT = """You are reading a shopping receipt. Look at the image and fill in the JSON \
structure you have been given.

Rules:
- total_amount is the amount actually paid, including tax and service charge. Do not
  use the subtotal. When the receipt shows both TOTAL and SUBTOTAL, go by TOTAL.
- date is the transaction date printed on the receipt, not today. Format YYYY-MM-DD.
- currency: judge from the currency symbol and the region. £ is GBP, € is EUR,
  ¥ is CNY, $ is usually USD.
- category by the nature of the merchant: supermarkets and restaurants are food;
  petrol stations, public transport and taxis are transport; clothing, department
  stores and household goods are shopping; pharmacies and clinics are medical;
  rent and utility bills are housing; cinema, games and live events are
  entertainment; everything else is other.
- payment_method: read the settlement lines at the bottom of the receipt, not only
  lines containing the word "payment". CREDIT / CREDIT CARD AUTH / VISA /
  MASTERCARD / AMEX / CHIP READ all count as credit_card; DEBIT / EFTPOS count as
  debit_card; CASH / CHANGE DUE count as cash. Only use unknown when no such line
  is present at all.
- items: line items only. Skip summary lines such as subtotal, tax, change,
  discounts and loyalty points. A leading number on the line is the quantity
  column, not part of the name. Receipts are narrow, so a long name often wraps
  onto the line below — join that continuation into the name.
- If anything is unreadable, or simply is not on the receipt, use the empty value
  described for that field. Do not guess — leaving it blank for the user to fill in
  is better than filling it in wrongly."""


_client: OpenAI | None = None


def _get_client() -> OpenAI:
    global _client
    if not settings.openai_api_key:
        raise HTTPException(503, "OPENAI_API_KEY is not set on the server, scanning is unavailable")
    if _client is None:
        _client = OpenAI(api_key=settings.openai_api_key)
    return _client


def to_jpeg(data: bytes) -> bytes:
    """任意格式（含 HEIC）→ 转正、缩小、压成 JPEG。"""
    try:
        image = Image.open(BytesIO(data))
        # 手机竖着拍出来的图，像素其实是横的，靠 EXIF 里的方向标记转正。
        # 不处理的话模型看到的是躺倒的文字，识别率掉得很厉害。
        image = ImageOps.exif_transpose(image)
        image.thumbnail((MAX_EDGE, MAX_EDGE))

        buffer = BytesIO()
        # JPEG 不支持透明通道，PNG 截图直接存会报错，统一转 RGB
        image.convert("RGB").save(buffer, format="JPEG", quality=JPEG_QUALITY)
        return buffer.getvalue()
    except (UnidentifiedImageError, OSError) as e:
        raise HTTPException(400, "This file is not a readable image") from e


def recognize_receipt(data: bytes) -> RecognizedReceipt:
    client = _get_client()
    jpeg = to_jpeg(data)

    # 图片入参要么给公网 URL、要么内联。小票不该为了让模型看一眼就先传到
    # 公网上，所以走 base64 data URL。
    data_url = f"data:image/jpeg;base64,{base64.b64encode(jpeg).decode()}"

    try:
        completion = client.chat.completions.parse(
            model=settings.openai_model,
            messages=[
                {"role": "system", "content": PROMPT},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": data_url,
                                # high：把图切成 512px 的小块分别看。小票上的单价
                                # 和日期都是小字，low 基本认不出来。贵一些，但一次
                                # 记账值这个钱。
                                "detail": "high",
                            },
                        }
                    ],
                },
            ],
            # 传 pydantic 类 = structured outputs 的 strict 模式：模型只能返回
            # 合 schema 的 JSON，省掉自己 json.loads + 校验那一层。
            response_format=ExtractedReceipt,
            temperature=0,  # 抽取事实不需要创造力，同图同结果才好复现
        )
    # 鉴权、限流、超时、网络断连都是 OpenAIError 的子类。对调用方来说是同一件事：
    # 这次识别没成。把原文透给前端，排查时不用翻服务端日志。
    except OpenAIError as e:
        logger.exception("OpenAI 调用失败")
        raise HTTPException(502, f"OpenAI request failed: {e}") from e

    message = completion.choices[0].message

    # strict 模式下模型仍然可以拒答
    if message.refusal:
        logger.warning("模型拒答：%s", message.refusal)
        raise HTTPException(502, f"The model refused to read this image: {message.refusal}")

    if message.parsed is None:
        # 输出被 max_tokens 截断时会走到这里
        logger.error("没拿到可解析的结果：%s", message.content)
        raise HTTPException(502, "The model could not read this image — try a clearer photo")

    return normalize(message.parsed)
