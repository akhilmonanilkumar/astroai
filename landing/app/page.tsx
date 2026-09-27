import { site, whatsappLink } from "@/site";

// Prices mirror the `app_config` seed in supabase/migrations; keep them in sync until the
// landing page reads them at build time.
const packs = [
  { price: 51, prashnas: 10 },
  { price: 101, prashnas: 25, badge: "Most chosen" },
  { price: 251, prashnas: 70 },
  { price: 501, prashnas: 160 },
];
const passes = [
  { name: "Monthly", price: "₹199" },
  { name: "Quarterly", price: "₹501" },
  { name: "Yearly", price: "₹1,501" },
];

function Cta() {
  return (
    <>
      <a className="cta" href={whatsappLink} rel="noopener">
        Chat with {site.brand} on WhatsApp
      </a>
      <p className="note">
        {site.brand} is an AI astrologer, not a human. 18+ only.
      </p>
    </>
  );
}

export default function Home() {
  return (
    <>
      <section className="hero">
        <h1>A personal Vedic astrologer who remembers you</h1>
        <p>
          Ask about career, marriage, health worries or timing, in Hindi, English or Hinglish,
          by text or voice note. Readings are based on your real kundli. No app to install.
        </p>
        <Cta />
      </section>

      <section>
        <h2>How it works</h2>
        <div className="card">
          <ol className="steps">
            <li>Say namaste on WhatsApp.</li>
            <li>Share your birth date, time and place, one simple question at a time.</li>
            <li>Get your first full kundli reading, free.</li>
            <li>Ask anything, anytime. {site.brand} remembers your chart and your story.</li>
          </ol>
        </div>
      </section>

      <section>
        <h2>Honest by design</h2>
        <div className="grid">
          <div className="card">
            <strong>Always AI, never pretending</strong>
            <p>{site.brand} tells you up front that it is an AI and never claims to be human.</p>
          </div>
          <div className="card">
            <strong>No fear-selling</strong>
            <p>No scary predictions to push remedies, and no guaranteed outcomes.</p>
          </div>
          <div className="card">
            <strong>Your data, your call</strong>
            <p>
              Type <em>delete my data</em> any time, or use{" "}
              <a href="/delete-my-data/">this page</a>.
            </p>
          </div>
        </div>
      </section>

      <section>
        <h2>Pricing</h2>
        <p>Your first reading and a few questions are free. After that, pick what suits you.</p>
        <div className="card">
          <table>
            <thead>
              <tr>
                <th>Dakshina pack</th>
                <th>Prashnas*</th>
              </tr>
            </thead>
            <tbody>
              {packs.map((p) => (
                <tr key={p.price}>
                  <td>
                    ₹{p.price} {p.badge ? <small>· {p.badge}</small> : null}
                  </td>
                  <td>{p.prashnas}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="note">
            *A prashna is one question plus up to 3 follow-ups on the same topic. A voice reply
            uses 2. Prices include GST.
          </p>
        </div>
        <div className="card" style={{ marginTop: 12 }}>
          <strong>Guru Plus</strong>: up to 5 prashnas a day, voice replies, a monthly forecast
          and timely alerts.{" "}
          {passes.map((p) => `${p.name} ${p.price}`).join(" · ")}
        </div>
      </section>

      <section>
        <h2>FAQ</h2>
        <details>
          <summary>Is {site.brand} a real astrologer?</summary>
          <p>
            No. {site.brand} is an AI trained to read Vedic charts. Your chart is calculated with
            precise astronomical data, and the AI explains what it means for you.
          </p>
        </details>
        <details>
          <summary>What if I don&apos;t know my birth time?</summary>
          <p>You can still get readings based on your Moon sign and nakshatra.</p>
        </details>
        <details>
          <summary>Can it predict health or money outcomes?</summary>
          <p>
            It offers astrological guidance only. It does not give medical diagnoses, trading
            tips, legal verdicts or predictions about death.
          </p>
        </details>
        <details>
          <summary>How do I pay?</summary>
          <p>Right inside WhatsApp with UPI, card or netbanking.</p>
        </details>
      </section>

      <section>
        <Cta />
      </section>
    </>
  );
}
