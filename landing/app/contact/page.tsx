import { LegalPage } from "../legal";
import { site, whatsappLink } from "@/site";

export const metadata = { title: `Contact | ${site.brand}` };

export default function Contact() {
  const c = site.company;
  const g = site.grievanceOfficer;
  return (
    <LegalPage title="Contact & grievance officer">
      <h2>Support</h2>
      <p>
        The fastest way is to <a href={whatsappLink}>message us on WhatsApp</a> and ask for a
        human. Email: <a href={`mailto:${site.supportEmail}`}>{site.supportEmail}</a>.
      </p>
      <h2>Grievance officer</h2>
      <p>
        {g.name}
        <br />
        <a href={`mailto:${g.email}`}>{g.email}</a>
        <br />
        We acknowledge grievances promptly and resolve them within {g.responseDays} days.
      </p>
      <h2>Company</h2>
      <p>
        {c.legalName}
        <br />
        CIN: {c.cin} · GSTIN: {c.gstin}
        <br />
        {c.address}
      </p>
    </LegalPage>
  );
}
