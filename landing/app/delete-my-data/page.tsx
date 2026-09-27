import { LegalPage } from "../legal";
import { site, whatsappLink } from "@/site";

export const metadata = { title: `Delete my data | ${site.brand}` };

export default function DeleteMyData() {
  return (
    <LegalPage title="Delete my data">
      <h2>From WhatsApp (instant)</h2>
      <p>
        Send <strong>delete my data</strong> to {site.brand} and confirm. We erase your messages,
        birth details, chart, memories and voice notes, including from backups within our
        retention window.
      </p>
      <p>
        <a className="cta" href={whatsappLink}>
          Open WhatsApp
        </a>
      </p>
      <h2>By email</h2>
      <p>
        No longer have the number? Write to{" "}
        <a href={`mailto:${site.grievanceOfficer.email}`}>{site.grievanceOfficer.email}</a> from
        an address we can verify, with the WhatsApp number used. We will confirm your identity
        before erasing anything.
      </p>
      <h2>What we keep</h2>
      <p>
        Payment and invoice records are kept as required by Indian tax law, stripped of your
        birth details and conversations.
      </p>
      <h2>Stop messages without deleting</h2>
      <p>
        Send <strong>STOP</strong> to stop all messages from {site.brand}.
      </p>
    </LegalPage>
  );
}
