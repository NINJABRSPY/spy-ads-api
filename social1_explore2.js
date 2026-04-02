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

  async function fetchFromPage(url) {
    const r = await send('Runtime.evaluate', {
      expression: `(async () => { const r = await fetch("${url}"); const t = await r.text(); return t.substring(0, 3000); })()`,
      returnByValue: true,
      awaitPromise: true,
    });
    return r.result.value;
  }

  ws.on('open', async () => {
    console.log('Connected!\n');

    // 1. Products API
    console.log('=== TOP PRODUCTS (UK, today, 3 items) ===');
    const prods = await fetchFromPage('/api/products/getTopProducts?limit=3&offset=0&days=1&region=uk');
    console.log(prods);

    // 2. Try US
    console.log('\n=== TOP PRODUCTS (US) ===');
    const prodsUS = await fetchFromPage('/api/products/getTopProducts?limit=2&offset=0&days=1&region=us');
    console.log(prodsUS);

    // 3. Try BR
    console.log('\n=== TOP PRODUCTS (BR) ===');
    const prodsBR = await fetchFromPage('/api/products/getTopProducts?limit=2&offset=0&days=1&region=br');
    console.log(prodsBR);

    // 4. Session
    console.log('\n=== SESSION ===');
    const session = await fetchFromPage('/api/auth/session');
    console.log(session);

    // 5. Credits
    console.log('\n=== CREDITS ===');
    const credits = await fetchFromPage('/api/user/credits');
    console.log(credits);

    // 6. Profile
    console.log('\n=== PROFILE ===');
    const profile = await fetchFromPage('/api/user/profile');
    console.log(profile);

    // 7. Try videos
    console.log('\n=== VIDEOS ===');
    const videos = await fetchFromPage('/api/videos/getTopVideos?limit=2&offset=0&days=1&region=uk');
    console.log((videos || '').substring(0, 1000));

    // 8. Try creators
    console.log('\n=== CREATORS ===');
    const creators = await fetchFromPage('/api/creators/getTopCreators?limit=2&offset=0&days=1&region=uk');
    console.log((creators || '').substring(0, 1000));

    // 9. Try 7 days
    console.log('\n=== 7 DAYS ===');
    const week = await fetchFromPage('/api/products/getTopProducts?limit=2&offset=0&days=7&region=us');
    console.log(week);

    // 10. Pagination - offset 12
    console.log('\n=== PAGE 2 ===');
    const page2 = await fetchFromPage('/api/products/getTopProducts?limit=3&offset=12&days=1&region=uk');
    console.log(page2);

    ws.close();
    process.exit(0);
  });
}

main().catch(console.error);
