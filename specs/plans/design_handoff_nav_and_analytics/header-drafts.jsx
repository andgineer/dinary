// Header drafts — living without the ••• overflow.
// Goal: ALL nav tabs visible (Add · Review · Analytics · Income · LLM = 5),
// while keeping the queue NOTIFICATION (between brand/version and the nav)
// and the brand+version. One inline row is too tight on mobile, so each
// draft solves the space squeeze differently.
//
// Mirrors dinary base.css tokens. Each draft shown at 390px; the tight ones
// also shown at 340px to prove they fit a small phone.

const HT = {
  bg: '#1a1a2e',
  surface: '#16213e',
  surface2: '#0f3460',
  accent: '#e94560',
  expense: '#f97316',
  income: '#22c55e',
  review: '#60a5fa',
  stat: '#818cf8',
  text: '#eeeeee',
  muted: '#94a3b8',
  muted2: '#64748b',
  warning: '#f59e0b',
  field: 'rgba(255,255,255,0.04)',
  fieldDeep: 'rgba(0,0,0,0.18)',
  border: 'rgba(255,255,255,0.08)',
  borderStrong: 'rgba(255,255,255,0.12)',
  fontNum: '"JetBrains Mono", ui-monospace, SFMono-Regular, monospace',
  font: 'system-ui, -apple-system, sans-serif',
};

const I = {
  plus: (s=22) => <svg width={s} height={s} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round"><path d="M12 5v14M5 12h14"/></svg>,
  list: (s=20) => <svg width={s} height={s} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polyline points="3.5 7 5 8.5 8 5.5"/><polyline points="3.5 13 5 14.5 8 11.5"/><polyline points="3.5 19 5 20.5 8 17.5"/><line x1="11" y1="6" x2="21" y2="6"/><line x1="11" y1="12" x2="21" y2="12"/><line x1="11" y1="18" x2="21" y2="18"/></svg>,
  chart: (s=20) => <svg width={s} height={s} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><line x1="4" y1="20" x2="4" y2="11"/><line x1="10" y1="20" x2="10" y2="4"/><line x1="16" y1="20" x2="16" y2="14"/></svg>,
  trendUp: (s=20) => <svg width={s} height={s} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round"><polyline points="22 7 13.5 15.5 8.5 10.5 2 17"/><polyline points="16 7 22 7 22 13"/></svg>,
  cpu: (s=18) => <svg width={s} height={s} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><rect x="5" y="5" width="14" height="14" rx="2"/><rect x="9" y="9" width="6" height="6"/><path d="M9 2v2M15 2v2M9 20v2M15 20v2M2 9h2M2 15h2M20 9h2M20 15h2"/></svg>,
  bell: (s=18) => <svg width={s} height={s} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.73 21a2 2 0 0 1-3.46 0"/></svg>,
  clock: (s=13) => <svg width={s} height={s} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="9"/><polyline points="12 7 12 12 15 14"/></svg>,
};

// The 5 tabs. `key` drives which is active per draft (default Add).
const TABS = [
  { key: 'add',       icon: I.plus,    color: HT.expense, label: 'Add' },
  { key: 'review',    icon: I.list,    color: HT.review,  label: 'Review' },
  { key: 'analytics', icon: I.chart,   color: HT.stat,    label: 'Stats' },
  { key: 'income',    icon: I.trendUp, color: HT.income,  label: 'Income' },
  { key: 'llm',       icon: I.cpu,     color: HT.muted,   label: 'LLM' },
];

// ─── Pieces ─────────────────────────────────────────────────────────
function Brand({ version=true, small=false }) {
  return (
    <h1 style={{ fontSize: small ? '1.05rem' : '1.25rem', fontWeight: 600,
      color: HT.text, margin: 0, whiteSpace: 'nowrap', flexShrink: 0 }}>
      Dinary{version && <span style={{ fontSize: '0.7rem', fontWeight: 400,
        color: HT.muted, marginLeft: 6 }}>v0.11</span>}
    </h1>
  );
}

// Queue notification — the thing that must stay visible.
// `dot` = bare presence dot (narrow phones), `compact` = count chip, else full pill.
function QueueBadge({ compact=false, dot=false }) {
  if (dot) {
    return (
      <span style={{ width: 10, height: 10, borderRadius: 999, background: HT.warning,
        boxShadow: `0 0 0 3px rgba(245,158,11,0.22)`, flexShrink: 0 }}/>
    );
  }
  if (compact) {
    return (
      <span style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
        minWidth: 20, height: 20, padding: '0 6px', borderRadius: 999,
        background: HT.warning, color: '#000', fontFamily: HT.fontNum,
        fontSize: '0.7rem', fontWeight: 700, flexShrink: 0 }}>2</span>
    );
  }
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5,
      padding: '3px 9px 3px 7px', borderRadius: 999,
      background: 'rgba(245,158,11,0.15)', border: `1px solid rgba(245,158,11,0.35)`,
      color: HT.warning, fontSize: '0.72rem', fontWeight: 600,
      whiteSpace: 'nowrap', flexShrink: 0 }}>
      {I.clock(12)}<span style={{ fontFamily: HT.fontNum }}>2</span> queued
    </span>
  );
}

// A single segment button.
function Tab({ tab, active, size='md' }) {
  const dims = {
    md:   { w: 54, h: 38, icon: 1 },
    sm:   { w: 40, h: 36, icon: 0.85 },
    wide: { w: undefined, h: 40, icon: 1 },   // flex:1 in a full-width bar
  }[size];
  const tint = `color-mix(in srgb, ${tab.color} 14%, transparent)`;
  return (
    <button style={{
      width: dims.w, height: dims.h, flex: dims.w ? '0 0 auto' : '1 1 0',
      border: 'none', borderRadius: 8, cursor: 'pointer', padding: 0,
      display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
      background: active ? tab.color : tint,
      color: active ? '#fff' : tab.color,
      boxShadow: active ? `0 4px 12px ${tab.color}66` : 'none',
    }}>{tab.icon(20)}</button>
  );
}

function SegBar({ active='add', size='md', style={} }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: size === 'sm' ? 1 : 2,
      background: HT.fieldDeep, border: `1px solid ${HT.border}`,
      borderRadius: 11, padding: 3, ...style }}>
      {TABS.map(t => <Tab key={t.key} tab={t} active={t.key === active} size={size}/>)}
    </div>
  );
}

// Content slice below a header so drafts read as real screens.
function ContentSlice({ label='ADD EXPENSE' }) {
  return (
    <div style={{ padding: '1rem 1.25rem', flex: 1 }}>
      <span style={{ fontSize: '0.6875rem', fontWeight: 700, letterSpacing: '0.07em',
        textTransform: 'uppercase', color: HT.muted }}>{label}</span>
      <div style={{ marginTop: 10, height: 56, borderRadius: 10,
        border: `1px solid ${HT.border}`, background: HT.field }}/>
      <div style={{ marginTop: 8, height: 80, borderRadius: 10,
        border: `1px solid ${HT.border}`, background: HT.field }}/>
      <div style={{ marginTop: 8, height: 44, borderRadius: 10,
        border: `1px solid ${HT.border}`, background: HT.field }}/>
    </div>
  );
}

// Phone shell — grows to content.
function Phone({ children }) {
  return (
    <div style={{ width: '100%', minHeight: '100%', background: HT.bg, color: HT.text,
      fontFamily: HT.font, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
      {children}
    </div>
  );
}

Object.assign(window, {
  HT, I, TABS, Brand, QueueBadge, Tab, SegBar, ContentSlice, Phone,
});
