const WebSocket = require('ws');
const http = require('http');
const https = require('https');

const SUPABASE_URL = "https://bbwgequqwrsmbrkdmsxm.supabase.co";
const ANON_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImJid2dlcXVxd3JzbWJya2Rtc3htIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzEzNjAxMDksImV4cCI6MjA4NjkzNjEwOX0.Nh0_9bgXmJNFdaK6Faur_L86nELS17hs9OOpJ7vxMoM";

function apiCall(path, method, body, token) {
  return new Promise((resolve) => {
    const url = new URL(path, SUPABASE_URL);
    const req = https.request(url, {
      method: method || 'GET',
      headers: {
        'apikey': ANON_KEY,
        'Authorization': `Bearer ${token}`,
        'Content-Type': 'application/json',
        'Prefer': 'return=representation',
      }
    }, (res) => {
      let d = '';
      res.on('data', c => d += c);
      res.on('end', () => {
        try { resolve({ s: res.statusCode, d: JSON.parse(d) }); }
        catch { resolve({ s: res.statusCode, d: d.substring(0, 300) }); }
      });
    });
    if (body) req.write(JSON.stringify(body));
    req.end();
  });
}

async function main() {
  // Get token from browser
  const wsUrl = await new Promise((resolve) => {
    http.get('http://localhost:9222/json', res => {
      let d = '';
      res.on('data', c => d += c);
      res.on('end', () => {
        const hub = JSON.parse(d).filter(t => t.type === 'page').find(p => p.url.includes('ninjabrhub'));
        resolve(hub.webSocketDebuggerUrl);
      });
    });
  });

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
    if (msg.id && pending[msg.id]) { pending[msg.id](msg.result); delete pending[msg.id]; }
  });

  await new Promise(r => ws.on('open', r));

  const tokenR = await send('Runtime.evaluate', {
    expression: `JSON.parse(localStorage.getItem('sb-bbwgequqwrsmbrkdmsxm-auth-token')).access_token`,
    returnByValue: true
  });
  const TOKEN = tokenR.result.value;

  const userR = await send('Runtime.evaluate', {
    expression: `JSON.parse(localStorage.getItem('sb-bbwgequqwrsmbrkdmsxm-auth-token')).user.id`,
    returnByValue: true
  });
  const USER_ID = userR.result.value;

  console.log('=== AUTHENTICATED USER PENETRATION TEST ===');
  console.log('User: teste@teste.com');
  console.log('ID:', USER_ID);
  console.log('Token:', TOKEN.substring(0, 40) + '...\n');

  ws.close();

  const tests = [];

  // TEST 1: Read ALL profiles
  console.log('--- 1. Ler perfis de outros usuários ---');
  let r = await apiCall('/rest/v1/profiles?select=id,email,display_name,expires_at&limit=20', 'GET', null, TOKEN);
  let others = Array.isArray(r.d) ? r.d.filter(p => p.id !== USER_ID && p.user_id !== USER_ID) : [];
  console.log(`  Retornou: ${Array.isArray(r.d) ? r.d.length : 0} perfis | Outros: ${others.length}`);
  if (others.length > 0) { console.log('  🔴 VAZAMENTO: emails de outros usuários!'); tests.push('FAIL'); }
  else { console.log('  ✅ Bloqueado'); tests.push('PASS'); }

  // TEST 2: Read ALL subscriptions
  console.log('\n--- 2. Ler assinaturas de outros ---');
  r = await apiCall('/rest/v1/user_subscriptions?select=*&limit=20', 'GET', null, TOKEN);
  others = Array.isArray(r.d) ? r.d.filter(s => s.user_id !== USER_ID) : [];
  console.log(`  Retornou: ${Array.isArray(r.d) ? r.d.length : 0} | Outros: ${others.length}`);
  if (others.length > 0) { console.log('  🔴 VAZAMENTO: assinaturas de outros!'); tests.push('FAIL'); }
  else { console.log('  ✅ Bloqueado'); tests.push('PASS'); }

  // TEST 3: Read messages
  console.log('\n--- 3. Ler mensagens de outros ---');
  r = await apiCall('/rest/v1/chatgpt_messages?select=user_id,content&limit=20', 'GET', null, TOKEN);
  others = Array.isArray(r.d) ? r.d.filter(m => m.user_id !== USER_ID) : [];
  console.log(`  Retornou: ${Array.isArray(r.d) ? r.d.length : 0} | Outros: ${others.length}`);
  if (others.length > 0) { console.log('  🔴 VAZAMENTO: conversas de outros!'); tests.push('FAIL'); }
  else { console.log('  ✅ Bloqueado'); tests.push('PASS'); }

  // TEST 4: Read sessions (IPs)
  console.log('\n--- 4. Ler sessões/IPs de outros ---');
  r = await apiCall('/rest/v1/user_sessions?select=user_id,ip_address,user_agent&limit=20', 'GET', null, TOKEN);
  others = Array.isArray(r.d) ? r.d.filter(s => s.user_id !== USER_ID) : [];
  console.log(`  Retornou: ${Array.isArray(r.d) ? r.d.length : 0} | Outros: ${others.length}`);
  if (others.length > 0) { console.log('  🔴 VAZAMENTO: IPs de outros!'); tests.push('FAIL'); }
  else { console.log('  ✅ Bloqueado'); tests.push('PASS'); }

  // TEST 5: Modify own subscription
  console.log('\n--- 5. Alterar própria assinatura (upgrade gratuito) ---');
  r = await apiCall(`/rest/v1/user_subscriptions?user_id=eq.${USER_ID}`, 'PATCH',
    { plan: 'unlimited', daily_limit: 999999 }, TOKEN);
  console.log(`  Status: ${r.s}`);
  if (r.s < 300 && Array.isArray(r.d) && r.d.length > 0) {
    console.log('  🔴 CRÍTICO: Conseguiu alterar própria assinatura!');
    tests.push('FAIL');
  } else { console.log('  ✅ Bloqueado'); tests.push('PASS'); }

  // TEST 6: Insert admin role
  console.log('\n--- 6. Se tornar admin ---');
  r = await apiCall('/rest/v1/user_roles', 'POST', { user_id: USER_ID, role: 'admin' }, TOKEN);
  console.log(`  Status: ${r.s}`);
  if (r.s < 300) { console.log('  🔴 CRÍTICO: Conseguiu virar admin!'); tests.push('FAIL'); }
  else { console.log('  ✅ Bloqueado'); tests.push('PASS'); }

  // TEST 7: Read provider configs
  console.log('\n--- 7. Ler chaves de API dos providers ---');
  r = await apiCall('/rest/v1/provider_configs?select=*', 'GET', null, TOKEN);
  console.log(`  Status: ${r.s} | Rows: ${Array.isArray(r.d) ? r.d.length : 'N/A'}`);
  if (Array.isArray(r.d) && r.d.length > 0) {
    console.log('  🔴 CRÍTICO: Chaves de API expostas!');
    tests.push('FAIL');
  } else { console.log('  ✅ Bloqueado'); tests.push('PASS'); }

  // TEST 8: Delete other users
  console.log('\n--- 8. Deletar dados de outros ---');
  r = await apiCall(`/rest/v1/profiles?id=neq.${USER_ID}`, 'DELETE', null, TOKEN);
  console.log(`  Status: ${r.s}`);
  if (r.s < 300 && r.d !== '[]' && r.d !== '') {
    console.log('  🔴 CRÍTICO: Deletou perfis de outros!');
    tests.push('FAIL');
  } else { console.log('  ✅ Bloqueado'); tests.push('PASS'); }

  // TEST 9: Insert news
  console.log('\n--- 9. Criar anúncios falsos ---');
  r = await apiCall('/rest/v1/news', 'POST',
    { title: 'HACKED', content: 'Security test' }, TOKEN);
  console.log(`  Status: ${r.s}`);
  if (r.s < 300) { console.log('  🔴 Conseguiu inserir news!'); tests.push('FAIL'); }
  else { console.log('  ✅ Bloqueado'); tests.push('PASS'); }

  // TEST 10: Bypass usage limits
  console.log('\n--- 10. Zerar contadores de uso ---');
  r = await apiCall(`/rest/v1/chatgpt_usage?user_id=eq.${USER_ID}`, 'PATCH',
    { message_count: 0 }, TOKEN);
  console.log(`  Status: ${r.s}`);
  if (r.s < 300 && Array.isArray(r.d) && r.d.length > 0) {
    console.log('  🔴 Conseguiu zerar uso!');
    tests.push('FAIL');
  } else { console.log('  ✅ Bloqueado'); tests.push('PASS'); }

  // TEST 11: Access Edge Functions without proper auth
  console.log('\n--- 11. Acessar Edge Functions ---');
  const funcs = ['chatgpt-proxy', 'session-validate', 'grok-proxy', 'claude-proxy'];
  for (const fn of funcs) {
    r = await apiCall(`/functions/v1/${fn}`, 'POST', { message: 'test' }, TOKEN);
    console.log(`  ${fn}: ${r.s} - ${JSON.stringify(r.d).substring(0, 80)}`);
  }
  tests.push('INFO');

  // TEST 12: Read user_roles to find admins
  console.log('\n--- 12. Descobrir quem são os admins ---');
  r = await apiCall('/rest/v1/user_roles?select=*&limit=20', 'GET', null, TOKEN);
  console.log(`  Status: ${r.s} | Rows: ${Array.isArray(r.d) ? r.d.length : 'N/A'}`);
  if (Array.isArray(r.d) && r.d.length > 1) {
    console.log('  ⚠️ Pode ver roles de outros');
    tests.push('WARN');
  } else { console.log('  ✅ Bloqueado'); tests.push('PASS'); }

  // SUMMARY
  const fails = tests.filter(t => t === 'FAIL').length;
  const warns = tests.filter(t => t === 'WARN').length;
  const passes = tests.filter(t => t === 'PASS').length;

  console.log('\n' + '='.repeat(50));
  console.log('  RESULTADO FINAL');
  console.log('='.repeat(50));
  console.log(`  ✅ Bloqueado: ${passes}`);
  console.log(`  ⚠️ Aviso: ${warns}`);
  console.log(`  🔴 Vulnerável: ${fails}`);
  if (fails === 0) console.log('\n  👍 Sistema SEGURO contra usuário comum');
  else console.log('\n  ⚠️ ATENÇÃO: Vulnerabilidades encontradas!');

  process.exit(0);
}

main().catch(console.error);
