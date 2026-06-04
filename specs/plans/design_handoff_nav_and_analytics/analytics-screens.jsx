// Analytics — the three layout sketches.

// ═══════════════════════════════════════════════════════════════════
// SKETCH A — "Savings hero"
// Q1 stats: savings is a full-width HERO card (rate as subtitle), then a
//           3-card row (this month / last month / YTD spent).
// Q2 events: OPEN = indigo left-border + live dot + OPEN pill; CLOSED = flat.
// Q3 trends: horizontal scroll CHIP row, between stats and events.
// ═══════════════════════════════════════════════════════════════════
function SketchA() {
  return (
    <Phone>
      <Eyebrow color={AT.stat} right="year to date">SUMMARY</Eyebrow>

      <StatCard hero label="Saved this year" value={`+${STATS.ytdSavings}`} cur={STATS.cur}
        accent={AT.income} sub={`${STATS.savingsRate} savings rate · income − expenses`} />

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 8, marginTop: 10 }}>
        <StatCard label="This month" value={STATS.thisMonth} cur={STATS.cur} />
        <StatCard label="Last month" value={STATS.lastMonth} cur={STATS.cur} />
        <StatCard label="YTD spent"  value={STATS.ytdSpent}  cur={STATS.cur} />
      </div>

      {/* TRENDS — scroll chips, bonus insight */}
      <div style={{ marginTop: 22 }}>
        <Eyebrow color={AT.muted}>BASKET TRENDS</Eyebrow>
        <div style={{ display: 'flex', gap: 8, overflowX: 'auto', padding: '0 0.25rem 2px' }}>
          {TRENDS.map(t => <TrendChip key={t.label} {...t} />)}
        </div>
      </div>

      {/* EVENTS */}
      <div style={{ marginTop: 22 }}>
        <Eyebrow color={AT.muted} right="last 12 months">EVENTS</Eyebrow>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          {EVENTS.map(e => <EventRowA key={e.name} event={e} />)}
        </div>
      </div>
    </Phone>
  );
}

function EventRowA({ event }) {
  const o = event.open;
  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: 12,
      background: AT.field, borderRadius: 10,
      borderLeft: `3px solid ${o ? AT.stat : 'transparent'}`,
      border: `1px solid ${AT.border}`,
      borderLeftWidth: 3, borderLeftColor: o ? AT.stat : 'transparent',
      padding: '0.7rem 0.85rem',
    }}>
      <div style={{ minWidth: 0, flex: 1 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
          {o && <span style={{ width: 7, height: 7, borderRadius: 999, background: AT.stat,
            boxShadow: `0 0 0 3px rgba(129,140,248,0.25)`, flexShrink: 0 }}/>}
          <span style={{ fontSize: '0.9375rem', fontWeight: 600, color: AT.text,
            whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{event.name}</span>
          {o && <span style={{ fontSize: '0.58rem', fontWeight: 700, letterSpacing: '0.05em',
            color: AT.stat, background: 'rgba(129,140,248,0.15)', borderRadius: 999,
            padding: '1px 7px', flexShrink: 0 }}>OPEN</span>}
        </div>
        <span style={{ fontFamily: AT.fontNum, fontSize: '0.72rem',
          color: o ? AT.muted : AT.muted2, marginTop: 3, display: 'block' }}>{event.range}</span>
      </div>
      <span style={{ fontFamily: AT.fontNum, fontSize: '0.95rem', fontWeight: 600,
        color: o ? AT.text : AT.muted, flexShrink: 0 }}>
        {event.total} <span style={{ fontSize: '0.7rem', color: AT.muted2 }}>{STATS.cur}</span>
      </span>
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════
// SKETCH B — "Even 2×2"
// Q1 stats: clean 2×2 grid, 4 equal cards. Savings is its own card with
//           the rate as an in-card subtitle. This-month card shows delta.
// Q2 events: OPEN = filled status chip + colored top hairline; CLOSED flat.
// Q3 trends: single inline TICKER line below the grid (most "bonus"-feeling).
// ═══════════════════════════════════════════════════════════════════
function SketchB() {
  return (
    <Phone>
      <Eyebrow color={AT.stat} right="year to date">SUMMARY</Eyebrow>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
        <StatCard label="This month" value={STATS.thisMonth} cur={STATS.cur} delta={STATS.monthDelta} />
        <StatCard label="Last month" value={STATS.lastMonth} cur={STATS.cur} />
        <StatCard label="YTD spent"  value={STATS.ytdSpent}  cur={STATS.cur} />
        <StatCard label="YTD saved"  value={`+${STATS.ytdSavings}`} cur={STATS.cur}
          accent={AT.income} sub={`${STATS.savingsRate} savings rate`} />
      </div>

      {/* TRENDS — inline ticker */}
      <div style={{ marginTop: 16, padding: '0.6rem 0.7rem',
        background: AT.fieldDeep, borderRadius: 10,
        display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        <span style={{ fontSize: '0.6rem', fontWeight: 700, letterSpacing: '0.06em',
          color: AT.muted2, textTransform: 'uppercase', flexShrink: 0 }}>TRENDS</span>
        {TRENDS.map((t, i) => (
          <React.Fragment key={t.label}>
            <TrendChip {...t} inline />
            {i < TRENDS.length - 1 && <span style={{ color: AT.muted2, fontSize: '0.7rem' }}>·</span>}
          </React.Fragment>
        ))}
      </div>

      {/* EVENTS */}
      <div style={{ marginTop: 22 }}>
        <Eyebrow color={AT.muted} right="last 12 months">EVENTS</Eyebrow>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          {EVENTS.map(e => <EventRowB key={e.name} event={e} />)}
        </div>
      </div>
    </Phone>
  );
}

function EventRowB({ event }) {
  const o = event.open;
  return (
    <div style={{
      background: AT.field, borderRadius: 10, border: `1px solid ${AT.border}`,
      overflow: 'hidden',
    }}>
      {o && <div style={{ height: 3, background: AT.income }}/>}
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '0.7rem 0.85rem' }}>
        <div style={{ minWidth: 0, flex: 1 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
            <span style={{ fontSize: '0.9375rem', fontWeight: 600,
              color: o ? AT.text : AT.muted, whiteSpace: 'nowrap', overflow: 'hidden',
              textOverflow: 'ellipsis' }}>{event.name}</span>
            <span style={{ fontSize: '0.56rem', fontWeight: 700, letterSpacing: '0.05em',
              flexShrink: 0, borderRadius: 999, padding: '1px 7px',
              color: o ? '#06241a' : AT.muted2,
              background: o ? AT.income : 'transparent',
              border: o ? 'none' : `1px solid ${AT.borderStrong}` }}>
              {o ? 'OPEN' : 'CLOSED'}
            </span>
          </div>
          <span style={{ fontFamily: AT.fontNum, fontSize: '0.72rem',
            color: AT.muted2, marginTop: 3, display: 'block' }}>{event.range}</span>
        </div>
        <span style={{ fontFamily: AT.fontNum, fontSize: '0.95rem', fontWeight: 600,
          color: o ? AT.text : AT.muted, flexShrink: 0 }}>
          {event.total} <span style={{ fontSize: '0.7rem', color: AT.muted2 }}>{STATS.cur}</span>
        </span>
      </div>
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════
// SKETCH C — "Now-first, trends as footer"
// Q1 stats: MIXED — this-month wide hero on top (the "now"), then 3 small
//           (last month / YTD spent / YTD saved w/ rate subtitle).
// Q2 events: grouped — OPEN section (live styling) above a recessed CLOSED
//           section. Grouping itself carries the distinction.
// Q3 trends: BELOW events, as a footer "INSIGHTS" block of mini bars.
// ═══════════════════════════════════════════════════════════════════
function SketchC() {
  const open = EVENTS.filter(e => e.open);
  const closed = EVENTS.filter(e => !e.open);
  return (
    <Phone>
      <Eyebrow color={AT.stat} right="May 2026">THIS MONTH</Eyebrow>

      <StatCard hero label="Spent this month" value={STATS.thisMonth} cur={STATS.cur}
        sub={null} delta={STATS.monthDelta} />

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 8, marginTop: 10 }}>
        <StatCard label="Last month" value={STATS.lastMonth} cur={STATS.cur} />
        <StatCard label="YTD spent"  value={STATS.ytdSpent}  cur={STATS.cur} />
        <StatCard label="YTD saved"  value={`+${STATS.ytdSavings}`} cur={STATS.cur}
          accent={AT.income} sub={STATS.savingsRate} />
      </div>

      {/* EVENTS — grouped */}
      <div style={{ marginTop: 22 }}>
        <Eyebrow color={AT.stat}>OPEN EVENTS</Eyebrow>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          {open.map(e => <EventRowC key={e.name} event={e} />)}
        </div>
      </div>
      <div style={{ marginTop: 18 }}>
        <Eyebrow color={AT.muted2} right="last 12 months">CLOSED</Eyebrow>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6, opacity: 0.82 }}>
          {closed.map(e => <EventRowC key={e.name} event={e} />)}
        </div>
      </div>

      {/* TRENDS — footer insights block */}
      <div style={{ marginTop: 24, padding: '0.9rem 1rem', background: AT.fieldDeep,
        borderRadius: 12, border: `1px solid ${AT.border}` }}>
        <Eyebrow color={AT.muted}>BASKET TRENDS · 90 DAYS</Eyebrow>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          {TRENDS.map(t => <TrendBar key={t.label} {...t} />)}
        </div>
      </div>
    </Phone>
  );
}

function EventRowC({ event }) {
  const o = event.open;
  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: 12,
      background: o ? AT.field : 'transparent',
      borderRadius: 10,
      border: `1px solid ${o ? 'rgba(129,140,248,0.3)' : AT.border}`,
      padding: o ? '0.7rem 0.85rem' : '0.55rem 0.85rem',
    }}>
      {o && <span style={{ flexShrink: 0, color: AT.stat, display: 'inline-flex' }}>{AI.plane(15)}</span>}
      <div style={{ minWidth: 0, flex: 1 }}>
        <span style={{ fontSize: o ? '0.9375rem' : '0.875rem', fontWeight: 600,
          color: o ? AT.text : AT.muted, whiteSpace: 'nowrap', overflow: 'hidden',
          textOverflow: 'ellipsis', display: 'block' }}>{event.name}</span>
        <span style={{ fontFamily: AT.fontNum, fontSize: '0.7rem',
          color: AT.muted2, marginTop: 2, display: 'block' }}>{event.range}</span>
      </div>
      <span style={{ fontFamily: AT.fontNum, fontSize: o ? '0.95rem' : '0.85rem', fontWeight: 600,
        color: o ? AT.text : AT.muted, flexShrink: 0 }}>
        {event.total} <span style={{ fontSize: '0.68rem', color: AT.muted2 }}>{STATS.cur}</span>
      </span>
    </div>
  );
}

function TrendBar({ label, dir, pct }) {
  const c = dir === 'up' ? AT.up : AT.down;
  const w = parseInt(pct, 10);
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
      <span style={{ fontSize: '0.8rem', color: AT.text, width: 96, flexShrink: 0 }}>{label}</span>
      <div style={{ flex: 1, height: 6, background: 'rgba(255,255,255,0.06)', borderRadius: 999,
        position: 'relative', overflow: 'hidden' }}>
        <div style={{ position: 'absolute', left: 0, top: 0, bottom: 0,
          width: `${Math.min(w * 2.2, 100)}%`, background: c, borderRadius: 999, opacity: 0.85 }}/>
      </div>
      <span style={{ display: 'inline-flex', alignItems: 'center', gap: 2, color: c,
        fontFamily: AT.fontNum, fontWeight: 600, fontSize: '0.74rem', width: 52,
        justifyContent: 'flex-end', flexShrink: 0 }}>
        {dir === 'up' ? AI.arrowUp(11) : AI.arrowDown(11)}{pct}
      </span>
    </div>
  );
}

Object.assign(window, { SketchA, SketchB, SketchC });
