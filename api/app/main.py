from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.vision import recognize_receipt
from app.schemas import RecognizedReceipt

app = FastAPI(title="Receipt API", version="0.1.0")

# 和 receipts bucket 的 file_size_limit 对齐。前端会先压图，正常走不到这个上限，
# 这里挡的是直接打接口的情况。
MAX_UPLOAD_BYTES = 10 * 1024 * 1024

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict:
    """前端首页用它点亮「后端」状态点。

    字段名不带厂商 —— 换模型时前端不用跟着改。ai_configured 只反映 key 填没填，
    不代表 key 有效；真正的校验留到 /api/recognize 实际调用时。
    """
    return {
        "status": "ok",
        "ai_configured": bool(settings.openai_api_key),
        "ai_model": settings.openai_model,
    }


# 故意写成同步 def：FastAPI 会把它丢进线程池，模型那个几秒的阻塞调用
# 就不会卡住事件循环。写成 async def 反而会让并发的请求排队。
@app.post("/api/recognize", response_model=RecognizedReceipt)
def recognize(file: UploadFile = File(...)) -> RecognizedReceipt:
    """读一张小票，返回识别结果。

    只读图，不落库 —— 用户在前端核对无误后，由前端直接写 Supabase。
    """
    data = file.file.read()
    if not data:
        raise HTTPException(400, "No image received")
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "Image is larger than 10 MB")

    return recognize_receipt(data)
