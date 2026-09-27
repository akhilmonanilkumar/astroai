import { LegalPage } from "../legal";
import { site } from "@/site";

export const metadata = { title: `Privacy notice | ${site.brand}` };

export default function Privacy() {
  const g = site.grievanceOfficer;
  return (
    <LegalPage title="Privacy notice">
      <p>
        This notice explains how {site.company.legalName} (&quot;we&quot;) processes your
        personal data when you use {site.brand} on WhatsApp, under India&apos;s Digital Personal
        Data Protection Act, 2023 and the DPDP Rules, 2025.
      </p>
      <h2>What we collect</h2>
      <ul>
        <li>Your WhatsApp number and profile name.</li>
        <li>Birth details you share: name, date, time and place of birth.</li>
        <li>Your messages and voice notes to {site.brand}, and facts you tell us about your life.</li>
        <li>Payment records (we never see your full card or UPI PIN).</li>
        <li>If you came from an ad: the ad identifier Meta passes to us.</li>
      </ul>
      <h2>Why we use it</h2>
      <ul>
        <li>To calculate your chart and give you personalised readings (the core service).</li>
        <li>To remember your conversations so readings stay consistent.</li>
        <li>To process payments and issue GST invoices.</li>
        <li>To keep you safe, including routing crisis messages to a human.</li>
      </ul>
      <p>We rely on your consent, which you give in the chat before onboarding.</p>
      <h2>Who processes it</h2>
      <p>
        Service providers acting for us: Meta (WhatsApp), our cloud database and hosting in India,
        AI model providers (for generating replies), speech providers (for voice notes) and our
        payment gateway. We do not sell your data.
      </p>
      <h2>Your rights</h2>
      <ul>
        <li>Withdraw consent: send <em>STOP</em>.</li>
        <li>
          Access or erase your data: send <em>delete my data</em> or{" "}
          <a href="/delete-my-data/">use this page</a>.
        </li>
        <li>Correct your details: just tell {site.brand} in the chat.</li>
        <li>Grievances: contact our grievance officer below.</li>
      </ul>
      <h2>Retention</h2>
      <p>
        We keep your data while your account is active. When you delete it, we erase your
        messages, birth details and chart. Payment records are kept as required by tax law.
      </p>
      <h2>Security</h2>
      <p>
        Your data is encrypted in transit and at rest, and birth details are encrypted
        field by field. Staff access is logged.
      </p>
      <h2>Age</h2>
      <p>{site.brand} is only for people aged 18 and over.</p>
      <h2>Grievance officer</h2>
      <p>
        {g.name}, <a href={`mailto:${g.email}`}>{g.email}</a>. We respond within{" "}
        {g.responseDays} days. You may also approach the Data Protection Board of India.
      </p>
    </LegalPage>
  );
}
