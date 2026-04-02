"""
SearchAPI.io Scraper — Meta Ad Library + YouTube
Integra dados oficiais do Facebook/Instagram + YouTube no NinjaSpy
"""

import json
import glob
import time
import requests
from datetime import datetime, timedelta
from pathlib import Path

API_KEY = "ZFDmiHTH75sZT3wjDBc7vGay"
BASE_URL = "https://www.searchapi.io/api/v1/search"
OUTPUT_DIR = "resultados"
DELAY = 2  # seconds between requests

# Keywords para buscar no Meta Ad Library
META_KEYWORDS = [
    "weight loss", "skincare", "fitness", "dropshipping",
    "supplement", "keto", "anti aging", "hair growth",
    "dental", "prostate", "testosterone", "diabetes",
    "manifestation", "make money online", "crypto",
    "dog training", "anxiety", "sleep", "joint pain",
    "ecommerce", "curso online", "marketing digital",
    "emagrecer", "suplemento", "renda extra",
]

META_COUNTRIES = ["US", "BR", "GB"]


def meta_search(keyword, country="US", limit=30):
    """Busca ads no Meta Ad Library via SearchAPI"""
    params = {
        "engine": "meta_ad_library",
        "q": keyword,
        "country": country,
        "active_status": "active",
        "media_type": "all",
        "sort_by": "impressions_high_to_low",
        "api_key": API_KEY,
    }

    try:
        r = requests.get(BASE_URL, params=params, timeout=30)
        data = r.json()

        if "error" in data:
            print(f"    Error: {data['error']}")
            return []

        ads = data.get("ads", [])
        print(f"    {len(ads)} ads found")
        return ads
    except Exception as e:
        print(f"    Error: {e}")
        return []


def enrich_with_existing_data(normalized_ad, existing_ads_by_advertiser):
    """Enriquece ad do Meta com métricas dos nossos scrapers (BigSpy, Minea, etc)"""
    advertiser = (normalized_ad.get("advertiser") or "").lower().strip()
    if not advertiser or advertiser not in existing_ads_by_advertiser:
        return normalized_ad

    # Pegar ads do mesmo anunciante que têm métricas
    matches = existing_ads_by_advertiser[advertiser]

    # Usar as melhores métricas disponíveis
    best_impressions = max((a.get("impressions", 0) or 0) for a in matches)
    best_likes = max((a.get("likes", 0) or 0) for a in matches)
    best_comments = max((a.get("comments", 0) or 0) for a in matches)
    best_shares = max((a.get("shares", 0) or 0) for a in matches)
    best_engagement = max((a.get("total_engagement", 0) or 0) for a in matches)
    best_heat = max((a.get("heat", 0) or 0) for a in matches)
    best_spend = max((a.get("estimated_spend", 0) or 0) for a in matches)
    best_score = max((a.get("potential_score", 0) or 0) for a in matches)

    # AI data do melhor match
    best_ai = max(matches, key=lambda a: a.get("potential_score", 0) or 0)

    normalized_ad["impressions"] = best_impressions if best_impressions > 0 else normalized_ad.get("impressions", 0)
    normalized_ad["likes"] = best_likes
    normalized_ad["comments"] = best_comments
    normalized_ad["shares"] = best_shares
    normalized_ad["total_engagement"] = best_engagement if best_engagement > 0 else best_likes + best_comments + best_shares
    normalized_ad["heat"] = best_heat if best_heat > 0 else normalized_ad.get("heat", 0)
    normalized_ad["estimated_spend"] = best_spend if best_spend > 0 else normalized_ad.get("estimated_spend", 0)
    normalized_ad["potential_score"] = best_score if best_score > 0 else normalized_ad.get("potential_score", 0)

    # AI enrichment
    for field in ["ai_niche", "ai_target_audience", "ai_strategy", "ai_hook_type",
                   "ai_product_type", "ai_copy_quality", "ai_emotion", "ai_language"]:
        if best_ai.get(field):
            normalized_ad[field] = best_ai[field]

    # Marcar que foi enriquecido
    normalized_ad["enriched_from"] = [a.get("source", "") for a in matches[:3]]
    normalized_ad["enriched_sources_count"] = len(set(a.get("source", "") for a in matches))

    return normalized_ad


def normalize_meta_ad(ad, keyword, country):
    """Converte ad do Meta Ad Library para formato unified NinjaSpy"""
    snapshot = ad.get("snapshot", {})
    page = ad.get("page", {})

    # Extrair body text
    body = ""
    raw_body = snapshot.get("body")
    if raw_body:
        if isinstance(raw_body, list):
            parts = []
            for b in raw_body:
                if isinstance(b, dict):
                    parts.append(b.get("text", str(b)))
                else:
                    parts.append(str(b))
            body = " ".join(parts)
        elif isinstance(raw_body, dict):
            body = raw_body.get("text", str(raw_body))
        else:
            body = str(raw_body)

    # Extrair imagens
    images = snapshot.get("images", [])
    image_url = ""
    if images:
        if isinstance(images[0], dict):
            image_url = images[0].get("original_image_url", "") or images[0].get("url", "")
        elif isinstance(images[0], str):
            image_url = images[0]

    # Extrair video
    videos = snapshot.get("videos", [])
    video_url = ""
    if videos:
        if isinstance(videos[0], dict):
            video_url = videos[0].get("video_hd_url", "") or videos[0].get("video_sd_url", "") or videos[0].get("video_url", "") or ""
            if not image_url:
                image_url = videos[0].get("video_preview_image_url", "")
        elif isinstance(videos[0], str):
            video_url = videos[0]

    # Extrair CTA dos cards
    cards = snapshot.get("cards", [])
    cta = ""
    landing_page = ""
    title = snapshot.get("title", "")
    if isinstance(title, dict):
        title = title.get("text", str(title))
    if cards:
        card = cards[0] if isinstance(cards[0], dict) else {}
        cta = card.get("cta_text", "")
        landing_page = card.get("link_url", "")
        if not title:
            title = card.get("title", "")

    # Calcular dias rodando
    start_date = ad.get("start_date", "")
    days_running = 0
    if start_date:
        try:
            start = datetime.strptime(start_date[:10], "%Y-%m-%d")
            days_running = (datetime.now() - start).days
        except:
            pass

    # Plataformas
    platforms = ad.get("publisher_platform", [])
    platform = "facebook"
    if isinstance(platforms, list):
        if "instagram" in platforms:
            platform = "instagram"
        elif "facebook" in platforms:
            platform = "facebook"

    ad_id = f"meta_official_{ad.get('ad_archive_id', '')}"

    return {
        "ad_id": ad_id,
        "source": "meta_official",
        "platform": platform,
        "advertiser": snapshot.get("page_name", ""),
        "advertiser_image": snapshot.get("page_profile_picture_url", ""),
        "title": title[:200] if title else "",
        "body": body[:2000] if body else "",
        "cta": cta,
        "landing_page": landing_page,
        "image_url": image_url,
        "video_url": video_url,
        "first_seen": start_date,
        "last_seen": ad.get("end_date", "") or datetime.now().strftime("%Y-%m-%d"),
        "is_active": ad.get("is_active", True),
        "likes": 0,
        "comments": 0,
        "shares": 0,
        "impressions": 0,
        "days_running": days_running,
        "heat": min(1000, days_running * 10) if days_running > 0 else 0,
        "ad_type": "video" if video_url else "image",
        "video_duration": 0,
        "channels": ", ".join(platforms) if isinstance(platforms, list) else "",
        "has_store": False,
        "total_engagement": 0,
        "search_keyword": keyword,
        "collected_at": datetime.now().isoformat(),
        "estimated_spend": 0,
        "spend_source": "",
        "potential_score": min(10, max(1, days_running // 3)),
        "country": country,
        "all_countries": [country],
        # Meta official specific
        "meta_ad_archive_id": ad.get("ad_archive_id", ""),
        "meta_page_id": ad.get("page_id", ""),
        "meta_collation_count": ad.get("collation_count", 0),
        "meta_display_format": snapshot.get("display_format", ""),
        "meta_publisher_platforms": platforms,
        "meta_snapshot_url": ad.get("ad_snapshot_url", ""),
    }


def run_meta_scraper():
    """Roda o scraper completo do Meta Ad Library + enriquece com dados existentes"""
    print("=== META AD LIBRARY (SearchAPI) ===\n")

    # Carregar ads existentes para enriquecimento
    print("  Loading existing ads for enrichment...")
    existing_by_advertiser = {}
    unified_files = sorted(glob.glob(f"{OUTPUT_DIR}/unified_*.json"), reverse=True)
    if unified_files:
        with open(unified_files[0], "r", encoding="utf-8") as f:
            existing = json.load(f)
        for a in existing:
            name = (a.get("advertiser") or "").lower().strip()
            if name and len(name) > 2:
                if name not in existing_by_advertiser:
                    existing_by_advertiser[name] = []
                existing_by_advertiser[name].append(a)
        print(f"  Loaded {len(existing_by_advertiser)} advertisers for cross-reference\n")

    all_ads = []
    seen_ids = set()
    enriched_count = 0

    for country in META_COUNTRIES:
        for keyword in META_KEYWORDS:
            print(f"  [{country}] {keyword}...", end=" ")
            ads = meta_search(keyword, country)

            for ad in ads:
                normalized = normalize_meta_ad(ad, keyword, country)
                if normalized["ad_id"] not in seen_ids:
                    seen_ids.add(normalized["ad_id"])

                    # Enriquecer com dados existentes
                    before = normalized.get("impressions", 0)
                    normalized = enrich_with_existing_data(normalized, existing_by_advertiser)
                    if normalized.get("impressions", 0) > before:
                        enriched_count += 1

                    all_ads.append(normalized)

            time.sleep(DELAY)

    print(f"\n  Total: {len(all_ads)} unique ads ({enriched_count} enriched with metrics)")

    # Save
    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
    output = {
        "source": "meta_official",
        "scraped_at": datetime.now().isoformat(),
        "total": len(all_ads),
        "enriched": enriched_count,
        "countries": META_COUNTRIES,
        "keywords": META_KEYWORDS,
        "ads": all_ads,
    }

    filename = f"{OUTPUT_DIR}/meta_official_{timestamp}.json"
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"  Saved: {filename}")

    # Merge with unified
    merge_to_unified(all_ads)

    return len(all_ads)


def merge_to_unified(new_ads):
    """Merge novos ads no unified JSON"""
    unified_files = sorted(glob.glob(f"{OUTPUT_DIR}/unified_*.json"), reverse=True)
    if not unified_files:
        print("No unified file found")
        return

    unified_path = unified_files[0]
    with open(unified_path, "r", encoding="utf-8") as f:
        existing = json.load(f)

    existing_ids = {a.get("ad_id", "") for a in existing if a.get("ad_id")}
    added = [a for a in new_ads if a["ad_id"] not in existing_ids]

    if added:
        combined = existing + added
        with open(unified_path, "w", encoding="utf-8") as f:
            json.dump(combined, f, ensure_ascii=False)
        print(f"Merged: +{len(added)} new ads -> unified (total: {len(combined)})")
    else:
        print(f"No new ads to merge ({len(new_ads)} already exist)")


def run():
    """Executa todos os scrapers do SearchAPI"""
    total = 0
    total += run_meta_scraper()
    print(f"\n=== SEARCHAPI TOTAL: {total} new ads ===")
    return total


if __name__ == "__main__":
    run()
