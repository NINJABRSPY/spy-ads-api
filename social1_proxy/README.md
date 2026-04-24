# Social1 Proxy — on-demand search para NinjaSpy

Playwright + FastAPI mantendo sessao Social1 persistente no HostDimer.

## Arquitetura

```
Cliente NinjaSpy
    ↓
Render /api/social1/search (cache 1h)
    ↓
https://social1.ninjabrhub.online (HostDimer nginx)
    ↓
127.0.0.1:3019 (ninja-social1.service)
    ↓
Playwright persistent Chrome (cookie salvo)
    ↓
fetch("/api/products/getTopProducts?...") dentro da aba social1
    ↓
JSON → volta pelo mesmo caminho
```

## Deploy (HostDimer)

### 1. Subir codigo
```bash
mkdir -p /opt/ninja-proxy/social1/browser_data
cd /opt/ninja-proxy/social1
# rsync ou scp do social1_server.py + requirements.txt
pip3 install -r requirements.txt
playwright install chromium
```

### 2. Systemd
```bash
cp ninja-social1.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable ninja-social1
# Nao iniciar ainda — precisa login primeiro
```

### 3. Login inicial (headful via DISPLAY ou cookies)

Opcao A — VNC/X-Forwarding:
```bash
SOCIAL1_HEADLESS=false python3 social1_server.py
# Abre Chrome visivel, loga manual, fecha server
```

Opcao B — importar cookies do browser local:
```bash
# Exportar cookies do Chrome local (EditThisCookie ou similar)
# Colocar em browser_data/Default/Cookies
```

### 4. Nginx + SSL
```bash
cp nginx-social1.conf /etc/nginx/sites-available/social1.ninjabrhub.online
cp nginx-social1.conf /etc/nginx/sites-available/social1.ninjabrhub.online.canonical
ln -s ../sites-available/social1.ninjabrhub.online /etc/nginx/sites-enabled/
nginx -t
certbot --nginx -d social1.ninjabrhub.online
systemctl reload nginx
```

### 5. Start service
```bash
systemctl start ninja-social1
systemctl status ninja-social1
curl https://social1.ninjabrhub.online/health
```

## Endpoints

Todos exigem `X-API-Key: njspy_social1_2026_q7w8e9` (header) ou `?key=...` (query).

- `GET /health` — publico, sem auth
- `GET /api/search?keyword=X&region=us&days=1&limit=20`
- `GET /api/products?region=us&days=1&limit=20`
- `GET /api/videos?region=us&days=1&limit=20`
- `GET /api/creators?region=us&limit=20`
- `GET /api/product/{id}?region=us`
- `GET /api/creator/{handle}/videos?region=us`
- `GET /api/raw?path=/api/whatever` — escape hatch

## Logs

- Service: `/var/log/ninja-social1.log`
- Nginx access: `/var/log/nginx/social1.access.log`
- Nginx error: `/var/log/nginx/social1.error.log`

## Sessao expirada

Se retornar `401 session_expired_or_invalid`:
```bash
systemctl stop ninja-social1
SOCIAL1_HEADLESS=false python3 /opt/ninja-proxy/social1/social1_server.py
# Login manual no Chrome
systemctl start ninja-social1
```

## NAO MEXER

- `admin.py` regenera configs nginx — este esta com `.canonical` para proteger
- porta 3019 exclusiva
- user_data_dir `/opt/ninja-proxy/social1/browser_data/` — nao deletar
