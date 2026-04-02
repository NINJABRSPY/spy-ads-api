// Test: What can an authenticated regular user do?
const WebSocket = require('ws');
const http = require('http');
const https = require('https');

const SUPABASE_URL = "https://bbwgequqwrsmbrkdmsxm.supabase.co";
const ANON_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImJid2dlcXVxd3JzbWJya2Rtc3htIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzEzNjAxMDksImV4cCI6MjA4NjkzNjEwOX0.Nh0_9bgXmJNFdaK6Faur_L86nELS17hs9OOpJ7vxMoM";

async function getWsUrl() {
  return new Promise((resolve, reject) => {
    http.get('http://localhost:9222/json', res => {
      let d = '';
      res.on('data', c => d += c);
      res.on('end', () => {
        const pages = JSON.parse(d).filter(t => t.type === 'page');
        const hub = pages.find(p => p.url.includes('ninjabrhub'));
        if (hub) resolve(hub.webSocketDebuggerUrl);
        else reject('Hub not found');
      });
    });
  });
}

function supabaseRequest(path, method, body, accessToken) {
  return new Promise((resolve) => {
    const url = new URL(path, SUPABASE_URL);
    const options = {
      method: method || 'GET',
      headers: {
        'apikey': ANON_KEY,
        'Authorization': `Bearer ${accessToken || ANON_KEY}`,
        'Content-Type': 'application/json',
        'Prefer': 'return=representation',
      }
    };

    const req = https.request(url, options, (res) => {
      let d = '';
      res.on('data', c => d += c);
      res.on('end', () => {
        try { resolve({ status: res.statusCode, data: JSON.parse(d) }); }
        catch { resolve({ status: res.statusCode, data: d.substring(0, 300) }); }
      });
    });
    if (body) req.write(JSON.stringify(body));
    req.end();
  });
}

async function main() {
  // Get the user's access token from the browser
  const wsUrl = await getWsUrl();
  const ws = new WebSocket(wsUrl);
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
    if (msg.id && pending[msg.id]) {
      pending[msg.id](msg.result);
      delete pending[msg.id];
    }
  });

  ws.on('open', async () => {
    console.log('=== USER PRIVILEGE ESCALATION TEST ===\n');

    // Extract the current user's access token
    const tokenResult = await send('Runtime.evaluate', {
      expression: `
        (function() {
          const key = Object.keys(localStorage).find(k => k.includes('supabase') && k.includes('auth'));
          if (key) {
            const data = JSON.parse(localStorage.getItem(key));
            return JSON.stringify({
              access_token: data.access_token,
              user_id: data.user?.id,
              email: data.user?.email,
              role: data.user?.role,
            });
          }
          return '{}';
        })()
      `,
      returnByValue: true
    });

    const auth = JSON.parse(tokenResult.result.value);
    const TOKEN = auth.access_token;
    console.log('Current user:', auth.email);
    console.log('User ID:', auth.user_id);
    console.log('Role:', auth.role);

    // =====================================================
    // TEST 1: Can I read OTHER users' profiles?
    // =====================================================
    console.log('\n--- TEST 1: Read other users profiles ---');
    const profiles = await supabaseRequest('/rest/v1/profiles?select=*&limit=10', 'GET', null, TOKEN);
    console.log(`Status: ${profiles.status}`);
    if (Array.isArray(profiles.data)) {
      console.log(`Profiles returned: ${profiles.data.length}`);
      if (profiles.data.length > 1) {
        console.log('⚠️ CAN SEE OTHER USERS PROFILES!');
        profiles.data.forEach(p => console.log(`  - ${p.email} (${p.user_id})`));
      } else if (profiles.data.length === 1) {
        console.log('✅ Only own profile visible');
      } else {
        console.log('✅ No profiles returned (RLS blocking)');
      }
    } else {
      console.log('Response:', JSON.stringify(profiles.data).substring(0, 200));
    }

    // =====================================================
    // TEST 2: Can I read other users' messages?
    // =====================================================
    console.log('\n--- TEST 2: Read other users messages ---');
    const msgs = await supabaseRequest('/rest/v1/chatgpt_messages?select=*&limit=10', 'GET', null, TOKEN);
    console.log(`Status: ${msgs.status}`);
    if (Array.isArray(msgs.data)) {
      const otherMsgs = msgs.data.filter(m => m.user_id !== auth.user_id);
      console.log(`Messages returned: ${msgs.data.length} (other users: ${otherMsgs.length})`);
      if (otherMsgs.length > 0) {
        console.log('⚠️ CAN READ OTHER USERS MESSAGES!');
      } else {
        console.log('✅ Only own messages visible');
      }
    }

    // =====================================================
    // TEST 3: Can I read other users' sessions (IPs)?
    // =====================================================
    console.log('\n--- TEST 3: Read other users sessions ---');
    const sessions = await supabaseRequest('/rest/v1/user_sessions?select=*&limit=10', 'GET', null, TOKEN);
    console.log(`Status: ${sessions.status}`);
    if (Array.isArray(sessions.data)) {
      const otherSessions = sessions.data.filter(s => s.user_id !== auth.user_id);
      console.log(`Sessions returned: ${sessions.data.length} (other users: ${otherSessions.length})`);
      if (otherSessions.length > 0) {
        console.log('⚠️ CAN SEE OTHER USERS IPs AND SESSIONS!');
      } else {
        console.log('✅ Only own sessions visible');
      }
    }

    // =====================================================
    // TEST 4: Can I modify my own subscription?
    // =====================================================
    console.log('\n--- TEST 4: Modify own subscription ---');
    const mySub = await supabaseRequest(`/rest/v1/user_subscriptions?user_id=eq.${auth.user_id}&select=*`, 'GET', null, TOKEN);
    console.log(`Current subscription:`, JSON.stringify(mySub.data).substring(0, 300));

    // Try to UPDATE subscription to unlimited
    if (Array.isArray(mySub.data) && mySub.data.length > 0) {
      const subId = mySub.data[0].id;
      const updateSub = await supabaseRequest(
        `/rest/v1/user_subscriptions?id=eq.${subId}`,
        'PATCH',
        { plan: 'unlimited', daily_limit: 999999, is_active: true },
        TOKEN
      );
      console.log(`Update attempt status: ${updateSub.status}`);
      if (updateSub.status < 300) {
        console.log('🔴 CRITICAL: USER CAN MODIFY THEIR OWN SUBSCRIPTION!');
      } else {
        console.log('✅ Cannot modify subscription (blocked by RLS)');
        console.log('  Response:', JSON.stringify(updateSub.data).substring(0, 200));
      }
    }

    // =====================================================
    // TEST 5: Can I escalate to admin role?
    // =====================================================
    console.log('\n--- TEST 5: Escalate to admin ---');
    const insertRole = await supabaseRequest(
      '/rest/v1/user_roles',
      'POST',
      { user_id: auth.user_id, role: 'admin' },
      TOKEN
    );
    console.log(`Insert admin role status: ${insertRole.status}`);
    if (insertRole.status < 300) {
      console.log('🔴 CRITICAL: USER CAN MAKE THEMSELVES ADMIN!');
    } else {
      console.log('✅ Cannot escalate to admin');
    }

    // =====================================================
    // TEST 6: Can I read provider configs (API keys)?
    // =====================================================
    console.log('\n--- TEST 6: Read provider configs ---');
    const configs = await supabaseRequest('/rest/v1/provider_configs?select=*', 'GET', null, TOKEN);
    console.log(`Status: ${configs.status}`);
    if (Array.isArray(configs.data) && configs.data.length > 0) {
      console.log('🔴 CRITICAL: CAN READ PROVIDER API KEYS!');
      console.log(JSON.stringify(configs.data).substring(0, 300));
    } else {
      console.log('✅ Cannot read provider configs');
    }

    // =====================================================
    // TEST 7: Can I access admin RPCs?
    // =====================================================
    console.log('\n--- TEST 7: Admin RPCs ---');
    const adminCheck = await supabaseRequest('/rest/v1/rpc/is_admin_or_above', 'POST', {}, TOKEN);
    console.log(`is_admin_or_above: ${JSON.stringify(adminCheck.data)}`);

    const detectSharing = await supabaseRequest('/rest/v1/rpc/detect_account_sharing', 'POST', { target_user_id: auth.user_id }, TOKEN);
    console.log(`detect_account_sharing: status ${detectSharing.status}`);

    // =====================================================
    // TEST 8: Can I read other users' usage?
    // =====================================================
    console.log('\n--- TEST 8: Read all usage data ---');
    const usage = await supabaseRequest('/rest/v1/chatgpt_usage?select=*&limit=10', 'GET', null, TOKEN);
    if (Array.isArray(usage.data)) {
      const otherUsage = usage.data.filter(u => u.user_id !== auth.user_id);
      console.log(`Usage rows: ${usage.data.length} (other users: ${otherUsage.length})`);
      if (otherUsage.length > 0) {
        console.log('⚠️ Can see other users usage data');
      } else {
        console.log('✅ Only own usage visible');
      }
    }

    // =====================================================
    // TEST 9: Can I delete other users' data?
    // =====================================================
    console.log('\n--- TEST 9: Delete other users data ---');
    const deleteTest = await supabaseRequest(
      '/rest/v1/profiles?user_id=neq.' + auth.user_id,
      'DELETE',
      null,
      TOKEN
    );
    console.log(`Delete other profiles status: ${deleteTest.status}`);
    if (deleteTest.status < 300) {
      console.log('🔴 CRITICAL: CAN DELETE OTHER USERS PROFILES!');
    } else {
      console.log('✅ Cannot delete other users data');
    }

    // =====================================================
    // TEST 10: Can I access Edge Functions directly?
    // =====================================================
    console.log('\n--- TEST 10: Edge Functions ---');
    const edgeFuncs = ['chatgpt-proxy', 'session-validate', 'admin-api'];
    for (const fn of edgeFuncs) {
      const r = await supabaseRequest(`/functions/v1/${fn}`, 'POST', { test: true }, TOKEN);
      console.log(`  ${fn}: status ${r.status} - ${JSON.stringify(r.data).substring(0, 100)}`);
    }

    // =====================================================
    // TEST 11: Can I insert fake news/announcements?
    // =====================================================
    console.log('\n--- TEST 11: Insert fake news ---');
    const fakeNews = await supabaseRequest(
      '/rest/v1/news',
      'POST',
      { title: 'SECURITY TEST - IGNORE', content: 'This is a security audit test', created_by: auth.user_id },
      TOKEN
    );
    console.log(`Insert news status: ${fakeNews.status}`);
    if (fakeNews.status < 300) {
      console.log('⚠️ User can create news/announcements!');
      // Clean up
      if (fakeNews.data && fakeNews.data[0]) {
        await supabaseRequest(`/rest/v1/news?id=eq.${fakeNews.data[0].id}`, 'DELETE', null, TOKEN);
      }
    } else {
      console.log('✅ Cannot insert news');
    }

    console.log('\n' + '='.repeat(50));
    console.log('  TEST COMPLETE');
    console.log('='.repeat(50));

    ws.close();
    process.exit(0);
  });
}

main().catch(console.error);
