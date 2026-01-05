import asyncio
import requests
from bs4 import BeautifulSoup


async def get_rating(username: str) -> int | None:
    url = f"https://hackerlab.pro/users/{username}"
    try:
        response = await asyncio.to_thread(requests.get, url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
    except Exception:
        return None
    if response.status_code != 200:
        return None
    soup = BeautifulSoup(response.text, "html.parser")
    img = soup.find("img", alt="Рейтинг")
    if not img:
        return None
    container = img.find_parent("div")
    if not container:
        return None
    rating_div = container.find_next_sibling("div")
    if not rating_div:
        return None
    text = rating_div.get_text(strip=True)
    try:
        return int(text)
    except ValueError:
        return None