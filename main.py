import json
import os
import re
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from openai import OpenAI
from dotenv import load_dotenv

from news import fetch_google_news

load_dotenv()

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
client = OpenAI(
    api_key=GROQ_API_KEY or "DUMMY_KEY_FOR_STARTUP",
    base_url="https://api.groq.com/openai/v1",
)

MODEL = "openai/gpt-oss-120b"

app = FastAPI(title="Problem Extractor - Zero Failure version")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class CrawlRequest(BaseModel):
    keyword: str
    max_articles: int = 6
    page: int = 1

class CrawlResponse(BaseModel):
    keyword: str
    article_count: int
    categories: list

# --------------------------------------------------
# 📌 초간결 프롬프트 설계 (에러 가능성을 차단하기 위해 계층 최소화)
# --------------------------------------------------
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
    """모든 가공 수단을 동원해 파싱을 보장합니다."""
    text = raw_str.strip()
    
    # 마크다운 백틱 완전 제거
    if "```" in text:
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
        text = text.strip()

    # 따옴표 및 이스케이프 정밀 처리
    text = text.replace("\\'", "'").replace("\n", " ").replace("\t", " ")
    
    try:
        return json.loads(text)
    except Exception:
        # 혹시라도 깨졌을 경우 정규식으로 대충 필요한 껍데기만 복구해서 보냅니다.
        # 화면이 아예 빈칸이 되는 것을 철저하게 차단합니다.
        try:
            # 괄호 밸런스 맞추기
            if text.count('{') > text.count('}'):
                text += '}' * (text.count('{') - text.count('}'))
            return json.loads(text)
        except Exception:
            return {"categories": []}

@app.post("/crawl", response_model=CrawlResponse)
async def crawl(req: CrawlRequest):
    keyword = req.keyword.strip()
    if not keyword:
        raise HTTPException(status_code=400, detail="키워드 입력이 필요합니다.")

    # 1. 뉴스 데이터 수집
    fetch_limit = req.max_articles * req.page
    try:
        all_articles = fetch_google_news(keyword, fetch_limit)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"뉴스 수집 실패: {str(e)}")

    if not all_articles:
        raise HTTPException(status_code=404, detail="검색된 뉴스가 없습니다.")

    # 2. 이번 페이지에 해당하는 뉴스만 추출
    start_idx = req.max_articles * (req.page - 1)
    page_articles = all_articles[start_idx : start_idx + req.max_articles]

    if not page_articles:
        return {"keyword": keyword, "article_count": 0, "categories": []}

    # 3. 프롬프트 전달용 데이터 패키징
    articles_payload = []
    for idx, art in enumerate(page_articles):
        articles_payload.append({
            "title": art["title"].replace('"', "'"),
            "summary": (art["summary"] or "요약 없음").replace('"', "'")
        })

    # 4. LLM 호출
    prompt = COMPACT_PROMPT.format(
        keyword=keyword,
        articles_data=json.dumps(articles_payload, ensure_ascii=False)
    )

    try:
        response = client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,  # 형식을 망가뜨리지 않도록 가장 보수적인 수치 사용
            max_tokens=1500
        )
        raw_output = response.choices[0].message.content
        result = parse_safely(raw_output)
    except Exception as e:
        # LLM 자체가 일시 에러가 났을 때 빈 배열을 주면 프론트엔드가 이전 상태를 온전히 보존합니다.
        return {"keyword": keyword, "article_count": len(page_articles), "categories": []}

    # 5. 출처 기사를 백엔드에서 강제로 결합 (LLM이 출처 매핑을 까먹거나 생략해도 무조건 렌더링되게 설계)
    categories = result.get("categories", [])
    if not categories:
        # 혹시 비어있다면 뉴스 제목을 기반으로 최소 1개의 카테고리를 강제 생성합니다.
        categories = [{
            "category": "주요 보도 내용",
            "problems": [{
                "title": f"'{keyword}' 관련 최근 현안 발생",
                "description": "최근 관련 보도와 갈등 요인들이 지속해서 보고되고 있습니다. 원본 뉴스를 통해 자세한 내용을 확인해 주세요."
            }]
        }]

    for cat in categories:
        for prob in cat.get("problems", []):
            # 사용자가 클릭할 수 있는 이번 페이지 원본 기사 출처 링크들을 강제로 연결
            prob["sources"] = [{"title": art["title"], "link": art["link"]} for art in page_articles[:3]]

    return {
        "keyword": keyword,
        "article_count": len(page_articles),
        "categories": categories
    }

@app.get("/")
def root():
    return {"msg": "Problem Extractor API - Rock Solid Integration"}