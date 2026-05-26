// About · Privacy · Terms

// ────────────────────────────────────────────────────────────
// ABOUT
// Visual concept: a manifesto. Big statement type, principles
// numbered like bylaw sections, then the people.

const ABOUT_PRINCIPLES = [
  {
    n: '01',
    t: 'A reading must be sourced.',
    d: 'Every assertion carries a citation. Every citation links to the exact paragraph in the consolidated bylaw, dated.',
  },
  {
    n: '02',
    t: 'The agent should refuse before it should guess.',
    d: 'When two clauses conflict, ABS surfaces both. When the parcel data is uncertain, the answer says so out loud.',
  },
  {
    n: '03',
    t: 'Depth before breadth.',
    d: 'One jurisdiction, indexed end-to-end, calibrated against planner-reviewed answers. Then the next.',
  },
  {
    n: '04',
    t: 'Built with planners, not around them.',
    d: 'HRM Planning reviews our test set. We do not pretend to replace the development officer — we make the conversation faster.',
  },
];

const ABOUT_TEAM = [
  { name: 'Mira Caulfield', role: 'Co-founder · Eng', loc: 'Halifax, NS', past: 'Prev. mapping at Esri Canada' },
  { name: 'Aaron Pictou', role: 'Co-founder · Planning', loc: 'Dartmouth, NS', past: 'Prev. HRM Development Officer' },
  { name: 'Sana Ng', role: 'Retrieval & ML', loc: 'Halifax, NS', past: 'Prev. Cohere' },
  { name: 'Joel Demaine', role: 'Design', loc: 'St. John\u2019s, NL', past: 'Prev. independent' },
];

const ABOUT_TIMELINE = [
  { d: 'Aug 2024', t: 'Founded in Halifax, in a kitchen above the Hydrostone market.' },
  { d: 'Nov 2024', t: 'First indexable parse of the HRM Land Use By-law.' },
  { d: 'Feb 2025', t: 'Pre-seed round closed. Hired Sana to lead retrieval.' },
  { d: 'Jul 2025', t: 'First planner-validated reading.' },
  { d: 'Feb 2026', t: 'Private beta opens to architects in HRM.' },
];

const AboutPage = ({ setRoute }) => {
  const t = useTheme();
  return (
    <Page>
      <PageHead
        kicker="ABOUT · ABS"
        title="An expert planner, in your workflow."
        sub="ABS is a small team building one thing: an agent that reads municipal by-laws accurately, against a real parcel, with sources you can hand to a development officer."
      />

      {/* Mission — huge statement */}
      <section style={{ padding: '40px 0 56px' }}>
        <Mono muted size={11} style={{ marginBottom: 18, display: 'block' }}>MISSION</Mono>
        <p style={{ fontSize: 56, fontWeight: 700, letterSpacing: '-0.04em', lineHeight: 1.02, margin: 0, maxWidth: 1080 }}>
          Most good buildings get worse — or never get built — because the rules
          that govern them are <HighlightWord height={0.16}>too slow to read</HighlightWord>. We're fixing the reading.
        </p>
      </section>

      {/* Principles — bylaw-numbered grid */}
      <section style={{ marginBottom: 72 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 14, marginBottom: 26 }}>
          <Mono muted size={10}>PRINCIPLES · 04 IN FORCE</Mono>
          <div style={{ flex: 1, height: 1, background: t.hair }} />
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 0, border: `1px solid ${t.hair}` }}>
          {ABOUT_PRINCIPLES.map((p, i) => (
            <div key={p.n} style={{
              padding: '32px 30px',
              borderRight: i % 2 === 0 ? `1px solid ${t.hair}` : 'none',
              borderBottom: i < 2 ? `1px solid ${t.hair}` : 'none',
              display: 'flex', flexDirection: 'column', gap: 14, position: 'relative',
            }}>
              <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between' }}>
                <Mono accent size={11}>§ {p.n}</Mono>
                <span style={{ width: 28, height: 4, background: t.accent }} />
              </div>
              <div style={{ fontSize: 26, fontWeight: 700, letterSpacing: '-0.025em', lineHeight: 1.1 }}>{p.t}</div>
              <div style={{ fontSize: 14, color: t.textMuted, lineHeight: 1.55 }}>{p.d}</div>
            </div>
          ))}
        </div>
      </section>

      {/* Origin / timeline + the place */}
      <section style={{ display: 'grid', gridTemplateColumns: '1.2fr 1fr', gap: 56, marginBottom: 72, alignItems: 'flex-start' }}>
        <div>
          <Mono muted size={10} style={{ marginBottom: 14, display: 'block' }}>ORIGIN</Mono>
          <h3 style={{ fontSize: 36, fontWeight: 700, letterSpacing: '-0.03em', lineHeight: 1.05, margin: '0 0 22px' }}>
            We started with one question:<br/>"What if reading a bylaw was as fast as building one?"
          </h3>
          <div style={{ fontSize: 15, lineHeight: 1.6, color: t.text, display: 'flex', flexDirection: 'column', gap: 14, maxWidth: 560 }}>
            <p style={{ margin: 0 }}>
              ABS began in Halifax, where the founders kept watching small-builder projects stall in the planner queue. The bylaw wasn't unclear — it just wasn't searchable.
            </p>
            <p style={{ margin: 0 }}>
              We started with one document, one zone, one address. Then a hundred. Now the entire HRM Land Use By-law system, with the same standard of evidence behind every answer.
            </p>
          </div>
        </div>

        <div style={{ border: `1px solid ${t.hair}` }}>
          <div style={{ padding: '14px 18px', background: t.surfaceAlt, borderBottom: `1px solid ${t.hair}`, display: 'flex', justifyContent: 'space-between' }}>
            <Mono muted size={10}>TIMELINE</Mono>
            <Mono muted size={10}>2024 — NOW</Mono>
          </div>
          {ABOUT_TIMELINE.map((m, i) => (
            <div key={m.d} style={{
              display: 'grid', gridTemplateColumns: '100px 1fr', gap: 14, padding: '14px 18px',
              borderBottom: i < ABOUT_TIMELINE.length - 1 ? `1px solid ${t.hair}` : 'none',
              alignItems: 'flex-start',
            }}>
              <span style={{ fontFamily: 'JetBrains Mono', fontSize: 10.5, letterSpacing: '0.06em', color: t.textMuted, paddingTop: 2 }}>{m.d.toUpperCase()}</span>
              <span style={{ fontSize: 13.5, lineHeight: 1.5 }}>{m.t}</span>
            </div>
          ))}
        </div>
      </section>

      {/* The place — Halifax */}
      <section style={{ background: t.text, color: t.surface, padding: '40px 36px', marginBottom: 72, display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 36, alignItems: 'center' }}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
          <Mono size={10} style={{ color: 'rgba(255,255,255,0.55)' }}>WHERE WE ARE · 44.6488° N, 63.5752° W</Mono>
          <h3 style={{ fontSize: 48, fontWeight: 800, letterSpacing: '-0.04em', lineHeight: 0.98, margin: 0 }}>Built in Halifax.</h3>
          <p style={{ fontSize: 15, color: 'rgba(255,255,255,0.7)', lineHeight: 1.55, margin: 0, maxWidth: 460 }}>
            We started here on purpose. Halifax is small enough to learn end-to-end, big enough to matter — and we walk past the buildings ABS has read every day.
          </p>
        </div>

        {/* Stylized harbour-ish thing — purely typographic */}
        <div style={{
          aspectRatio: '4 / 2.2', background: 'rgba(255,255,255,0.04)',
          border: '1px solid rgba(255,255,255,0.18)', display: 'flex', flexDirection: 'column', justifyContent: 'space-between',
          padding: 18,
        }}>
          <div style={{ display: 'flex', justifyContent: 'space-between' }}>
            <Mono size={9} style={{ color: 'rgba(255,255,255,0.55)' }}>HRM</Mono>
            <Mono size={9} style={{ color: 'rgba(255,255,255,0.55)' }}>5,490 km²</Mono>
          </div>
          <div style={{
            height: 1, background: 'rgba(255,255,255,0.25)', margin: '0 -18px',
          }} />
          <div style={{ display: 'flex', gap: 1, alignItems: 'flex-end', height: 64 }}>
            {[18, 32, 28, 46, 36, 58, 42, 54, 38, 72, 50, 64, 48, 56, 40, 42, 30, 24, 18, 14].map((h, i) => (
              <div key={i} style={{ flex: 1, background: i % 5 === 0 ? t.accent : 'rgba(255,255,255,0.55)', height: `${h}px` }} />
            ))}
          </div>
          <div style={{
            height: 1, background: 'rgba(255,255,255,0.25)', margin: '0 -18px',
          }} />
          <div style={{ display: 'flex', justifyContent: 'space-between' }}>
            <Mono size={9} style={{ color: 'rgba(255,255,255,0.55)' }}>PENINSULA</Mono>
            <Mono size={9} style={{ color: 'rgba(255,255,255,0.55)' }}>DARTMOUTH</Mono>
            <Mono size={9} style={{ color: 'rgba(255,255,255,0.55)' }}>BEDFORD</Mono>
            <Mono size={9} style={{ color: 'rgba(255,255,255,0.55)' }}>SACKVILLE</Mono>
          </div>
        </div>
      </section>

      {/* People */}
      <section style={{ marginBottom: 24 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 26 }}>
          <div>
            <Mono muted size={10} style={{ marginBottom: 8, display: 'block' }}>PEOPLE · 04 + GROWING</Mono>
            <h3 style={{ fontSize: 32, fontWeight: 700, letterSpacing: '-0.03em', lineHeight: 1.05, margin: 0 }}>The team.</h3>
          </div>
          <Btn variant="ghost" size="sm">We're hiring →</Btn>
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 14 }}>
          {ABOUT_TEAM.map((p, i) => (
            <div key={p.name} style={{ border: `1px solid ${t.hair}`, display: 'flex', flexDirection: 'column' }}>
              {/* Portrait placeholder — striped */}
              <div style={{
                aspectRatio: '1 / 1', background: i === 0 ? t.accent : t.surfaceAlt,
                position: 'relative', overflow: 'hidden',
                backgroundImage: i === 0 ? 'none' : `repeating-linear-gradient(45deg, ${t.hair} 0 1px, transparent 1px 12px)`,
                display: 'flex', alignItems: 'flex-end', padding: 12,
              }}>
                <Mono size={9} style={{ color: i === 0 ? t.onAccent : t.textMuted }}>PORTRAIT · {i + 1}/4</Mono>
              </div>
              <div style={{ padding: '16px 16px 18px', display: 'flex', flexDirection: 'column', gap: 4, borderTop: `1px solid ${t.hair}` }}>
                <div style={{ fontSize: 16, fontWeight: 700, letterSpacing: '-0.015em' }}>{p.name}</div>
                <div style={{ fontSize: 13, color: t.text }}>{p.role}</div>
                <div style={{ fontSize: 12, color: t.textMuted, marginTop: 6 }}>{p.past}</div>
                <Mono muted size={9} style={{ marginTop: 6 }}>{p.loc.toUpperCase()}</Mono>
              </div>
            </div>
          ))}
        </div>
      </section>

      {/* Closing CTA */}
      <section style={{ marginTop: 56, padding: '28px 32px', border: `1.5px solid ${t.text}`, display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 24, flexWrap: 'wrap' }}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
          <Mono muted size={10}>SAY HELLO</Mono>
          <div style={{ fontSize: 22, fontWeight: 700, letterSpacing: '-0.02em' }}>hello@abs.app · @abs.halifax</div>
        </div>
        <div style={{ display: 'flex', gap: 10 }}>
          <Btn variant="ghost" size="md" onClick={() => setRoute('support')}>Talk to us</Btn>
          <Btn variant="primary" size="md" onClick={() => setRoute('signup')}>Get an invite →</Btn>
        </div>
      </section>
    </Page>
  );
};

// ────────────────────────────────────────────────────────────
// LEGAL — shared shell for Privacy + Terms
// Visual concept: a bylaw document itself. Section-numbered sidebar TOC,
// each section a § with consolidation date. Plain English summary on top.

const LegalShell = ({ kicker, title, sub, plainSummary, consolidatedAt, version, sections }) => {
  const t = useTheme();
  const [active, setActive] = React.useState(sections[0].id);

  React.useEffect(() => {
    const onScroll = () => {
      const offsets = sections.map(s => {
        const el = document.getElementById(s.id);
        return { id: s.id, top: el ? el.getBoundingClientRect().top : Infinity };
      });
      const current = offsets.filter(o => o.top < 140).pop();
      if (current) setActive(current.id);
    };
    window.addEventListener('scroll', onScroll, { passive: true });
    onScroll();
    return () => window.removeEventListener('scroll', onScroll);
  }, [sections]);

  return (
    <Page>
      <PageHead kicker={kicker} title={title} sub={sub} />

      {/* Plain-English summary card */}
      <div style={{ background: t.accent, color: t.onAccent, padding: '22px 26px', marginBottom: 40, display: 'grid', gridTemplateColumns: '160px 1fr', gap: 24, alignItems: 'flex-start' }}>
        <Mono size={10} style={{ color: t.onAccent }}>IN PLAIN ENGLISH</Mono>
        <div style={{ fontSize: 17, fontWeight: 500, lineHeight: 1.45, letterSpacing: '-0.01em' }}>{plainSummary}</div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '240px 1fr', gap: 56, alignItems: 'flex-start' }}>
        {/* Sidebar TOC */}
        <aside style={{ position: 'sticky', top: 92, display: 'flex', flexDirection: 'column', gap: 0 }}>
          <div style={{ padding: '0 0 14px', borderBottom: `1px solid ${t.hair}`, marginBottom: 10 }}>
            <Mono muted size={10}>CONSOLIDATED</Mono>
            <div style={{ fontSize: 13.5, fontWeight: 600, marginTop: 4 }}>{consolidatedAt}</div>
            <Mono muted size={9.5} style={{ marginTop: 4, display: 'block' }}>VERSION {version}</Mono>
          </div>
          <Mono muted size={10} style={{ padding: '0 0 8px' }}>CONTENTS</Mono>
          {sections.map(s => (
            <a key={s.id} href={`#${s.id}`} onClick={() => setActive(s.id)} style={{
              padding: '8px 10px', textDecoration: 'none', display: 'flex', alignItems: 'baseline', gap: 8,
              color: active === s.id ? t.text : t.textMuted,
              background: active === s.id ? t.surfaceAlt : 'transparent',
              borderLeft: `2px solid ${active === s.id ? t.accent : 'transparent'}`,
              fontSize: 13, lineHeight: 1.35,
            }}>
              <span style={{ fontFamily: 'JetBrains Mono', fontSize: 10.5, letterSpacing: '0.04em', minWidth: 24 }}>§{s.n}</span>
              <span style={{ fontWeight: active === s.id ? 600 : 400 }}>{s.t}</span>
            </a>
          ))}

          <div style={{ marginTop: 18, padding: 14, background: t.surfaceAlt, border: `1px solid ${t.hair}` }}>
            <Mono muted size={9.5}>EXPORT</Mono>
            <div style={{ fontSize: 12, color: t.textMuted, lineHeight: 1.45, marginTop: 6, marginBottom: 10 }}>
              Need an archival copy? Download the PDF stamped with the consolidation date.
            </div>
            <Btn variant="ghost" size="sm" style={{ width: '100%' }}>Download PDF</Btn>
          </div>
        </aside>

        {/* Body */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 40, maxWidth: 720 }}>
          {sections.map(s => (
            <section id={s.id} key={s.id} style={{ scrollMarginTop: 96 }}>
              <header style={{ display: 'flex', alignItems: 'baseline', gap: 14, paddingBottom: 14, borderBottom: `1px solid ${t.hair}`, marginBottom: 18 }}>
                <Mono accent size={12}>§ {s.n}</Mono>
                <h2 style={{ fontSize: 28, fontWeight: 700, letterSpacing: '-0.025em', lineHeight: 1.1, margin: 0 }}>{s.t}</h2>
              </header>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 14, fontSize: 15, lineHeight: 1.6, color: t.text }}>
                {s.body.map((b, i) => {
                  if (b.k === 'p') return <p key={i} style={{ margin: 0 }}>{b.v}</p>;
                  if (b.k === 'ul') return (
                    <ul key={i} style={{ margin: 0, paddingLeft: 0, listStyle: 'none', display: 'flex', flexDirection: 'column', gap: 8 }}>
                      {b.v.map((li, j) => (
                        <li key={j} style={{ display: 'grid', gridTemplateColumns: '24px 1fr', gap: 4, alignItems: 'flex-start' }}>
                          <span style={{ fontFamily: 'JetBrains Mono', fontSize: 12, color: t.textMuted }}>({String.fromCharCode(97 + j)})</span>
                          <span>{li}</span>
                        </li>
                      ))}
                    </ul>
                  );
                  if (b.k === 'note') return (
                    <div key={i} style={{ padding: '12px 14px', background: t.surfaceAlt, borderLeft: `2px solid ${t.brick}`, fontSize: 13.5, lineHeight: 1.55, color: t.text }}>
                      <Mono muted size={9.5} style={{ display: 'block', marginBottom: 4, color: t.brick }}>NOTE</Mono>
                      {b.v}
                    </div>
                  );
                  return null;
                })}
              </div>
            </section>
          ))}

          {/* Closing block */}
          <div style={{ marginTop: 24, padding: '20px 24px', border: `1px dashed ${t.hair}`, display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 16, flexWrap: 'wrap' }}>
            <div style={{ fontSize: 13, color: t.textMuted, lineHeight: 1.5 }}>
              Questions about this document? Email <span style={{ color: t.text, fontWeight: 600 }}>legal@abs.app</span>.
            </div>
            <Mono muted size={9.5}>END OF DOCUMENT</Mono>
          </div>
        </div>
      </div>
    </Page>
  );
};

// — PRIVACY
const PrivacyPage = ({ setRoute }) => (
  <LegalShell
    kicker="POLICY · PRIVACY"
    title="Privacy."
    sub="What we collect, what we don't, and the small set of cases where information leaves your workspace."
    plainSummary="We collect what's needed to run readings — your address inputs, the bylaw fragments we retrieve, and your account info. We never sell anything. We never use your reading history to train models."
    consolidatedAt="May 14, 2026"
    version="2.1"
    sections={[
      { id: 'pv-1', n: '1.0', t: 'Scope', body: [
        { k: 'p', v: 'This policy explains how ABS Reading Inc. ("ABS", "we") handles information when you use our website, the ABS application, or talk to us by email.' },
        { k: 'p', v: 'It applies to everyone — drafter, practice, and developer tier customers — and to visitors of our public site.' },
      ]},
      { id: 'pv-2', n: '2.0', t: 'Information we collect', body: [
        { k: 'p', v: 'We collect three categories of information, all of them in service of producing a reading you can trust:' },
        { k: 'ul', v: [
          'Account information: name, email, organization, billing details.',
          'Reading inputs: addresses, parcel identifiers, and the questions you type into the agent.',
          'Diagnostic data: timing, errors, and the agent\u2019s intermediate steps, so we can debug bad readings.',
        ]},
      ]},
      { id: 'pv-3', n: '3.0', t: 'How we use it', body: [
        { k: 'p', v: 'Information you give us is used to run the agent, deliver readings back to you, bill you, and maintain the service.' },
        { k: 'p', v: 'We may use anonymized, aggregated metrics about which sections of a bylaw are most queried — never with your account attached — to prioritize coverage improvements.' },
        { k: 'note', v: 'We do not use your readings, your questions, or your address inputs to train foundation models. Period.' },
      ]},
      { id: 'pv-4', n: '4.0', t: 'Subprocessors', body: [
        { k: 'p', v: 'We use a small number of subprocessors to operate the service. Each is contractually bound to the same standards we hold ourselves to.' },
        { k: 'ul', v: [
          'Cloud hosting: Amazon Web Services (ca-central-1, Montréal).',
          'Payment processing: Stripe.',
          'Email delivery: Postmark.',
          'Geocoding (HRM-only): Open Address Search + HRM Open Data.',
        ]},
      ]},
      { id: 'pv-5', n: '5.0', t: 'Data location & retention', body: [
        { k: 'p', v: 'Customer data is stored in Canada (ca-central-1) and never leaves Canadian jurisdiction during regular operation. Inference may transit other regions; payloads are encrypted in transit.' },
        { k: 'p', v: 'We retain reading history for the life of your account. You can delete a thread at any time. Deleted data is purged from backups within 35 days.' },
      ]},
      { id: 'pv-6', n: '6.0', t: 'Your rights', body: [
        { k: 'p', v: 'You can request a copy of, or the deletion of, any data we hold about you. We will respond within 14 days.' },
        { k: 'p', v: 'Send the request to privacy@abs.app from the email address associated with the account.' },
      ]},
      { id: 'pv-7', n: '7.0', t: 'Children', body: [
        { k: 'p', v: 'ABS is not intended for, and we do not knowingly collect information from, anyone under the age of 16.' },
      ]},
      { id: 'pv-8', n: '8.0', t: 'Changes to this policy', body: [
        { k: 'p', v: 'We will email any material change at least 30 days before it takes effect. The consolidation date at the top of this page reflects the most recent change.' },
      ]},
    ]}
  />
);

// — TERMS
const TermsPage = ({ setRoute }) => (
  <LegalShell
    kicker="POLICY · TERMS OF USE"
    title="Terms of use."
    sub="The agreement between you and ABS. We have tried to keep it short and to the point. Please read § 4 carefully — it covers the limits of what an ABS reading can be used for."
    plainSummary="ABS produces research, not legal advice. Always verify with the municipality before relying on a reading for a permit decision. We bill monthly, cancel anytime, and won't sue you for using ABS reasonably."
    consolidatedAt="May 14, 2026"
    version="3.0"
    sections={[
      { id: 'tm-1', n: '1.0', t: 'Acceptance', body: [
        { k: 'p', v: 'By creating an account or using the ABS application, you agree to these terms and to our Privacy Policy.' },
        { k: 'p', v: 'If you are accepting on behalf of an organization, you confirm you have authority to bind that organization.' },
      ]},
      { id: 'tm-2', n: '2.0', t: 'The service', body: [
        { k: 'p', v: 'ABS provides an agent that reads consolidated municipal by-laws, applies them to a parcel you supply, and returns a sourced reading.' },
        { k: 'p', v: 'During private beta the service is limited to the Halifax Regional Municipality. We will add jurisdictions per the published Coverage roadmap.' },
      ]},
      { id: 'tm-3', n: '3.0', t: 'Accounts & access', body: [
        { k: 'p', v: 'You are responsible for activity that occurs under your account. Keep your credentials secret. Notify us promptly of any compromise.' },
        { k: 'ul', v: [
          'One person per seat. Sharing a seat is not permitted.',
          'You must be at least 16 years old to hold an account.',
          'We may refuse or close accounts that misuse the service.',
        ]},
      ]},
      { id: 'tm-4', n: '4.0', t: 'Not legal advice', body: [
        { k: 'p', v: 'ABS is research, not legal advice. A reading is a structured retrieval of bylaw text plus the agent\u2019s reasoning. It is not, and should not be relied on as, a development permit, a planning approval, or a legal opinion.' },
        { k: 'note', v: 'Always verify a reading with HRM Planning before submitting a permit application or making an irreversible decision.' },
        { k: 'p', v: 'You agree that ABS is not liable for decisions you take in reliance on a reading without independent verification.' },
      ]},
      { id: 'tm-5', n: '5.0', t: 'Acceptable use', body: [
        { k: 'p', v: 'You may not:' },
        { k: 'ul', v: [
          'Use ABS to scrape, mirror, or rebuild a competing bylaw retrieval service.',
          'Use ABS for anything illegal, defamatory, or harmful.',
          'Attempt to circumvent rate limits, seats, or security controls.',
        ]},
      ]},
      { id: 'tm-6', n: '6.0', t: 'Payment & cancellation', body: [
        { k: 'p', v: 'Paid plans bill monthly in advance, in CAD. You may cancel at any time from the Billing page; the cancellation takes effect at the end of the current billing period.' },
        { k: 'p', v: 'Beta pricing is locked for the first 12 months on any plan started before public launch.' },
      ]},
      { id: 'tm-7', n: '7.0', t: 'Confidentiality', body: [
        { k: 'p', v: 'We treat your reading inputs as confidential. We will only disclose them where required by law and, where lawful, will tell you about the request first.' },
      ]},
      { id: 'tm-8', n: '8.0', t: 'Liability', body: [
        { k: 'p', v: 'To the maximum extent permitted by law, ABS\u2019s aggregate liability for any claim arising out of or related to the service is limited to the amount you paid us in the 12 months before the claim arose.' },
        { k: 'p', v: 'We do not exclude liability for fraud, gross negligence, or anything that cannot be excluded by law.' },
      ]},
      { id: 'tm-9', n: '9.0', t: 'Governing law', body: [
        { k: 'p', v: 'These terms are governed by the laws of the Province of Nova Scotia and the federal laws of Canada applicable therein. The courts of Nova Scotia have exclusive jurisdiction.' },
      ]},
      { id: 'tm-10', n: '10.0', t: 'Changes', body: [
        { k: 'p', v: 'We will email any material change at least 30 days before it takes effect. Continued use of the service after the change takes effect constitutes acceptance.' },
      ]},
    ]}
  />
);

Object.assign(window, { AboutPage, PrivacyPage, TermsPage });
