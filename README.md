# Issue Insight

키워드를 넣으면 구글 뉴스에서 관련 기사를 자동 수집하고, AI가 기사들을 읽고 **문제점을 뽑아 카테고리로 분류**해서 보여주는 대시보드.
FastAPI 백엔드([Groq](https://console.groq.com) 무료 API) + 순수 HTML/JS 프론트엔드.

```
problem-extractor/
├── backend/
│   ├── main.py           # FastAPI 서버 (/crawl)
│   ├── news.py           # 구글 뉴스 RSS 크롤러 (키워드 -> 기사 목록)
│   ├── requirements.txt
│   └── .env.example      # GROQ_API_KEY 템플릿
├── frontend/
│   └── index.html        # 브라우저에서 바로 열면 됨 (빌드 불필요)
└── README.md
```

## 동작 방식

1. `news.py`가 구글 뉴스 RSS에서 키워드로 기사 목록(제목/링크/출처/발행일/요약)을 가져옴 — API 키 불필요
2. 기사 헤드라인 전체를 **한 번의 LLM 호출**로 묶어서 보냄 → AI가 각 헤드라인이 보도하는 문제를 추출하고, 2~5개 카테고리로 분류
   - 기사 하나하나에 개별 호출을 하지 않는 이유: 호출 횟수를 1번으로 고정해야 Groq 무료 티어의 분당 토큰(TPM) 한도를 안정적으로 넘기지 않음
   - 프론트엔드에서 `if (title.includes("법")) ...` 같은 단어 매칭으로 카테고리를 나누지 않음 — 문맥을 이해 못 하고 자주 틀리기 때문에, 분류는 전부 AI가 함
3. AI가 반환한 문제점마다 어떤 기사에서 나왔는지(`indexes`)를 실제 기사 정보로 되돌려서 응답
4. `page`로 다음 묶음(기사 6개 단위 기본값)을 추가로 불러올 수 있음 — RSS에는 총 개수가 없어서 "이번 페이지가 꽉 찼으면 다음 페이지가 있을 수도 있다"는 식의 근사치로 `has_next`를 판단함

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

정상 실행되면 `http://localhost:8000` 에서 `{"msg": "Issue Insight API OK"}` 확인 가능.

## 2. 프론트엔드 실행

빌드 과정이 없으므로 `frontend/index.html` 파일을 브라우저로 그냥 열면 된다.

```bash
# 예: macOS
open frontend/index.html

# 또는 간단한 정적 서버로
cd frontend && python -m http.server 5500
# → http://localhost:5500 접속
```

키워드를 입력하고 **쟁점 분류하기**를 누르면 결과가 뜬다. 페이지 상단 우측의 **다음 묶음** 버튼으로 다음 기사 묶음을 이어서 불러올 수 있다.

> 프론트엔드가 `localhost`/`127.0.0.1`이 아닌 다른 곳에서 열리면 자동으로 배포용 API 주소(`main.py`의 `ALLOWED_ORIGINS`, `index.html`의 `API_BASE`)를 쓰도록 되어 있다. 실제 배포 도메인이 바뀌면 이 두 곳을 같이 수정해야 한다.

## 3. API 스펙

**POST** `/crawl` — 분당 5회로 rate limit이 걸려 있음 (IP 기준)

```json
// request
{ "keyword": "전세 사기", "max_articles": 6, "page": 1 }

// response
{
  "keyword": "전세 사기",
  "page": 1,
  "has_next": true,
  "categories": [
    {
      "category": "제도/정책 공백",
      "count": 3,
      "problems": [
        {
          "title": "...",
          "description": "...",
          "sources": [
            { "title": "기사 제목", "link": "https://...", "source": "언론사", "published": "..." }
          ]
        }
      ]
    }
  ],
  "articles": [ { "title": "...", "link": "...", "source": "...", "published": "...", "summary": "..." } ]
}
```

- `keyword`는 1~80자, `max_articles`는 1~15, `page`는 1~10 (Pydantic으로 검증됨)
- 뉴스 검색 실패 시 `502`
- AI 응답이 JSON 파싱에 실패하면 `502` (원인 메시지 포함)
- Groq 무료 티어 요청 한도 초과 시 자체 재시도(지수 백오프) 후에도 안 되면 `429`
- 분당 5회를 초과해 호출하면 `429` (`slowapi` rate limit)
- 해당 페이지에 기사가 없으면(RSS 소진) `categories: []`, `articles: []`, `has_next: false` 반환 (에러 아님)

## 4. 알아두면 좋은 것

- 모델은 `openai/gpt-oss-120b` (Groq 무료 티어) 사용, `backend/main.py`의 `MODEL` 상수에서 변경 가능
- CORS는 `ALLOWED_ORIGINS` 화이트리스트로 잠겨 있음 — 새 프론트엔드 도메인을 추가하면 여기도 같이 추가해야 함
- 뉴스 제목/요약/언론사명은 외부(크롤링) 데이터라 프론트엔드에서 전부 `escapeHtml()`을 거쳐 렌더링함 (XSS 방지)
- 뉴스 분석은 **제목+짧은 요약** 기준. 구글 뉴스 검색 RSS는 실제 요약이 아니라 제목을 반복하거나 빈 값을 주는 경우가 많아, `news.py`에서 그런 값은 자동으로 걸러내고 헤드라인만으로 판단하게 되어 있음
- 구글 뉴스 RSS의 `link`는 리다이렉트 URL이라 브라우저로 열면 실제 기사로 이동하지만, 서버에서 최종 URL을 바로 얻지는 못함
- `page`를 올리면 매번 `max_articles * page`만큼 RSS를 다시 가져와서 뒤쪽을 슬라이스함 (RSS 자체가 페이지네이션을 지원하지 않아서 생기는 구조적 한계 — 페이지가 커질수록 매 요청이 조금씩 더 무거워짐)