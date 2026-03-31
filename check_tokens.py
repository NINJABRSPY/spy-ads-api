"""
Verifica validade dos tokens e mostra aviso no Windows quando perto de expirar.
Rodar diariamente junto com o daily_scraper.
"""
import json
import base64
import subprocess
from datetime import datetime, timedelta

TOKENS = {
    "AdsParo": {
        "file": "adsparo_client.py",
        "var": "ADSPARO_TOKEN",
    },
    "BigSpy": {
        "file": "config.py",
        "var": "BIGSPY_JWT",
    },
    "PiPiAds": {
        "file": "pipi_auto.py",
        "var": "TOKEN",
        "base64_decode": True,
    },
}


def decode_jwt_exp(token, token_name=""):
    """Extrai data de expiracao de um JWT ou token custom"""
    try:
        parts = token.split(".")
        if len(parts) == 3:
            # JWT padrao
            payload = parts[1]
            payload += "=" * (4 - len(payload) % 4)
            data = json.loads(base64.b64decode(payload))

            # Tentar diferentes campos de expiracao
            exp = data.get("exp") or data.get("expire") or data.get("expires") or 0
            if exp:
                return datetime.fromtimestamp(exp)
    except:
        pass
    return None


def decode_pipiads_exp(token):
    """PiPiAds: base64 -> 'userId-timestamp', validade ~30 dias"""
    try:
        decoded = base64.b64decode(token).decode()
        ts = int(decoded.split("-")[-1])
        # Token criado nessa data, validade ~30 dias
        return datetime.fromtimestamp(ts) + timedelta(days=30)
    except:
        return None


def get_token_from_file(filepath, varname):
    """Extrai valor do token de um arquivo Python"""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip().startswith(varname):
                    # Pega o valor entre aspas
                    start = line.find('"')
                    end = line.rfind('"')
                    if start >= 0 and end > start:
                        return line[start + 1:end]
    except:
        pass
    return None


def show_windows_alert(title, message):
    """Mostra popup no Windows"""
    try:
        subprocess.Popen(
            ["msg.exe", "*", f"{title}: {message}"],
            creationflags=0x08000000  # CREATE_NO_WINDOW
        )
    except:
        print(f"ALERTA: {title} - {message}")


def check_all_tokens():
    print("=== Verificando Tokens ===\n")
    now = datetime.now()
    alerts = []

    for name, info in TOKENS.items():
        token = get_token_from_file(info["file"], info["var"])
        if not token:
            print(f"  {name}: token nao encontrado em {info['file']}")
            continue

        # PiPiAds usa formato especial
        if info.get("base64_decode"):
            exp = decode_pipiads_exp(token)
        else:
            exp = decode_jwt_exp(token, name)
        if not exp:
            print(f"  {name}: nao conseguiu decodificar expiracao")
            continue

        days_left = (exp - now).days
        hours_left = (exp - now).total_seconds() / 3600

        status = "OK"
        if hours_left <= 0:
            status = "EXPIRADO"
        elif days_left <= 1:
            status = "EXPIRA AMANHA"
        elif days_left <= 3:
            status = f"EXPIRA EM {days_left} DIAS"

        print(f"  {name}: expira em {exp.strftime('%d/%m/%Y %H:%M')} ({days_left}d restantes) [{status}]")

        # Alertar se expira em 1 dia ou menos
        if 0 < hours_left <= 24:
            alerts.append(f"{name} expira AMANHA! Renovar token em {info['file']}")
        elif hours_left <= 0:
            alerts.append(f"{name} EXPIROU! Renovar token em {info['file']}")
        elif days_left <= 3:
            alerts.append(f"{name} expira em {days_left} dias. Renovar token em {info['file']}")

    if alerts:
        print(f"\n  {len(alerts)} alerta(s)!")
        for alert in alerts:
            print(f"  >> {alert}")
            show_windows_alert("NinjaSpy Token", alert)
    else:
        print("\n  Todos os tokens OK.")

    return alerts


if __name__ == "__main__":
    check_all_tokens()
