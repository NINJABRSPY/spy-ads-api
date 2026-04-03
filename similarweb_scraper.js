// SimilarWeb Scraper — Traffic data for any domain via CDP response intercept
// Method: Navigate to domain page → capture widgetApi responses → extract data
const WebSocket = require('ws');
const http = require('http');
const fs = require('fs');

const WAIT_TIME = 15000; // Time to wait for all widgets to load
const DELAY_BETWEEN = 5000; // Delay between domains

async function main() {
  const tabs = await new Promise(r => {
    http.get('http://localhost:9222/json', res => {
      let d = '';
      res.on('data', c => d += c);
      res.on('end', () => r(JSON.parse(d).filter(t => t.type === 'page')));
    });
  });

  const sw = tabs.find(p => p.url.includes('similarweb'));
  if (!sw) { console.log('SimilarWeb tab not found'); process.exit(1); }

  const ws = new WebSocket(sw.webSocketDebuggerUrl);
  let msgId = 1;
  const pending = {};

  function send(m, p = {}) {
    return new Promise(r => { const i = msgId++; pending[i] = r; ws.send(JSON.stringify({id: i, method: m, params: p})); });
  }

  ws.on('message', d => {
    const msg = JSON.parse(d.toString());
    if (msg.id && pending[msg.id]) { pending[msg.id](msg.result); delete pending[msg.id]; }
  });

  // Get domains to analyze from command line or use defaults
  let domains = process.argv.slice(2);
  if (domains.length === 0) {
    // Load top domains from our ads data
    try {
      const gzip = require('zlib');
      const unified = JSON.parse(gzip.gunzipSync(fs.readFileSync('resultados/unified_latest.json.gz')).toString());
      const domainCount = {};
      unified.forEach(ad => {
        const lp = ad.landing_page || '';
        if (lp.length > 10) {
          try {
            const d = new URL(lp).hostname.replace('www.', '');
            if (d && !d.includes('facebook') && !d.includes('google') && !d.includes('tiktok') && !d.includes('instagram')) {
              domainCount[d] = (domainCount[d] || 0) + 1;
            }
          } catch {}
        }
      });
      domains = Object.entries(domainCount)
        .sort((a, b) => b[1] - a[1])
        .slice(0, 30)
        .map(([d]) => d);
      console.log(`Auto-detected ${domains.length} top domains from ads data`);
    } catch {
      domains = ['medicube.com', 'willowhealth.com', 'shopify.com'];
    }
  }

  ws.on('open', async () => {
    console.log(`=== SIMILARWEB SCRAPER — ${domains.length} domains ===\n`);
    await send('Network.enable');

    const allResults = {};

    for (let i = 0; i < domains.length; i++) {
      const domain = domains[i];
      console.log(`[${i + 1}/${domains.length}] ${domain}...`);

      // Collect responses immediately when they arrive
      const domainData = { domain, scraped_at: new Date().toISOString() };
      let pendingBodies = [];

      const responseListener = (data) => {
        const msg = JSON.parse(data.toString());
        if (msg.method === 'Network.responseReceived') {
          const url = msg.params.response.url;
          if (url.includes('widgetApi') || (url.includes('/api/') && url.includes('WebsiteOverview'))) {
            const reqId = msg.params.requestId;
            const endpoint = url.split('?')[0].replace('https://pro.similarweb.com/', '');
            // Queue body fetch immediately
            pendingBodies.push({ endpoint, reqId });
          }
        }
      };
      ws.on('message', responseListener);

      // Navigate to domain page
      const navUrl = `https://pro.similarweb.com/#/digitalsuite/websiteanalysis/overview/website-performance/*/999/3m?webSource=Total&key=${domain}`;
      await send('Page.navigate', { url: navUrl });
      await new Promise(r => setTimeout(r, WAIT_TIME));

      ws.removeListener('message', responseListener);

      // Fetch all response bodies
      for (const { endpoint, reqId } of pendingBodies) {
        try {
          const body = await send('Network.getResponseBody', { requestId: reqId });
          if (body && body.body) {
            try {
              domainData[endpoint] = JSON.parse(body.body);
            } catch {}
          }
        } catch {}
      }

      // Extract key metrics
      const metrics = extractMetrics(domainData, domain);
      if (metrics) {
        allResults[domain] = metrics;
        console.log(`  Visits: ${metrics.monthly_visits?.toLocaleString() || 'N/A'} | Sources: Direct ${metrics.traffic_sources?.direct_pct || 'N/A'}% | Rank: ${metrics.global_rank || 'N/A'}`);
      } else {
        console.log(`  No data available`);
      }

      await new Promise(r => setTimeout(r, DELAY_BETWEEN));
    }

    // Save
    const timestamp = new Date().toISOString().slice(0, 10).replace(/-/g, '');
    const output = {
      source: 'similarweb',
      scraped_at: new Date().toISOString(),
      total_domains: Object.keys(allResults).length,
      domains: allResults,
    };

    const filename = `resultados/similarweb_${timestamp}.json`;
    fs.writeFileSync(filename, JSON.stringify(output, null, 2));
    console.log(`\n=== DONE: ${Object.keys(allResults).length} domains analyzed ===`);
    console.log(`Saved to: ${filename}`);

    ws.close();
    process.exit(0);
  });
}

function extractMetrics(data, domain) {
  const metrics = { domain };

  // Header
  const header = data['api/WebsiteOverview/getheader'];
  if (header && header[domain]) {
    const h = header[domain];
    metrics.title = h.title || '';
    metrics.icon = h.icon || '';
    metrics.global_rank = h.globalRanking || 0;
    metrics.country_rank = h.highestTrafficCountryRanking || 0;
    metrics.employee_range = h.employeeRange || '';
  }

  // Visits
  const visits = data['widgetApi/WebsiteOverview/EngagementVisits/SingleMetric'];
  if (visits?.Data?.[domain]) {
    const v = visits.Data[domain];
    metrics.monthly_visits = Math.round(v.TotalVisits || 0);
    metrics.visits_change = Math.round((v.Change || 0) * 100) / 100;
    metrics.visits_trend = v.Trend || [];
  }

  // Engagement
  const engagement = data['widgetApi/WebsiteOverview/EngagementOverview/Table'];
  if (engagement?.Data?.[0]) {
    const e = engagement.Data[0];
    metrics.bounce_rate = Math.round((e.BounceRate || 0) * 1000) / 10;
    metrics.avg_duration = Math.round(e.AvgVisitDuration || 0);
    metrics.pages_per_visit = Math.round((e.PagesPerVisit || 0) * 100) / 100;
    metrics.unique_users = Math.round(e.UniqueUsers || 0);
  }

  // Desktop vs Mobile
  const devices = data['widgetApi/WebsiteOverview/EngagementDesktopVsMobileVisits/PieChart'];
  if (devices?.Data?.[domain]) {
    const d = devices.Data[domain];
    const total = (d.Desktop || 0) + (d['Mobile Web'] || 0);
    metrics.desktop_pct = total > 0 ? Math.round((d.Desktop || 0) / total * 100) : 0;
    metrics.mobile_pct = total > 0 ? Math.round((d['Mobile Web'] || 0) / total * 100) : 0;
  }

  // Ranks with trend
  const ranks = data['widgetApi/WebsiteOverview/WebRanks/SingleMetric'];
  if (ranks?.Data?.[domain]) {
    const r = ranks.Data[domain];
    metrics.global_rank = r.GlobalRank?.Value || metrics.global_rank;
    metrics.global_rank_trend = (r.GlobalRank?.Trend || []).map(t => ({ month: t.Key, rank: t.Value }));
    metrics.country_rank = r.CountryRank?.Value || metrics.country_rank;
  }

  // Traffic sources
  const sources = data['widgetApi/MarketingMixTotal/TrafficSourcesOverview/PieChart'];
  if (sources?.Data?.Total?.[domain]) {
    const s = sources.Data.Total[domain];
    const total = Object.values(s).reduce((a, b) => a + b, 0);
    metrics.traffic_sources = {
      direct: Math.round(s.Direct || 0),
      direct_pct: total > 0 ? Math.round((s.Direct || 0) / total * 100) : 0,
      organic_search: Math.round(s['Organic Search'] || 0),
      organic_pct: total > 0 ? Math.round((s['Organic Search'] || 0) / total * 100) : 0,
      paid_search: Math.round(s['Paid Search'] || 0),
      paid_pct: total > 0 ? Math.round((s['Paid Search'] || 0) / total * 100) : 0,
      social: Math.round(s.Social || 0),
      social_pct: total > 0 ? Math.round(s.Social / total * 100) : 0,
      referrals: Math.round(s.Referrals || 0),
      display_ads: Math.round(s['Display Ads'] || 0),
      email: Math.round(s.Email || 0),
    };
  }

  // Geography
  const geo = data['widgetApi/WebsiteGeography/Geography/Table'];
  if (geo?.Data) {
    metrics.geography = geo.Data.map(g => ({
      country_code: g.Country,
      share: Math.round((g.Share || 0) * 1000) / 10,
    })).slice(0, 5);
    if (geo.Filters?.country) {
      metrics.geography_names = geo.Filters.country.map(c => ({
        code: c.id,
        name: c.text,
      }));
    }
  }

  // Visits graph
  const graph = data['widgetApi/WebsiteOverview/EngagementVisits/Graph'];
  if (graph?.Data?.[domain]?.Total?.[0]) {
    metrics.visits_graph = graph.Data[domain].Total[0].map(p => ({
      month: p.Key,
      visits: Math.round(p.Value),
    }));
  }

  // Top referrals
  const refs = data['widgetApi/WebsiteOverviewDesktop/TopReferrals/Table'];
  if (refs?.Data) {
    metrics.top_referrals = refs.Data.map(r => ({
      domain: r.Domain,
      share: Math.round((r.Share || 0) * 1000) / 10,
    })).slice(0, 5);
  }

  // Only return if we got meaningful data
  if (metrics.monthly_visits || metrics.global_rank) {
    return metrics;
  }
  return null;
}

main().catch(console.error);
