import asyncio
import logging

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ru,en;q=0.9",
}


def _text_snippet(text: str, limit: int = 200) -> str:
    compact = " ".join(text.split())
    return compact[:limit]


async def _fetch_json(url: str, username: str, params: dict | None = None) -> dict | None:
    try:
        response = await asyncio.to_thread(
            requests.get,
            url,
            timeout=10,
            headers=HEADERS,
            params=params,
        )
    except Exception as exc:
        logger.warning(
            "rating_request_failed: username=%s url=%s params=%s error=%s",
            username,
            url,
            params,
            exc,
        )
        return None
    if response.status_code != 200:
        logger.warning(
            "rating_http_status: username=%s url=%s status=%s",
            username,
            response.url,
            response.status_code,
        )
        return None
    try:
        payload = response.json()
    except ValueError:
        logger.warning(
            "rating_invalid_json: username=%s url=%s body_snippet=%s",
            username,
            response.url,
            _text_snippet(response.text),
        )
        return None
    if not payload.get("status"):
        logger.warning(
            "rating_api_error: username=%s url=%s errors=%s body_snippet=%s",
            username,
            response.url,
            payload.get("errors"),
            _text_snippet(response.text),
        )
        return None
    data = payload.get("data")
    if data is None:
        logger.warning(
            "rating_api_missing_data: username=%s url=%s body_snippet=%s",
            username,
            response.url,
            _text_snippet(response.text),
        )
        return None
    return data


async def _get_rating_api(username: str) -> int | None:
    user_url = "https://hackerlab.pro/game_api/users"
    user_data = await _fetch_json(user_url, username, params={"filter.login": username})
    if not user_data:
        return None
    user_id = user_data.get("id")
    if not user_id:
        logger.warning("rating_user_missing_id: username=%s data=%s", username, user_data)
        return None
    scoreboard_url = "https://hackerlab.pro/game_api/scoreboard/user"
    scoreboard = await _fetch_json(scoreboard_url, username, params={"filter.id": user_id})
    if not scoreboard:
        return None
    place = scoreboard.get("place")
    if place is None:
        logger.warning(
            "rating_missing_place: username=%s user_id=%s updated_at=%s",
            username,
            user_id,
            scoreboard.get("updated_at"),
        )
        return None
    try:
        return int(place)
    except (TypeError, ValueError):
        logger.warning(
            "rating_invalid_place: username=%s user_id=%s place=%s",
            username,
            user_id,
            place,
        )
        return None


async def _get_rating_html(username: str) -> int | None:
    url = f"https://hackerlab.pro/users/{username}"
    html_headers = dict(HEADERS)
    html_headers["Accept"] = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
    try:
        response = await asyncio.to_thread(requests.get, url, timeout=10, headers=html_headers)
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
            _text_snippet(response.text),
        )
        return None
    container = img.find_parent("div")
    if not container:
        logger.warning(
            "rating_parse_missing_container: username=%s url=%s html_snippet=%s",
            username,
            url,
            _text_snippet(response.text),
        )
        return None
    rating_div = container.find_next_sibling("div")
    if not rating_div:
        logger.warning(
            "rating_parse_missing_value: username=%s url=%s html_snippet=%s",
            username,
            url,
            _text_snippet(response.text),
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
            _text_snippet(response.text),
        )
        return None


async def get_rating(username: str) -> int | None:
    username = username.strip()
    if not username:
        logger.warning("rating_empty_username")
        return None
    api_rating = await _get_rating_api(username)
    if api_rating is not None:
        return api_rating
    return await _get_rating_html(username)
