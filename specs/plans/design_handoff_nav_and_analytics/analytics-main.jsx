// Canvas layout — analytics screen sketches.

function PhoneFrame({ children, height }) {
  return (
    <IOSDevice width={390} height={height} dark={true}>
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
        title="Analytics screen — layout sketches"
        subtitle="Dashboard-glance view, native to dinary (dark, mono numbers, per-context primary). Three approaches to stat cards, open/closed events, and trends placement."
      >
        <DCArtboard id="notes" label="Brief & rationale" width={560} height={1180}>
          <Notes/>
        </DCArtboard>
      </DCSection>

      <DCSection
        id="a"
        title="A · Savings hero"
        subtitle="Stats: full-width savings hero + 3-card row. Events: indigo left-border + live dot + OPEN pill. Trends: scroll chips, between stats and events."
      >
        <DCArtboard id="a-screen" label="A · full screen" width={390} height={1180}>
          <PhoneFrame height={1180}><SketchA/></PhoneFrame>
        </DCArtboard>
      </DCSection>

      <DCSection
        id="b"
        title="B · Even 2×2"
        subtitle="Stats: equal 2×2 grid, savings its own card (rate = subtitle). Events: filled status chip + colored top hairline. Trends: inline ticker line below grid."
      >
        <DCArtboard id="b-screen" label="B · full screen" width={390} height={1120}>
          <PhoneFrame height={1120}><SketchB/></PhoneFrame>
        </DCArtboard>
      </DCSection>

      <DCSection
        id="c"
        title="C · Now-first"
        subtitle="Stats: mixed — this-month hero + 3 small. Events: grouped Open / Closed sections (grouping carries the distinction). Trends: footer INSIGHTS bars, below events."
      >
        <DCArtboard id="c-screen" label="C · full screen" width={390} height={1260}>
          <PhoneFrame height={1260}><SketchC/></PhoneFrame>
        </DCArtboard>
      </DCSection>
    </DesignCanvas>
  );
}

function Notes() {
  return (
    <div style={{
      padding: 26, height: '100%', boxSizing: 'border-box',
      background: '#fff', color: '#1f1a13',
      fontFamily: '-apple-system, BlinkMacSystemFont, system-ui, sans-serif',
      fontSize: 13.5, lineHeight: 1.55, overflow: 'auto',
    }}>
      <h2 style={{ fontSize: 19, fontWeight: 700, margin: '0 0 4px' }}>Analytics — read me first</h2>
      <p style={{ color: '#7a6a55', margin: '0 0 14px' }}>
        Built in dinary’s real design language (dark, JetBrains-Mono numbers,
        per-context primary color) — <strong>not Vuetify</strong>. The brief said
        Vuetify, but the shipped app is a custom dark PWA, so these are drop-in.
        Shout if you actually want Material styling.
      </p>

      <div style={{ padding: 12, background: '#f5f0e6', border: '1px solid #e0d4ba',
        borderRadius: 8, color: '#5a4a2a', margin: '0 0 16px' }}>
        <strong>New token proposed:</strong> <code>--stat: #818cf8</code> (indigo) as
        Analytics’ per-context primary — distinct from Add-orange, Income-green,
        Review-blue, accent-red. Used only for the active tab + section eyebrows.
        Savings stays <span style={{ color: '#1b8a4b', fontWeight: 600 }}>green</span>;
        “spending up” trends are <span style={{ color: '#c0392b', fontWeight: 600 }}>red</span>
        (up = spending more), “down” green.
      </div>

      <H>The three open questions</H>

      <Q n="1" title="Stat-card arrangement">
        <ul style={ul}>
          <li><B>A — Savings hero.</B> One full-width hero for YTD savings (the
            number people actually care about), rate as its subtitle, then a tidy
            3-up row for the spend totals. Most opinionated; great if savings is
            the headline.</li>
          <li><B>B — Even 2×2.</B> Four equal cards, savings is the 4th with the
            rate as an in-card subtitle. Most balanced + scannable; no card claims
            primacy. Safest default.</li>
          <li><B>C — Now-first.</B> This-month spend is the hero (with a ↓18% vs
            last-month delta), the other three shrink to a row. Best if the daily
            question is “how am I doing <em>this</em> month”.</li>
        </ul>
        <P><B>Savings rate:</B> in all three it’s a <em>subtitle</em>, never its
        own card — a percentage isn’t a peer of four currency totals, and a 5th
        card would crowd the glance.</P>
      </Q>

      <Q n="2" title="Open vs closed events">
        <ul style={ul}>
          <li><B>A — Border + dot + pill.</B> Open rows get an indigo left-border,
            a pulsing live-dot, and an OPEN pill. Loud and unmistakable; reads even
            mid-scroll.</li>
          <li><B>B — Status chip + top hairline.</B> Every row carries an
            OPEN(green)/CLOSED(outline) chip; open rows add a green top hairline.
            Most explicit (closed is labeled too), slightly busier.</li>
          <li><B>C — Grouping.</B> Open events sit in their own pinned section above
            a recessed “Closed” group. No per-row chrome needed — position is the
            signal. Cleanest, scales worst if there are many open events.</li>
        </ul>
      </Q>

      <Q n="3" title="Trends placement">
        <ul style={ul}>
          <li><B>A — Scroll chips, between.</B> A horizontal chip rail under the
            stats. Feels like a bonus, swipes for more baskets.</li>
          <li><B>B — Inline ticker, between.</B> One muted line:
            “Food ↑14% · Pocket money ↑8% · Travel ↓30%”. Lightest touch — most
            “bonus, not primary”.</li>
          <li><B>C — Footer insights, below events.</B> Mini bar chart at the
            bottom. Most data-forward; furthest from the top glance (deliberately,
            since it’s secondary).</li>
        </ul>
        <P>All three <B>hide the whole block when there’s no basket data</B> —
        nothing collapses to an empty state.</P>
      </Q>

      <H>If you want my pick</H>
      <P><B>B for the stats</B> (balanced, no false hierarchy) + <B>A’s event
      treatment</B> (border/dot/pill reads best while scrolling) + <B>B’s inline
      ticker</B> for trends (truest to “bonus insight”). Easy to mix — say the word
      and I’ll combine into one.</P>
    </div>
  );
}

function Q({ n, title, children }) {
  return (
    <div style={{ margin: '0 0 14px' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, margin: '0 0 4px' }}>
        <span style={{ width: 20, height: 20, borderRadius: 999, background: '#6366f1',
          color: '#fff', fontSize: 12, fontWeight: 700, display: 'inline-flex',
          alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>{n}</span>
        <h3 style={{ fontSize: 13.5, fontWeight: 700, margin: 0 }}>{title}</h3>
      </div>
      {children}
    </div>
  );
}
function H({ children }) {
  return <h3 style={{ fontSize: 12, fontWeight: 700, textTransform: 'uppercase',
    letterSpacing: '0.06em', color: '#6366f1', margin: '18px 0 8px' }}>{children}</h3>;
}
function B({ children }) { return <strong>{children}</strong>; }
function P({ children }) { return <p style={{ margin: '6px 0 0' }}>{children}</p>; }
const ul = { margin: '0 0 4px 2px', padding: 0, listStyle: 'none', display: 'flex',
  flexDirection: 'column', gap: 6 };

const root = ReactDOM.createRoot(document.getElementById('root'));
root.render(<App/>);
