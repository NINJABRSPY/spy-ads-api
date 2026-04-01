const WebSocket = require('ws');
const http = require('http');
const fs = require('fs');

const REGIONS = ['us', 'uk', 'br', 'de', 'fr', 'es', 'it', 'mx', 'id', 'my', 'ph', 'sg', 'th', 'vn'];

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
    catch { return null; }
  }

  ws.on('open', async () => {
    console.log('=== SOCIAL1 CREATORS SCRAPER ===\n');

    // First check full response structure
    console.log('=== SAMPLE (US) ===');
    const sample = await fetchAPI('/api/creators?region=us&limit=3');
    console.log(JSON.stringify(sample, null, 2).substring(0, 1500));

    // Now scrape all regions
    const allCreators = [];
    const creatorIds = new Set();

    for (const region of REGIONS) {
      const data = await fetchAPI(`/api/creators?region=${region}`);

      if (!data || data.message || !Array.isArray(data)) {
        console.log(`  ${region.toUpperCase()}: no data`);
        continue;
      }

      let added = 0;
      for (const c of data) {
        const uid = c.creator_oecuid || c.handle || c.id;
        if (uid && !creatorIds.has(uid)) {
          creatorIds.add(uid);
          allCreators.push({
            ...c,
            _region: region,
            _scraped_at: new Date().toISOString(),
          });
          added++;
        }
      }

      console.log(`  ${region.toUpperCase()}: ${data.length} creators (${added} new, ${allCreators.length} total)`);
      await new Promise(r => setTimeout(r, 800));
    }

    // Phase 2: Fetch videos for each creator
    console.log(`\n--- PHASE 2: CREATOR VIDEOS ---\n`);

    for (let i = 0; i < allCreators.length; i++) {
      const c = allCreators[i];
      const cid = c.creator_oecuid;
      const region = c._region;
      const handle = c.handle;

      if (!cid) continue;

      const vids = await fetchAPI(`/api/creators/${cid}/videos?limit=12&offset=0&region=${region}&sort=popular&handle=${handle}`);

      if (vids && vids.results && vids.results.length > 0) {
        allCreators[i].videos = vids.results.map(v => ({
          video_id: v.video_id,
          description: (v.description || '').substring(0, 200),
          views: v.views || 0,
          likes: v.likes || 0,
          comments: v.comments || 0,
          is_ad: v.is_ad || false,
          time_posted: v.time_posted || '',
          thumbnail: v.thumbnail || '',
          video_url: v.video_url || '',
          gmv: v.gmv || '',
          insights: v.insights || null,
        }));
        allCreators[i].video_count = vids.results.length;
        allCreators[i].total_views = vids.results.reduce((sum, v) => sum + (v.views || 0), 0);
      } else {
        allCreators[i].videos = [];
        allCreators[i].video_count = 0;
        allCreators[i].total_views = 0;
      }

      if ((i + 1) % 10 === 0) {
        console.log(`  ${i + 1}/${allCreators.length} creators processed`);
      }

      await new Promise(r => setTimeout(r, 600));
    }

    const withVideos = allCreators.filter(c => c.video_count > 0).length;
    console.log(`  ${withVideos}/${allCreators.length} creators with videos\n`);

    // Save
    const timestamp = new Date().toISOString().slice(0, 10).replace(/-/g, '');
    const output = {
      source: 'social1',
      type: 'creators',
      scraped_at: new Date().toISOString(),
      total: allCreators.length,
      with_videos: withVideos,
      regions: [...new Set(allCreators.map(c => c._region))],
      creators: allCreators,
    };

    const filename = `resultados/social1_creators_${timestamp}.json`;
    fs.writeFileSync(filename, JSON.stringify(output, null, 2));

    console.log(`\n=== DONE ===`);
    console.log(`Total: ${allCreators.length} unique creators -> ${filename}`);

    // Stats
    console.log('\nTop 10 by GMV:');
    allCreators.sort((a, b) => (b.med_gmv_revenue || 0) - (a.med_gmv_revenue || 0));
    allCreators.slice(0, 10).forEach((c, i) => {
      const gmv = c.med_gmv_revenue || 0;
      const followers = c.follower_cnt || 0;
      console.log(`  ${i + 1}. @${c.handle} (${c.nickname}) | GMV: $${gmv.toLocaleString()} | Followers: ${followers.toLocaleString()} | Region: ${c._region}`);
    });

    ws.close();
    process.exit(0);
  });
}

main().catch(console.error);
