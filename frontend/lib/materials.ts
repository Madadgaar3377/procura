export type Material = {
  code: string;
  name: string;
  arabicName: string;
  category: string;
  subcategory: string;
  unit: string;
  specification: string;
};

// Representative records for the prototype. Production imports belong to the admin workflow.
export const materials: Material[] = [
  { code: "HVAC-CHW-0150", name: "Butterfly valve, chilled water DN150", arabicName: "صمام فراشة لمياه التبريد DN150", category: "Mechanical & HVAC", subcategory: "Valves", unit: "Each", specification: "Ductile iron body; EPDM liner; PN16; flanged ends; EN 593" },
  { code: "HVAC-AHU-0040", name: "Air handling unit, 4,000 CFM", arabicName: "وحدة مناولة هواء، 4000 قدم مكعب/دقيقة", category: "Mechanical & HVAC", subcategory: "Air handling", unit: "Unit", specification: "Double-skin insulated casing; 400V/3Ph/60Hz; include filters and controls" },
  { code: "ELEC-CBL-LV-0025", name: "Low-voltage cable, 4C x 25 mm²", arabicName: "كابل جهد منخفض، 4 نوى × 25 مم²", category: "Electrical & controls", subcategory: "Cables", unit: "Meter", specification: "Cu/XLPE/PVC; 0.6/1kV; IEC 60502-1; flame-retardant sheath" },
  { code: "SEC-CCTV-FIX-0008", name: "Fixed IP CCTV camera, 8 MP", arabicName: "كاميرا مراقبة شبكية ثابتة، 8 ميجابكسل", category: "Security systems", subcategory: "CCTV", unit: "Each", specification: "Outdoor IP66; IR 50 m; ONVIF Profile S; PoE; H.265" },
  { code: "MECH-PMP-END-0030", name: "End-suction pump, 30 kW", arabicName: "مضخة طرد مركزي أفقية، 30 كيلوواط", category: "Mechanical & HVAC", subcategory: "Pumps", unit: "Set", specification: "Flow and head per approved schedule; 400V/3Ph/60Hz motor; mechanical seal" },
  { code: "CONS-REBAR-0016", name: "Deformed reinforcement bar, 16 mm", arabicName: "قضيب تسليح مضلع، 16 مم", category: "Construction & infrastructure", subcategory: "Steel", unit: "Metric ton", specification: "Grade B500B; compliant with ASTM A615 or approved equivalent; mill certificate" },
  { code: "IND-PLC-PNL-0012", name: "PLC control panel, 12 I/O", arabicName: "لوحة تحكم PLC، 12 مدخلاً/مخرجاً", category: "Electrical & controls", subcategory: "Control panels", unit: "Panel", specification: "IP54 enclosure; 24VDC; labeled terminals; FAT documentation required" },
  { code: "WTR-PIP-HDPE-0110", name: "HDPE pressure pipe, 110 mm", arabicName: "أنبوب ضغط HDPE، 110 مم", category: "Water & utilities", subcategory: "Piping", unit: "Meter", specification: "PE100; SDR11; PN16; ISO 4427; include jointing fittings as scheduled" },
];
