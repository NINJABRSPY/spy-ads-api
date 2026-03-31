const WebSocket = require('ws');
const http = require('http');
const fs = require('fs');

async function getWsUrl() {
  return new Promise((resolve, reject) => {
    http.get('http://localhost:9222/json', res => {
      let d = '';
      res.on('data', c => d += c);
      res.on('end', () => {
        const pages = JSON.parse(d).filter(t => t.type === 'page');
        const midas = pages.find(p => p.url.includes('clickmidas'));
        if (midas) resolve(midas.webSocketDebuggerUrl);
        else reject('ClickMidas tab not found');
      });
    });
  });
}

async function main() {
  const wsUrl = await getWsUrl();
  const ws = new WebSocket(wsUrl);
  let msgId = 1;
  const pending = {};

  function send(method, params = {}) {
    return new Promise((resolve) => {
      const id = msgId++;
      pending[id] = resolve;
      ws.send(JSON.stringify({ id, method, params }));
    });
  }

  ws.on('message', (data) => {
    const msg = JSON.parse(data.toString());
    if (msg.id && pending[msg.id]) {
      pending[msg.id](msg.result);
      delete pending[msg.id];
    }
  });

  ws.on('open', async () => {
    console.log('Connected! Checking current page...\n');

    // Check current URL
    const urlResult = await send('Runtime.evaluate', {
      expression: 'window.location.href',
      returnByValue: true
    });
    console.log('Current URL:', urlResult.result.value);

    // Wait a moment then get body text
    await new Promise(r => setTimeout(r, 3000));

    const result = await send('Runtime.evaluate', {
      expression: `
        (function() {
          return document.body.innerText.substring(0, 8000);
        })()
      `,
      returnByValue: true
    });

    console.log('=== PAGE CONTENT ===');
    console.log(result.result.value);

    ws.close();
    process.exit(0);
  });
}

main().catch(console.error);
