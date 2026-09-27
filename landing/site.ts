// Single source for brand/legal details. Open items from the product plan are marked TODO.
export const site = {
  brand: "Guruji", // TODO: final brand name + domain
  domain: "example.in", // TODO
  // TODO: real WhatsApp Business number (digits only, with country code)
  whatsappNumber: "910000000000",
  whatsappPrefill: "Namaste Guruji",
  company: {
    legalName: "TODO Private Limited", // pending MCA SPICe+ incorporation
    cin: "TODO",
    gstin: "TODO",
    address: "TODO, Kerala, India",
  },
  supportEmail: "support@example.in", // TODO
  grievanceOfficer: {
    name: "TODO",
    email: "grievance@example.in", // TODO
    responseDays: 30,
  },
  lastUpdated: "2026-09-28",
} as const;

export const whatsappLink = `https://wa.me/${site.whatsappNumber}?text=${encodeURIComponent(
  site.whatsappPrefill,
)}`;
