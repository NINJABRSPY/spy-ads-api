"""
YouTube Mass Transcriber — transcreve vídeos em massa via SearchAPI
Indexa transcrições para busca por palavra falada
"""

import json
import glob
import time
import requests
from datetime import datetime

API_KEY = "ZFDmiHTH75sZT3wjDBc7vGay"
OUTPUT_DIR = "resultados"
DELAY = 2
MAX_PER_RUN = 200  # ~200 transcrições por dia (economiza créditos)

# Keywords para buscar vídeos para transcrever
SEARCH_KEYWORDS = [
    # DR Health (mais VSLs)
    "weight loss supplement", "blood sugar supplement", "prostate health",
    "testosterone booster", "joint pain relief", "brain health supplement",
    "anti aging secret", "gut health", "sleep supplement", "anxiety remedy",
    "hair growth treatment", "teeth whitening", "nail fungus treatment",
    "diabetes natural remedy", "cholesterol supplement",
    # DR Wealth
    "make money online", "affiliate marketing", "dropshipping tutorial",
    "passive income", "crypto trading",
    # DR Other
    "manifestation technique", "dog training", "survival prepping",
    # BR
    "suplemento emagrecer", "renda extra", "marketing digital",
    "ganhar dinheiro online",
]


def search_videos(keyword, num=10):
    """Busca vídeos no YouTube por keyword"""
    try:
        r = requests.get("https://www.searchapi.io/api/v1/search", params={
            "engine": "youtube",
            "q": keyword,
            "num": num,
            "api_key": API_KEY,
        }, timeout=15)
        data = r.json()
        return data.get("videos", [])
    except:
        return []


def transcribe_video(video_id, lang="en"):
    """Transcreve um vídeo via SearchAPI"""
    try:
        r = requests.get("https://www.searchapi.io/api/v1/search", params={
            "engine": "youtube_transcripts",
            "video_id": video_id,
            "lang": lang,
            "api_key": API_KEY,
        }, timeout=15)
        data = r.json()
        segments = data.get("transcripts", [])
        if segments:
            full_text = " ".join(s.get("text", "") for s in segments)
            return {
                "text": full_text,
                "word_count": len(full_text.split()),
                "segments": segments,
                "languages": data.get("available_languages", []),
            }
    except:
        pass
    return None


def load_transcript_index():
    """Carrega índice de transcrições existente"""
    index_file = f"{OUTPUT_DIR}/transcript_index.json"
    try:
        with open(index_file, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {"videos": {}, "total": 0, "last_updated": ""}


def save_transcript_index(index):
    """Salva índice de transcrições"""
    index["last_updated"] = datetime.now().isoformat()
    index["total"] = len(index["videos"])
    index_file = f"{OUTPUT_DIR}/transcript_index.json"
    with open(index_file, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False)


def run():
    """Busca vídeos e transcreve em massa"""
    print("=" * 60)
    print("  YouTube Mass Transcriber")
    print("=" * 60)

    index = load_transcript_index()
    print(f"Existing transcriptions: {index['total']}")

    transcribed_count = 0
    all_videos = []

    # Phase 1: Buscar vídeos por keyword
    print("\n--- Searching videos ---")
    for kw in SEARCH_KEYWORDS:
        videos = search_videos(kw, num=10)
        for v in videos:
            vid_id = v.get("id", "")
            if vid_id and vid_id not in index["videos"]:
                channel = v.get("channel", {})
                thumb = v.get("thumbnail", "")
                if isinstance(thumb, dict):
                    thumb = thumb.get("rich") or thumb.get("static") or ""
                all_videos.append({
                    "video_id": vid_id,
                    "title": v.get("title", ""),
                    "description": (v.get("description", "") or "")[:300],
                    "views": v.get("views", 0),
                    "link": v.get("link", ""),
                    "thumbnail": thumb,
                    "published": v.get("published_time", ""),
                    "duration": v.get("length", ""),
                    "channel_name": channel.get("title", ""),
                    "channel_id": channel.get("id", ""),
                    "channel_verified": channel.get("is_verified", False),
                    "keyword": kw,
                })
        time.sleep(DELAY)
        print(f"  {kw}: {len(videos)} videos found")

    # Deduplicate
    seen = set()
    unique = []
    for v in all_videos:
        if v["video_id"] not in seen:
            seen.add(v["video_id"])
            unique.append(v)

    # Sort by views (transcribe most popular first)
    unique.sort(key=lambda x: x.get("views", 0) or 0, reverse=True)
    to_transcribe = unique[:MAX_PER_RUN]

    print(f"\nUnique new videos: {len(unique)}")
    print(f"Will transcribe: {len(to_transcribe)}")

    # Phase 2: Transcrever
    print("\n--- Transcribing ---")
    for i, v in enumerate(to_transcribe):
        vid_id = v["video_id"]

        # Try English first, then Portuguese
        transcript = transcribe_video(vid_id, "en")
        lang = "en"
        if not transcript:
            transcript = transcribe_video(vid_id, "pt")
            lang = "pt"

        if transcript and transcript["word_count"] > 10:
            index["videos"][vid_id] = {
                "video_id": vid_id,
                "title": v["title"],
                "description": v["description"],
                "views": v["views"],
                "link": v["link"],
                "thumbnail": v["thumbnail"],
                "published": v["published"],
                "duration": v["duration"],
                "channel_name": v["channel_name"],
                "channel_id": v["channel_id"],
                "channel_verified": v["channel_verified"],
                "keyword": v["keyword"],
                "transcript": transcript["text"],
                "word_count": transcript["word_count"],
                "language": lang,
                "transcribed_at": datetime.now().isoformat(),
            }
            transcribed_count += 1

        if (i + 1) % 20 == 0 or i == 0:
            print(f"  {i + 1}/{len(to_transcribe)} transcribed ({transcribed_count} success)")

        time.sleep(DELAY)

    # Save
    save_transcript_index(index)
    print(f"\n=== DONE ===")
    print(f"New transcriptions: {transcribed_count}")
    print(f"Total in index: {index['total']}")

    return transcribed_count


if __name__ == "__main__":
    run()
