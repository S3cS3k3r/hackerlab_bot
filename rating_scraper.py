import asyncio
import logging

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)


def _html_snippet(html: str, limit: int = 200) -> str:
    compact = " ".join(html.split())
    return compact[:limit]


async def get_rating(username: str) -> int | None:
    url = f"https://hackerlab.pro/users/{username}"
    try:
        response = await asyncio.to_thread(requests.get, url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
    except Exception as exc:
        logger.warning(
            "rating_request_failed: username=%s url=%s error=%s",
            username,
            url,
            exc,
        )
        return None
    if response.status_code != 200:
        logger.warning(
            "rating_http_status: username=%s url=%s status=%s",
            username,
            url,
            response.status_code,
        )
        return None
    soup = BeautifulSoup(response.text, "html.parser")
    img = soup.find("img", alt="Рейтинг")
    if not img:
        logger.warning(
            "rating_parse_missing_img: username=%s url=%s html_len=%s html_snippet=%s",
            username,
            url,
            len(response.text),
            _html_snippet(response.text),
        )
        return None
    container = img.find_parent("div")
    if not container:
        logger.warning(
            "rating_parse_missing_container: username=%s url=%s html_snippet=%s",
            username,
            url,
            _html_snippet(response.text),
        )
        return None
    rating_div = container.find_next_sibling("div")
    if not rating_div:
        logger.warning(
            "rating_parse_missing_value: username=%s url=%s html_snippet=%s",
            username,
            url,
            _html_snippet(response.text),
        )
        return None
    text = rating_div.get_text(strip=True)
    try:
        return int(text)
    except ValueError:
        logger.warning(
            "rating_parse_invalid_value: username=%s url=%s text=%s html_snippet=%s",
            username,
            url,
            text,
            _html_snippet(response.text),
        )
        return None
