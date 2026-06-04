// Analytics screen — sketch exploration for dinary.
// Three layout approaches, all native to dinary's design language
// (dark surfaces, mono numbers, per-context primary color). NOT Vuetify —
// these mirror webapp/src/assets/base.css tokens so they're drop-in.
//
// The Analytics view proposes a NEW per-context primary: indigo #818cf8
// (a calm "insight" hue, distinct from Add-orange / Income-green /
// Review-blue / accent-red). Used sparingly — section accents + active tab.

// ─── Tokens (mirrors base.css) ──────────────────────────────────────
const AT = {
  bg: '#1a1a2e',
  surface: '#16213e',
  surface2: '#0f3460',
  accent: '#e94560',
  expense: '#f97316',
  income: '#22c55e',
  review: '#60a5fa',
  stat: '#818cf8',             // PROPOSED --stat (analytics primary, indigo)
  statDeep: '#6366f1',
  text: '#eeeeee',
  muted: '#94a3b8',
  muted2: '#64748b',
  success: '#22c55e',
  warning: '#f59e0b',
  danger: '#ef4444',
  up: '#f87171',               // trend up = spending more = bad-ish (red)
  down: '#34d399',             // trend down = spending less = good (green)
  field: 'rgba(255,255,255,0.04)',
  fieldDeep: 'rgba(0,0,0,0.18)',
  border: 'rgba(255,255,255,0.08)',
  borderStrong: 'rgba(255,255,255,0.12)',
  fontNum: '"JetBrains Mono", ui-monospace, SFMono-Regular, monospace',
  font: 'system-ui, -apple-system, sans-serif',
};

// ─── Icons ──────────────────────────────────────────────────────────
const AI = {
  plus: (s=22) => <svg width={s} height={s} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round"><path d="M12 5v14M5 12h14"/></svg>,
  list: (s=22) => <svg width={s} height={s} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polyline points="3.5 7 5 8.5 8 5.5"/><polyline points="3.5 13 5 14.5 8 11.5"/><polyline points="3.5 19 5 20.5 8 17.5"/><line x1="11" y1="6" x2="21" y2="6"/><line x1="11" y1="12" x2="21" y2="12"/><line x1="11" y1="18" x2="21" y2="18"/></svg>,
  chart: (s=22) => <svg width={s} height={s} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><line x1="4" y1="20" x2="4" y2="11"/><line x1="10" y1="20" x2="10" y2="4"/><line x1="16" y1="20" x2="16" y2="14"/><line x1="22" y1="20" x2="2" y2="20" opacity="0"/></svg>,
  dots: (s=16) => <svg width={s} height={s} viewBox="0 0 24 24" fill="currentColor"><circle cx="5" cy="12" r="1.7"/><circle cx="12" cy="12" r="1.7"/><circle cx="19" cy="12" r="1.7"/></svg>,
  arrowUp: (s=12) => <svg width={s} height={s} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round"><line x1="12" y1="19" x2="12" y2="6"/><polyline points="6 12 12 6 18 12"/></svg>,
  arrowDown: (s=12) => <svg width={s} height={s} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round"><line x1="12" y1="5" x2="12" y2="18"/><polyline points="6 12 12 18 18 12"/></svg>,
  plane: (s=14) => <svg width={s} height={s} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M17.8 19.2 16 11l3.5-3.5a2.1 2.1 0 0 0-3-3L13 8 4.8 6.2a.5.5 0 0 0-.5.8L9 11l-2 2-2.5-.5a.5.5 0 0 0-.4.9L7 16l1.6 2.9a.5.5 0 0 0 .9-.4L9 16l2-2 3.9 4.7a.5.5 0 0 0 .9-.5Z"/></svg>,
  cal: (s=13) => <svg width={s} height={s} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><rect x="3" y="4" width="18" height="18" rx="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/></svg>,
  refresh: (s=15) => <svg width={s} height={s} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/><path d="M3.51 9a9 9 0 0114.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0020.49 15"/></svg>,
  trendUp: (s=15) => <svg width={s} height={s} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round"><polyline points="22 7 13.5 15.5 8.5 10.5 2 17"/><polyline points="16 7 22 7 22 13"/></svg>,
  cpu: (s=15) => <svg width={s} height={s} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><rect x="5" y="5" width="14" height="14" rx="2"/><rect x="9" y="9" width="6" height="6"/><path d="M9 2v2M15 2v2M9 20v2M15 20v2M2 9h2M2 15h2M20 9h2M20 15h2"/></svg>,
};

// ─── Demo data ──────────────────────────────────────────────────────
const STATS = {
  thisMonth: '84 200',
  lastMonth: '102 400',
  ytdSpent: '489 000',
  ytdSavings: '156 000',
  savingsRate: '24%',
  monthDelta: -18,            // % vs last month
  cur: 'RSD',
};

const TRENDS = [
  { label: 'Food',         dir: 'up',   pct: '14%' },
  { label: 'Pocket money', dir: 'up',   pct: '8%'  },
  { label: 'Travel',       dir: 'down', pct: '30%' },
];

const EVENTS = [
  { name: 'Belgrade → Novi Sad',  range: '12–18 May 2026',  total: '42 800',  open: true  },
  { name: 'Poker nights',         range: 'since 2 May',     total: '18 400',  open: true  },
  { name: 'Montenegro holiday',   range: '2–9 Apr 2026',    total: '96 200',  open: false },
  { name: 'Nadia’s birthday',     range: '14 Mar 2026',     total: '23 500',  open: false },
  { name: 'Ski · Kopaonik',       range: '18–25 Jan 2026',  total: '78 000',  open: false },
];

// ─── Phone shell (dark, grows to content) ───────────────────────────
function Phone({ children, activeTab='chart' }) {
  return (
    <div style={{
      width: '100%', minHeight: '100%', background: AT.bg, color: AT.text,
      fontFamily: AT.font, display: 'flex', flexDirection: 'column',
      overflow: 'hidden',
    }}>
      <AHeader activeTab={activeTab}/>
      <div style={{ flex: 1, padding: '1rem 1.25rem 2rem',
        maxWidth: 480, width: '100%', margin: '0 auto', boxSizing: 'border-box' }}>
        {children}
      </div>
    </div>
  );
}

// Header with the segmented control — Analytics (chart) tab active.
function AHeader({ activeTab }) {
  const Seg = ({ w, h, bg='transparent', color, shadow, children }) => (
    <button style={{
      width: w, height: h, border: 'none', borderRadius: 8, cursor: 'pointer',
      display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
      background: bg, color, padding: 0, boxShadow: shadow || 'none', flexShrink: 0,
    }}>{children}</button>
  );
  return (
    <div style={{ background: AT.surface, borderBottom: `1px solid ${AT.surface2}`,
      position: 'sticky', top: 0, zIndex: 10 }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        gap: 8, padding: '1rem 1.25rem' }}>
        <h1 style={{ fontSize: '1.25rem', fontWeight: 600, color: AT.text, margin: 0 }}>
          Dinary <span style={{ fontSize: '0.7rem', fontWeight: 400, color: AT.muted, marginLeft: 6 }}>v0.11</span>
        </h1>
        <div style={{ position: 'relative' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 2,
            background: AT.fieldDeep, border: `1px solid ${AT.border}`, borderRadius: 11, padding: 3 }}>
            {/* Add + Review stay inline (frequent). */}
            <Seg w={56} h={38} bg="rgba(249,115,22,0.12)" color={AT.expense}>{AI.plus(22)}</Seg>
            <Seg w={56} h={38} bg="rgba(96,165,250,0.12)" color={AT.review}>{AI.list(22)}</Seg>
            {/* ••• overflow — active (accent fill) because a rare tab (Analytics) is current. */}
            <Seg w={36} h={30} bg={AT.accent} color="#fff" shadow="0 4px 12px rgba(233,69,96,0.4)">{AI.dots(16)}</Seg>
          </div>
          {/* Dropdown — Analytics highlighted. Shows the switch lives behind ••• */}
          <div style={{
            position: 'absolute', top: 'calc(100% + 8px)', right: 0, zIndex: 30,
            background: AT.surface, border: `1px solid ${AT.borderStrong}`,
            borderRadius: 10, minWidth: 200, padding: 4,
            boxShadow: '0 12px 28px rgba(0,0,0,0.45)',
          }}>
            <MenuRow icon={AI.trendUp(15)} color={AT.income} label="Income"/>
            <MenuRow icon={AI.cpu(15)} color={AT.muted} label="LLM providers"/>
            <MenuRow icon={AI.chart(15)} color={AT.stat} label="Analytics" active/>
          </div>
        </div>
      </div>
    </div>
  );
}

function MenuRow({ icon, color, label, active=false }) {
  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: 10,
      padding: '0.55rem 0.65rem', borderRadius: 7,
      color: AT.text, fontSize: '0.88rem', fontWeight: active ? 600 : 400,
      background: active ? AT.surface2 : 'transparent',
    }}>
      <span style={{ color, display: 'inline-flex', width: 18,
        justifyContent: 'center', flexShrink: 0 }}>{icon}</span>
      {label}
    </div>
  );
}

// ─── Shared bits ────────────────────────────────────────────────────
function Eyebrow({ children, color=AT.muted, right }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8,
      padding: '0 0.25rem', marginBottom: '0.6rem' }}>
      <span style={{ fontSize: '0.6875rem', fontWeight: 700, letterSpacing: '0.07em',
        textTransform: 'uppercase', color }}>{children}</span>
      {right && <span style={{ marginLeft: 'auto', fontSize: '0.7rem', color: AT.muted }}>{right}</span>}
    </div>
  );
}

// One stat card. variant: 'sm' | 'lg' | 'hero'
function StatCard({ label, value, cur, sub, accent, hero=false, delta }) {
  return (
    <div style={{
      background: hero ? `linear-gradient(135deg, rgba(99,102,241,0.18), ${AT.field})` : AT.field,
      border: `1px solid ${hero ? 'rgba(129,140,248,0.35)' : AT.border}`,
      borderRadius: hero ? 14 : 10,
      padding: hero ? '1rem 1.1rem' : '0.75rem 0.8rem',
      display: 'flex', flexDirection: 'column', gap: hero ? 6 : 4,
      minWidth: 0,
    }}>
      <span style={{ fontSize: hero ? '0.7rem' : '0.625rem', fontWeight: 700,
        letterSpacing: '0.06em', textTransform: 'uppercase',
        color: accent || AT.muted }}>{label}</span>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 5, flexWrap: 'wrap' }}>
        <span style={{ fontFamily: AT.fontNum, fontWeight: 600,
          fontSize: hero ? '2rem' : '1.15rem',
          color: accent && hero ? accent : AT.text, lineHeight: 1.05 }}>{value}</span>
        <span style={{ fontFamily: AT.fontNum, fontSize: hero ? '0.85rem' : '0.7rem',
          color: AT.muted2 }}>{cur}</span>
      </div>
      {sub && <span style={{ fontSize: hero ? '0.78rem' : '0.68rem', color: AT.muted }}>{sub}</span>}
      {delta != null && (
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 3,
          fontFamily: AT.fontNum, fontSize: '0.68rem',
          color: delta < 0 ? AT.down : AT.up }}>
          {delta < 0 ? AI.arrowDown(11) : AI.arrowUp(11)}{Math.abs(delta)}% vs last
        </span>
      )}
    </div>
  );
}

// Trend chip — "Food ↑14%"
function TrendChip({ label, dir, pct, inline=false }) {
  const c = dir === 'up' ? AT.up : AT.down;
  return (
    <span style={{
      display: 'inline-flex', alignItems: 'center', gap: 5,
      padding: inline ? 0 : '0.35rem 0.6rem',
      background: inline ? 'transparent' : AT.field,
      border: inline ? 'none' : `1px solid ${AT.border}`,
      borderRadius: 999, whiteSpace: 'nowrap', flexShrink: 0,
      fontSize: '0.78rem', color: AT.text,
    }}>
      {label}
      <span style={{ display: 'inline-flex', alignItems: 'center', gap: 2,
        color: c, fontFamily: AT.fontNum, fontWeight: 600, fontSize: '0.74rem' }}>
        {dir === 'up' ? AI.arrowUp(11) : AI.arrowDown(11)}{pct}
      </span>
    </span>
  );
}

Object.assign(window, {
  AT, AI, STATS, TRENDS, EVENTS,
  Phone, AHeader, Eyebrow, StatCard, TrendChip,
});
