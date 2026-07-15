"""
키워드로 구글 뉴스 RSS를 검색해서 기사 목록(제목/링크/발행일/출처/요약)을 가져온다.
API 키가 필요 없고, 한국어 뉴스 검색에 적합하도록 hl/gl/ceid를 ko-KR로 고정한다.
"""

import re
import xml.etree.ElementTree as ET
from urllib.parse import quote

import requests

GOOGLE_NEWS_RSS_URL = "https://news.google.com/rss/search?q={query}&hl=ko&gl=KR&ceid=KR:ko"


def _strip_html(raw_html: str) -> str:
    """RSS description 필드에 섞여 있는 HTML 태그를 제거한다."""
    text = re.sub(r"<[^>]+>", " ", raw_html or "")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def fetch_google_news(keyword: str, max_articles: int = 8) -> list[dict]:
    """
    구글 뉴스 RSS에서 keyword로 검색한 기사 목록을 반환한다.
    각 항목: { title, link, source, published, summary }
    """
    url = GOOGLE_NEWS_RSS_URL.format(query=quote(keyword))

    resp = requests.get(
        url,
        headers={"User-Agent": "Mozilla/5.0 (ProblemExtractorBot/1.0)"},
        timeout=10,
    )
    resp.raise_for_status()

    root = ET.fromstring(resp.content)
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