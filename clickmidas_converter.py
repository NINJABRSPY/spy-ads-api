"""
Converte dados do ClickMidas para formato NinjaSpy
Os dados aparecem como análise própria do NinjaSpy, sem referência ao ClickMidas
"""

import json
import glob
import os
from datetime import datetime

INPUT_DIR = "resultados"
OUTPUT_DIR = "resultados"


def normalize_score(midas_score, gravity):
    """Converte Midas Score + Gravity para escala NinjaSpy 1-10"""
    # Midas Score original vai de ~-10 a ~10
    # Normalizar para 1-10
    base = max(0, min(10, (midas_score + 5) * 1.0))

    # Boost por gravidade (produtos com mais vendas = mais relevantes)
    if gravity > 200:
        base = min(10, base + 1.5)
    elif gravity > 100:
        base = min(10, base + 1.0)
    elif gravity > 50:
        base = min(10, base + 0.5)

    return round(max(1, min(10, base)), 1)


def classify_niche(product_name):
    """Classifica nicho automaticamente pelo nome do produto"""
    name = product_name.lower()

    niche_map = {
        "health": ["weight", "loss", "diet", "slim", "burn", "keto", "detox", "metabolism",
                    "glucose", "gluco", "sugar", "diabetes", "blood", "cholesterol", "heart",
                    "supplement", "vitamin", "probiotic", "biome", "gut", "digestive",
                    "immune", "energy", "fatigue", "sleep", "insomnia", "joint", "pain",
                    "inflammation", "arthritis", "nerve", "neuro", "vision", "eye", "sight",
                    "hearing", "ear", "tinnitus", "lung", "breath", "liver", "kidney",
                    "thyroid", "hormone", "cortisol", "stress", "anxiety", "depression",
                    "memory", "cognitive", "vertigo", "fungus", "antifung", "parasite",
                    "cleanse", "purif", "heal", "remedy", "cure", "doctor", "clinical",
                    "medical", "health", "wellness", "pelvic", "bladder", "urin", "pee",
                    "constip", "digest", "ibs", "bloat", "nausea", "migraine", "headache",
                    "dental", "teeth", "dent", "gum", "oral", "mouth", "cavity",
                    "prostate", "prosta", "genics", "defendr", "defender", "genesis",
                    "metabo", "lipo", "lean", "belly", "flat", "tone"],
        "fitness": ["muscle", "workout", "exercise", "fitness", "body", "strength",
                    "testosterone", "male enhancement", "nitric", "boost", "stamina",
                    "erect", "libido", "sparta", "alpha", "virility", "potency",
                    "performance", "endurance", "athletic"],
        "beauty": ["skin", "wrinkle", "aging", "anti-aging", "hair", "nail", "beauty",
                   "glow", "collagen", "cream", "serum", "complexion", "acne", "scar",
                   "pigment", "cellulite", "lash", "brow", "cosmetic", "radiant",
                   "youthful", "rejuven", "derma", "kerassential"],
        "wealth": ["money", "wealth", "income", "rich", "profit", "trading", "crypto",
                   "bitcoin", "forex", "investment", "stock", "cash", "earn", "passive",
                   "financial", "millionaire", "billionaire", "bank", "credit", "debt",
                   "loan", "paid", "salary", "jobs", "freelanc", "commission", "revenue",
                   "lottery", "lotto", "jackpot"],
        "education": ["course", "learn", "training", "guide", "method",
                      "blueprint", "master", "tutorial", "class", "academy",
                      "certif", "skill", "teach", "instruct", "lesson", "ebook",
                      "language", "spanish", "french", "chinese", "speak"],
        "spirituality": ["astrology", "numerolog", "moon", "reading", "zodiac", "horoscope",
                        "psychic", "tarot", "manifest", "frequency", "wave", "brain wave",
                        "soulmate", "sketch", "angel", "chakra", "meditation", "spiritual",
                        "divine", "prayer", "faith", "god", "bible", "christian", "shield",
                        "prophecy", "church", "sacred", "soul", "lunar", "cosmic", "karma",
                        "destiny", "fate", "truths", "seer", "mystic"],
        "survival": ["survival", "prepper", "emergency", "water freedom", "solar",
                     "power", "generator", "self-defense", "tactical", "emp",
                     "blackout", "crisis", "stockpile", "bunker", "aqua tower",
                     "water box", "off-grid", "prepard"],
        "relationships": ["dating", "relationship", "love", "attract", "marriage",
                          "romance", "sex", "intimacy", "ejaculation", "lasting",
                          "obsession", "desire", "commitment", "breakup", "ex back",
                          "husband", "wife", "couple", "seduc", "flirt"],
        "pets": ["dog", "cat", "pet", "puppy", "canine", "vet", "feline", "parrot",
                 "bird", "fish", "aquarium"],
        "home": ["woodworking", "garden", "home", "house", "diy", "renovation",
                 "furniture", "craft", "decor", "kitchen", "cook", "recipe",
                 "organize", "clean", "pillow", "mattress", "sleep aid"],
        "tech": ["software", "app", "ai", "tech", "digital", "automation",
                 "robot", "drone", "phone", "computer", "cyber", "hack",
                 "vpn", "data", "cloud", "vehicle", "car", "vin"],
        "betting": ["bet", "racing", "horse", "tips", "picks", "gambling", "sport",
                    "winner", "handicap", "odds", "wager", "casino", "poker"],
    }

    for niche, keywords in niche_map.items():
        for kw in keywords:
            if kw in name:
                return niche

    return "other"


def classify_trend(trend_1d, trend_7d, trend_30d):
    """Classifica tendência do produto"""
    if trend_7d > 5 and trend_30d > 0:
        return "rising_fast"
    elif trend_7d > 0 and trend_30d > 0:
        return "rising"
    elif trend_7d > 0 and trend_30d < 0:
        return "recovering"
    elif trend_7d < 0 and trend_30d > 0:
        return "cooling"
    elif trend_7d < -5 or trend_30d < -10:
        return "declining_fast"
    elif trend_7d < 0:
        return "declining"
    return "stable"


def convert_clickmidas_to_ninjaspy(input_file=None):
    """Converte arquivo ClickMidas JSON para formato NinjaSpy"""

    # Encontrar arquivo mais recente se não especificado
    if not input_file:
        files = sorted(glob.glob(f"{INPUT_DIR}/clickmidas_*.json"), reverse=True)
        if not files:
            print("Nenhum arquivo clickmidas encontrado!")
            return None
        input_file = files[0]

    print(f"Convertendo: {input_file}")

    with open(input_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    products = data.get("products", [])
    converted = []

    for p in products:
        name = p.get("name", "")
        if not name:
            continue

        gravity = p.get("gravity", 0) or 0
        g1d = p.get("gravity_1d", 0) or 0
        g7d = p.get("gravity_7d", 0) or 0
        g30d = p.get("gravity_30d", 0) or 0
        midas = p.get("midas_score", 0) or 0
        traffic = p.get("traffic", 0) or 0

        # Campos extras da tabela grande
        max_commission = p.get("max_commission", 0) or 0
        currency = p.get("currency", "USD")
        rating = p.get("rating", 0) or 0
        overall_score = p.get("overall_score", 0) or 0

        niche = classify_niche(name)
        trend = classify_trend(g1d, g7d, g30d)
        ninja_score = normalize_score(midas, gravity)

        converted.append({
            # Identificação
            "product_id": name.lower().replace(" ", "-").replace("(", "").replace(")", "")[:60],
            "name": name,
            "source": "ninja_affiliate",
            "platform": p.get("platform_source", "clickbank"),
            "collected_at": data.get("scraped_at", datetime.now().isoformat()),

            # Métricas transformadas
            "ninja_score": ninja_score,
            "sales_volume": gravity,
            "trend_1d": g1d,
            "trend_7d": g7d,
            "trend_30d": g30d,
            "estimated_traffic": traffic,
            "market_heat": overall_score if overall_score else round(gravity * 0.1, 1),

            # Financeiro
            "max_commission": max_commission,
            "currency": currency,

            # Análise automática
            "niche": niche,
            "trend_direction": trend,
            "competition_level": "high" if gravity > 150 else "medium" if gravity > 50 else "low",

            # Rankings (em quais views apareceu)
            "rankings": p.get("rankings", {}),
            "tables": p.get("tables", []),
        })

    # Ordenar por ninja_score desc
    converted.sort(key=lambda x: x["ninja_score"], reverse=True)

    # Salvar
    output = {
        "source": "ninja_affiliate",
        "scraped_at": data.get("scraped_at", datetime.now().isoformat()),
        "total_products": len(converted),
        "platforms": list(set(p["platform"] for p in converted)),
        "niches": dict(sorted(
            {n: sum(1 for p in converted if p["niche"] == n) for n in set(p["niche"] for p in converted)}.items(),
            key=lambda x: x[1], reverse=True
        )),
        "products": converted
    }

    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
    output_file = f"{OUTPUT_DIR}/affiliate_products_{timestamp}.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"Convertido: {len(converted)} produtos")
    print(f"Salvo em: {output_file}")
    print(f"Nichos: {output['niches']}")

    return output_file


if __name__ == "__main__":
    convert_clickmidas_to_ninjaspy()
