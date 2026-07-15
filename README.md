# Problem Extractor

문제점만 뽑아내는 텍스트 분석 앱. FastAPI 백엔드([Groq](https://console.groq.com) 무료 API 호출) + 순수 HTML/JS 프론트엔드.

```
problem-extractor/
├── backend/
│   ├── main.py           # FastAPI 서버 (/analyze, /crawl)
│   ├── news.py           # 구글 뉴스 RSS 크롤러 (키워드 -> 기사 목록)
│   ├── requirements.txt
│   └── .env.example      # GROQ_API_KEY 템플릿
├── frontend/
│   └── index.html        # 브라우저에서 바로 열면 됨 (빌드 불필요) - 탭 2개: 텍스트 분석 / 뉴스 트렌드 리포트
└── README.md
```

## 기능 2가지

1. **텍스트 분석** — 직접 붙여넣은 글에서 문제점만 추출 (`/analyze`)
2. **뉴스 트렌드 리포트** — 키워드를 넣으면 구글 뉴스에서 관련 기사를 자동 수집하고,
   기사별로 문제점을 뽑은 뒤 카테고리별로 집계해서 트렌드 리포트를 보여줌 (`/crawl`)

## 1. 백엔드 실행

```bash
cd backend
python -m venv venv && source venv/bin/activate   # (선택) 가상환경
pip install -r requirements.txt

cp .env.example .env
# .env 파일을 열어 GROQ_API_KEY=gsk_... 로 실제 키 입력
# 키는 https://console.groq.com/keys 에서 무료로 발급 (신용카드 불필요)

uvicorn main:app --reload --port 8000
```

정상 실행되면 `http://localhost:8000` 에서 `{"msg": "Problem Extractor API Running"}` 확인 가능.

## 2. 프론트엔드 실행

빌드 과정이 없으므로 `frontend/index.html` 파일을 브라우저로 그냥 열면 된다.

```bash
# 예: macOS
open frontend/index.html

# 또는 간단한 정적 서버로
cd frontend && python -m http.server 5500
# → http://localhost:5500 접속
```

화면 상단 "API" 입력창에 백엔드 주소(기본값 `http://localhost:8000`)가 맞는지 확인 후 텍스트를 입력하고 **분석 실행**을 누른다. (Ctrl/Cmd + Enter로도 실행 가능)

## 3. API 스펙

**POST** `/analyze`

```json
// request
{ "text": "분석할 원문 텍스트" }

// response
{ "result": "### 🔴 Critical Problems\n1. [...] ..." }
```

- `text`가 비어있으면 `400` 반환
- Groq 호출 실패 시 `502`, 무료 티어 요청 한도 초과(429) 시 `429` 반환

**POST** `/crawl`

```json
// request
{ "keyword": "전세 사기", "max_articles": 8 }

// response
{
  "keyword": "전세 사기",
  "article_count": 8,
  "categories": [
    {
      "category": "제도/정책 공백",
      "count": 4,
      "problems": [
        {
          "title": "...",
          "description": "...",
          "sources": [{ "title": "기사 제목", "link": "https://..." }]
        }
      ]
    }
  ],
  "skipped": [{ "title": "분석 실패한 기사 제목", "reason": "..." }]
}
```

**동작 방식 (2단계 파이프라인 + 폴백)**
1. 구글 뉴스 RSS(`news.py`)에서 키워드로 기사 목록(제목/링크/출처/요약)을 가져옴 — API 키 불필요
2. 기사마다 LLM을 호출해 문제점을 JSON으로 추출 (제목+요약 스니펫 기준, 본문 전체는 스크래핑하지 않음)
   - 구글 뉴스 검색 RSS의 요약 필드는 실제 요약이 아니라 제목 반복이거나 빈 값인 경우가 많음 → 그런 경우는 자동으로 걸러내고 헤드라인만으로 추론하도록 프롬프트가 설계되어 있음
3. 1차 패스에서 문제점이 하나도 안 나오면, 전체 헤드라인을 한 번에 모아 재시도하는 폴백 패스를 자동 실행 (`used_fallback: true`로 응답에 표시됨)
4. 모든 문제점을 모아 한 번 더 LLM 호출 → 카테고리별로 군집화·중복 제거·건수 집계 → 트렌드 리포트 반환

- `keyword`로 검색된 뉴스가 없으면 `404`
- 1차+폴백 모두 실패하면 `422` (이 경우는 정말 관련 뉴스 자체가 거의 없는 키워드일 가능성이 높음)
- 일부 기사만 실패한 경우 `skipped` 배열에 사유와 함께 표시되고 나머지로 리포트는 정상 생성됨

## 4. 참고

- CORS는 개발 편의를 위해 전체 허용(`*`)으로 열려 있음. 배포 시 프론트엔드 도메인으로 제한 권장.
- 모델은 기본적으로 `openai/gpt-oss-120b` (Groq 무료 티어) 사용 (`backend/main.py`의 `MODEL` 상수에서 변경 가능).
- Groq 무료 티어는 분당 요청 수 제한이 있어 기사 수가 많으면(`max_articles` 값이 크면) 시간이 걸릴 수 있음. 429 발생 시 자동으로 지수 백오프 재시도함.
- 프론트엔드는 결과를 마크다운 패턴으로 파싱해서 카드 형태로 보여주고, 형식이 어긋나면 원문 그대로(raw) 표시함.
- 뉴스 크롤링은 **제목+요약 스니펫** 기준으로 분석함. 기사 본문 전체를 스크래핑하지 않으므로, 언론사별 페이월/봇 차단 이슈 없이 안정적으로 동작하지만 요약 정보만큼의 정확도를 가짐.
- 구글 뉴스 RSS의 `link`는 리다이렉트 URL이라 브라우저로 열면 실제 기사로 정상 이동하지만, 서버 사이드에서 최종 URL을 바로 얻지는 못함.