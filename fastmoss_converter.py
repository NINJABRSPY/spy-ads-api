"""
Converte FastMoss para formato NinjaSpy e merge com Social1 no tiktok_shop
"""
import json
import glob
import gzip
from datetime import datetime

INPUT_DIR = "resultados"


def convert_and_merge():
    # Load FastMoss
    fm_files = sorted(glob.glob(f"{INPUT_DIR}/fastmoss_*.json"), reverse=True)
    if not fm_files:
        print("No FastMoss data found")
        return

    with open(fm_files[0], "r", encoding="utf-8") as f:
        fm = json.load(f)

    print(f"FastMoss: {fm['totals']}")

    # Load existing tiktok_shop
    ts_files = sorted(glob.glob(f"{INPUT_DIR}/tiktok_shop_*.json"), reverse=True)
    existing = {"products": [], "videos": [], "creators": []}
    if ts_files:
        with open(ts_files[0], "r", encoding="utf-8") as f:
            existing = json.load(f)
    print(f"Existing TikTok Shop: {len(existing.get('products', []))} products, {len(existing.get('videos', []))} videos, {len(existing.get('creators', []))} creators")

    # Index existing by ID
    existing_prod_ids = {p.get("product_id", ""): i for i, p in enumerate(existing.get("products", []))}
    existing_creator_ids = {c.get("creator_id", ""): i for i, c in enumerate(existing.get("creators", []))}

    # ======= MERGE PRODUCTS =======
    for p in fm.get("products", []):
        pid = f"tks_{p.get('product_id', '')}"
        product = {
            "product_id": pid,
            "name": p.get("title", "")[:200],
            "source": "ninja_tiktok_shop",
            "platform": "tiktok_shop",
            "region": "us",
            "collected_at": fm.get("scraped_at", ""),
            "price": 0,
            "price_display": p.get("real_price", ""),
            "units_sold": p.get("sold_count", 0) or p.get("total_sold_count", 0),
            "gmv": p.get("sale_amount", 0) or p.get("total_sale_amount", 0),
            "video_views": 0,
            "video_count": p.get("aweme_count", 0),
            "creator_count": p.get("author_count", 0) or p.get("total_author_count", 0),
            "category": (p.get("all_category_name") or p.get("category_name") or [""])[0],
            "subcategory": (p.get("all_category_name") or ["", ""])[1] if len(p.get("all_category_name", [])) > 1 else "",
            "viral_score": min(10, round(
                min(3, (p.get("sold_count", 0) or 0) / 1000) +
                min(3, (p.get("total_sold_count", 0) or 0) / 10000) +
                min(2, (p.get("author_count", 0) or 0) / 100) +
                min(2, (p.get("sale_amount", 0) or 0) / 50000)
            , 1)),
            "competition_level": "high" if (p.get("total_author_count", 0) or 0) > 500 else "medium" if (p.get("total_author_count", 0) or 0) > 50 else "low",
            "shop_name": p.get("shop_info", {}).get("name", ""),
            "shop_image": p.get("shop_info", {}).get("avatar", ""),
            "product_image": p.get("cover", ""),
            "ranking": p.get("ranking", 0),
            "period_days": 1,
            # FastMoss exclusive
            "commission_rate": p.get("commission_rate", ""),
            "total_units_sold": p.get("total_sold_count", 0),
            "total_gmv": p.get("total_sale_amount", 0),
            "growth_rate": p.get("sold_count_inc_rate", ""),
            "live_count": p.get("live_count", 0),
            "launch_date": p.get("launch_time", ""),
            "detail_url": p.get("detail_url", ""),
            "data_source": "fastmoss",
        }

        if pid in existing_prod_ids:
            # Update existing with richer FastMoss data
            idx = existing_prod_ids[pid]
            old = existing["products"][idx]
            # Keep Social1 data, add FastMoss extras
            old.update({k: v for k, v in product.items() if v and (not old.get(k) or k.startswith("commission") or k.startswith("total_") or k.startswith("growth") or k == "data_source")})
            old["data_source"] = "social1+fastmoss"
        else:
            existing["products"].append(product)
            existing_prod_ids[pid] = len(existing["products"]) - 1

    # ======= MERGE CREATORS =======
    for c in fm.get("creators", []):
        cid = c.get("uid", "")
        portrait = c.get("fansPortrait", {})
        genders = portrait.get("follower_genders", [])
        ages = portrait.get("follower_ages", [])
        states = portrait.get("state_distribution", [])

        contacts = []
        for ct in (c.get("contact") or []):
            if ct.get("name") and ct["name"] != "Bio":
                contacts.append({"type": ct["name"], "icon": ct.get("cover", "")})

        creator = {
            "creator_id": cid,
            "handle": c.get("unique_id", ""),
            "nickname": c.get("nickname", ""),
            "source": "ninja_tiktok_shop",
            "platform": "tiktok_shop",
            "region": "us",
            "collected_at": fm.get("scraped_at", ""),
            "followers": c.get("follower_count", 0),
            "gmv_30d": round(c.get("video_sale_amount", 0) + c.get("live_sale_amount", 0), 2),
            "profile_picture": c.get("avatar", ""),
            "influence_score": min(10, round(
                min(4, (c.get("follower_count", 0) or 0) / 10000000) +
                min(3, (c.get("video_sale_amount", 0) or 0) / 500000) +
                3
            , 1)),
            "videos": [],
            "video_count": c.get("aweme_count", 0),
            "total_views": 0,
            # FastMoss exclusive
            "interact_rate": c.get("interact_rate", ""),
            "avg_views": c.get("avg_play_count", 0),
            "avg_likes": c.get("avg_digg_count", 0),
            "is_verified": c.get("verify_type") == "1",
            "category": (c.get("category") or [""])[0],
            "contacts": contacts,
            "demographics": {
                "genders": genders,
                "ages": ages,
                "top_states": states[:5],
            },
            "follower_growth_30d": c.get("follower_28d_count", 0),
            "is_ecommerce": c.get("is_ecommerce") == 1,
            "data_source": "fastmoss",
        }

        if cid in existing_creator_ids:
            idx = existing_creator_ids[cid]
            old = existing["creators"][idx]
            old.update({k: v for k, v in creator.items() if v and (not old.get(k) or k in ("contacts", "demographics", "interact_rate", "is_verified", "data_source"))})
            old["data_source"] = "social1+fastmoss"
        else:
            existing["creators"].append(creator)
            existing_creator_ids[cid] = len(existing["creators"]) - 1

    # ======= ADD SHOPS (new data type) =======
    shops = []
    for s in fm.get("shops", []):
        info = s.get("shop_info", {})
        trend = s.get("trend", [])
        shops.append({
            "shop_id": info.get("seller_id", ""),
            "name": info.get("name", ""),
            "avatar": info.get("avatar", ""),
            "category": info.get("category_name", ""),
            "region": "us",
            "rating": s.get("shop_rating", 0),
            "total_sold": s.get("sold_count", 0),
            "total_gmv": s.get("sale_amount", 0),
            "products_count": s.get("on_sell_product_count", 0),
            "creators_count": s.get("sales_author_count", 0),
            "day7_sold": s.get("day7_sold_count", 0),
            "day7_gmv": s.get("day7_sale_amount", 0),
            "sales_trend": [{"date": t.get("dt", ""), "sold": t.get("inc_sold_count", 0)} for t in trend],
            "shop_type": "cross_border" if s.get("shop_type") == 2 else "local",
            "data_source": "fastmoss",
        })
    existing["shops"] = shops

    # ======= ADD ADS WITH ROAS (new data type) =======
    tiktok_ads = []
    for a in fm.get("ads", []):
        tiktok_ads.append({
            "ad_id": a.get("id", ""),
            "video_id": a.get("video_id", ""),
            "description": (a.get("desc") or "")[:300],
            "cover": a.get("cover", ""),
            "advertiser": a.get("advertiser", ""),
            "advertiser_avatar": a.get("avatar", ""),
            "advertiser_type": a.get("advertiser_type", ""),
            "views": a.get("play_count", 0),
            "likes": a.get("digg_count", 0),
            "shares": a.get("share_count", 0),
            "comments": a.get("comment_count", 0),
            "roas": a.get("roas", 0),
            "estimated_cost": a.get("estimate_cost", 0),
            "estimated_conversions": a.get("estimated_conversion", 0),
            "duration_seconds": a.get("duration", 0),
            "days_running": a.get("put_days", 0),
            "is_spark_ad": a.get("is_spark") == 1,
            "is_commission": "Commission" in str(a.get("bc_label_text", [])),
            "like_follower_rate": a.get("digg_follower_rate", ""),
            "product_count": a.get("product_count", 0),
            "first_date": a.get("first_put_date", ""),
            "last_date": a.get("last_put_date", ""),
            "region": "us",
            "data_source": "fastmoss",
        })
    existing["tiktok_ads"] = tiktok_ads

    # Update metadata
    existing["source"] = "ninja_tiktok_shop"
    existing["scraped_at"] = datetime.now().isoformat()
    existing["total_products"] = len(existing.get("products", []))
    existing["total_videos"] = len(existing.get("videos", []))
    existing["total_creators"] = len(existing.get("creators", []))
    existing["total_shops"] = len(shops)
    existing["total_tiktok_ads"] = len(tiktok_ads)
    existing["fastmoss_integrated"] = True

    # Save
    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
    output_file = f"{INPUT_DIR}/tiktok_shop_{timestamp}.json"

    raw = json.dumps(existing, indent=2, ensure_ascii=False)
    clean = raw.encode('utf-8', errors='replace').decode('utf-8')
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(clean)

    print(f"\nMerged TikTok Shop:")
    print(f"  Products: {existing['total_products']} (Social1 + FastMoss)")
    print(f"  Videos: {existing['total_videos']}")
    print(f"  Creators: {existing['total_creators']} (with demographics + contacts)")
    print(f"  Shops: {existing['total_shops']} (with 7-day trend)")
    print(f"  TikTok Ads: {existing['total_tiktok_ads']} (with ROAS!)")
    print(f"  Saved: {output_file}")

    # Also merge ads into unified
    merge_ads_to_unified(tiktok_ads)

    return output_file


def merge_ads_to_unified(tiktok_ads):
    """Merge FastMoss ads into unified"""
    uf = sorted(glob.glob(f"{INPUT_DIR}/unified_*.json"), reverse=True)
    if not uf:
        return

    with open(uf[0], "r", encoding="utf-8") as f:
        existing = json.load(f)

    existing_ids = {a.get("ad_id", "") for a in existing}
    added = 0

    for a in tiktok_ads:
        uid = f"fastmoss_{a['ad_id']}"
        if uid in existing_ids:
            continue

        existing.append({
            "ad_id": uid,
            "source": "fastmoss",
            "platform": "tiktok",
            "advertiser": a.get("advertiser", ""),
            "title": a.get("description", "")[:100],
            "body": a.get("description", ""),
            "cta": "Shop Now" if a.get("is_commission") else "",
            "landing_page": "",
            "image_url": a.get("cover", ""),
            "video_url": "",
            "first_seen": a.get("first_date", ""),
            "last_seen": a.get("last_date", ""),
            "is_active": True,
            "likes": a.get("likes", 0),
            "comments": a.get("comments", 0),
            "shares": a.get("shares", 0),
            "impressions": a.get("views", 0),
            "days_running": a.get("days_running", 0),
            "heat": min(1000, a.get("views", 0) // 100),
            "ad_type": "video",
            "total_engagement": a.get("likes", 0) + a.get("comments", 0) + a.get("shares", 0),
            "search_keyword": "tiktok ads",
            "collected_at": datetime.now().isoformat(),
            "estimated_spend": a.get("estimated_cost", 0),
            "potential_score": min(10, round((a.get("roas", 0) or 0) * 2)),
            "country": "US",
            # FastMoss exclusive
            "fastmoss_roas": a.get("roas", 0),
            "fastmoss_cost": a.get("estimated_cost", 0),
            "fastmoss_conversions": a.get("estimated_conversions", 0),
            "fastmoss_is_spark": a.get("is_spark_ad", False),
            "fastmoss_is_commission": a.get("is_commission", False),
        })
        added += 1

    with open(uf[0], "w", encoding="utf-8") as f:
        json.dump(existing, f, ensure_ascii=False)

    # Recompress
    with gzip.open(f"{INPUT_DIR}/unified_latest.json.gz", "wt", encoding="utf-8") as f:
        json.dump(existing, f, ensure_ascii=False)

    print(f"  Unified: +{added} FastMoss ads (total: {len(existing)})")


if __name__ == "__main__":
    convert_and_merge()
