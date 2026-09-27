import { LegalPage } from "../legal";
import { site } from "@/site";

export const metadata = { title: `Terms of service | ${site.brand}` };

export default function Terms() {
  return (
    <LegalPage title="Terms of service">
      <h2>The service</h2>
      <p>
        {site.brand} is an AI that offers Vedic astrology guidance over WhatsApp. It is not a
        human, and its readings are guidance and entertainment. They are not medical, legal,
        financial or psychological advice, and outcomes are never guaranteed.
      </p>
      <h2>Eligibility</h2>
      <p>You must be 18 or older and use the service for your own chart.</p>
      <h2>Credits and passes</h2>
      <ul>
        <li>
          Credits (&quot;prashnas&quot;) can only be used within {site.brand}. They cannot be
          cashed out or transferred.
        </li>
        <li>{site.brand} tells you the cost before it uses a credit.</li>
        <li>Prices include GST. Validity and expiry are shown at purchase.</li>
      </ul>
      <h2>Acceptable use</h2>
      <p>No abuse, spam, attempts to extract other users&apos; data, or unlawful use.</p>
      <h2>Safety</h2>
      <p>
        If a conversation suggests you may be in danger, {site.brand} will share helpline numbers
        (Tele-MANAS 14416, emergency 112) and may bring in a human from our team.
      </p>
      <h2>Liability</h2>
      <p>To the extent permitted by law, decisions you make based on readings are your own.</p>
      <h2>Governing law</h2>
      <p>These terms are governed by the laws of India. Courts in Kerala have jurisdiction.</p>
    </LegalPage>
  );
}
