# Daily Intel Service Proxy

Acesso on-demand ao dailyintelservice.com via conta paga $29/mes.

## Stack

- FastAPI + requests (sem Playwright — dados vem via JSON API)
- Cache 15min in-memory
- Filtros client-side (API do site nao aceita filtros server-side)
- Auth via cookie `member_session`

## Deploy HostDimer

```bash
mkdir -p /opt/ninja-proxy/dailyintel
cd /opt/ninja-proxy/dailyintel
# rsync dos files
cp ninja-dailyintel.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable ninja-dailyintel

cp nginx-dailyintel.conf /etc/nginx/sites-available/intel.ninjabrhub.online
cp nginx-dailyintel.conf /etc/nginx/sites-available/intel.ninjabrhub.online.canonical
ln -s ../sites-available/intel.ninjabrhub.online /etc/nginx/sites-enabled/
nginx -t
certbot --nginx -d intel.ninjabrhub.online --non-interactive --agree-tos -m ninjabr.servidor@gmail.com --redirect

systemctl start ninja-dailyintel
```

## Endpoints

Todos com `X-API-Key: njspy_dailyintel_2026_r8t9y0` (header) ou `?key=...` (query).

- `GET /health` — publico, sem auth
- `GET /api/search?niche=X&platform=Y&has_vsl=true&page=1&limit=20`
- `GET /api/niches` — facet com contagem por nicho
- `GET /api/platforms` — facet por platform
- `GET /api/video/{id}` — detalhes 1 video
- `POST /api/reload-cookies` — recarrega cookies.json (depois de substituir)

## Filtros disponiveis em /api/search

- `niche` (partial match case-insensitive)
- `platform` (Facebook, Instagram)
- `traffic_type` (paid, direct, referral, social, other)
- `is_paid` (bool)
- `has_vsl` (bool) — apenas com `has_clean_vsl=true`
- `has_ads` (bool) — apenas com `has_clean_ads=true`
- `funnel_stage`
- `device_type` (desktop, mobile)
- `search` — busca em product_name + niche + utm_*
- `date_from`, `date_to` (YYYY-MM-DD)
- `sort` (date_desc | date_asc | niche | product)

## Campos retornados por video

Originais da API:
- `id`, `product_name`, `niche`, `platform`, `traffic_type`, `is_paid_traffic`
- `funnel_stage`, `device_type`
- `utm_source`, `utm_medium`, `utm_campaign`
- `bunny_vsl_id`, `bunny_ads_id` — IDs dos videos no BunnyCDN
- `page_link`, `checkout_link`
- `country`, `campaign_status`
- `daily_reports`: {title, report_date, is_published}
- `has_clean_vsl`, `has_clean_ads`

Adicionados pelo proxy:
- `vsl_preview_url`, `vsl_playlist_url` (construidos do bunny_vsl_id)
- `ads_preview_url`, `ads_playlist_url` (construidos do bunny_ads_id)

## Sessao expirada

Sintoma: `/health` mostra `cookies_loaded=true` mas `/api/search` retorna
`{"error":"session_expired"}`.

Fix:
1. Fazer login no Chrome local em dailyintelservice.com
2. Exportar cookie `member_session` (EditThisCookie ou similar)
3. Substituir `/opt/ninja-proxy/dailyintel/cookies.json`
4. `curl -X POST 'https://intel.ninjabrhub.online/api/reload-cookies?key=...'`

Cookie `member_session` dura ~60 dias (exp em 2026-06).

## Dataset

- ~1.400 videos (VSLs + Ad Creatives)
- 28 nichos (Weight Loss, Memory, Nerve, Diabetes, ED, Vision, Joint Pain, Gut, etc)
- Range de datas: ultimos 13-16 dias
- ~73% com clean VSL, ~50% com clean Ads
