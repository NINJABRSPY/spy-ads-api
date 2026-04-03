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

    // Enrich each creator with detail data
    console.log(`\n  Enriching ${allCreators.length} creators with detail data...`);
    for (let i = 0; i < allCreators.length; i++) {
      const c = allCreators[i];
      const uid = c.uid;
      if (!uid) continue;

      // Base info
      const base = await fetchAPI(`/api/author/v3/detail/baseInfo?uid=${uid}`);
      if (base) {
        c._detail_base = {
          category: base.category_name || '',
          selling_categories: base.market_category_l1_name || [],
          mcn: base.mcn?.company_name || '',
          first_video: base.first_video_time || '',
          shop_active: base.show_shop_tab === 1,
          region: base.region_name || '',
        };
      }
      await new Promise(r => setTimeout(r, 800));

      // Index (rankings, avg GMV)
      const idx = await fetchAPI(`/api/author/v3/detail/authorIndex?uid=${uid}`);
      if (idx) {
        c._detail_index = {
          region_rank: idx.region_rank || 0,
          category_rank: idx.category_rank || 0,
          flow_index: idx.flow_index || 0,
          carry_index: idx.carry_index || 0,
          followers: idx.follower_count || 0,
          new_followers_28d: idx.follower_28_count || 0,
          videos_28d: idx.aweme_28_count || 0,
          lives_28d: idx.live_28_count || 0,
          avg_video_views: idx.video_28_avg_play_count || 0,
          avg_live_sold: idx.live_28_avg_sold_count || 0,
          avg_live_gmv: idx.live_28_avg_sale_amount || 0,
        };
      }
      await new Promise(r => setTimeout(r, 800));

      // Stats (GMV, total views)
      const stats = await fetchAPI(`/api/author/v3/detail/getStatInfo?uid=${uid}`);
      if (stats) {
        c._detail_stats = {
          total_gmv: stats.goods_sale_amount || 0,
          video_gmv: stats.video_sale_amount || 0,
          live_gmv: stats.live_sale_amount || 0,
          country_rank: stats.goods_sale_country_rank || 0,
          total_videos: stats.aweme_total_count || 0,
          total_views: stats.aweme_play_count || 0,
          avg_views: stats.aweme_avg_play_count || 0,
          avg_engagement_rate: stats.aweme_avg_interaction_rate || '',
          live_count: stats.live_count || 0,
          live_avg_time: stats.live_avg_time_show || '',
          live_avg_viewers: stats.live_avg_user_count || 0,
          live_peak_viewers: stats.live_max_peak_count || 0,
          video_gpm: stats.aweme_min_gpm || 0,
          live_gpm: stats.live_gpm_min || 0,
        };
      }
      await new Promise(r => setTimeout(r, 800));

      // Contact
      const contact = await fetchAPI(`/api/author/v3/detail/authorContact?uid=${uid}`);
      if (contact) {
        c._detail_contact = contact;
      }
      await new Promise(r => setTimeout(r, 800));

      if ((i + 1) % 5 === 0 || i === 0) {
        console.log(`  ${i + 1}/${allCreators.length} enriched`);
      }
    }

    console.log(`\n  TOTAL CREATORS: ${allCreators.length} (enriched)\n`);

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
    // Strategy: query by category × ad type × period to maximize unique results
    // ========================================
    console.log('--- PHASE 4: CREATIVE CENTER ADS ---\n');
    const allAds = [];
    const adIds = new Set();

    // Navigate to creative center
    await send('Page.navigate', { url: 'https://www.fastmoss.com/creativecenter/search?region=US' });
    await new Promise(r => setTimeout(r, 6000));

    // Get ad filter info (categories for ads)
    const adFilterData = await fetchAPI(`/api/da/V4/filterInfo?region=${REGION}`);
    const adCategories = adFilterData?.category || [];
    console.log(`  Ad categories: ${adCategories.length}`);

    // da_type: 1=all, 2=hot product, 3=shop ads, 4=commission ads
    const adTypes = [
      { type: 1, name: 'All' },
      { type: 2, name: 'Hot Product' },
      { type: 3, name: 'Shop Ads' },
      { type: 4, name: 'Commission' },
    ];

    // Sort options for variety
    const sortOptions = [
      { field: 'play_count', order: '1,2', name: 'views' },
      { field: 'roas', order: '7,2', name: 'roas' },
      { field: 'digg_count', order: '2,2', name: 'likes' },
    ];

    // Period options
    const periods = ['7', '28', '90'];

    // 1. Global by type × sort
    for (const adType of adTypes) {
      for (const sort of sortOptions) {
        const data = await fetchAPI(
          `/api/da/V4/search?page=1&pagesize=12&da_type=${adType.type}&region=${REGION}&order=${sort.order}`
        );
        if (data?.ad_list) {
          let added = 0;
          for (const ad of data.ad_list) {
            if (!adIds.has(ad.id)) { adIds.add(ad.id); allAds.push(ad); added++; }
          }
          if (added > 0) console.log(`  ${adType.name} by ${sort.name}: +${added} (${allAds.length} total)`);
        }
        await new Promise(r => setTimeout(r, DELAY));
      }
    }

    // 2. By category (top 15 categories)
    const catCodes = adCategories.slice(0, 15).map(c => c.c_code || c.code);
    for (const catCode of catCodes) {
      const catName = adCategories.find(c => (c.c_code || c.code) === catCode)?.c_name || catCode;
      for (const adType of [adTypes[0], adTypes[3]]) { // All + Commission per category
        const data = await fetchAPI(
          `/api/da/V4/search?page=1&pagesize=12&da_type=${adType.type}&region=${REGION}&category=${catCode}`
        );
        if (data?.ad_list) {
          let added = 0;
          for (const ad of data.ad_list) {
            if (!adIds.has(ad.id)) { adIds.add(ad.id); allAds.push(ad); added++; }
          }
          if (added > 0) console.log(`  ${catName} (${adType.name}): +${added}`);
        }
        await new Promise(r => setTimeout(r, DELAY));
      }
    }

    // 3. By period
    for (const period of periods) {
      const data = await fetchAPI(
        `/api/da/V4/search?page=1&pagesize=12&da_type=1&region=${REGION}&date_type=${period}`
      );
      if (data?.ad_list) {
        let added = 0;
        for (const ad of data.ad_list) {
          if (!adIds.has(ad.id)) { adIds.add(ad.id); allAds.push(ad); added++; }
        }
        if (added > 0) console.log(`  Last ${period}d: +${added}`);
      }
      await new Promise(r => setTimeout(r, DELAY));
    }

    // 4. High ROAS specifically
    const roasData = await fetchAPI(
      `/api/da/V4/search?page=1&pagesize=12&da_type=1&region=${REGION}&order=7,2&roas_min=2`
    );
    if (roasData?.ad_list) {
      let added = 0;
      for (const ad of roasData.ad_list) {
        if (!adIds.has(ad.id)) { adIds.add(ad.id); allAds.push(ad); added++; }
      }
      if (added > 0) console.log(`  High ROAS (>2x): +${added}`);
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
