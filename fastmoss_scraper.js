// FastMoss Full Scraper — Products, Creators, Shops by category
// IMPORTANT: region MUST stay US, pagesize MUST be 10 (plan limit)
const WebSocket = require('ws');
const http = require('http');
const fs = require('fs');

const REGION = 'US';
const PAGE_SIZE = 10; // Plan limit
const DELAY = 1500;

async function main() {
  const tabs = await new Promise((resolve) => {
    http.get('http://localhost:9222/json', res => {
      let d = '';
      res.on('data', c => d += c);
      res.on('end', () => resolve(JSON.parse(d).filter(t => t.type === 'page')));
    });
  });

  const fm = tabs.find(p => p.url.includes('fastmoss.com'));
  if (!fm) { console.log('FastMoss tab not found'); process.exit(1); }

  const ws = new WebSocket(fm.webSocketDebuggerUrl);
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
    const ts = Math.floor(Date.now() / 1000);
    const cn = Math.floor(Math.random() * 99999999);
    const sep = path.includes('?') ? '&' : '?';
    const fullPath = `${path}${sep}_time=${ts}&cnonce=${cn}`;

    const r = await send('Runtime.evaluate', {
      expression: `(async () => { const r = await fetch("${fullPath}"); return await r.text(); })()`,
      returnByValue: true,
      awaitPromise: true,
    });

    try {
      const data = JSON.parse(r.result.value);
      if (data.data) return data.data;
      return null;
    } catch { return null; }
  }

  ws.on('open', async () => {
    console.log('=== FASTMOSS FULL SCRAPER (US) ===\n');

    // Verify URL
    const urlCheck = await send('Runtime.evaluate', { expression: 'window.location.href', returnByValue: true });
    console.log('URL:', urlCheck.result.value);
    if (!urlCheck.result.value.includes('fastmoss.com')) {
      console.log('ERROR: Not on FastMoss!'); ws.close(); process.exit(1);
    }

    const timestamp = new Date().toISOString().slice(0, 10).replace(/-/g, '');
    const allProducts = [];
    const allCreators = [];
    const allShops = [];
    const productIds = new Set();
    const creatorIds = new Set();
    const shopIds = new Set();

    // ========================================
    // GET CATEGORIES
    // ========================================
    console.log('--- Getting categories ---');
    const filterData = await fetchAPI(`/api/goods/filterInfo?region=${REGION}`);
    const categories = filterData?.category || [];
    console.log(`Found ${categories.length} main categories\n`);

    // ========================================
    // PHASE 1: PRODUCTS — top selling per category
    // ========================================
    console.log('--- PHASE 1: PRODUCTS (by category) ---\n');

    // Global top selling first
    const globalData = await fetchAPI(`/api/goods/saleRank?page=1&pagesize=${PAGE_SIZE}&order=1,2&region=${REGION}&columnKey=3`);
    if (globalData?.rank_list) {
      for (const p of globalData.rank_list) {
        if (!productIds.has(p.product_id)) { productIds.add(p.product_id); allProducts.push({...p, _source_type: 'top_selling'}); }
      }
      console.log(`  Global top: +${globalData.rank_list.length} (${allProducts.length} total)`);
    }
    await new Promise(r => setTimeout(r, DELAY));

    // By each category
    for (const cat of categories) {
      const data = await fetchAPI(`/api/goods/saleRank?page=1&pagesize=${PAGE_SIZE}&order=1,2&region=${REGION}&columnKey=3&category=${cat.c_code}`);
      if (data?.rank_list) {
        let added = 0;
        for (const p of data.rank_list) {
          if (!productIds.has(p.product_id)) { productIds.add(p.product_id); allProducts.push({...p, _source_type: 'category_top', _category: cat.c_name}); added++; }
        }
        if (added > 0) console.log(`  ${cat.c_name}: +${added} (${allProducts.length} total)`);
      }
      await new Promise(r => setTimeout(r, DELAY));

      // Sub-categories
      if (cat.sub) {
        for (const sub of cat.sub.slice(0, 5)) { // Top 5 subcategories
          const subData = await fetchAPI(`/api/goods/saleRank?page=1&pagesize=${PAGE_SIZE}&order=1,2&region=${REGION}&columnKey=3&category=${sub.c_code}`);
          if (subData?.rank_list) {
            let added = 0;
            for (const p of subData.rank_list) {
              if (!productIds.has(p.product_id)) { productIds.add(p.product_id); allProducts.push({...p, _source_type: 'subcategory_top', _category: cat.c_name, _subcategory: sub.c_name}); added++; }
            }
            if (added > 0) console.log(`    ${sub.c_name}: +${added}`);
          }
          await new Promise(r => setTimeout(r, DELAY));
        }
      }
    }

    // Search top by GMV
    console.log('\n  Searching by GMV...');
    const gmvData = await fetchAPI(`/api/goods/saleRank?page=1&pagesize=${PAGE_SIZE}&order=1,2&region=${REGION}&columnKey=7`);
    if (gmvData?.rank_list) {
      let added = 0;
      for (const p of gmvData.rank_list) {
        if (!productIds.has(p.product_id)) { productIds.add(p.product_id); allProducts.push({...p, _source_type: 'top_gmv'}); added++; }
      }
      console.log(`  Top GMV: +${added}`);
    }
    await new Promise(r => setTimeout(r, DELAY));

    // High commission products
    console.log('\n  High commission products...');
    const commData = await fetchAPI(`/api/goods/V2/search?page=1&pagesize=${PAGE_SIZE}&order=1,2&region=${REGION}&columnKey=1&field=crate`);
    if (commData?.result) {
      let added = 0;
      for (const p of commData.result) {
        if (!productIds.has(p.product_id)) { productIds.add(p.product_id); allProducts.push({...p, _source_type: 'high_commission'}); added++; }
      }
      console.log(`  High commission: +${added}`);
    }

    console.log(`\n  TOTAL PRODUCTS: ${allProducts.length}\n`);

    // ========================================
    // PHASE 2: CREATORS
    // ========================================
    console.log('--- PHASE 2: CREATORS ---\n');
    const df = Buffer.from('fs_https://www.fastmoss.com_ps').toString('base64');

    // Top creators
    const crData = await fetchAPI(`/api/author/search?page=1&pagesize=${PAGE_SIZE}&df=${df}&region=${REGION}&order=1,2`);
    if (crData?.author_list) {
      for (const c of crData.author_list) {
        if (!creatorIds.has(c.uid)) { creatorIds.add(c.uid); allCreators.push({...c, _source_type: 'top_followers'}); }
      }
      console.log(`  Top by followers: +${crData.author_list.length}`);
    }
    await new Promise(r => setTimeout(r, DELAY));

    // Ecommerce creators
    const ecData = await fetchAPI(`/api/author/search?page=1&pagesize=${PAGE_SIZE}&df=${df}&region=${REGION}&order=1,2&shop_window=1&product=2`);
    if (ecData?.author_list) {
      let added = 0;
      for (const c of ecData.author_list) {
        if (!creatorIds.has(c.uid)) { creatorIds.add(c.uid); allCreators.push({...c, _source_type: 'ecommerce'}); added++; }
      }
      console.log(`  Ecommerce creators: +${added}`);
    }
    await new Promise(r => setTimeout(r, DELAY));

    // Creators by category
    const creatorCats = ['Shopping & Retail', 'Beauty', 'Health & Fitness', 'Food & Beverage', 'Home, Furniture & Appliances'];
    for (const catName of creatorCats) {
      const catData = await fetchAPI(`/api/author/search?page=1&pagesize=${PAGE_SIZE}&df=${df}&region=${REGION}&order=1,2&category=${encodeURIComponent(catName)}`);
      if (catData?.author_list) {
        let added = 0;
        for (const c of catData.author_list) {
          if (!creatorIds.has(c.uid)) { creatorIds.add(c.uid); allCreators.push({...c, _source_type: 'category', _category: catName}); added++; }
        }
        if (added > 0) console.log(`  ${catName}: +${added}`);
      }
      await new Promise(r => setTimeout(r, DELAY));
    }

    console.log(`\n  TOTAL CREATORS: ${allCreators.length}\n`);

    // ========================================
    // PHASE 3: SHOPS
    // ========================================
    console.log('--- PHASE 3: SHOPS ---\n');

    const shopData = await fetchAPI(`/api/shop/v3/search?page=1&pagesize=${PAGE_SIZE}&order=1,2&region=${REGION}`);
    if (shopData?.list) {
      for (const s of shopData.list) {
        const sid = s.shop_info?.seller_id || s.seller_id;
        if (sid && !shopIds.has(sid)) { shopIds.add(sid); allShops.push(s); }
      }
      console.log(`  Top shops: +${shopData.list.length}`);
    }
    await new Promise(r => setTimeout(r, DELAY));

    // Shops by category
    for (const cat of categories.slice(0, 8)) {
      const catShops = await fetchAPI(`/api/shop/v3/search?page=1&pagesize=${PAGE_SIZE}&order=1,2&region=${REGION}&category=${cat.c_code}`);
      if (catShops?.list) {
        let added = 0;
        for (const s of catShops.list) {
          const sid = s.shop_info?.seller_id || s.seller_id;
          if (sid && !shopIds.has(sid)) { shopIds.add(sid); allShops.push(s); added++; }
        }
        if (added > 0) console.log(`  ${cat.c_name}: +${added}`);
      }
      await new Promise(r => setTimeout(r, DELAY));
    }

    console.log(`\n  TOTAL SHOPS: ${allShops.length}\n`);

    // ========================================
    // PHASE 4: CREATIVE CENTER ADS (ROAS + metrics!)
    // ========================================
    console.log('--- PHASE 4: CREATIVE CENTER ADS ---\n');
    const allAds = [];
    const adIds = new Set();

    // Navigate to creative center first
    await send('Page.navigate', { url: 'https://www.fastmoss.com/creativecenter/search?region=US' });
    await new Promise(r => setTimeout(r, 6000));

    // da_type: 1=all, can try different types
    for (let page = 1; page <= 50; page++) {
      const data = await fetchAPI(`/api/da/V4/search?page=${page}&pagesize=12&da_type=1&region=${REGION}`);

      if (!data || !data.ad_list || data.ad_list.length === 0) break;

      for (const ad of data.ad_list) {
        if (!adIds.has(ad.id)) {
          adIds.add(ad.id);
          allAds.push(ad);
        }
      }

      if (page % 10 === 0 || page === 1) {
        console.log(`  Page ${page}: ${data.ad_list.length} ads (${allAds.length} total)`);
      }

      if (data.ad_list.length < 12) break;
      await new Promise(r => setTimeout(r, DELAY));
    }

    // Also get Commission Ads specifically
    console.log('\n  Commission ads...');
    for (let page = 1; page <= 20; page++) {
      const data = await fetchAPI(`/api/da/V4/search?page=${page}&pagesize=12&da_type=4&region=${REGION}`);
      if (!data || !data.ad_list || data.ad_list.length === 0) break;

      let added = 0;
      for (const ad of data.ad_list) {
        if (!adIds.has(ad.id)) { adIds.add(ad.id); allAds.push(ad); added++; }
      }
      if (page === 1) console.log(`  Commission page 1: +${added}`);
      if (data.ad_list.length < 12) break;
      await new Promise(r => setTimeout(r, DELAY));
    }

    // Hot Product Ads
    console.log('  Hot product ads...');
    for (let page = 1; page <= 20; page++) {
      const data = await fetchAPI(`/api/da/V4/search?page=${page}&pagesize=12&da_type=2&region=${REGION}`);
      if (!data || !data.ad_list || data.ad_list.length === 0) break;

      let added = 0;
      for (const ad of data.ad_list) {
        if (!adIds.has(ad.id)) { adIds.add(ad.id); allAds.push(ad); added++; }
      }
      if (page === 1) console.log(`  Hot product page 1: +${added}`);
      if (data.ad_list.length < 12) break;
      await new Promise(r => setTimeout(r, DELAY));
    }

    console.log(`\n  TOTAL ADS: ${allAds.length}\n`);

    // ========================================
    // SAVE
    // ========================================
    const output = {
      source: 'fastmoss',
      region: REGION,
      scraped_at: new Date().toISOString(),
      totals: {
        products: allProducts.length,
        creators: allCreators.length,
        shops: allShops.length,
        ads: allAds.length,
      },
      categories: categories.map(c => ({ code: c.c_code, name: c.c_name, subs: c.sub?.length || 0 })),
      products: allProducts,
      creators: allCreators,
      shops: allShops,
      ads: allAds,
    };

    const filename = `resultados/fastmoss_${timestamp}.json`;
    fs.writeFileSync(filename, JSON.stringify(output, null, 2));

    console.log('=== DONE ===');
    console.log(`Products: ${allProducts.length}`);
    console.log(`Creators: ${allCreators.length}`);
    console.log(`Shops: ${allShops.length}`);
    console.log(`Ads: ${allAds.length}`);
    console.log(`Saved to: ${filename}`);

    if (allAds.length > 0) {
      console.log('\nTop 5 ads by ROAS:');
      allAds.sort((a, b) => (b.roas || 0) - (a.roas || 0));
      allAds.slice(0, 5).forEach((a, i) => {
        console.log(`  ${i + 1}. ROAS: ${a.roas} | Views: ${a.play_count_show} | Cost: $${a.estimate_cost} | ${(a.desc || '').substring(0, 50)}`);
      });
    }

    if (allProducts.length > 0) {
      console.log('\nTop 5 products:');
      allProducts.sort((a, b) => (b.sold_count || 0) - (a.sold_count || 0));
      allProducts.slice(0, 5).forEach((p, i) => {
        console.log(`  ${i + 1}. ${(p.title || '').substring(0, 50)} | Sold: ${p.sold_count_show} | Commission: ${p.commission_rate}`);
      });
    }

    ws.close();
    process.exit(0);
  });
}

main().catch(console.error);
