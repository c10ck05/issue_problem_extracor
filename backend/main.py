import asyncio
import json
import os
from typing import Optional

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from openai import APIStatusError, AsyncOpenAI
from pydantic import BaseModel, Field

# ✅ 무지성 연타 방어용 rate limit
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

from news import fetch_google_news

load_dotenv()

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
if not GROQ_API_KEY:
    raise RuntimeError("GROQ_API_KEY가 설정되지 않았습니다. .env 파일을 확인하세요.")

# Groq은 OpenAI SDK와 완전히 호환된다 — base_url만 Groq 엔드포인트로 바꾸면 끝.
# AsyncOpenAI를 써야 이벤트 루프를 블로킹하지 않고 여러 요청을 동시에 처리할 수 있다.
client = AsyncOpenAI(api_key=GROQ_API_KEY, base_url="https://api.groq.com/openai/v1")

# openai/gpt-oss-120b: Groq에서 JSON 모드를 공식 지원하는 프로덕션 모델.
# 구형 llama-3.3-70b-versatile은 폐지 절차 중이라 사용하지 않음.
MODEL = "openai/gpt-oss-120b"

limiter = Limiter(key_func=get_remote_address)
app = FastAPI(title="Issue Insight API", redirect_slashes=False)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# ✅ CORS 도메인 화이트리스트 (실제 프론트엔드 주소만 허용)
ALLOWED_ORIGINS = [
    "http://localhost:5500",
    "http://127.0.0.1:5500",
    "http://localhost:8000",
    "http://127.0.0.1:8000",
    "https://issue-tracker.hyunjae.co.kr",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["POST", "GET", "OPTIONS"],
    allow_headers=["*"],
)


# -----------------------------
# 📌 요청 바디 (상/하한 있는 검증 포함)
# -----------------------------
class CrawlRequest(BaseModel):
    keyword: str = Field(..., min_length=1, max_length=80, description="검색할 이슈 키워드")
    max_articles: int = Field(6, ge=1, le=15, description="페이지당 기사 수")
    page: int = Field(1, ge=1, le=10, description="페이지 번호 (묶음)")


# -----------------------------
# 📌 프롬프트
# -----------------------------
# 기사 하나하나에 AI를 호출하는 대신, 헤드라인 묶음 전체를 한 번의 호출로
# "문제 추출 + 카테고리 분류"까지 같이 시킨다.
# - 호출 횟수가 1번으로 고정되므로 Groq 무료 티어의 분당 토큰(TPM) 한도를 넘기지 않는다.
# - 프론트엔드에서 단어 매칭(if text.includes("법") ...)으로 분류하던 걸 AI 판단으로 대체한다.
CATEGORIZE_PROMPT = """
You are a strict problem-extraction AI analyzing Korean news headlines about the keyword "{keyword}".
Below is a numbered list of headlines (with short summaries where available).

TASK:
- For each headline, identify the concrete problem it reports (accident, dispute, allegation, policy
  failure, safety concern, financial loss, delay, backlash, shortage, legal action, etc). Judge based on
  the full meaning of the headline, not just individual keywords — do not pick a category just because
  one word appears; find which problem the headline is actually centered on.
- Group these problems into 2-5 clear categories that fit the actual content (invent category names that
  fit — don't force a fixed taxonomy).
- Each extracted problem must list which headline "indexes" (0-based) support it.
- Only omit a headline if it is genuinely unrelated to any real-world problem.

LANGUAGE: Write "category", "title", "description" values in Korean (한국어). Keep JSON keys in English.

Respond with ONLY valid JSON, no markdown, no commentary.

JSON SHAPE:
{{
  "categories": [
    {{
      "category": "카테고리명 (한국어)",
      "problems": [
        {{ "title": "짧은 문제 제목 (한국어)", "description": "구체적인 설명 (한국어)", "indexes": [0, 2] }}
      ]
    }}
  ]
}}

HEADLINES:
{headlines}
"""


def _truncate(text: str, max_len: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= max_len else text[: max_len - 1].rstrip() + "…"


async def _chat_json(prompt: str, max_retries: int = 3, max_tokens: Optional[int] = 1800) -> dict:
    """
    Groq(OpenAI 호환)를 JSON 모드로 호출하고 파싱한다.
    - 429(rate limit)는 지수 백오프로 재시도
    - max_tokens로 완성 길이를 제한해 TPM 한도 초과를 방지
    - 실패 원인을 절대 삼키지 않고 그대로 위로 올린다 (bare except 금지)
    - 비동기 클라이언트를 써서 응답을 기다리는 동안 다른 요청을 막지 않는다
    """
    last_error: Exception = RuntimeError("알 수 없는 오류")
    for attempt in range(max_retries):
        try:
            response = await client.chat.completions.create(
                model=MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2,
                response_format={"type": "json_object"},
                max_tokens=max_tokens,
            )
            content = response.choices[0].message.content
            return json.loads(content)
        except APIStatusError as e:
            last_error = e
            if e.status_code == 429 and attempt < max_retries - 1:
                await asyncio.sleep(2**attempt)
                continue
            raise
        except json.JSONDecodeError as e:
            last_error = e
            raise HTTPException(status_code=502, detail=f"AI 응답이 올바른 JSON이 아닙니다: {e}")
    raise last_error


@app.post("/crawl")
@limiter.limit("5/minute")
async def crawl(req: CrawlRequest, request: Request):
    keyword = req.keyword.strip()

    try:
        all_articles = await fetch_google_news(keyword, req.max_articles * req.page)
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"뉴스 검색 실패: {e}")

    start = (req.page - 1) * req.max_articles
    page_articles = all_articles[start : start + req.max_articles]

    if not page_articles:
        return {
            "keyword": keyword,
            "page": req.page,
            "has_next": False,
            "categories": [],
            "articles": [],
        }

    headlines_block = "\n".join(
        f"{i}. {a['title']} — {_truncate(a['summary'], 100) or '(요약 없음)'}"
        for i, a in enumerate(page_articles)
    )
    prompt = CATEGORIZE_PROMPT.format(keyword=keyword, headlines=headlines_block)

    try:
        parsed = await _chat_json(prompt)
    except APIStatusError as e:
        if e.status_code == 429:
            raise HTTPException(
                status_code=429,
                detail="Groq 무료 티어 요청 한도를 초과했습니다. 잠시 후 다시 시도하세요.",
            )
        raise HTTPException(status_code=502, detail=f"AI 분석 실패: {e}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"AI 분석 실패: {e}")

    # AI가 돌려준 index를 실제 기사(title/link/source/published)로 되풀이해서 확장한다.
    categories = []
    for cat in parsed.get("categories", []):
        problems = []
        for p in cat.get("problems", []):
            idxs = [
                i for i in p.get("indexes", []) if isinstance(i, int) and 0 <= i < len(page_articles)
            ]
            if not idxs:
                continue
            sources = [
                {
                    "title": page_articles[i]["title"],
                    "link": page_articles[i]["link"],
                    "source": page_articles[i]["source"],
                    "published": page_articles[i]["published"],
                }
                for i in idxs
            ]
            problems.append(
                {
                    "title": p.get("title", ""),
                    "description": p.get("description", ""),
                    "sources": sources,
                }
            )
        if problems:
            categories.append(
                {
                    "category": cat.get("category", "기타"),
                    "count": len(problems),
                    "problems": problems,
                }
            )

    return {
        "keyword": keyword,
        "page": req.page,
        # RSS가 요청한 만큼 다 채워줬다면 다음 페이지가 더 있을 가능성이 있다고 본다 (RSS엔 총 개수가 없음)
        "has_next": len(page_articles) == req.max_articles,
        "categories": categories,
        "articles": page_articles,
    }


@app.get("/")
def root():
    return {"msg": "Issue Insight API OK"}


@app.get("/health")
def health():
    return {"status": "ok"}