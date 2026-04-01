"""
Converte dados do Social1 (TikTok Shop) para o formato unified do NinjaSpy
e faz merge com o unified_*.json existente
"""

import json
import glob
import os
from datetime import datetime

OUTPUT_DIR = "resultados"


def convert_and_merge():
    # Carregar dados Social1
    prod_files = sorted(glob.glob(f"{OUTPUT_DIR}/social1_products_*.json"), reverse=True)
    vid_files = sorted(glob.glob(f"{OUTPUT_DIR}/social1_videos_*.json"), reverse=True)

    if not prod_files and not vid_files:
        print("Nenhum arquivo Social1 encontrado")
        return 0

    new_ads = []

    # --- PRODUCTS -> unified ads ---
    if prod_files:
        with open(prod_files[0], "r", encoding="utf-8") as f:
            prod_data = json.load(f)

        for p in prod_data.get("products", []):
            name = p.get("name", "")
            if not name:
                continue

            units = p.get("units_sold", 0) or 0
            gmv = float(p.get("gmv", 0) or 0)
            views = p.get("video_views", 0) or 0
            creators = p.get("creator_count", 0) or 0
            price = float(p.get("price_value", 0) or 0)
            categories = p.get("categories", [])
            shop = p.get("shop", {})

            new_ads.append({
                "ad_id": f"social1_prod_{p.get('product_id', '')}",
                "source": "social1",
                "platform": "tiktok",
                "advertiser": shop.get("shop_name", "TikTok Shop"),
                "advertiser_image": shop.get("shop_img_url", ""),
                "title": name[:200],
                "body": f"Units sold: {units:,} | GMV: ${gmv:,.0f} | {creators:,} creators promoting | {categories[0] if categories else ''}",
                "cta": "Shop Now",
                "landing_page": "",
                "image_url": p.get("product_img_url", ""),
                "video_url": "",
                "first_seen": p.get("last_scrape", "") or p.get("_scraped_at", ""),
                "last_seen": p.get("_scraped_at", datetime.now().isoformat()),
                "is_active": True,
                "likes": 0,
                "comments": 0,
                "shares": 0,
                "impressions": views,
                "days_running": p.get("_days", 1),
                "heat": min(1000, int(views / 10000)) if views else 0,
                "ad_type": "product",
                "video_duration": 0,
                "channels": [],
                "has_store": True,
                "total_engagement": views,
                "search_keyword": categories[0].lower() if categories else "tiktok shop",
                "collected_at": p.get("_scraped_at", datetime.now().isoformat()),
                "estimated_spend": 0,
                "spend_source": "",
                "potential_score": min(10, round(
                    min(3, views / 1000000) +
                    min(3, units / 50000) +
                    min(2, creators / 1000) +
                    min(2, gmv / 5000000)
                )),
                # Campos extras Social1
                "country": p.get("_region", "").upper(),
                "all_countries": [p.get("_region", "").upper()],
                "social1_units_sold": units,
                "social1_gmv": round(gmv, 2),
                "social1_creators": creators,
                "social1_videos": p.get("video_count", 0),
                "social1_price": price,
                "social1_category": categories[0] if categories else "",
                "social1_subcategory": categories[1] if len(categories) > 1 else "",
                "social1_ranking": p.get("ranking", 0),
                "social1_region": p.get("_region", ""),
            })

        print(f"Products converted: {len([a for a in new_ads if a['ad_type'] == 'product'])}")

    # --- VIDEOS -> unified ads ---
    if vid_files:
        with open(vid_files[0], "r", encoding="utf-8") as f:
            vid_data = json.load(f)

        for v in vid_data.get("videos", []):
            desc = (v.get("description", "") or "")[:500]
            views = v.get("views", 0) or 0
            likes = v.get("likes", 0) or 0
            comments = v.get("comments", 0) or 0
            insights = v.get("insights") or []
            insights_text = []
            for ins in insights:
                if isinstance(ins, dict) and ins.get("insight"):
                    insights_text.append(ins["insight"])

            body_parts = [desc]
            if insights_text:
                body_parts.append("\n\nAI Insights: " + " | ".join(insights_text[:2]))

            engagement = likes + comments
            eng_rate = round(engagement / max(views, 1) * 100, 2)

            new_ads.append({
                "ad_id": f"social1_vid_{v.get('video_id', '')}",
                "source": "social1",
                "platform": "tiktok",
                "advertiser": f"@{v.get('handle', 'unknown')}",
                "advertiser_image": "",
                "title": desc[:100] if desc else "TikTok Video",
                "body": "\n".join(body_parts),
                "cta": "Watch" if not v.get("is_ad") else "Shop Now",
                "landing_page": "",
                "image_url": "",
                "video_url": f"https://www.tiktok.com/@{v.get('handle', '')}/video/{v.get('video_id', '')}",
                "first_seen": v.get("time_posted", ""),
                "last_seen": v.get("_scraped_at", datetime.now().isoformat()),
                "is_active": True,
                "likes": likes,
                "comments": comments,
                "shares": 0,
                "impressions": views,
                "days_running": 0,
                "heat": min(1000, int(views / 10000)) if views else 0,
                "ad_type": "video" if v.get("is_ad") else "organic_video",
                "video_duration": 0,
                "channels": [],
                "has_store": False,
                "total_engagement": engagement,
                "search_keyword": "tiktok shop",
                "collected_at": v.get("_scraped_at", datetime.now().isoformat()),
                "estimated_spend": 0,
                "spend_source": "",
                "potential_score": min(10, round(
                    min(4, views / 5000000) +
                    min(3, eng_rate) +
                    min(3, 3 if insights_text else 0)
                )),
                "country": v.get("_region", "").upper(),
                "all_countries": [v.get("_region", "").upper()],
                # AI fields from insights
                "ai_niche": "tiktok shop",
                "ai_strategy": insights_text[0][:200] if insights_text else "",
                "ai_hook_type": insights_text[1][:200] if len(insights_text) > 1 else "",
                "social1_is_ad": v.get("is_ad", False),
                "social1_engagement_rate": eng_rate,
                "social1_insights": insights_text,
                "social1_region": v.get("_region", ""),
            })

        vid_count = len([a for a in new_ads if 'video' in a.get('ad_type', '')])
        print(f"Videos converted: {vid_count}")

    # --- MERGE with unified ---
    unified_files = sorted(glob.glob(f"{OUTPUT_DIR}/unified_*.json"), reverse=True)
    if not unified_files:
        print("No unified file found!")
        return 0

    unified_path = unified_files[0]
    with open(unified_path, "r", encoding="utf-8") as f:
        existing = json.load(f)

    existing_ids = {a.get("ad_id", "") for a in existing if a.get("ad_id")}

    # Only add new ones
    added = [a for a in new_ads if a["ad_id"] not in existing_ids]

    if added:
        combined = existing + added
        with open(unified_path, "w", encoding="utf-8") as f:
            json.dump(combined, f, ensure_ascii=False)
        print(f"\nMerged: +{len(added)} new ads into {unified_path} (total: {len(combined)})")
    else:
        print(f"\nNo new ads to merge (all {len(new_ads)} already exist)")

    return len(added)


if __name__ == "__main__":
    convert_and_merge()
