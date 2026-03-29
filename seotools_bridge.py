"""
SEOTools Bridge - Mini servidor local que faz ponte entre o Hub e o SEOTools
Roda no PC do cliente na porta 3993
O Hub faz fetch para http://127.0.0.1:3993/open?id=6 e o bridge abre a ferramenta

Uso: python seotools_bridge.py
"""
import requests
import re
from http.server import HTTPServer, BaseHTTPRequestHandler
import json

SEOTOOLS_PORT = 3992
BRIDGE_PORT = 3993
COOKIE = "9ee05bd4-66b7-4cc7-80e7-6df21ed58643"
VERSION = "20250906"


def open_tool(tool_id):
    """Envia browser:open para o SEOTools via HTTP polling"""
    try:
        # Handshake
        r = requests.get(
            f'http://127.0.0.1:{SEOTOOLS_PORT}/socket.io/?EIO=4&transport=polling',
            headers={'Origin': 'https://groupbuyseotools.org'},
            timeout=5
        )
        sid = re.search(r'"sid":"([^"]+)"', r.text).group(1)

        # Enviar browser:open
        url = f'https://groupbuyseotools.org/amember/content/p/id/{tool_id}/'
        payload = f'42["browser:open",{{"cookie":"{COOKIE}","v":"{VERSION}","url":"{url}"}}]'
        encoded = str(len(payload)) + ':' + payload

        r2 = requests.post(
            f'http://127.0.0.1:{SEOTOOLS_PORT}/socket.io/?EIO=4&transport=polling&sid={sid}',
            data=encoded,
            headers={
                'Content-Type': 'text/plain;charset=UTF-8',
                'Origin': 'https://groupbuyseotools.org'
            },
            timeout=5
        )
        return r2.status_code == 200
    except Exception as e:
        print(f'Erro: {e}')
        return False


class BridgeHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        # CORS headers para permitir qualquer origem
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, OPTIONS')
        self.send_header('Content-Type', 'application/json')
        self.end_headers()

        if self.path == '/status':
            # Verificar se SEOTools esta rodando
            try:
                r = requests.get(
                    f'http://127.0.0.1:{SEOTOOLS_PORT}/socket.io/?EIO=4&transport=polling',
                    headers={'Origin': 'https://groupbuyseotools.org'},
                    timeout=3
                )
                self.wfile.write(json.dumps({"status": "online"}).encode())
            except:
                self.wfile.write(json.dumps({"status": "offline"}).encode())

        elif self.path.startswith('/open?id='):
            # Abrir ferramenta
            tool_id = self.path.split('id=')[1].split('&')[0]
            success = open_tool(tool_id)
            self.wfile.write(json.dumps({
                "status": "opened" if success else "error",
                "tool_id": tool_id
            }).encode())

        else:
            self.wfile.write(json.dumps({
                "service": "SEOTools Bridge",
                "endpoints": ["/status", "/open?id=TOOL_ID"]
            }).encode())

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', '*')
        self.end_headers()

    def log_message(self, format, *args):
        print(f'  {args[0]}')


if __name__ == '__main__':
    print('=' * 50)
    print('  SEOTools Bridge - Porta', BRIDGE_PORT)
    print('  Status: http://127.0.0.1:3993/status')
    print('  Abrir:  http://127.0.0.1:3993/open?id=6')
    print('=' * 50)

    server = HTTPServer(('127.0.0.1', BRIDGE_PORT), BridgeHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\nBridge encerrado.')
