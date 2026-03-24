"""
AdsParo API Client
"""

import time
import requests
from datetime import datetime

ADSPARO_TOKEN = "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzUxMiJ9.eyJpYXQiOjE3NzQyNzM4NTEsImV4cCI6MTc3NTEzNzg1MSwidmVyIjoiNjg3MTllYzIxOGI2ZDE4NmUxOTk3OGIwMTA2NDIzN2FiNDVhZjE5YSIsInVubSI6ImNkdW9MQ1NFTVFBVzBoOHVyU0tvVUxBUVI1UEd6d3gwRm5NM3FPQ2NROUk9IiwiY2lkIjoiS2xuL2RPWXAxSkFwcElDNW42VWkzcDRXVm1ZakxXRFMxSFpGUFRvN1FtST0iLCJhdWQiOiJhZHNwYXJvLmNvbS9hcGkiLCJleHBfcyI6MTc3NDI3NDE1MSwiaXNzIjoxNzYyODQsInN1Yl9lbmQiOjE3NzY5NTgzNDd9.29HIA-vHXbE59jkT3T4uwWO9_0-clmqnQSXqA3Zrwi_v6lhBiU14UuCffr4KoBkKrwAGXrIN9UU9-rmK58vMag"
ADSPARO_DELAY = 3

def search_ads(keyword, country="", sort_dir="desc", min_ads=1, max_ads=1000):
    """Busca anuncios no AdsParo por keyword"""
    print(f"  [AdsParo] keyword='{keyword}'")

    headers = {
        "authorization": ADSPARO_TOKEN,
        "content-type": "application/x-www-form-urlencoded",
    }

    data = {
        "searchby": "",
        "searchtext": keyword,
        "sortby": "",
        "sortdir": sort_dir,
        "minads": str(min_ads),
        "maxads": str(max_ads),
        "country": country,
        "language": "",
        "tld": "",
        "startdate": "",
        "enddate": "",
        "ad_pl_id": "",
        "type": "",
    }

    try:
        r = requests.post("https://adsparo.com/api/ad/read.php",
                          headers=headers, data=data, timeout=60)
        result = r.json()
        ads = result.get("ads", [])
        print(f"    {len(ads)} ads encontrados")
        time.sleep(ADSPARO_DELAY)
        return ads
    except Exception as e:
        print(f"    ERRO: {e}")
        return []


def normalize_adsparo_ad(ad, keyword):
    """Converte ad do AdsParo para schema unificado"""

    # Determinar plataformas
    platforms = []
    if ad.get("a_tiktok"): platforms.append("tiktok")
    if ad.get("a_pinterest"): platforms.append("pinterest")
    if ad.get("a_twitter"): platforms.append("twitter")
    if ad.get("a_snapchat"): platforms.append("snapchat")
    if ad.get("a_google_conversion"): platforms.append("google")
    # Se nenhuma outra, e Facebook (padrao do AdsParo)
    platform = "facebook"

    # Calcular dias rodando
    days_running = 0
    try:
        from datetime import datetime as dt
        d1 = dt.strptime(ad.get("date_found", "")[:10], "%Y-%m-%d")
        d2 = dt.strptime(ad.get("date_updated", "")[:10], "%Y-%m-%d")
        days_running = (d2 - d1).days
    except:
        pass

    # Contar plataformas onde roda
    also_on = []
    if ad.get("a_tiktok"): also_on.append("tiktok")
    if ad.get("a_pinterest"): also_on.append("pinterest")
    if ad.get("a_twitter"): also_on.append("twitter")
    if ad.get("a_snapchat"): also_on.append("snapchat")
    if ad.get("a_google_conversion"): also_on.append("google")

    return {
        "ad_id": str(ad.get("id", "")),
        "source": "adsparo",
        "platform": platform,
        "advertiser": ad.get("p_title", ""),
        "advertiser_username": ad.get("p_username", ""),
        "advertiser_image": ad.get("p_img", ""),
        "facebook_page_id": str(ad.get("p_page_id", "")),
        "title": ad.get("p_title", ""),
        "body": (ad.get("description", "") or "")[:800],
        "cta": "",
        "landing_page": ad.get("cta_link", ""),
        "image_url": ad.get("thumbnail", ""),
        "video_url": ad.get("video_link", ""),
        "first_seen": ad.get("date_found", ""),
        "last_seen": ad.get("date_updated", ""),
        "is_active": True,
        # Engajamento
        "likes": 0,
        "comments": 0,
        "shares": 0,
        "impressions": 0,
        # Benchmarking
        "days_running": days_running,
        "heat": 0,
        "ad_type": "video" if ad.get("video_link") else "image",
        "video_duration": 0,
        "total_ads_from_advertiser": int(ad.get("totalads", 0) or 0),
        "max_ads_from_advertiser": int(ad.get("max_totalads", 0) or 0),
        "country": ad.get("country", ""),
        "all_countries": ad.get("all_countries", ""),
        "channels": ", ".join(["facebook"] + also_on),
        # EXCLUSIVOS ADSPARO
        "also_on_tiktok": bool(ad.get("a_tiktok", False)),
        "also_on_pinterest": bool(ad.get("a_pinterest", False)),
        "also_on_twitter": bool(ad.get("a_twitter", False)),
        "also_on_snapchat": bool(ad.get("a_snapchat", False)),
        "uses_google_conversion": bool(ad.get("a_google_conversion", False)),
        "also_on": ", ".join(also_on) if also_on else "",
        "page_banned": bool(ad.get("p_banned", False)),
        "max_country": ad.get("max_country", ""),
        "peak_date": ad.get("max_lastupdate", ""),
        "has_store": bool(ad.get("cta_link")),
        "total_engagement": 0,
        "search_keyword": keyword,
        "collected_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
