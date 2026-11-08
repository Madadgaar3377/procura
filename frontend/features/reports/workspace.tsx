"use client";

import { useMemo } from "react";
import { Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { IconDownload, IconRefresh } from "@tabler/icons-react";
import { Button } from "@/components/ui/button";
import { Heading, State } from "@/features/shared/ui";
import { useApi } from "@/lib/api/use-api";
import { useLocale } from "@/lib/locale";
import { useToast } from "@/features/shared/toast";
import { useSession } from "@/features/auth/session";
import type { Schemas } from "@/lib/api/generated/schema";

type Workspace = "buyer" | "supplier" | "admin";

export default function Reports({ workspace }: { workspace: Workspace }) {
  const { t, arabic } = useLocale();
  const { org } = useSession();
  const toast = useToast();
  const scope = workspace === "admin" || !org ? "" : `&org_id=${encodeURIComponent(org.id)}`;
  const report = useApi<Schemas["ReportSummary"]>(`reports/summary?workspace=${workspace}${scope}`);
  const data = report.data;
  const number = useMemo(() => new Intl.NumberFormat(arabic ? "ar-SA" : "en-SA"), [arabic]);
  const month = useMemo(() => new Intl.DateTimeFormat(arabic ? "ar-SA-u-ca-gregory" : "en-SA", { month: "short", year: "2-digit", timeZone: "UTC" }), [arabic]);
  const chartData = (data?.activity || []).map((point) => ({
    ...point,
    monthLabel: month.format(new Date(`${point.month}-01T00:00:00Z`)),
  }));
  const hasHistory=chartData.some(point=>point.rfqs>0||point.invitations>0||point.quotations_submitted>0);

  function exportCsv() {
    if (!data) return;
    const rows: (string | number)[][] = [
      ["month", "rfqs", "invitations", "quotations_submitted"],
      ...data.activity.map((point) => [point.month, point.rfqs, point.invitations, point.quotations_submitted]),
    ];
    const csv = `\uFEFF${rows.map((row) => row.map((value) => `"${String(value).replaceAll('"', '""')}"`).join(",")).join("\r\n")}`;
    const url = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8" }));
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `procura-${workspace}-activity.csv`;
    anchor.click();
    URL.revokeObjectURL(url);
    toast.success(t("Report downloaded"));
  }

  const metrics = [
    { label: workspace === "admin" ? "RFQs on platform" : "RFQs created", value: data?.rfqs_created ?? 0 },
    { label: workspace === "buyer" ? "Supplier invitations sent" : "RFQ invitations received", value: data?.invitations ?? 0 },
    { label: "Invitations accepted", value: data?.invitations_accepted ?? 0 },
    { label: workspace === "buyer" ? "Quotations received" : "Quotations submitted", value: data?.quotations_submitted ?? 0 },
    ...(workspace === "supplier" ? [{ label: "Quotation drafts", value: data?.quotation_drafts ?? 0 }] : []),
  ];

  return <section className="reports-workspace">
    <div className="reports-heading">
      <Heading title={workspace === "admin" ? t("Platform reports") : t("Performance reports")} description="Recent activity from your authorized Procumaterial workspace." />
      <div className="reports-actions">
        <Button type="button" variant="outline" disabled={report.loading} onClick={report.reload}><IconRefresh size={16}/>{t("Refresh")}</Button>
        <Button type="button" variant="outline" disabled={!data} onClick={exportCsv}><IconDownload size={16}/>{t("Export CSV")}</Button>
      </div>
    </div>
    <State loading={report.loading} error={report.error} retry={report.reload}/>
    {data && <>
      <div className="reports-metrics">
        {metrics.map((metric) => <article className="stat-card" key={metric.label}>
          <span className="stat-top">{t(metric.label)}</span>
          <strong className="stat-value">{number.format(metric.value)}</strong>
        </article>)}
      </div>
      <section className="panel reports-chart-panel">
        <div className="reports-chart-heading"><div><h2>{t("Six-month activity")}</h2><p>{t("Activity recorded by the backend for this workspace.")}</p></div><span className="reports-period">{t("Last six months")}</span></div>
        {!hasHistory&&<p className="reports-empty" role="status">{t("There is no reportable activity yet. Create a request or complete an invitation to start building your report.")}</p>}
        {hasHistory&&<><div className="reports-chart" role="img" aria-label={t("Six-month RFQ, invitation and submitted quotation activity chart")}>
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={chartData} margin={{ top: 12, right: 8, left: -16, bottom: 0 }}>
              <defs>
                <linearGradient id="report-rfq" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="#4c7198" stopOpacity={0.2}/><stop offset="95%" stopColor="#4c7198" stopOpacity={0}/></linearGradient>
                <linearGradient id="report-invite" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="#4b8a70" stopOpacity={0.17}/><stop offset="95%" stopColor="#4b8a70" stopOpacity={0}/></linearGradient>
              </defs>
              <CartesianGrid stroke="#e9edf2" strokeDasharray="3 5" vertical={false}/>
              <XAxis dataKey="monthLabel" tickLine={false} axisLine={false} tick={{ fill: "#8793a3", fontSize: 11 }}/>
              <YAxis allowDecimals={false} tickLine={false} axisLine={false} tick={{ fill: "#8793a3", fontSize: 11 }}/>
              <Tooltip formatter={(value) => number.format(Number(value ?? 0))} contentStyle={{ border: "1px solid #e3e8ef", borderRadius: 6, fontSize: 12 }}/>
              <Area type="monotone" dataKey="rfqs" name={t("RFQs")} stroke="#4c7198" strokeWidth={2} fill="url(#report-rfq)"/>
              <Area type="monotone" dataKey="invitations" name={t("Invitations")} stroke="#4b8a70" strokeWidth={2} fill="url(#report-invite)"/>
              <Area type="monotone" dataKey="quotations_submitted" name={t("Quotations submitted")} stroke="#c18c4d" strokeWidth={2} fill="transparent"/>
            </AreaChart>
          </ResponsiveContainer>
        </div>
        <div className="reports-legend"><span><i className="rfq"/>{t("RFQs")}</span><span><i className="invitation"/>{t("Invitations")}</span><span><i className="quotation"/>{t("Quotations submitted")}</span></div></>}
      </section>
    </>}
  </section>;
}
