import json
import os
import re
from typing import Optional
from fastapi import FastAPI, HTTPException, Header, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from openai import OpenAI
from dotenv import load_dotenv

# ✅ Rate Limit(무지성 호출 방어) 라이브러리 도입
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

from news import fetch_google_news

load_dotenv()

GROQ_API_KEY = os.getenv("GROQ_API_KEY")

if not GROQ_API_KEY:
    raise RuntimeError("GROQ_API_KEY 없음 (.env 확인)")

client = OpenAI(
    api_key=GROQ_API_KEY,
    base_url="https://api.groq.com/openai/v1",
)

MODEL = "openai/gpt-oss-120b"

# ✅ 1. Rate Limit 설정 (IP 주소 기준으로 제한)
limiter = Limiter(key_func=get_remote_address)
app = FastAPI(redirect_slashes=False)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# ✅ CORS 도메인 잠금 수정 (실제 사용하는 도메인 등록)
origins = [
    "http://localhost:5500",
    "http://127.0.0.1:5500",
    "http://localhost:8000",
    "http://127.0.0.1:8000",
    "https://issue-tracker.hyunjae.co.kr",  # ◀ 실제 프론트엔드 주소 추가 완료!
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins, 
    allow_credentials=True,
    allow_methods=["POST", "GET", "OPTIONS"], # OPTIONS 메서드 명시
    allow_headers=["*"],
)

class CrawlRequest(BaseModel):
    keyword: str
    max_articles: int = 6
    page: int = 1

# ✅ 3. 무지성 연타 방어 적용 ("1분에 최대 5번만 호출 가능")
@app.post("/crawl")
@limiter.limit("5/minute")
async def crawl(req: CrawlRequest, request: Request):  # slowapi 규칙상 request: Request 필수
    keyword = req.keyword.strip()
    if not keyword:
        raise HTTPException(status_code=400, detail="키워드 필요")

    try:
        all_articles = fetch_google_news(keyword, req.max_articles * req.page)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    start = (req.page - 1) * req.max_articles
    page_articles = all_articles[start:start + req.max_articles]

    if not page_articles:
        return {
            "keyword": keyword,
            "analysis": "분석할 뉴스 기사가 존재하지 않습니다.",
            "articles": []
        }

    articles_text = "\n".join(
        [f"- {a['title']} ({a['summary']})" for a in page_articles]
    )

    prompt = f"""
다음 뉴스들을 보고 핵심 문제를 한국어로 요약해줘:

{articles_text}

간결하게 핵심만 정리해줘.
"""

    try:
        res = client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
        )
        analysis = res.choices[0].message.content
    except:
        analysis = "AI 분석에 실패했습니다."

    return {
        "keyword": keyword,
        "analysis": analysis,
        "articles": page_articles
    }

@app.get("/")
def root():
    return {"msg": "API OK"}