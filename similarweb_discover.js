const WebSocket = require('ws');
const http = require('http');

async function main() {
  const tabs = await new Promise(r => { http.get('http://localhost:9222/json', res => { let d=''; res.on('data',c=>d+=c); res.on('end',()=>r(JSON.parse(d).filter(t=>t.type==='page'))); }); });
  const sw = tabs.find(p => p.url.includes('similarweb'));
  if (!sw) { console.log('SimilarWeb not found'); process.exit(1); }

  const ws = new WebSocket(sw.webSocketDebuggerUrl);
  let msgId = 1;
  const pending = {};
  const apiCalls = [];

  function send(m, p = {}) {
    return new Promise(r => { const i = msgId++; pending[i] = r; ws.send(JSON.stringify({id: i, method: m, params: p})); });
  }

  ws.on('message', d => {
    const msg = JSON.parse(d.toString());
    if (msg.id && pending[msg.id]) { pending[msg.id](msg.result); delete pending[msg.id]; }

    if (msg.method === 'Network.requestWillBeSent') {
      const url = msg.params.request.url;
      if ((url.includes('similarweb') || url.includes('api')) &&
          !url.includes('.js') && !url.includes('.css') && !url.includes('.png') &&
          !url.includes('.svg') && !url.includes('.woff') && !url.includes('google') &&
          !url.includes('analytics') && !url.includes('sentry') && !url.includes('intercom') &&
          !url.includes('segment') && !url.includes('hotjar') && !url.includes('clarity')) {
        apiCalls.push({ method: msg.params.request.method, url });
        console.log(`[API] ${url.substring(0, 200)}`);
      }
    }
  });

  ws.on('open', async () => {
    console.log('Connected to SimilarWeb!\n');
    await send('Network.enable');

    // Reload to capture all API calls
    console.log('=== RELOADING ===\n');
    await send('Page.reload');
    await new Promise(r => setTimeout(r, 10000));

    // Get page content
    const body = await send('Runtime.evaluate', {
      expression: 'document.body.innerText.substring(0, 2000)',
      returnByValue: true
    });
    console.log('\n=== PAGE CONTENT ===');
    console.log(body.result.value.substring(0, 1000));

    console.log('\n=== UNIQUE API ENDPOINTS ===');
    const unique = [...new Set(apiCalls.map(a => `${a.method} ${a.url.split('?')[0]}`))];
    unique.forEach(u => console.log(`  ${u}`));

    console.log(`\nTotal: ${apiCalls.length} calls, ${unique.length} unique`);
    ws.close();
    process.exit(0);
  });
}

main().catch(console.error);
