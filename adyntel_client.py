"""
Adyntel API Client
Busca anuncios em Meta, Google, LinkedIn, TikTok + Keywords
"""

import time
import requests
from datetime import datetime
from config import ADYNTEL_API_KEY, ADYNTEL_EMAIL, ADYNTEL_DELAY

BASE = "https://api.adyntel.com"


def _post(endpoint, extra_params=None):
    """Request base para Adyntel"""
    body = {
        "api_key": ADYNTEL_API_KEY,
        "email": ADYNTEL_EMAIL,
    }
    if extra_params:
        body.update(extra_params)

    try:
        r = requests.post(f"{BASE}/{endpoint}", json=body, timeout=60,
                          headers={"Content-Type": "application/json"})
        if r.status_code == 204:
            return {"status": "no_content", "data": []}
        if r.status_code == 200:
            return r.json()
        return {"error": f"HTTP {r.status_code}", "body": r.text[:300]}
    except Exception as e:
        return {"error": str(e)}


def search_meta_by_keyword(keyword, country_code="ALL"):
    """Busca anuncios no Meta (Facebook/Instagram) por keyword"""
    print(f"  [Adyntel Meta] keyword='{keyword}' country={country_code}")
    data = _post("facebook_ad_search", {
        "keyword": keyword,
        "country_code": country_code,
    })
    time.sleep(ADYNTEL_DELAY)
    return data


def search_meta_by_domain(domain):
    """Busca anuncios Meta de uma empresa por dominio"""
    print(f"  [Adyntel Meta] domain='{domain}'")
    data = _post("facebook", {"company_domain": domain})
    time.sleep(ADYNTEL_DELAY)
    return data


def search_google(domain, media_type=None):
    """Busca anuncios Google de uma empresa"""
    print(f"  [Adyntel Google] domain='{domain}'")
    params = {"company_domain": domain}
    if media_type:
        params["media_type"] = media_type
    data = _post("google", params)
    time.sleep(ADYNTEL_DELAY)
    return data


def search_linkedin(domain):
    """Busca anuncios LinkedIn de uma empresa"""
    print(f"  [Adyntel LinkedIn] domain='{domain}'")
    data = _post("linkedin", {"company_domain": domain})
    time.sleep(ADYNTEL_DELAY)
    return data


def search_tiktok(keyword, country_code="ALL"):
    """Busca anuncios TikTok por keyword"""
    print(f"  [Adyntel TikTok] keyword='{keyword}'")
    data = _post("tiktok_search", {
        "keyword": keyword,
        "country_code": country_code,
    })
    time.sleep(ADYNTEL_DELAY)
    return data


def get_domain_keywords(domain):
    """Analise de keywords pagas vs organicas"""
    print(f"  [Adyntel Keywords] domain='{domain}'")
    data = _post("domain-keywords", {"company_domain": domain})
    time.sleep(ADYNTEL_DELAY)
    return data


# ============================================================
# NORMALIZACAO - Converte Adyntel para schema unificado
# ============================================================

def normalize_meta_keyword_ads(data, keyword):
    """Normaliza anuncios do Meta Ad Search"""
    ads = []
    results = data.get("results", data.get("data", []))
    if not isinstance(results, list):
        return ads

    # Achatar lista aninhada
    flat = []
    for item in results:
        if isinstance(item, list):
            flat.extend(item)
        elif isinstance(item, dict):
            flat.append(item)
    results = flat

    for ad in results:
        snapshot = ad.get("snapshot", {})
        body_text = ""
        if snapshot.get("body"):
            body_text = snapshot["body"].get("text", "") if isinstance(snapshot["body"], dict) else str(snapshot["body"])

        images = []
        if snapshot.get("images"):
            images = [img.get("original_image_url", "") for img in snapshot["images"] if isinstance(img, dict)]

        ads.append({
            "ad_id": str(ad.get("adArchiveID", ad.get("ad_archive_id", ""))),
            "source": "adyntel_meta",
            "platform": "facebook",
            "advertiser": ad.get("pageName", ad.get("page_name", "")),
            "title": snapshot.get("title", ""),
            "body": (body_text or "")[:500],
            "cta": snapshot.get("cta_text", ""),
            "landing_page": snapshot.get("link_url", ""),
            "image_url": images[0] if images else "",
            "video_url": "",
            "first_seen": "",
            "last_seen": "",
            "is_active": ad.get("isActive", ad.get("is_active", None)),
            "likes": 0,
            "comments": 0,
            "shares": 0,
            "search_keyword": keyword,
            "collected_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        })
    return ads


def normalize_meta_domain_ads(data, domain):
    """Normaliza anuncios Meta por dominio"""
    results = data.get("results", [])
    if not isinstance(results, list):
        return []

    # Achatar lista aninhada: [[ad], [ad]] -> [ad, ad]
    flat = []
    for item in results:
        if isinstance(item, list):
            flat.extend(item)
        elif isinstance(item, dict):
            flat.append(item)
    results = flat

    ads = []
    for ad in results:
        snapshot = ad.get("snapshot", {})
        body_text = ""
        if snapshot.get("body"):
            body_text = snapshot["body"].get("text", "") if isinstance(snapshot["body"], dict) else str(snapshot["body"])

        images = []
        if snapshot.get("images"):
            images = [img.get("original_image_url", "") for img in snapshot["images"] if isinstance(img, dict)]

        ads.append({
            "ad_id": str(ad.get("ad_archive_id", "")),
            "source": "adyntel_meta",
            "platform": "facebook",
            "advertiser": ad.get("page_name", ""),
            "title": snapshot.get("title", ""),
            "body": (body_text or "")[:500],
            "cta": snapshot.get("cta_text", ""),
            "landing_page": snapshot.get("link_url", ""),
            "image_url": images[0] if images else "",
            "video_url": "",
            "first_seen": "",
            "last_seen": "",
            "is_active": ad.get("is_active", None),
            "likes": 0,
            "comments": 0,
            "shares": 0,
            "search_keyword": domain,
            "collected_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        })
    return ads


def normalize_google_ads(data, domain):
    """Normaliza anuncios Google"""
    results = data.get("ads", [])
    if not isinstance(results, list):
        return []

    ads = []
    for ad in results:
        ads.append({
            "ad_id": str(ad.get("creative_id", "")),
            "source": "adyntel_google",
            "platform": "google",
            "advertiser": ad.get("advertiser_name", ""),
            "title": ad.get("advertiser_name", ""),
            "body": "",
            "cta": "",
            "landing_page": ad.get("original_url", ""),
            "image_url": "",
            "video_url": "",
            "first_seen": ad.get("start", ""),
            "last_seen": ad.get("last_seen", ""),
            "is_active": None,
            "likes": 0,
            "comments": 0,
            "shares": 0,
            "search_keyword": domain,
            "collected_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        })
    return ads


def normalize_linkedin_ads(data, domain):
    """Normaliza anuncios LinkedIn"""
    results = data.get("ads", [])
    if not isinstance(results, list):
        return []

    ads = []
    for ad in results:
        advertiser = ad.get("advertiser", {})
        headline = ad.get("headline", {})
        commentary = ad.get("commentary", {})

        ads.append({
            "ad_id": str(ad.get("ad_id", "")),
            "source": "adyntel_linkedin",
            "platform": "linkedin",
            "advertiser": advertiser.get("name", "") if isinstance(advertiser, dict) else "",
            "title": headline.get("text", "") if isinstance(headline, dict) else "",
            "body": (commentary.get("text", "") if isinstance(commentary, dict) else "")[:500],
            "cta": "",
            "landing_page": ad.get("view_details_link", ""),
            "image_url": "",
            "video_url": "",
            "first_seen": "",
            "last_seen": "",
            "is_active": True,
            "likes": 0,
            "comments": 0,
            "shares": 0,
            "search_keyword": domain,
            "collected_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        })
    return ads


def normalize_tiktok_ads(data, keyword):
    """Normaliza anuncios TikTok"""
    results = data.get("data", [])
    if not isinstance(results, list):
        return []

    ads = []
    for ad in results:
        video_url = ""
        if ad.get("videos") and isinstance(ad["videos"], list) and len(ad["videos"]) > 0:
            video_url = ad["videos"][0].get("video_url", "")

        image_url = ""
        if ad.get("image_urls") and isinstance(ad["image_urls"], list) and len(ad["image_urls"]) > 0:
            image_url = ad["image_urls"][0]

        ads.append({
            "ad_id": str(ad.get("id", "")),
            "source": "adyntel_tiktok",
            "platform": "tiktok",
            "advertiser": ad.get("name", ""),
            "title": ad.get("name", ""),
            "body": "",
            "cta": "",
            "landing_page": "",
            "image_url": image_url,
            "video_url": video_url,
            "first_seen": "",
            "last_seen": "",
            "is_active": True,
            "likes": 0,
            "comments": 0,
            "shares": 0,
            "search_keyword": keyword,
            "collected_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        })
    return ads
