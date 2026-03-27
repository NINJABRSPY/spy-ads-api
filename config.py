"""
Configuracao central do sistema de Spy Ads
BigSpy + Adyntel unificados
"""

# ============================================================
# BIGSPY
# ============================================================
BIGSPY_JWT = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJsYW4iOiJlbiIsInZlciI6ImJzIiwidGltZXN0YW1wIjoxNzc0NjI1MDk0LCJleHBpcmUiOjE3NzQ4ODQyOTQsInVzZXJfaWQiOiJTRmRxUTE5V2JrZz0iLCJhcHBuYW1lIjoiQmlnU3B5IiwidXNlcl9uYW1lIjoiTklOSkFCUiIsInN1YnNjcmlwdGlvbiI6eyJjb2RlIjoiYmlnc3B5X21vbnRobHlfcHJvMjUiLCJhZHNfcGVybWlzc2lvbiI6eyJzZWFyY2giOjEsImV4Y2x1ZGVfc2VhcmNoIjoxLCJmaWx0ZXIiOjEsInBhZ2VfbGltaXQiOjAsInF1ZXJ5X251bSI6MjAwMCwiZG93bmxvYWRfbnVtIjoyNTAsInZpZGVvX3JlY29nbml6ZV9saW1pdCI6MTAwfSwibmV0d29ya3MiOnsiZmFjZWJvb2siOjEsImluc3RhZ3JhbSI6MSwidHdpdHRlciI6MSwiYWRtb2IiOjAsInBpbnRlcmVzdCI6MSwieWFob28iOjEsInlvdXR1YmUiOjAsInRpa3RvayI6MSwidW5pdHkiOjB9LCJ0cmFja19wZXJtaXNzaW9uIjp7ImZlYXR1cmVfYWRzIjoxLCJwZW9wbGVfYWRzIjoxLCJteV90cmFjayI6MSwidHJhY2tfbnVtIjoyNTAsInBhZ2VfYW5hbHlzaXMiOjEsInBhZ2VfdHJhY2tfbnVtIjoyMH0sIm1vZHVsZV9wZXJtaXNzaW9uIjp7InBhZ2VfYW5hbHlzaXMiOjEsImZlYXR1cmVfYWRzIjoyLCJwbGF5YWJsZSI6MCwiYWRzcHkiOjEsImxhbmRpbmdfcGFnZSI6MSwidGlrdG9rX3Nob3AiOjEsInRpa3Rva19zdG9yZV9jaGFydCI6MX0sInRlYW1faW5mbyI6eyJpZCI6MCwibmV3X3RlYW1fcG9wdXAiOjAsInRlYW1fcmVxdWVzdCI6MH0sImluZHVzdHJ5X2luZm8iOnsidG90YWxfaW5kdXN0cnlfY291bnQiOjMsInJlbWFpbl9pbmR1c3RyeV9jb3VudCI6MywicGVybWlzc2lvbl9hcHBfdHlwZSI6WzEsMiwzXSwibGFzdF9hcHBfdHlwZSI6M30sInVzZXJfc3RhdHVzIjozLCJpc19hZG1pbiI6MH0sImNvbXBhbnlfaWQiOjAsImVtYWlsIjoibmluamFici5zZXJ2aWRvckBnbWFpbC5jb20ifQ.TNvrzdn_d_TiJoLpT29ofxNY5sPNX8fI95V25jtPn84"
BIGSPY_DEVICE_ID = "bbe6b60cf45b3967acc419c461ddff0a"

# ============================================================
# ADYNTEL
# ============================================================
ADYNTEL_API_KEY = "hd-bec5d4a316758d4272-3"
ADYNTEL_EMAIL = "felipe_capoart@hotmail.com"

# ============================================================
# BUSCAS - Edite aqui para adicionar/remover
# ============================================================

# Keywords para busca geral (BigSpy + Adyntel Meta/TikTok)
KEYWORDS = [
    # Originais
    "dropshipping",
    "skincare",
    "fitness",
    "ecommerce",
    "marketing digital",
    "curso online",
    "suplementos",
    "moda feminina",
    "pet shop",
    "infoproduto",
    # Novos
    "afiliado",
    "renda extra",
    "trafego pago",
    "loja virtual",
    "cosmeticos",
    "coaching",
    "mentoria",
    "investimentos",
    "saude",
    "emagrecimento",
]

# Plataformas BigSpy
BIGSPY_PLATFORMS = ["facebook", "instagram", "tiktok"]

# Dominios de concorrentes para Adyntel (Google/LinkedIn/Keywords)
COMPETITOR_DOMAINS = [
    "shopify.com",
    "hotmart.com",
    "kiwify.com.br",
]

# ============================================================
# LIMITES
# ============================================================
BIGSPY_MAX_PAGES = 5          # paginas por keyword/plataforma (60 ads/pag)
BIGSPY_DELAY = 3              # segundos entre requests
ADYNTEL_DELAY = 2             # segundos entre requests
OUTPUT_DIR = "resultados"
