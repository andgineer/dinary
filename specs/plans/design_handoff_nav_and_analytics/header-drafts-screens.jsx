// The four header drafts.

// ═══════════════════════════════════════════════════════════════════
// DRAFT A — Two-row header
// Row 1: brand + version + queue notification (keeps its exact spot).
// Row 2: full-width segmented control, all 5 tabs equal (flex:1).
// Each tab gets real room; scales to 6–7 tabs. Costs ~46px of height.
// ═══════════════════════════════════════════════════════════════════
function DraftA({ active='analytics' }) {
  return (
    <Phone>
      <div style={{ background: HT.surface, borderBottom: `1px solid ${HT.surface2}`,
        position: 'sticky', top: 0, zIndex: 10 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10,
          padding: '0.85rem 1.25rem 0' }}>
          <Brand/>
          <QueueBadge/>
        </div>
        <div style={{ padding: '0.7rem 1.25rem 0.85rem' }}>
          <SegBar active={active} size="wide" style={{ width: '100%' }}/>
        </div>
      </div>
      <ContentSlice label="ANALYTICS"/>
    </Phone>
  );
}

// ═══════════════════════════════════════════════════════════════════
// DRAFT B — Compact single row
// Brand keeps its name (version dropped from the chrome → lives in a
// settings/long-press), queue becomes a compact count chip, 5 tabs are
// icon-only at 44px. Everything stays on the original single row.
// Tight: proven at 340px below.
// ═══════════════════════════════════════════════════════════════════
function DraftB({ active='analytics', narrow=false }) {
  return (
    <Phone>
      <div style={{ background: HT.surface, borderBottom: `1px solid ${HT.surface2}`,
        position: 'sticky', top: 0, zIndex: 10 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6,
          padding: '0.9rem 1rem' }}>
          <Brand version={false} small/>
          {/* count chip when there's room; bare dot on small phones */}
          <QueueBadge compact={!narrow} dot={narrow}/>
          <div style={{ flex: 1, minWidth: 4 }}/>
          <SegBar active={active} size="sm"/>
        </div>
      </div>
      <ContentSlice label="ANALYTICS"/>
    </Phone>
  );
}

// ═══════════════════════════════════════════════════════════════════
// DRAFT C — Notification as a strip below the header
// The queue moves OUT of the top row into a full-width tappable strip
// (same idiom as the existing offline notice). That frees the whole row
// for brand + 5 inline tabs. Strip only shows when the queue is non-empty.
// ═══════════════════════════════════════════════════════════════════
function DraftC({ active='analytics', showStrip=true }) {
  return (
    <Phone>
      <div style={{ background: HT.surface, borderBottom: `1px solid ${HT.surface2}`,
        position: 'sticky', top: 0, zIndex: 10 }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          gap: 8, padding: '0.9rem 1rem' }}>
          <Brand version={false}/>
          <SegBar active={active} size="sm"/>
        </div>
        {showStrip && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 8,
            padding: '0.5rem 1rem', background: 'rgba(245,158,11,0.12)',
            borderTop: `1px solid rgba(245,158,11,0.25)`, color: HT.warning,
            fontSize: '0.78rem', cursor: 'pointer' }}>
            {I.clock(13)}
            <span><span style={{ fontFamily: HT.fontNum, fontWeight: 700 }}>2</span> receipts queued</span>
            <span style={{ marginLeft: 'auto', fontSize: '0.72rem', opacity: 0.85 }}>tap to review →</span>
          </div>
        )}
      </div>
      <ContentSlice label="ANALYTICS"/>
    </Phone>
  );
}

// ═══════════════════════════════════════════════════════════════════
// DRAFT D — Bottom tab bar
// Header keeps only brand + version + queue notification (roomy). Nav
// moves to a fixed bottom bar with all 5 tabs, labeled. Most scalable +
// thumb-friendly; biggest departure from today's top-nav identity.
// ═══════════════════════════════════════════════════════════════════
function DraftD({ active='analytics' }) {
  return (
    <Phone>
      <div style={{ background: HT.surface, borderBottom: `1px solid ${HT.surface2}`,
        position: 'sticky', top: 0, zIndex: 10,
        display: 'flex', alignItems: 'center', gap: 10, padding: '1rem 1.25rem' }}>
        <Brand/>
        <div style={{ flex: 1 }}/>
        <button style={{ position: 'relative', width: 36, height: 36, border: 'none',
          background: 'transparent', color: HT.muted, cursor: 'pointer',
          display: 'inline-flex', alignItems: 'center', justifyContent: 'center' }}>
          {I.bell(19)}
          <span style={{ position: 'absolute', top: 4, right: 4, width: 8, height: 8,
            borderRadius: 999, background: HT.warning, border: `2px solid ${HT.surface}` }}/>
        </button>
      </div>
      <ContentSlice label="ANALYTICS"/>
      {/* fixed bottom bar */}
      <div style={{ background: HT.surface, borderTop: `1px solid ${HT.surface2}`,
        display: 'flex', alignItems: 'stretch', padding: '0.5rem 0.5rem 0.85rem' }}>
        {TABS.map(t => {
          const on = t.key === active;
          return (
            <button key={t.key} style={{ flex: 1, border: 'none', background: 'transparent',
              cursor: 'pointer', display: 'flex', flexDirection: 'column', alignItems: 'center',
              gap: 4, padding: '4px 0', color: on ? t.color : HT.muted2 }}>
              {t.icon(22)}
              <span style={{ fontSize: '0.62rem', fontWeight: on ? 700 : 500,
                letterSpacing: '0.02em' }}>{t.label}</span>
            </button>
          );
        })}
      </div>
    </Phone>
  );
}

Object.assign(window, { DraftA, DraftB, DraftC, DraftD });
