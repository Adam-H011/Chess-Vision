const api = globalThis.browser || globalThis.chrome;
const $ = id => document.getElementById(id);
async function saved() { const x = await api.storage.local.get(['url','token']); $('url').value=x.url||''; $('token').value=x.token||''; }
saved();
$('import').onclick = async () => {
  const url=$('url').value.trim(), token=$('token').value.trim();
  if (!/^http:\/\/127\.0\.0\.1:\d+$/.test(url) || token.length < 20) { $('status').textContent='Enter the exact local address and session token shown by ChessVision.'; return; }
  await api.storage.local.set({url,token});
  const [tab] = await api.tabs.query({active:true,currentWindow:true});
  const reply = await api.tabs.sendMessage(tab.id,{type:'extract'}).catch(e=>({error:e.message}));
  if (!reply || reply.error) { $('status').textContent='Could not extract: '+(reply&&reply.error||'unsupported page'); return; }
  try { const r=await fetch(url,{method:'POST',headers:{Authorization:'Bearer '+token,'Content-Type':'application/json'},body:JSON.stringify(reply)}); $('status').textContent=r.ok?'Imported into ChessVision.':`ChessVision rejected import (${r.status}): ${await r.text()}`; }
  catch(e) { $('status').textContent='Cannot contact ChessVision. Start Browser import and re-pair: '+e.message; }
};
