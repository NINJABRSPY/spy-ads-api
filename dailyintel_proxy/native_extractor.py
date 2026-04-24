"""Extrator de VSL nativa (sem watermark) das landing pages dos anunciantes.

Abre `page_link` via Playwright e intercepta a primeira request HLS master.
Suporta ConverteAI (dominante em DR BR), Vidalytics, e HLS genérico.

Retorna:
    {
        "master_url": "https://cdn.converteai.net/.../main.m3u8",
        "player": "converteai" | "vidalytics" | "generic_hls",
        "poster_url": "...",        # se encontrado
        "title": "...",
        "extracted_at": 1714000000,
        "page_link": "https://..."
    }

ou None se nao conseguiu.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from pathlib import Path
from typing import Optional

log = logging.getLogger("native_extractor")

# Order matters — mais especifico primeiro
_HLS_PATTERNS = [
    # ConverteAI (brasileiro, dominante em DR)
    (r"cdn\.converteai\.net/[a-f0-9-]+/[a-f0-9]+/main\.m3u8", "converteai"),
    # Vidalytics (US)
    (r"fast\.vidalytics\.com/video/[^/]+/[^/]+/[^/]+/[^/]+__FFMPEG/stream\.m3u8", "vidalytics"),
    # Wistia HLS
    (r"embed-ssl\.wistia\.com/deliveries/[^/\"']+\.m3u8", "wistia"),
    (r"fast\.wistia\.com/embed/medias/([a-z0-9]+)\.m3u8", "wistia"),
    # Vimeo master
    (r"vod-adaptive(-akc)?\.akamaized\.net/exp=[0-9]+~acl=[^\"']+\.mpd", "vimeo"),
    # JWPlayer
    (r"cdn\.jwplayer\.com/manifests/[a-zA-Z0-9]+\.m3u8", "jwplayer"),
    # Cloudflare Stream
    (r"videodelivery\.net/[a-f0-9]+/manifest/video\.m3u8", "cloudflare_stream"),
    # BridTV
    (r"services\.brid\.tv/services/[^\"']+\.m3u8", "brid"),
    # Generic (ultimo fallback)
    (r"https?://[^\"'\s]+/(?:master|playlist|manifest|stream)\.m3u8(?:\?[^\"'\s]*)?", "generic_hls"),
]


async def extract_native_hls(page_link: str, timeout_s: int = 25) -> Optional[dict]:
    """Visita a landing e intercepta a primeira request HLS master."""
    from playwright.async_api import async_playwright

    found = {"master_url": None, "player": None, "poster_url": None, "title": None}

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox", "--disable-dev-shm-usage"],
        )
        ctx = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 720},
            locale="pt-BR",
        )
        page = await ctx.new_page()

        def _on_request(req):
            if found["master_url"]:
                return
            url = req.url
            # Excluir URLs que sabemos ser do DailyIntel (com watermark)
            if "b-cdn.net" in url and ("vz-54ae9b93" in url or "vz-077e15c9" in url):
                return
            if "dailyintelservice.com" in url or "iframe.mediadelivery.net" in url:
                return
            for patt, player in _HLS_PATTERNS:
                if re.search(patt, url, re.IGNORECASE):
                    found["master_url"] = url
                    found["player"] = player
                    return

        page.on("request", _on_request)

        try:
            await page.goto(page_link, wait_until="domcontentloaded", timeout=timeout_s * 1000)
        except Exception as e:
            log.debug(f"page.goto warn {page_link}: {e}")

        # Aguardar o player carregar (ate 20s)
        for _ in range(40):
            if found["master_url"]:
                break
            await page.wait_for_timeout(500)

        # Pegar title + poster do DOM
        try:
            info = await page.evaluate(
                """() => {
                    const poster = document.querySelector('video[poster], [poster]')?.getAttribute('poster') || '';
                    return {
                        title: document.title || '',
                        poster: poster,
                    };
                }"""
            )
            found["title"] = (info.get("title") or "")[:200]
            found["poster_url"] = info.get("poster") or ""
        except Exception:
            pass

        await browser.close()

    if not found["master_url"]:
        return None

    found["extracted_at"] = int(time.time())
    found["page_link"] = page_link
    return found


class NativeCache:
    """Cache em disco dos master URLs descobertos por rowId."""

    def __init__(self, path: str):
        self.path = Path(path)
        self.path.mkdir(parents=True, exist_ok=True)
        self._index_file = self.path / "index.json"
        self._index = self._load()

    def _load(self) -> dict:
        if not self._index_file.exists():
            return {}
        try:
            with open(self._index_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _save(self):
        tmp = self._index_file.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._index, f, ensure_ascii=False, indent=2)
        tmp.replace(self._index_file)

    def get(self, row_id: str) -> Optional[dict]:
        return self._index.get(row_id)

    def set(self, row_id: str, data: dict):
        self._index[row_id] = data
        self._save()

    def mark_failed(self, row_id: str, reason: str):
        self._index[row_id] = {
            "failed": True,
            "reason": reason[:200],
            "tried_at": int(time.time()),
        }
        self._save()

    def stats(self) -> dict:
        total = len(self._index)
        success = sum(1 for v in self._index.values() if v.get("master_url"))
        failed = sum(1 for v in self._index.values() if v.get("failed"))
        by_player = {}
        for v in self._index.values():
            p = v.get("player")
            if p:
                by_player[p] = by_player.get(p, 0) + 1
        return {"total": total, "success": success, "failed": failed, "by_player": by_player}
