"""
키워드로 구글 뉴스 RSS를 검색해서 기사 목록(제목/링크/발행일/출처/요약)을 가져온다.
API 키가 필요 없고, 한국어 뉴스 검색에 적합하도록 hl/gl/ceid를 ko-KR로 고정한다.

비동기(httpx)로 동작한다 — FastAPI 이벤트 루프를 블로킹하지 않기 위함.
"""

import re
import time
import xml.etree.ElementTree as ET
from urllib.parse import quote

import httpx

GOOGLE_NEWS_RSS_URL = "https://news.google.com/rss/search?q={query}&hl=ko&gl=KR&ceid=KR:ko"

# -----------------------------
# 📌 인메모리 캐시 (키워드 -> (캐싱 시각, 그 시점에 가져온 기사 리스트))
# 페이지네이션 시 매번 처음부터 다시 긁어오지 않도록, 지금까지 요청된 것 중
# 가장 큰 개수만큼을 캐싱해둔다. 요청한 개수가 캐시보다 많으면 새로 긁어와서 갱신한다.
# 주의: 단일 프로세스 메모리에만 저장됨 — 여러 워커/인스턴스로 스케일하면 공유되지 않고,
# 재시작하면 초기화된다. 값이 아주 자주 안 바뀌는 뉴스 검색 특성상 이 정도로도 페이지 이동 시
# 반복 호출을 크게 줄여준다.
# -----------------------------
_CACHE_TTL_SECONDS = 300
_cache: dict[str, tuple[float, list[dict]]] = {}


def _strip_html(raw_html: str) -> str:
    """RSS description 필드에 섞여 있는 HTML 태그를 제거한다."""
    text = re.sub(r"<[^>]+>", " ", raw_html or "")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _parse_articles(xml_bytes: bytes, max_articles: int) -> list[dict]:
    root = ET.fromstring(xml_bytes)
    items = root.findall("./channel/item")

    articles = []
    for item in items[:max_articles]:
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        pub_date = (item.findtext("pubDate") or "").strip()
        description = _strip_html(item.findtext("description") or "")

        source_el = item.find("source")
        source_name = source_el.text.strip() if source_el is not None and source_el.text else ""

        if not title:
            continue

        # 구글 뉴스 검색 RSS의 description은 실제 요약이 아니라 제목을 그대로 반복하거나
        # 관련 기사 목록(HTML <ol>)인 경우가 많다. 그런 경우는 정보량이 0이므로 비워둔다.
        normalized_desc = description.replace(" ", "")
        normalized_title = title.replace(" ", "")
        if not description or normalized_desc == normalized_title or len(description) < 8:
            description = ""

        articles.append(
            {
                "title": title,
                "link": link,
                "source": source_name,
                "published": pub_date,
                "summary": description,
            }
        )

    return articles


async def fetch_google_news(keyword: str, max_articles: int = 8) -> list[dict]:
    """
    구글 뉴스 RSS에서 keyword로 검색한 기사 목록을 반환한다.
    각 항목: { title, link, source, published, summary }
    """
    now = time.time()
    cached = _cache.get(keyword)
    if cached and (now - cached[0]) < _CACHE_TTL_SECONDS and len(cached[1]) >= max_articles:
        return cached[1][:max_articles]

    url = GOOGLE_NEWS_RSS_URL.format(query=quote(keyword))

    async with httpx.AsyncClient(timeout=10) as http_client:
        resp = await http_client.get(
            url, headers={"User-Agent": "Mozilla/5.0 (ProblemExtractorBot/1.0)"}
        )
        resp.raise_for_status()

    articles = _parse_articles(resp.content, max_articles)
    _cache[keyword] = (now, articles)
    return articles