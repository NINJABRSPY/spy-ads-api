const WebSocket = require('ws');
const http = require('http');

async function main() {
  const tabs = await new Promise((resolve) => {
    http.get('http://localhost:9222/json', res => {
      let d = '';
      res.on('data', c => d += c);
      res.on('end', () => resolve(JSON.parse(d).filter(t => t.type === 'page')));
    });
  });

  const social1 = tabs.find(p => p.url.includes('social1'));
  const ws = new WebSocket(social1.webSocketDebuggerUrl);
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
    if (msg.id && pending[msg.id]) { pending[msg.id](msg.result); delete pending[msg.id]; }
  });

  async function fetchAPI(path) {
    const r = await send('Runtime.evaluate', {
      expression: `(async () => { const r = await fetch("${path}"); return await r.text(); })()`,
      returnByValue: true,
      awaitPromise: true,
    });
    try { return JSON.parse(r.result.value); }
    catch { return { raw: r.result.value?.substring(0, 300) }; }
  }

  ws.on('open', async () => {
    console.log('=== EXPLORING CREATORS API ===\n');

    // Try different creator endpoints
    const endpoints = [
      '/api/creators/getTopCreators?limit=5&offset=0&days=1&region=us',
      '/api/creators/getTopCreators?limit=5&offset=0&days=7&region=us',
      '/api/creators/getTopCreators?limit=5&offset=0&days=1&region=uk',
      '/api/creators/getTopCreators?limit=5&offset=0&days=1&region=br',
      '/api/creators/top?limit=5&region=us',
      '/api/creators/trending?limit=5&region=us',
      '/api/creators/list?limit=5&region=us',
      '/api/creators?limit=5&region=us',
      '/api/creators/getCreators?limit=5&offset=0&days=1&region=us',
      '/api/creators/getRisingCreators?limit=5&offset=0&days=1&region=us',
    ];

    for (const ep of endpoints) {
      console.log(`[GET] ${ep}`);
      const r = await fetchAPI(ep);
      const preview = JSON.stringify(r).substring(0, 300);
      console.log(`  -> ${preview}\n`);
      await new Promise(r => setTimeout(r, 500));
    }

    // Now navigate to the creators page and intercept network calls
    console.log('=== NAVIGATING TO CREATORS PAGE ===\n');
    await send('Network.enable');

    const apiCalls = [];
    const originalHandler = ws.listeners('message').find(f => f !== undefined);

    // Add network listener
    const networkListener = (data) => {
      const msg = JSON.parse(data.toString());
      if (msg.method === 'Network.requestWillBeSent') {
        const req = msg.params.request;
        if (req.url.includes('/api/') && req.url.includes('social1')) {
          apiCalls.push({ method: req.method, url: req.url });
          console.log(`  [${req.method}] ${req.url}`);
        }
      }
    };
    ws.on('message', networkListener);

    await send('Page.navigate', { url: 'https://www.social1.ai/creators' });
    await new Promise(r => setTimeout(r, 6000));

    // Get page content
    const body = await send('Runtime.evaluate', {
      expression: 'document.body.innerText.substring(0, 3000)',
      returnByValue: true,
      awaitPromise: false,
    });
    console.log('\n=== CREATORS PAGE CONTENT ===');
    console.log(body.result.value?.substring(0, 2000));

    console.log('\n=== API CALLS INTERCEPTED ===');
    apiCalls.forEach(c => console.log(`  ${c.method} ${c.url}`));

    ws.removeListener('message', networkListener);
    ws.close();
    process.exit(0);
  });
}

main().catch(console.error);
