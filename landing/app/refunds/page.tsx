import { LegalPage } from "../legal";
import { site } from "@/site";

export const metadata = { title: `Refunds & cancellation | ${site.brand}` };

export default function Refunds() {
  return (
    <LegalPage title="Refunds & cancellation">
      <h2>Automatic credit refunds</h2>
      <p>
        If an answer fails, or you mark an answer with a thumbs-down, the credit is returned to
        your balance automatically.
      </p>
      <h2>Payment refunds</h2>
      <p>
        If you were charged but did not receive credits, or were charged twice, write to{" "}
        <a href={`mailto:${site.supportEmail}`}>{site.supportEmail}</a> or tell {site.brand} in
        the chat. Verified refunds go back to the original payment method within 5–7 working
        days.
      </p>
      <h2>Unused credits</h2>
      <p>
        Unused credits are not refundable as cash, except where the law requires it or the
        service is discontinued.
      </p>
      <h2>Guru Plus and Autopay</h2>
      <ul>
        <li>Prepaid passes run until the end of their period and do not auto-renew.</li>
        <li>
          If you set up UPI Autopay, you get a notice before each debit. Send{" "}
          <em>cancel subscription</em> any time to revoke the mandate. Access continues until
          the paid period ends.
        </li>
      </ul>
    </LegalPage>
  );
}
