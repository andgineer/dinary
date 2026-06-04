// Canvas — header drafts without the ••• overflow.

function Frame({ children, height, width=390 }) {
  return (
    <IOSDevice width={width} height={height} dark={true}>
      <div style={{ position: 'absolute', inset: 0, paddingTop: 47, overflow: 'hidden' }}>
        {children}
      </div>
    </IOSDevice>
  );
}

function App() {
  return (
    <DesignCanvas>
      <DCSection
        id="intro"
        title="Header without ••• — all tabs visible"
        subtitle="5 tabs (Add · Review · Stats · Income · LLM) + brand + version + queue notification. One inline row is too tight — four ways to fix it."
      >
        <DCArtboard id="notes" label="Trade-offs & fit math" width={560} height={840}>
          <Notes/>
        </DCArtboard>
      </DCSection>

      <DCSection
        id="a"
        title="A · Two-row header"
        subtitle="Brand + version + queue notification keep the top row; the 5-tab segmented control drops to a full-width second row. Each tab gets real room; scales to 6–7 tabs. Cost: ~46px taller header."
      >
        <DCArtboard id="a" label="A · 390px" width={390} height={560}>
          <Frame height={560}><DraftA/></Frame>
        </DCArtboard>
      </DCSection>

      <DCSection
        id="b"
        title="B · Compact single row"
        subtitle="Keeps the original one-row layout: brand (version dropped to settings), queue as a count chip, 5 icon-only tabs at 44px. Tight — shown at 390 AND 340 to prove it fits a small phone."
      >
        <DCArtboard id="b390" label="B · 390px" width={390} height={520}>
          <Frame height={520}><DraftB/></Frame>
        </DCArtboard>
        <DCArtboard id="b340" label="B · 340px (small phone)" width={340} height={520}>
          <Frame height={520} width={340}><DraftB narrow/></Frame>
        </DCArtboard>
      </DCSection>

      <DCSection
        id="c"
        title="C · Notification as a strip"
        subtitle="The queue moves out of the row into a full-width tappable strip — same idiom as the existing offline notice. That frees the whole row for brand + 5 inline tabs. Strip only shows when the queue is non-empty."
      >
        <DCArtboard id="c" label="C · 390px (queue present)" width={390} height={560}>
          <Frame height={560}><DraftC showStrip={true}/></Frame>
        </DCArtboard>
        <DCArtboard id="c-empty" label="C · queue empty (no strip)" width={390} height={520}>
          <Frame height={520}><DraftC showStrip={false}/></Frame>
        </DCArtboard>
      </DCSection>

      <DCSection
        id="d"
        title="D · Bottom tab bar"
        subtitle="Nav moves to a fixed bottom bar (labeled, thumb-friendly, scales freely). Header keeps just brand + version + a bell with a dot. Biggest departure from today's top-nav identity."
      >
        <DCArtboard id="d" label="D · 390px" width={390} height={620}>
          <Frame height={620}><DraftD/></Frame>
        </DCArtboard>
      </DCSection>
    </DesignCanvas>
  );
}

function Notes() {
  return (
    <div style={{ padding: 26, height: '100%', boxSizing: 'border-box',
      background: '#fff', color: '#1f1a13',
      fontFamily: '-apple-system, BlinkMacSystemFont, system-ui, sans-serif',
      fontSize: 13.5, lineHeight: 1.55, overflow: 'auto' }}>
      <h2 style={{ fontSize: 19, fontWeight: 700, margin: '0 0 4px' }}>The squeeze</h2>
      <p style={{ color: '#7a6a55', margin: '0 0 12px' }}>
        Dropping <code>•••</code> means 5 tabs must show at once. At 390px the
        content row is ~350px wide. Budget for one inline row:
      </p>
      <div style={{ padding: 12, background: '#f5f0e6', border: '1px solid #e0d4ba',
        borderRadius: 8, color: '#5a4a2a', margin: '0 0 16px',
        fontFamily: 'ui-monospace, SFMono-Regular, monospace', fontSize: 12, lineHeight: 1.7 }}>
        Brand "Dinary v0.11" … ~108px<br/>
        Queue "2 queued" pill … ~78px<br/>
        5 tabs @ 54px + gaps … ~284px<br/>
        ──────────────────────────────<br/>
        needed ≈ 470px &nbsp;vs&nbsp; 350px available → <strong>overflow</strong>
      </div>
      <p style={{ margin: '0 0 14px' }}>
        Something has to give: a <strong>row</strong> (A), the <strong>brand/labels</strong> (B),
        the <strong>notification's seat</strong> (C), or the <strong>whole nav location</strong> (D).
      </p>

      <H>The four moves</H>
      <Row letter="A" name="Two-row header">
        Notification keeps its exact spot. Tabs drop to a full-width second row, each
        with real room. Safest; scales best. <Cost>+46px header height</Cost>
      </Row>
      <Row letter="B" name="Compact single row">
        Stays one row. Version leaves the chrome (→ settings), tabs go icon-only @40px.
        At ≥360px the queue shows a count chip; on small phones (≤340px) it drops to a
        bare presence dot so all 5 tabs still fit. <Cost>loses the version string + tab labels; count → dot under ~360px</Cost>
      </Row>
      <Row letter="C" name="Notification as a strip">
        Queue moves below the row as a tappable amber strip (reuses the offline-notice
        idiom). Row now fits brand + 5 tabs. Strip vanishes when the queue is empty.
        <Cost>notification leaves the "between version and +" spot; +34px only when queued</Cost>
      </Row>
      <Row letter="D" name="Bottom tab bar">
        Nav to the bottom, labeled + thumb-reachable, scales to many tabs. Header becomes
        roomy (brand + bell-with-dot). <Cost>biggest identity change; +64px bottom bar</Cost>
      </Row>

      <H>My read</H>
      <P><B>C</B> is the most dinary-native answer — it reuses a pattern you already
      ship (the offline strip), keeps all 5 tabs inline, and the notification only
      takes space when it has something to say. <B>A</B> is the safe pick if you want
      to keep the notification pinned beside the brand. <B>D</B> only if you expect the
      tab count to keep growing.</P>
    </div>
  );
}

function Row({ letter, name, children }) {
  return (
    <div style={{ display: 'flex', gap: 10, margin: '0 0 11px' }}>
      <span style={{ width: 22, height: 22, borderRadius: 6, background: '#6366f1',
        color: '#fff', fontSize: 12, fontWeight: 700, display: 'inline-flex',
        alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>{letter}</span>
      <div style={{ flex: 1 }}>
        <strong>{name}.</strong> {children}
      </div>
    </div>
  );
}
function H({ children }) { return <h3 style={{ fontSize: 12, fontWeight: 700,
  textTransform: 'uppercase', letterSpacing: '0.06em', color: '#6366f1',
  margin: '16px 0 8px' }}>{children}</h3>; }
function B({ children }) { return <strong>{children}</strong>; }
function P({ children }) { return <p style={{ margin: '6px 0 0' }}>{children}</p>; }
function Cost({ children }) {
  return <span style={{ display: 'block', marginTop: 3, fontSize: 12, color: '#a06a3a' }}>
    ⚖︎ {children}</span>;
}

const root = ReactDOM.createRoot(document.getElementById('root'));
root.render(<App/>);
