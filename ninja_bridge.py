"""
NinjaBR Tools Bridge
Conecta o Hub NinjaBR com o SEOTools.
O cliente executa este .exe e ele fica rodando na bandeja do sistema.
"""
import requests
import re
import json
import sys
import os
from http.server import HTTPServer, BaseHTTPRequestHandler
from threading import Thread

SEOTOOLS_PORT = 3992
BRIDGE_PORT = 3993
COOKIE = "9ee05bd4-66b7-4cc7-80e7-6df21ed58643"
VERSION = "20250906"


def open_tool(tool_id):
    try:
        r = requests.get(
            f'http://127.0.0.1:{SEOTOOLS_PORT}/socket.io/?EIO=4&transport=polling',
            headers={'Origin': 'https://groupbuyseotools.org'},
            timeout=5
        )
        sid = re.search(r'"sid":"([^"]+)"', r.text).group(1)
        url = f'https://groupbuyseotools.org/amember/content/p/id/{tool_id}/'
        payload = f'42["browser:open",{{"cookie":"{COOKIE}","v":"{VERSION}","url":"{url}"}}]'
        encoded = str(len(payload)) + ':' + payload
        r2 = requests.post(
            f'http://127.0.0.1:{SEOTOOLS_PORT}/socket.io/?EIO=4&transport=polling&sid={sid}',
            data=encoded,
            headers={'Content-Type': 'text/plain;charset=UTF-8', 'Origin': 'https://groupbuyseotools.org'},
            timeout=5
        )
        return r2.status_code == 200
    except:
        return False


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, OPTIONS')
        self.send_header('Content-Type', 'application/json')
        self.end_headers()

        if self.path == '/status':
            try:
                requests.get(
                    f'http://127.0.0.1:{SEOTOOLS_PORT}/socket.io/?EIO=4&transport=polling',
                    headers={'Origin': 'https://groupbuyseotools.org'}, timeout=3)
                self.wfile.write(b'{"status":"online"}')
            except:
                self.wfile.write(b'{"status":"offline"}')
        elif self.path.startswith('/open?id='):
            tid = self.path.split('id=')[1].split('&')[0]
            ok = open_tool(tid)
            self.wfile.write(json.dumps({"status": "opened" if ok else "error", "tool_id": tid}).encode())
        else:
            self.wfile.write(b'{"service":"NinjaBR Tools Bridge","version":"1.0"}')

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', '*')
        self.end_headers()

    def log_message(self, *args):
        pass


def main():
    print("NinjaBR Tools Bridge v1.0")
    print("Porta: 3993")
    print("Nao feche esta janela.")
    print("Minimize e use o Hub normalmente.")
    print("")
    server = HTTPServer(('127.0.0.1', BRIDGE_PORT), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass

if __name__ == '__main__':
    main()
