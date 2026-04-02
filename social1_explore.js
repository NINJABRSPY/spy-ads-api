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

  async function ev(expr) {
    const r = await send('Runtime.evaluate', { expression: expr, returnByValue: true });
    return r.result.value;
  }

  ws.on('open', async () => {
    console.log('Connected!\n');

    // 1. Get session/auth info
    console.log('=== AUTH ===');
    const session = await ev(`
      fetch('/api/auth/session').then(r => r.text()).then(t => t.substring(0, 500))
    `);
    console.log('Session:', session);

    // 2. Get user profile
    console.log('\n=== PROFILE ===');
    const profile = await ev(`
      fetch('/api/user/profile').then(r => r.text()).then(t => t.substring(0, 500))
    `);
    console.log('Profile:', profile);

    // 3. Get top products with full response
    console.log('\n=== TOP PRODUCTS (sample) ===');
    const products = await ev(`
      fetch('/api/products/getTopProducts?limit=3&offset=0&days=1&region=uk')
        .then(r => r.text())
        .then(t => t.substring(0, 3000))
    `);
    console.log(products);

    // 4. Try different regions
    console.log('\n=== REGIONS ===');
    const regions = ['us', 'uk', 'br', 'de', 'fr', 'es', 'it', 'global'];
    for (const region of regions) {
      const r = await ev(`
        fetch('/api/products/getTopProducts?limit=1&offset=0&days=1&region=${region}')
          .then(r => r.json())
          .then(d => JSON.stringify({region: '${region}', count: d.data ? d.data.length : (d.length || 0), hasData: !d.error}))
          .catch(e => JSON.stringify({region: '${region}', error: e.message}))
      `);
      console.log(`  ${r}`);
    }

    // 5. Try different days
    console.log('\n=== TIME PERIODS ===');
    const days = [1, 7, 30];
    for (const d of days) {
      const r = await ev(`
        fetch('/api/products/getTopProducts?limit=1&offset=0&days=${d}&region=us')
          .then(r => r.json())
          .then(data => JSON.stringify({days: ${d}, count: data.data ? data.data.length : (data.length || 0)}))
          .catch(e => JSON.stringify({days: ${d}, error: e.message}))
      `);
      console.log(`  ${r}`);
    }

    // 6. Try other API endpoints
    console.log('\n=== OTHER ENDPOINTS ===');
    const endpoints = [
      '/api/products/getCategories',
      '/api/products/getStores',
      '/api/products/search?q=skincare',
      '/api/products/trending',
      '/api/videos/getTopVideos?limit=2&region=us',
      '/api/creators/getTopCreators?limit=2&region=us',
    ];
    for (const ep of endpoints) {
      const r = await ev(`
        fetch('${ep}')
          .then(r => ({status: r.status, text: r.text()}))
          .then(async o => {
            const t = await o.text;
            return JSON.stringify({endpoint: '${ep}', status: o.status, preview: t.substring(0, 200)});
          })
          .catch(e => JSON.stringify({endpoint: '${ep}', error: e.message}))
      `);
      console.log(`  ${r}`);
    }

    // 7. Check pagination limits
    console.log('\n=== PAGINATION ===');
    const pagTest = await ev(`
      fetch('/api/products/getTopProducts?limit=50&offset=0&days=1&region=us')
        .then(r => r.json())
        .then(d => JSON.stringify({
          returned: d.data ? d.data.length : (d.length || 0),
          total: d.total || d.totalCount || 'unknown',
          hasMore: d.hasMore || d.has_more || 'unknown'
        }))
    `);
    console.log('  Limit 50:', pagTest);

    ws.close();
    process.exit(0);
  });
}

main().catch(console.error);
