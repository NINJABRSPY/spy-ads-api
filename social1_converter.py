"""
Converte dados do Social1 para formato NinjaSpy
Source aparece como "ninja_tiktok_shop" — sem referência ao Social1
"""

import json
import glob
import os
from datetime import datetime

INPUT_DIR = "resultados"


def convert_social1():
    """Converte produtos, vídeos e creators do Social1"""

    # --- PRODUCTS ---
    prod_files = sorted(glob.glob(f"{INPUT_DIR}/social1_products_*.json"), reverse=True)
    products = []
    if prod_files:
        with open(prod_files[0], "r", encoding="utf-8") as f:
            data = json.load(f)

        for p in data.get("products", []):
            name = p.get("name", "")
            if not name:
                continue

            units = p.get("units_sold", 0) or 0
            gmv = float(p.get("gmv", 0) or 0)
            views = p.get("video_views", 0) or 0
            creators = p.get("creator_count", 0) or 0
            videos = p.get("video_count", 0) or 0
            price = float(p.get("price_value", 0) or 0)
            region = p.get("_region", "unknown")
            categories = p.get("categories", [])

            # Calculate scores
            viral_score = min(10, round(
                min(3, views / 1000000) +
                min(3, units / 50000) +
                min(2, creators / 1000) +
                min(2, gmv / 5000000)
            , 1))

            competition = "high" if creators > 5000 else "medium" if creators > 500 else "low"

            products.append({
                "product_id": f"tks_{p.get('product_id', '')}",
                "name": name[:200],
                "source": "ninja_tiktok_shop",
                "platform": "tiktok_shop",
                "region": region,
                "collected_at": p.get("_scraped_at", datetime.now().isoformat()),
                "price": price,
                "price_display": p.get("price_display", ""),
                "units_sold": units,
                "gmv": round(gmv, 2),
                "video_views": views,
                "video_count": videos,
                "creator_count": creators,
                "category": categories[0] if categories else "Unknown",
                "subcategory": categories[1] if len(categories) > 1 else "",
                "viral_score": viral_score,
                "competition_level": competition,
                "shop_name": p.get("shop", {}).get("shop_name", ""),
                "shop_image": p.get("shop", {}).get("shop_img_url", ""),
                "product_image": p.get("product_img_url", ""),
                "ranking": p.get("ranking", 0),
                "period_days": p.get("_days", 1),
            })

    # --- VIDEOS ---
    vid_files = sorted(glob.glob(f"{INPUT_DIR}/social1_videos_*.json"), reverse=True)
    videos_list = []
    if vid_files:
        with open(vid_files[0], "r", encoding="utf-8") as f:
            data = json.load(f)

        for v in data.get("videos", []):
            views = v.get("views", 0) or 0
            likes = v.get("likes", 0) or 0
            comments = v.get("comments", 0) or 0

            engagement_rate = round((likes + comments) / max(views, 1) * 100, 2)

            insights = v.get("insights") or []
            insights_text = [i.get("insight", "") for i in insights if isinstance(i, dict) and i.get("insight")]

            videos_list.append({
                "video_id": f"tkv_{v.get('video_id', '')}",
                "source": "ninja_tiktok_shop",
                "platform": "tiktok_shop",
                "region": v.get("_region", "unknown"),
                "collected_at": v.get("_scraped_at", datetime.now().isoformat()),
                "description": (v.get("description", "") or "")[:500],
                "creator_handle": v.get("handle", ""),
                "creator_id": v.get("author_id", ""),
                "views": views,
                "likes": likes,
                "comments": comments,
                "engagement_rate": engagement_rate,
                "is_ad": v.get("is_ad", False),
                "time_posted": v.get("time_posted", ""),
                "ai_insights": insights_text,
                "has_insights": len(insights_text) > 0,
                "period_days": v.get("_days", 1),
            })

    # --- CREATORS ---
    creator_files = sorted(glob.glob(f"{INPUT_DIR}/social1_creators_*.json"), reverse=True)
    creators_list = []
    if creator_files:
        with open(creator_files[0], "r", encoding="utf-8") as f:
            data = json.load(f)

        for c in data.get("creators", []):
            followers = c.get("follower_cnt", 0) or 0
            gmv = float(c.get("med_gmv_revenue", 0) or 0)

            influence_score = min(10, round(
                min(4, gmv / 1000000) +
                min(3, followers / 500000) +
                3  # base
            , 1))

            creators_list.append({
                "creator_id": c.get("creator_oecuid", ""),
                "handle": c.get("handle", ""),
                "nickname": c.get("nickname", ""),
                "source": "ninja_tiktok_shop",
                "platform": "tiktok_shop",
                "region": c.get("_region", "unknown"),
                "collected_at": c.get("_scraped_at", datetime.now().isoformat()),
                "followers": followers,
                "gmv_30d": round(gmv, 2),
                "profile_picture": c.get("profilePicture", ""),
                "influence_score": influence_score,
            })

    # --- SAVE ALL ---
    timestamp = datetime.now().strftime("%Y%m%d_%H%M")

    output = {
        "source": "ninja_tiktok_shop",
        "scraped_at": datetime.now().isoformat(),
        "total_products": len(products),
        "total_videos": len(videos_list),
        "total_creators": len(creators_list),
        "products": products,
        "videos": videos_list,
        "creators": creators_list,
    }

    output_file = f"{INPUT_DIR}/tiktok_shop_{timestamp}.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"Products: {len(products)}")
    print(f"Videos: {len(videos_list)} ({sum(1 for v in videos_list if v['has_insights'])} with AI insights)")
    print(f"Creators: {len(creators_list)}")
    print(f"Saved to: {output_file}")

    return output_file


if __name__ == "__main__":
    convert_social1()
