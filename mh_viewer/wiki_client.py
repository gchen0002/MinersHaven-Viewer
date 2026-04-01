from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import requests


API_URL = "https://minershaven.fandom.com/api.php"


@dataclass(slots=True)
class WikiPage:
    title: str
    pageid: int
    revid: int
    content: str


class WikiClient:
    def __init__(self, api_url: str = API_URL, timeout: int = 30, pause_seconds: float = 0.05) -> None:
        self.api_url = api_url
        self.timeout = timeout
        self.pause_seconds = pause_seconds
        self.session = requests.Session()

    def _get(self, params: dict[str, Any]) -> dict[str, Any]:
        response = self.session.get(self.api_url, params=params, timeout=self.timeout)
        response.raise_for_status()
        payload = response.json()
        if self.pause_seconds > 0:
            time.sleep(self.pause_seconds)
        return payload

    def get_category_members(self, category_title: str) -> list[str]:
        members: list[str] = []
        cmcontinue: str | None = None
        while True:
            params: dict[str, Any] = {
                "action": "query",
                "list": "categorymembers",
                "cmtitle": category_title,
                "cmlimit": 500,
                "format": "json",
            }
            if cmcontinue:
                params["cmcontinue"] = cmcontinue

            payload = self._get(params)
            for member in payload.get("query", {}).get("categorymembers", []):
                title = member.get("title")
                if isinstance(title, str):
                    members.append(title)

            cmcontinue = payload.get("continue", {}).get("cmcontinue")
            if not cmcontinue:
                break
        return members

    def get_revision_map(self, titles: list[str], batch_size: int = 30) -> dict[str, int]:
        revisions: dict[str, int] = {}
        for batch in _chunks(titles, batch_size):
            payload = self._get(
                {
                    "action": "query",
                    "prop": "revisions",
                    "rvslots": "main",
                    "rvprop": "ids",
                    "titles": "|".join(batch),
                    "format": "json",
                    "formatversion": 2,
                }
            )
            pages = payload.get("query", {}).get("pages", [])
            for page in pages:
                title = page.get("title")
                if not isinstance(title, str):
                    continue
                page_revisions = page.get("revisions") or []
                if not page_revisions:
                    continue
                revid = page_revisions[0].get("revid")
                if isinstance(revid, int):
                    revisions[title] = revid
        return revisions

    def get_pages_wikitext(self, titles: list[str], batch_size: int = 12) -> list[WikiPage]:
        pages_out: list[WikiPage] = []
        for batch in _chunks(titles, batch_size):
            payload = self._get(
                {
                    "action": "query",
                    "prop": "revisions",
                    "rvslots": "main",
                    "rvprop": "ids|content",
                    "titles": "|".join(batch),
                    "format": "json",
                    "formatversion": 2,
                }
            )
            pages = payload.get("query", {}).get("pages", [])
            for page in pages:
                title = page.get("title")
                pageid = page.get("pageid")
                if not isinstance(title, str) or not isinstance(pageid, int):
                    continue
                revisions = page.get("revisions") or []
                if not revisions:
                    continue
                revision = revisions[0]
                revid = revision.get("revid")
                content = revision.get("slots", {}).get("main", {}).get("content")
                if not isinstance(revid, int) or not isinstance(content, str):
                    continue
                pages_out.append(WikiPage(title=title, pageid=pageid, revid=revid, content=content))
        return pages_out


def _chunks(items: list[str], batch_size: int) -> list[list[str]]:
    return [items[i : i + batch_size] for i in range(0, len(items), batch_size)]
