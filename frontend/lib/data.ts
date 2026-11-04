export type Supplier = {
  id: string; name: string; city: string; categories: string[]; capabilities: string[];
  match?: number; verified: boolean; cr: string; qualifications: string[];
};

export type Rfq = {
  id: string; title: string; category: string; city: string; due: string; status: "Open" | "In review" | "Awarded" | "Draft";
  items: number; suppliers: number; quotations: number; buyer: string;
};

export const suppliers: Supplier[] = [
  { id: "SUP-014", name: "Riyadh Industrial Supply Co.", city: "Riyadh", categories: ["Industrial valves", "Water systems"], capabilities: ["Valve assemblies", "Flow control", "Commissioning"], match: 96, verified: true, cr: "1010845276", qualifications: ["ISO 9001", "SASO"] },
  { id: "SUP-027", name: "Gulf Engineering Solutions", city: "Dammam", categories: ["Mechanical", "HVAC"], capabilities: ["Chillers", "Air handling", "Installation"], match: 92, verified: true, cr: "2050146821", qualifications: ["ISO 9001", "Civil Defense"] },
  { id: "SUP-039", name: "Najd Technical Trading", city: "Riyadh", categories: ["Electrical", "Water systems"], capabilities: ["Switchgear", "Cabling", "Piping"], match: 88, verified: true, cr: "1010921740", qualifications: ["SASO", "ISO 14001"] },
  { id: "SUP-046", name: "Arabian Site Services", city: "Jeddah", categories: ["Construction", "Safety"], capabilities: ["Site services", "Equipment supply", "Installation"], match: 84, verified: true, cr: "4030391264", qualifications: ["ISO 45001"] },
  { id: "SUP-053", name: "Eastern Power & Controls", city: "Dammam", categories: ["Electrical", "Industrial"], capabilities: ["Control panels", "Transformers", "Testing"], match: 81, verified: true, cr: "2050194348", qualifications: ["SASO", "ISO 9001"] },
];

export const rfqs: Rfq[] = [
  { id: "RFQ-1026-001", title: "Industrial valves for water treatment project", category: "Mechanical equipment", city: "Riyadh", due: "29 Oct 2026", status: "Open", items: 4, suppliers: 8, quotations: 3, buyer: "Al Noor Infrastructure Co." },
  { id: "RFQ-1026-004", title: "CCTV and access control package", category: "Security systems", city: "Jeddah", due: "03 Nov 2026", status: "In review", items: 6, suppliers: 6, quotations: 2, buyer: "Westline Facilities" },
  { id: "RFQ-1026-008", title: "HVAC equipment for commercial complex", category: "HVAC", city: "Dammam", due: "07 Nov 2026", status: "Open", items: 3, suppliers: 7, quotations: 1, buyer: "Gulf Coast Developments" },
  { id: "RFQ-1026-011", title: "Low-voltage cables and accessories", category: "Electrical materials", city: "Riyadh", due: "17 Oct 2026", status: "Awarded", items: 8, suppliers: 5, quotations: 4, buyer: "Rimal Contracting" },
  { id: "RFQ-1026-014", title: "Structural steel and fixings", category: "Construction supplies", city: "Medina", due: "12 Nov 2026", status: "Draft", items: 2, suppliers: 0, quotations: 0, buyer: "Al Manar Projects" },
];

export const categories = [
  { name: "Construction & infrastructure", count: "Materials · Site services" },
  { name: "Electrical & controls", count: "Cables · Switchgear · Panels" },
  { name: "Mechanical & HVAC", count: "Valves · Chillers · Pumps" },
  { name: "Security systems", count: "CCTV · Access control" },
  { name: "Industrial services", count: "Maintenance · Commissioning" },
  { name: "Water & utilities", count: "Treatment · Piping · Flow control" },
];

export const quotations = [
  { supplier: "Riyadh Industrial Supply Co.", brand: "FlowTek · V-400 Series", fit: "Compliant", unit: 18400, qty: 4, vat: 15, lead: "14 days", city: "Riyadh" },
  { supplier: "Najd Technical Trading", brand: "AquaLine · AV-80", fit: "Compliant with note", unit: 17150, qty: 4, vat: 15, lead: "21 days", city: "Riyadh" },
  { supplier: "Gulf Engineering Solutions", brand: "GulfPro · GV-22", fit: "Alternative offered", unit: 16200, qty: 4, vat: 15, lead: "28 days", city: "Dammam" },
];

export const notifications = [
  { title: "New quotation received", detail: "RFQ-1026-001 · Riyadh Industrial Supply Co.", time: "18 min ago", unread: true },
  { title: "Supplier invitation accepted", detail: "RFQ-1026-004 · Eastern Power & Controls", time: "1 hour ago", unread: true },
  { title: "Company review completed", detail: "Riyadh Industrial Supply Co. · Verified", time: "Yesterday", unread: false },
];

export const fmtSAR = (amount: number) => new Intl.NumberFormat("en-SA", { style: "currency", currency: "SAR", maximumFractionDigits: 0 }).format(amount);

export const demoSteps = [
  { title: "Start with the buyer", body: "Create a material request with clear specifications, quantities and a delivery location." },
  { title: "Review relevant suppliers", body: "See how category, coverage and qualification rules explain each supplier match." },
  { title: "Submit a supplier offer", body: "Respond with a brand or model, technical fit, price, VAT and lead time." },
  { title: "Compare and close", body: "Compare structured quotations, then record the buyer’s decision." },
];
