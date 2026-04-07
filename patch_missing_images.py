"""
Patch: Corrige ads do Meta Official que estão sem image_url
Usa advertiser_image como fallback para ads que têm esse campo
"""
import json
import gzip
import glob

OUTPUT_DIR = "resultados"

def main():
    files = sorted(glob.glob(f"{OUTPUT_DIR}/unified_*.json.gz"), reverse=True)
    if not files:
        print("Nenhum arquivo unified encontrado")
        return

    print(f"Carregando {files[0]}...")
    with gzip.open(files[0], "rt", encoding="utf-8") as f:
        ads = json.load(f)

    print(f"Total ads: {len(ads):,}")

    patched = 0
    for ad in ads:
        if not ad.get("image_url") and not ad.get("video_url"):
            # Try advertiser_image as fallback
            fallback = ad.get("advertiser_image", "")
            if fallback and len(fallback) > 10:
                ad["image_url"] = fallback
                ad["image_source"] = "advertiser_avatar"
                patched += 1

    print(f"Patched: {patched} ads com advertiser_image como fallback")

    # Save back
    print("Salvando...")
    with gzip.open(files[0], "wt", encoding="utf-8") as f:
        json.dump(ads, f, ensure_ascii=False)

    print("Concluído!")


if __name__ == "__main__":
    main()
