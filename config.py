"""
Configuracao central do sistema de Spy Ads
BigSpy + Adyntel unificados
"""

# ============================================================
# BIGSPY
# ============================================================
BIGSPY_JWT = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJsYW4iOiJlbiIsInZlciI6ImJzIiwidGltZXN0YW1wIjoxNzc0MjA5MzE4LCJleHBpcmUiOjE3NzQ0Njg1MTgsInVzZXJfaWQiOiJTRmRvUjF4U2Frdz0iLCJhcHBuYW1lIjoiQmlnU3B5IiwidXNlcl9uYW1lIjoiTmljIEtvIiwic3Vic2NyaXB0aW9uIjp7ImNvZGUiOiJiaWdzcHlfbW9udGhseV9wcm8yNSIsImFkc19wZXJtaXNzaW9uIjp7InNlYXJjaCI6MSwiZXhjbHVkZV9zZWFyY2giOjEsImZpbHRlciI6MSwicGFnZV9saW1pdCI6MCwicXVlcnlfbnVtIjoxMDAwLCJkb3dubG9hZF9udW0iOjI1MCwidmlkZW9fcmVjb2duaXplX2xpbWl0IjoxMDB9LCJuZXR3b3JrcyI6eyJmYWNlYm9vayI6MSwiaW5zdGFncmFtIjoxLCJ0d2l0dGVyIjoxLCJhZG1vYiI6MCwicGludGVyZXN0IjoxLCJ5YWhvbyI6MSwieW91dHViZSI6MCwidGlrdG9rIjoxLCJ1bml0eSI6MH0sInRyYWNrX3Blcm1pc3Npb24iOnsiZmVhdHVyZV9hZHMiOjEsInBlb3BsZV9hZHMiOjEsIm15X3RyYWNrIjoxLCJ0cmFja19udW0iOjI1MCwicGFnZV9hbmFseXNpcyI6MSwicGFnZV90cmFja19udW0iOjIwfSwibW9kdWxlX3Blcm1pc3Npb24iOnsicGFnZV9hbmFseXNpcyI6MSwiZmVhdHVyZV9hZHMiOjIsInBsYXlhYmxlIjowLCJhZHNweSI6MSwibGFuZGluZ19wYWdlIjoxLCJ0aWt0b2tfc2hvcCI6MSwidGlrdG9rX3N0b3JlX2NoYXJ0IjoxfSwidGVhbV9pbmZvIjp7ImlkIjowLCJuZXdfdGVhbV9wb3B1cCI6MCwidGVhbV9yZXF1ZXN0IjowfSwiaW5kdXN0cnlfaW5mbyI6eyJ0b3RhbF9pbmR1c3RyeV9jb3VudCI6MywicmVtYWluX2luZHVzdHJ5X2NvdW50IjozLCJwZXJtaXNzaW9uX2FwcF90eXBlIjpbMSwyLDNdLCJsYXN0X2FwcF90eXBlIjozfSwidXNlcl9zdGF0dXMiOjMsImlzX2FkbWluIjowfSwiY29tcGFueV9pZCI6MCwiZW1haWwiOiJrYXJhbmdhc2FtMTk4OEBnbWFpbC5jb20ifQ.1hl-HiUz75UHFeEwUbYNTXe0nCgF1MAu_8NSKQaTw8Y"
BIGSPY_DEVICE_ID = "bbe6b60cf45b3967acc419c461ddff0a"

# ============================================================
# ADYNTEL
# ============================================================
ADYNTEL_API_KEY = "hd-bec5d4a316758d4272-3"
ADYNTEL_EMAIL = "karangasam1988@gmail.com"

# ============================================================
# BUSCAS - Edite aqui para adicionar/remover
# ============================================================

# Keywords para busca geral (BigSpy + Adyntel Meta/TikTok)
KEYWORDS = [
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
BIGSPY_MAX_PAGES = 3          # paginas por keyword/plataforma (60 ads/pag)
BIGSPY_DELAY = 3              # segundos entre requests
ADYNTEL_DELAY = 2             # segundos entre requests
OUTPUT_DIR = "resultados"
