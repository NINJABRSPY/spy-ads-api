// Social1.ai Full Scraper — Products + Videos, all regions and periods
const WebSocket = require('ws');
const http = require('http');
const fs = require('fs');

const REGIONS = ['us', 'uk', 'br', 'de', 'fr', 'es', 'it', 'id', 'my', 'ph', 'sg', 'th', 'vn', 'mx', 'jp', 'kr', 'sa', 'ae'];
const DAYS = [1, 7, 30];
const PRODUCTS_PER_PAGE = 50;
const MAX_PAGES = 20; // 50 x 20 = 1000 per region/day combo

async function main() {
  const tabs = await new Promise((resolve) => {
    http.get('http://localhost:9222/json', res => {
      let d = '';
      res.on('data', c => d += c);
      res.on('end', () => resolve(JSON.parse(d).filter(t => t.type === 'page')));
    });
  });

  const social1 = tabs.find(p => p.url.includes('social1'));
  if (!social1) { console.log('Social1 tab not found'); process.exit(1); }

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
    catch { return null; }
  }

  ws.on('open', async () => {
    console.log('=== SOCIAL1.AI FULL SCRAPER ===\n');

    const allProducts = [];
    const allVideos = [];
    const productIds = new Set();
    const videoIds = new Set();

    // =============================================
    // PHASE 1: PRODUCTS — all regions x all periods
    // =============================================
    console.log('--- PHASE 1: PRODUCTS ---\n');

    for (const days of DAYS) {
      for (const region of REGIONS) {
        let offset = 0;
        let pageNum = 0;
        let regionTotal = 0;

        while (pageNum < MAX_PAGES) {
          const data = await fetchAPI(
            `/api/products/getTopProducts?limit=${PRODUCTS_PER_PAGE}&offset=${offset}&days=${days}&region=${region}`
          );

          if (!data || !data.results || data.results.length === 0) break;

          for (const p of data.results) {
            const uid = `social1:${p.product_id}`;
            if (!productIds.has(uid)) {
              productIds.add(uid);
              allProducts.push({
                ...p,
                _region: region,
                _days: days,
                _scraped_at: new Date().toISOString(),
              });
            }
            regionTotal++;
          }

          if (!data.has_more) break;
          offset += data.results.length;
          pageNum++;

          // Small delay
          await new Promise(r => setTimeout(r, 800));
        }

        if (regionTotal > 0) {
          console.log(`  [${days}d] ${region.toUpperCase()}: ${regionTotal} products (${allProducts.length} unique total)`);
        }
      }
    }

    console.log(`\nProducts total: ${allProducts.length} unique\n`);

    // =============================================
    // PHASE 2: VIDEOS — all regions x all periods
    // =============================================
    console.log('--- PHASE 2: VIDEOS ---\n');

    for (const days of DAYS) {
      for (const region of REGIONS) {
        let offset = 0;
        let pageNum = 0;
        let regionTotal = 0;

        while (pageNum < MAX_PAGES) {
          const data = await fetchAPI(
            `/api/videos/getTopVideos?limit=${PRODUCTS_PER_PAGE}&offset=${offset}&days=${days}&region=${region}`
          );

          if (!data || !data.results || data.results.length === 0) break;

          for (const v of data.results) {
            const uid = `social1:${v.video_id}`;
            if (!videoIds.has(uid)) {
              videoIds.add(uid);
              allVideos.push({
                ...v,
                _region: region,
                _days: days,
                _scraped_at: new Date().toISOString(),
              });
            }
            regionTotal++;
          }

          if (!data.has_more) break;
          offset += data.results.length;
          pageNum++;

          await new Promise(r => setTimeout(r, 800));
        }

        if (regionTotal > 0) {
          console.log(`  [${days}d] ${region.toUpperCase()}: ${regionTotal} videos (${allVideos.length} unique total)`);
        }
      }
    }

    console.log(`\nVideos total: ${allVideos.length} unique\n`);

    // =============================================
    // PHASE 3: PRODUCT DETAILS + PRODUCT VIDEOS
    // For top products, fetch detail + associated videos
    // =============================================
    console.log('--- PHASE 3: PRODUCT DETAILS + PRODUCT VIDEOS ---\n');

    const productDetails = [];
    const productVideos = [];
    // Get top 50 products by views (most relevant ones)
    const topProducts = [...allProducts]
      .sort((a, b) => (b.video_views || 0) - (a.video_views || 0))
      .slice(0, 100);

    for (let i = 0; i < topProducts.length; i++) {
      const p = topProducts[i];
      const pid = p.product_id;
      const region = p._region || 'us';

      // Fetch product detail
      const detail = await fetchAPI(`/api/products/${pid}?region=${region}`);
      if (detail && !detail.message) {
        productDetails.push({ ...detail, _region: region });
      }

      // Fetch product videos (top 12)
      const vids = await fetchAPI(`/api/videos/getTopVideos?limit=12&offset=0&days=7&region=${region}&productID=${pid}`);
      if (vids && vids.results) {
        for (const v of vids.results) {
          const uid = `social1:${v.video_id}`;
          if (!videoIds.has(uid)) {
            videoIds.add(uid);
            allVideos.push({
              ...v,
              _region: region,
              _days: 7,
              _product_id: pid,
              _scraped_at: new Date().toISOString(),
            });
          }
        }
        productVideos.push({ product_id: pid, video_count: vids.results.length });
      }

      if ((i + 1) % 10 === 0) {
        console.log(`  ${i + 1}/${topProducts.length} products processed (${allVideos.length} total videos)`);
      }

      await new Promise(r => setTimeout(r, 600));
    }

    console.log(`  Product details: ${productDetails.length}`);
    console.log(`  Product videos added: ${productVideos.reduce((a, b) => a + b.video_count, 0)}`);
    console.log(`  Total videos now: ${allVideos.length}\n`);

    // =============================================
    // SAVE
    // =============================================
    const timestamp = new Date().toISOString().slice(0, 10).replace(/-/g, '');

    const productsOutput = {
      source: 'social1',
      type: 'products',
      scraped_at: new Date().toISOString(),
      total: allProducts.length,
      regions_scraped: [...new Set(allProducts.map(p => p._region))],
      periods_scraped: DAYS,
      categories: {},
      products: allProducts,
    };
    // Count categories
    allProducts.forEach(p => {
      const cat = p.top_category || p.categories?.[0] || 'Unknown';
      productsOutput.categories[cat] = (productsOutput.categories[cat] || 0) + 1;
    });

    const videosOutput = {
      source: 'social1',
      type: 'videos',
      scraped_at: new Date().toISOString(),
      total: allVideos.length,
      regions_scraped: [...new Set(allVideos.map(v => v._region))],
      periods_scraped: DAYS,
      videos: allVideos,
    };

    const prodFile = `resultados/social1_products_${timestamp}.json`;
    const vidFile = `resultados/social1_videos_${timestamp}.json`;

    fs.writeFileSync(prodFile, JSON.stringify(productsOutput, null, 2));
    fs.writeFileSync(vidFile, JSON.stringify(videosOutput, null, 2));

    console.log('=== DONE ===');
    console.log(`Products: ${allProducts.length} -> ${prodFile}`);
    console.log(`Videos: ${allVideos.length} -> ${vidFile}`);

    // Stats
    console.log('\nTop categories:');
    Object.entries(productsOutput.categories)
      .sort((a, b) => b[1] - a[1])
      .slice(0, 10)
      .forEach(([cat, count]) => console.log(`  ${cat}: ${count}`));

    console.log('\nTop regions by products:');
    const byRegion = {};
    allProducts.forEach(p => { byRegion[p._region] = (byRegion[p._region] || 0) + 1; });
    Object.entries(byRegion).sort((a, b) => b[1] - a[1]).forEach(([r, c]) => console.log(`  ${r}: ${c}`));

    ws.close();
    process.exit(0);
  });
}

main().catch(console.error);
