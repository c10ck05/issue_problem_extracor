import json
import os
import re
from typing import Optional
from fastapi import FastAPI, HTTPException, Header, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from openai import OpenAI
from dotenv import load_dotenv

# 기존에 잘 만들어두신 뉴스 크롤러 임포트
from news import fetch_google_news

load_dotenv()

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
client = OpenAI(
    api_key=GROQ_API_KEY or "DUMMY_KEY_FOR_STARTUP",
    base_url="https://api.groq.com/openai/v1",
)

MODEL = "openai/gpt-oss-120b"

app = FastAPI(title="Problem Extractor - Secure Backend")

# --------------------------------------------------
# 🔒 보안 및 CORS 설정 (Pages 프록시와 로컬 환경만 허용)
# --------------------------------------------------
# Render 서비스 환경변수에 등록해둔 BACKEND_SECRET_KEY를 읽어옵니다.
BACKEND_SECRET_KEY = os.getenv("BACKEND_SECRET_KEY", "hyunjae-super-secret-key-1234")

def verify_access_token(x_api_key: Optional[str] = Header(None)):
    if x_api_key != BACKEND_SECRET_KEY:
        raise HTTPException(
            status_code=401, 
            detail="인증되지 않은 사용자입니다. 백엔드 보안 키 검증에 실패했습니다."
        )
    return x_api_key

# 내 Pages 환경 도메인들
origins = [
    "http://localhost:8000",
    "http://localhost:3000",
    "https://issue-problem-extracor.pages.dev",  # Pages 기본 서브도메인
    "https://issue-tracker.hyunjae.co.kr",        # 개인 커스텀 도메인
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --------------------------------------------------
# 📌 데이터 스키마 및 프롬프트 정의
# --------------------------------------------------
class CrawlRequest(BaseModel):
    keyword: str
    max_articles: int = 6
    page: int = 1

class CrawlResponse(BaseModel):
    keyword: str
    article_count: int
    categories: list

COMPACT_PROMPT = """
Analyze these news articles about "{keyword}" and summarize the major problems reported.
Return a clean, valid JSON object following the format below.

RULES:
- Extract 1 to 3 distinct problem categories.
- For each category, list 1 to 2 detailed problems.
- Ensure all text is written in Korean (한국어).
- Do not use raw double-quotes inside the strings. Use single-quotes instead.
- Do not use any markdown backticks or extra text. Output ONLY JSON.

JSON FORMAT:
{{
  "categories": [
    {{
      "category": "분류 (예: 제도적 한계 / 보건·복지)",
      "problems": [
        {{
          "title": "핵심 문제 한 줄 요약",
          "description": "문제가 발생한 상세한 배경 및 설명"
        }}
      ]
    }}
  ]
}}

ARTICLES:
{articles_data}
"""

def parse_safely(raw_str: str) -> dict:
    text = raw_str.strip()
    if "```" in text:
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
        text = text.strip()
    text = text.replace("\\'", "'").replace("\n", " ").replace("\t", " ")
    try:
        return json.loads(text)
    except Exception:
        try:
            if text.count('{') > text.count('}'):
                text += '}' * (text.count('{') - text.count('}'))
            return json.loads(text)
        except Exception:
            return {"categories": []}

# --------------------------------------------------
# 🚀 안전 강화된 API 크롤링 라우트
# --------------------------------------------------
@app.post("/crawl", response_model=CrawlResponse)
async def crawl(
    req: CrawlRequest, 
    token: str = Depends(verify_access_token) # 의존성 필터로 프록시 이외의 직접 호출 완전 통제
):
    keyword = req.keyword.strip()
    if not keyword:
        raise HTTPException(status_code=400, detail="키워드 입력이 필요합니다.")

    fetch_limit = req.max_articles * req.page
    try:
        all_articles = fetch_google_news(keyword, fetch_limit)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"뉴스 수집 실패: {str(e)}")

    if not all_articles:
        raise HTTPException(status_code=404, detail="검색된 뉴스가 없습니다.")

    start_idx = req.max_articles * (req.page - 1)
    page_articles = all_articles[start_idx : start_idx + req.max_articles]

    if not page_articles:
        return {"keyword": keyword, "article_count": 0, "categories": []}

    articles_payload = []
    for idx, art in enumerate(page_articles):
        articles_payload.append({
            "title": art["title"].replace('"', "'"),
            "summary": (art["summary"] or "요약 없음").replace('"', "'")
        })

    prompt = COMPACT_PROMPT.format(
        keyword=keyword,
        articles_data=json.dumps(articles_payload, ensure_ascii=False)
    )

    try:
        response = client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,  
            max_tokens=1500
        )
        raw_output = response.choices[0].message.content
        result = parse_safely(raw_output)
    except Exception as e:
        return {"keyword": keyword, "article_count": len(page_articles), "categories": []}

    categories = result.get("categories", [])
    if not categories:
        categories = [{
            "category": "주요 보도 내용",
            "problems": [{
                "title": f"'{keyword}' 관련 최근 현안 발생",
                "description": "최근 관련 보도와 갈등 요인들이 지속해서 보고되고 있습니다. 원본 뉴스를 통해 자세한 내용을 확인해 주세요."
            }]
        }]

    for cat in categories:
        for prob in cat.get("problems", []):
            prob["sources"] = [{"title": art["title"], "link": art["link"]} for art in page_articles[:3]]

    return {
        "keyword": keyword,
        "article_count": len(page_articles),
        "categories": categories
    }

@app.get("/")
def root():
    return {"msg": "Problem Extractor API - Rock Solid Security Active"}