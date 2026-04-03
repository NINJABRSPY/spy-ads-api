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

  const fm = tabs.find(p => p.url.includes('fastmoss.com'));
  const ws = new WebSocket(fm.webSocketDebuggerUrl);
  let msgId = 1;
  const pending = {};
  const apiCalls = [];

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
    if (msg.method === 'Network.requestWillBeSent') {
      const url = msg.params.request.url;
      if (url.includes('fastmoss.com/api/') &&
          !url.includes('notify') && !url.includes('userInfo') &&
          !url.includes('userPay') && !url.includes('getUserCards') &&
          !url.includes('user/user') && !url.includes('info/handle') &&
          !url.includes('country')) {
        apiCalls.push({ method: msg.params.request.method, url, postData: msg.params.request.postData || null });
        console.log(`[API] ${url.substring(0, 200)}`);
        if (msg.params.request.postData) console.log(`  Body: ${msg.params.request.postData.substring(0, 200)}`);
      }
    }
  });

  async function ev(expr) {
    const r = await send('Runtime.evaluate', { expression: expr, returnByValue: true, awaitPromise: true });
    return r.result.value;
  }

  ws.on('open', async () => {
    console.log('Connected!\n');
    await send('Network.enable');

    // Navigate directly to each page URL
    const pages = [
      { name: 'Products Top Selling', url: 'https://www.fastmoss.com/e-commerce/saleslist?columnKey=3' },
      { name: 'Products Search', url: 'https://www.fastmoss.com/e-commerce/search' },
      { name: 'High Commission', url: 'https://www.fastmoss.com/e-commerce/search?columnKey=1&field=crate&order=1%2C2' },
      { name: 'Creators Search', url: 'https://www.fastmoss.com/influencer/search' },
      { name: 'Shops', url: 'https://www.fastmoss.com/shop-marketing/search' },
      { name: 'LIVE Search', url: 'https://www.fastmoss.com/live/search' },
    ];

    for (const page of pages) {
      console.log(`\n=== ${page.name} ===`);
      apiCalls.length = 0;
      await send('Page.navigate', { url: page.url });
      await new Promise(r => setTimeout(r, 10000));

      const body = await ev('document.body.innerText.substring(0, 1000)');
      console.log('Content:', body.substring(0, 400));
      console.log('APIs called:', apiCalls.length);
    }

    // Get unique API endpoints
    console.log('\n\n=== SUMMARY ===');
    const unique = [...new Set(apiCalls.map(a => `${a.method} ${a.url.split('?')[0]}`))];
    unique.forEach(u => console.log(`  ${u}`));

    // Test the first product API found
    if (apiCalls.length > 0) {
      console.log('\n=== SAMPLE API RESPONSE ===');
      const firstApi = apiCalls.find(a => a.url.includes('product') || a.url.includes('commerce') || a.url.includes('shop'));
      if (firstApi) {
        const result = await ev(`(async () => { const r = await fetch("${firstApi.url.replace('https://www.fastmoss.com', '')}"); return await r.text(); })()`);
        console.log(result?.substring(0, 1500));
      }
    }

    ws.close();
    process.exit(0);
  });
}

main().catch(console.error);
