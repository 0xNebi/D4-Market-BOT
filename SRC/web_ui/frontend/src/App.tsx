import React, { useEffect, useState } from 'react';
import axios from 'axios';
import { Activity, Package, MessageSquare, Clock, Check, Server, BrainCircuit, RefreshCw, Power, Cpu } from 'lucide-react';
import { AreaChart, Area, ResponsiveContainer, XAxis, Tooltip, YAxis, BarChart, Bar } from 'recharts';

const formatNum = (n: number) => n?.toLocaleString() || '0';
  function formatGold(price: number) {
    if (!price) return '0';
    if (price >= 1_000_000_000) {
      const b = price / 1_000_000_000;
      return (b === Math.floor(b) ? b : b.toFixed(1)) + 'B';
    }
    if (price >= 1_000_000) {
      const m = price / 1_000_000;
      return (m === Math.floor(m) ? m : m.toFixed(1)) + 'M';
    }
    if (price >= 1000 && price < 1_000_000) {
      const k = price / 1000;
      return (k === Math.floor(k) ? k : k.toFixed(1)) + 'K';
    }
    if (price < 1000) {
      return price + 'M';
    }
    return price.toLocaleString();
  }
function formatTime(unix: number) {
  if (!unix) return 'N/A';
    return new Date(unix * 1000).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });
}

export default function App() {
  const [statusData, setStatusData] = useState<any>(null);
  const [inventoryList, setInventoryList] = useState<any[]>([]);
  const [activities, setActivities] = useState<any[]>([]);
  const [isConnected, setIsConnected] = useState<boolean>(false);
  const [activeTab, setActiveTab] = useState<string>("MAIN");

  const fetchData = async () => {
    try {
      const acctParam = activeTab !== "MAIN" ? `?account_id=${activeTab}` : '';
      const [statusRes, invRes, actRes] = await Promise.all([
        axios.get(`/api/status${acctParam}`),
        axios.get(`/api/inventory${acctParam}`),
        axios.get(`/api/activity${acctParam}`)
      ]);
      const sd = statusRes.data;
      setStatusData(sd);
      setInventoryList(invRes.data.items || []);
      setActivities(actRes.data.actions || []);
      setIsConnected(true);

    } catch (err) {
      console.error(err);
      setIsConnected(false);
    }
  };

  useEffect(() => {
    fetchData();
    const interval = setInterval(fetchData, 2000);
    return () => clearInterval(interval);
  }, [activeTab]);

  const handleAction = async (action: 'sold' | 'release', uuid: string) => {
    try {
      await axios.post(`/api/${action}/${uuid}`);
      fetchData();
    } catch (err) {
      alert(`Failed to perform action ${action} for ${uuid}.`);
    }
  };

    let totalValue = 0;
    let totalItems = 0;
    if(inventoryList) {
       inventoryList.forEach((item: any) => {
         totalItems += (item.quantity || 1);
         totalValue += (item.quantity || 1) * (item.price || 0);
       });
    }

    const timelineRaw = statusData?.stats?.ai_timeline || [];


  const cumulativeTimeline = React.useMemo(() => {
    const raw = statusData?.stats?.ai_timeline || [];
    if (!raw.length) return [];
    const sorted = [...raw].sort((a,b) => a.ts - b.ts);
    let currentTokens = 0;
    const series: any[] = [];
    sorted.forEach((row: any) => {
       currentTokens += row.tokens;
       series.push({
         time: new Date(row.ts * 1000).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', hour12: false }),
         tokens: currentTokens
       });
    });
    return series;
  }, [statusData]);

  const binnedTimeline = React.useMemo(() => {
    const bins: Record<string, number> = {};
    timelineRaw.forEach((row: any) => {
      const bucket = Math.floor(row.ts / 300) * 300;
      if (!bins[bucket]) bins[bucket] = 0;
      bins[bucket] += row.tokens || 0;
    });
    const results = Object.keys(bins).sort().map(tsStr => {
      const ts = parseInt(tsStr, 10);
        const label = new Date(ts * 1000).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', hour12: false });
      return { time: label, tokens: bins[tsStr] };
    });
    return results;
  }, [timelineRaw]);

  if (!statusData) {
    return (
      <div className="flex h-screen w-screen items-center justify-center bg-[#0a0a0a] text-[#e0e0e0]">
        <RefreshCw className="w-6 h-6 animate-spin text-gray-500" />
        <span className="ml-4 text-sm font-medium tracking-wide text-gray-400">CONNECTING TO CONTROL SERVER</span>
      </div>
    );
  }

  const { stats, conversations, accounts } = statusData;
  const holds = statusData.holds || [];
  const soldItems = statusData.sold || [];
  const currentTokens = stats?.total_tokens || stats?.ai_used * 150 || 0;

  const accountTabs = (accounts || []).map((acc: any) => ({
    id: acc.id,
    label: acc.username?.toUpperCase() || acc.player_tag?.toUpperCase() || "ACCOUNT"
  }));
  const availableTabs = [{ id: "MAIN", label: "MAIN OVERVIEW" }, ...accountTabs];

  const isMainTab = activeTab === "MAIN";
  const activeAccount = accountTabs.find((t: any) => t.id === activeTab);

  return (
    <div className="min-h-screen bg-[#0a0a0a] text-[#d4d4d4] pb-12 font-sans selection:bg-gray-800 flex flex-col">
      <header className="sticky top-0 z-50 bg-[#0a0a0a]/95 border-b border-[#222] backdrop-blur-xl">
        <div className="max-w-[1400px] mx-auto px-8 py-5 flex items-center justify-between">
          <div className="flex items-center gap-4">
            <h1 className="text-xl font-bold tracking-widest text-[#ececec] flex items-center gap-3">
              <img src="/icon.svg" alt="Logo" className="w-12 h-12 object-contain invert" onError={(e) => { e.currentTarget.style.display='none' }} />
              <div className="flex items-center">D4<span className="text-gray-500">MARKET-BOT</span></div>
            </h1>
            <div className={`w-2 h-2 rounded-full ${isConnected ? 'bg-emerald-500 shadow-[0_0_8px_#10b981]' : 'bg-rose-500'}`} />
          </div>
          <div className="flex items-center gap-4 text-xs font-medium tracking-wide text-gray-400">
            <span className="flex items-center gap-1.5"><Power className="w-3.5 h-3.5" /> {isConnected ? 'SYSTEM ONLINE' : 'NODE OFFLINE'}</span>
          </div>
        </div>

        <div className="max-w-[1400px] mx-auto px-8 flex gap-6 overflow-x-auto no-scrollbar">
          {availableTabs.map(tab => (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              className={`pb-3 text-[11px] font-bold tracking-widest uppercase transition-all whitespace-nowrap border-b-2 ${
                activeTab === tab.id
                ? "border-emerald-500 text-emerald-400 drop-shadow-[0_0_8px_rgba(16,185,129,0.5)]"
                : "border-transparent text-gray-500 hover:text-gray-300 hover:border-gray-600"
              }`}
            >
              {tab.label}
            </button>
          ))}
        </div>
      </header>

      <main className="flex-1 max-w-[1400px] w-full mx-auto px-8 py-8 space-y-8 flex flex-col">
        {isMainTab ? (
          <div className="space-y-8 fade-in flex-1">
            <div className="flex items-center justify-between">
              <h2 className="text-lg font-light text-[#ececec] tracking-widest flex items-center gap-2">
                <Server className="w-5 h-5 text-gray-500" />
                GLOBAL TELEMETRY
              </h2>
            </div>
<div className="grid grid-cols-2 md:grid-cols-4 xl:grid-cols-8 gap-4">
                <StatCard title="Sessions" value={statusData?.accounts?.length || 0} icon={<Server className="w-5 h-5 text-emerald-400 opacity-80" />} />
                <StatCard title="Total Value" value={formatGold(totalValue)} icon={<Package className="w-5 h-5 text-emerald-400 opacity-80" />} />
                <StatCard title="Items" value={formatNum(totalItems)} icon={<Package className="w-5 h-5 text-gray-400" />} />
                <StatCard title="Holds" value={stats?.items_on_hold || 0} icon={<Activity className="w-5 h-5 text-gray-400" />} />
                <StatCard title="Unread" value={statusData?.poll_stats?.unread || 0} icon={<MessageSquare className="w-5 h-5 text-emerald-400 opacity-80" />} />
                <StatCard title="AI Replies" value={stats?.replied || 0} icon={<Check className="w-5 h-5 text-emerald-400 opacity-80" />} />
                <StatCard title="Pending" value={stats?.pending || 0} icon={<Clock className="w-5 h-5 text-gray-400" />} />
                <StatCard title="Tokens" value={formatNum(currentTokens)} icon={<Cpu className="w-5 h-5 text-emerald-400 opacity-80" />} />
            </div>

            <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
              <Card title="ACTIVE AI TOKENS / 5 MIN">
                <div className="h-[250px] p-4">
                  <ResponsiveContainer width="100%" height="100%">
                    <BarChart data={binnedTimeline}>
                      <XAxis dataKey="time" stroke="#444" fontSize={10} tickMargin={10} minTickGap={20} />
                      <YAxis stroke="#444" fontSize={10} tickFormatter={formatNum} />
                      <Tooltip contentStyle={{ backgroundColor: '#111', borderColor: '#333', fontSize: '11px', color: '#ececec' }} />
                      <Bar dataKey="tokens" fill="#10b981" radius={[2, 2, 0, 0]} />
                    </BarChart>
                  </ResponsiveContainer>
                </div>
              </Card>

              <Card title="24H CUMULATIVE AI TOKENS">
                  <div className="h-[250px] p-4">
                    <ResponsiveContainer width="100%" height="100%">
                      <AreaChart data={cumulativeTimeline}>
                      <defs>
                        <linearGradient id="colorTokens" x1="0" y1="0" x2="0" y2="1">
                          <stop offset="5%" stopColor="#10b981" stopOpacity={0.3}/>
                          <stop offset="95%" stopColor="#10b981" stopOpacity={0}/>
                        </linearGradient>
                      </defs>
                      <XAxis dataKey="time" stroke="#444" fontSize={10} tickMargin={10} minTickGap={20} />
                      <YAxis stroke="#444" fontSize={10} tickFormatter={formatNum} />
                      <Tooltip contentStyle={{ backgroundColor: '#111', borderColor: '#333', fontSize: '11px', color: '#ececec' }} />
                      <Area type="monotone" dataKey="tokens" stroke="#10b981" fillOpacity={1} fill="url(#colorTokens)" strokeWidth={2} isAnimationActive={false} />
                    </AreaChart>
                  </ResponsiveContainer>
                </div>
              </Card>
            </div>

            <Card title="ACTIVE SESSIONS PREVIEW">
               <div className="p-4 grid gap-4 grid-cols-1 md:grid-cols-2 lg:grid-cols-3">
                 {accounts && accounts.map((acc: any) => (
                    <div key={acc.id} className="border border-[#222] rounded-md p-4 bg-[#0d0d0d] flex items-center justify-between">
                       <div>
                         <div className="text-xs text-gray-500 uppercase tracking-widest font-bold">Session / Tag</div>
                         <div className="text-sm text-[#ececec] font-medium mt-1">{acc.player_tag || "Unknown"}</div>
                       </div>
                       <button onClick={() => setActiveTab(acc.id)} className="px-3 py-1.5 bg-[#111] hover:bg-[#222] text-xs uppercase tracking-widest text-emerald-500 rounded transition-colors whitespace-nowrap">
                         VIEW
                       </button>
                    </div>
                 ))}
                 {(!accounts || accounts.length === 0) && <EmptyState message="NO SESSIONS CONFIGURED" />}
               </div>
            </Card>

            <Card title="RECENT CONVERSATIONS — ALL SESSIONS" badgeCount={conversations?.length || 0}>
              <div className="max-h-[500px] overflow-y-auto no-scrollbar">
                {conversations?.length > 0 ? (
                  <div className="divide-y divide-[#1a1a1a]">
                    {conversations.map((c: any, i: number) => {
                      const ownerAcct = (accounts || []).find((a: any) => a.id === c.account_id);
                      const sessionLabel = ownerAcct
                        ? (ownerAcct.username || ownerAcct.player_tag || 'UNKNOWN').toUpperCase()
                        : (c.account_id ? c.account_id.slice(0, 8).toUpperCase() : 'LEGACY');
                      return (
                        <div key={i} className="p-5 hover:bg-[#111] transition-colors">
                          <div className="flex items-center justify-between mb-3 border-b border-[#222] pb-3">
                            <span className="text-sm font-bold text-emerald-400 flex items-center gap-2">
                              {c.player_name}
                              {c.ai_used === 1 && <span className="text-[9px] uppercase tracking-widest bg-emerald-500/10 text-emerald-400 px-1.5 py-0.5 rounded flex items-center gap-1"><BrainCircuit className="w-3 h-3"/> AI</span>}
                            </span>
                            <div className="flex gap-3 items-center text-[10px] font-mono text-gray-500">
                              <span className="text-[9px] uppercase tracking-widest bg-indigo-500/10 text-indigo-400 px-1.5 py-0.5 rounded font-bold">{sessionLabel}</span>
                              <span className={c.status === 'replied' ? 'text-emerald-400' : 'text-yellow-400'}>
                                [{c.status?.toUpperCase()}]
                              </span>
                              <span>{formatTime(c.first_seen_at)}</span>
                            </div>
                          </div>
                          {c.item_name && (
                            <div className="mb-4 flex items-center justify-between text-xs font-medium text-indigo-400 bg-indigo-500/10 px-3 py-2 rounded-md border border-indigo-500/20">
                              <div className="flex items-center gap-2">
                                <Package className="w-3.5 h-3.5 opacity-70" />
                                <span>{c.item_name}</span>
                              </div>
                              <span className="font-mono bg-indigo-500/20 px-2 py-0.5 rounded text-indigo-300">{formatGold(c.listed_price)} gold</span>
                            </div>
                          )}
                          <div className="space-y-3">
                            <div>
                              <div className="text-[10px] uppercase tracking-widest text-gray-500 mb-1 font-bold">Buyer Message</div>
                              <div className="text-[#e0e0e0] text-sm bg-[#0a0a0a] p-3 rounded border border-[#222]">{c.raw_message || '-'}</div>
                            </div>
                            {c.reply_text && (
                              <div>
                                <div className="text-[10px] uppercase tracking-widest text-gray-500 mb-1 font-bold">Bot Reply</div>
                                <div className="text-emerald-400/90 text-sm bg-emerald-500/5 p-3 rounded border border-emerald-500/10 italic">"{c.reply_text}"</div>
                              </div>
                            )}
                          </div>
                        </div>
                      );
                    })}
                  </div>
                ) : <EmptyState message="No conversations recorded yet" />}
              </div>
            </Card>

          </div>
        ) : (
          <div className="space-y-8 fade-in flex-1">
             <div className="flex items-center justify-between">
              <h2 className="text-lg font-light text-[#ececec] tracking-widest flex items-center gap-2 uppercase">
                <Activity className="w-5 h-5 text-emerald-500" />
                {activeAccount?.label} WORKSPACE
              </h2>
            </div>

            <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
              <div className="space-y-6 lg:col-span-1 flex flex-col">
                <Card title="Active Holds" badgeCount={holds?.length || 0}>
                  <div className="max-h-[300px] overflow-y-auto no-scrollbar">
                    {holds?.length > 0 ? (
                      <div className="divide-y divide-[#1a1a1a]">
                        {holds.map((h: any, i: number) => (
                          <div key={i} className="p-4 hover:bg-[#111] transition-colors">
                            <div className="flex items-center justify-between mb-2">
                              <span className="text-sm font-medium text-emerald-400">{h.player_name}</span>
                              <span className="text-[10px] text-gray-500 font-mono">{formatTime(h.held_at)}</span>
                            </div>
                            <div className="text-[11px] text-gray-400 font-mono truncate mb-4">{h.item_uuid}</div>
                            <div className="flex gap-2">
                              <button onClick={() => handleAction('sold', h.item_uuid)} className="flex-1 bg-emerald-500/10 hover:bg-emerald-500/20 text-emerald-500 text-[10px] font-bold uppercase tracking-widest py-2 rounded transition-colors border border-emerald-500/20">Mark Sold</button>
                              <button onClick={() => handleAction('release', h.item_uuid)} className="flex-1 bg-zinc-800 hover:bg-zinc-700 text-zinc-400 text-[10px] font-bold uppercase tracking-widest py-2 rounded transition-colors border border-zinc-700">Release</button>
                            </div>
                          </div>
                        ))}
                      </div>
                    ) : <EmptyState message="No item holds active" />}
                  </div>
                </Card>

                <Card title="Sold Items" badgeCount={soldItems?.length || 0}>
                  <div className="max-h-[250px] overflow-y-auto no-scrollbar">
                    {soldItems?.length > 0 ? (
                      <div className="divide-y divide-[#1a1a1a]">
                        {soldItems.map((s: any, i: number) => (
                          <div key={i} className="p-4 hover:bg-[#111] transition-colors">
                            <div className="flex items-center justify-between mb-1">
                              <span className="text-sm font-medium text-emerald-400">{s.player_name}</span>
                              <span className="text-[9px] uppercase tracking-widest bg-emerald-500/10 text-emerald-400 px-2 py-0.5 rounded font-bold">sold</span>
                            </div>
                            <div className="text-[11px] text-gray-400 font-mono truncate">{s.item_uuid}</div>
                            <div className="text-[10px] text-gray-500 font-mono mt-1">{formatTime(s.held_at)}</div>
                          </div>
                        ))}
                      </div>
                    ) : <EmptyState message="No sold items yet" />}
                  </div>
                </Card>

                <Card title="System Activity">
                  <div className="max-h-[400px] overflow-y-auto no-scrollbar">
                    {activities?.length > 0 ? (
                      <div className="divide-y divide-[#1a1a1a]">
                        {activities.map((a: any, i: number) => (
                          <div key={i} className="px-4 py-3 flex gap-4 hover:bg-[#111] transition-colors">
                            <div className="text-[10px] text-gray-500 font-mono mt-0.5 whitespace-nowrap">{formatTime(a.ts)}</div>
                            <div>
                              <div className="text-xs font-medium text-[#ececec] mb-1">
                                {a.action === 'POLL' && <Activity className="inline w-3 h-3 mr-1 text-blue-400" />}
                                {a.action === 'REPLY' && <MessageSquare className="inline w-3 h-3 mr-1 text-emerald-400" />}
                                {a.action === 'AI_METRICS' && <BrainCircuit className="inline w-3 h-3 mr-1 text-yellow-400" />}
                                {a.action}
                              </div>
                              <div className="text-[11px] text-gray-400 leading-relaxed max-w-[200px] break-words">
                                {a.detail || "-"}
                              </div>
                            </div>
                          </div>
                        ))}
                      </div>
                    ) : <EmptyState message="No recent activity logs" />}
                  </div>
                </Card>
              </div>

              <div className="space-y-6 lg:col-span-2 flex flex-col">
                <Card title="Tracked Inventory" badgeCount={inventoryList?.length || 0}>
                  <div className="max-h-[300px] overflow-y-auto no-scrollbar">
                    {inventoryList?.length > 0 ? (
                      <div className="divide-y divide-[#1a1a1a]">
                         {inventoryList.map((item: any, i: number) => (
                          <div key={i} className="px-5 py-3 flex items-center justify-between hover:bg-[#111] transition-colors group">
                             <div className="flex items-center gap-3">
                               <Package className="w-4 h-4 text-emerald-500 shrink-0" />
                               <div>
                                 <div className="flex items-center gap-1.5 flex-wrap">
                                   <span className="text-sm text-[#e0e0e0] font-medium">{item.name}</span>
                                   {item.is_ancestral && (
                                     <span className="text-[9px] uppercase tracking-widest font-bold text-lime-300 bg-lime-400/10 border border-lime-400/20 px-1.5 py-0.5 rounded-sm">Ancestral</span>
                                   )}
                                   {item.greater_affix_count > 0 && (
                                     <span className="text-[9px] uppercase tracking-widest font-bold text-emerald-300 bg-emerald-400/10 border border-emerald-400/20 px-1.5 py-0.5 rounded-sm">{item.greater_affix_count}GA</span>
                                   )}
                                   {item.material_type && (
                                     <span className="text-[9px] uppercase tracking-widest font-bold text-green-300 bg-green-400/10 border border-green-400/20 px-1.5 py-0.5 rounded-sm">{item.material_type}</span>
                                   )}
                                 </div>
                                 <div className="text-[10px] text-gray-500 font-mono mt-0.5">{item.id}</div>
                               </div>
                             </div>
                               <div className="flex items-center justify-end gap-x-4">
                                 <div className="w-16 flex justify-end">
                                   <span className="text-[11px] uppercase font-bold tracking-widest text-[#10b981] bg-[#10b981]/15 px-2.5 py-1 rounded-sm w-full text-center">x{item.quantity}</span>
                                 </div>
                                 <div className="w-24 flex justify-end items-center gap-1">
                                     <span className="text-sm font-mono text-[#ececec]">{formatGold(item.price)}</span>
                                   <span className="text-[10px] text-gray-500 uppercase tracking-widest leading-none mt-0.5">gold</span>
                                 </div>
                             </div>
                          </div>
                         ))}
                      </div>
                    ) : <EmptyState message="No inventory items detected" />}
                  </div>
                </Card>

                <Card title="Recent Conversations" badgeCount={conversations?.length || 0}>
                  <div className="max-h-[400px] overflow-y-auto no-scrollbar">
                    {conversations?.length > 0 ? (
                      <div className="divide-y divide-[#1a1a1a]">
                        {conversations.map((c: any, i: number) => (
                          <div key={i} className="p-5 hover:bg-[#111] transition-colors">
                            <div className="flex items-center justify-between mb-3 border-b border-[#222] pb-3">
                              <span className="text-sm font-bold text-emerald-400 flex items-center gap-2">
                                {c.player_name}
                                {c.ai_used === 1 && <span className="text-[9px] uppercase tracking-widest bg-emerald-500/10 text-emerald-400 px-1.5 py-0.5 rounded flex items-center gap-1"><BrainCircuit className="w-3 h-3"/> AI</span>}
                              </span>
                              <div className="flex gap-3 text-[10px] font-mono text-gray-500">
                                <span className={c.status === 'replied' ? 'text-emerald-400' : 'text-yellow-400'}>
                                  [{c.status?.toUpperCase()}]
                                </span>
                                <span>{formatTime(c.first_seen_at)}</span>
                              </div>
                            </div>                              {c.item_name && (
                                <div className="mb-4 flex items-center justify-between text-xs font-medium text-indigo-400 bg-indigo-500/10 px-3 py-2 rounded-md border border-indigo-500/20">
                                  <div className="flex items-center gap-2">
                                    <Package className="w-3.5 h-3.5 opacity-70" />
                                    <span>{c.item_name}</span>
                                  </div>
                                  <span className="font-mono bg-indigo-500/20 px-2 py-0.5 rounded text-indigo-300">{formatGold(c.listed_price)} gold</span>
                                </div>
                              )}                            <div className="space-y-4">
                              <div>
                                <div className="text-[10px] uppercase tracking-widest text-gray-500 mb-1 font-bold">Buyer Message</div>
                                <div className="text-[#e0e0e0] text-sm bg-[#0a0a0a] p-3 rounded border border-[#222]">{c.raw_message || '-'}</div>
                              </div>
                              {c.reply_text && (
                                <div>
                                  <div className="text-[10px] uppercase tracking-widest text-gray-500 mb-1 font-bold">Bot Reply</div>
                                  <div className="text-emerald-400/90 text-sm bg-emerald-500/5 p-3 rounded border border-emerald-500/10 italic">"{c.reply_text}"</div>
                                </div>
                              )}
                            </div>
                          </div>
                        ))}
                      </div>
                    ) : <EmptyState message="No active messages" />}
                  </div>
                </Card>
              </div>
            </div>
          </div>
        )}
      </main>

      <style>{`
        .no-scrollbar::-webkit-scrollbar { display: none; }
        .no-scrollbar { -ms-overflow-style: none; scrollbar-width: none; }
        .fade-in { animation: fadeIn 0.3s ease-out forwards; }
        @keyframes fadeIn {
          from { opacity: 0; transform: translateY(5px); }
          to { opacity: 1; transform: translateY(0); }
        }
      `}</style>
    </div>
  );
}

function StatCard({ title, value, icon }: { title: string, value: string | number, icon: React.ReactNode }) {
  return (
    <div className="bg-[#0f0f0f] border border-[#222] rounded-lg p-5 flex flex-col justify-between hover:border-gray-600 transition-colors">
      <div className="flex items-center justify-between mb-4">
        <div className="text-[10px] uppercase tracking-widest text-gray-500 font-semibold truncate pr-2">{title}</div>
        {icon}
      </div>
      <div className="text-3xl font-light text-[#ececec] font-mono tracking-tight">{value}</div>
    </div>
  );
}

function Card({ title, badgeCount, children }: { title: string, badgeCount?: number, children: React.ReactNode }) {
  return (
    <div className="bg-[#0f0f0f] border border-[#222] rounded-lg shadow-2xl overflow-hidden flex flex-col">
      <div className="px-5 py-4 border-b border-[#222] flex items-center justify-between">
        <h2 className="text-[11px] font-bold uppercase tracking-widest text-gray-400">{title}</h2>
        {badgeCount !== undefined && (
          <span className="bg-[#1a1a1a] text-gray-400 px-2 py-0.5 rounded text-[10px] font-mono">
            {badgeCount}
          </span>
        )}
      </div>
      <div className="flex-1 bg-[#0a0a0a]">
        {children}
      </div>
    </div>
  );
}

function EmptyState({ message }: { message: string }) {
  return (
    <div className="flex flex-col items-center justify-center py-10 text-center text-gray-600">
      <Activity className="w-6 h-6 mb-3 opacity-20" />
      <p className="text-[11px] uppercase tracking-widest">{message}</p>
    </div>
  );
}
