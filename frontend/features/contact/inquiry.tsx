"use client";

import { useState, type FormEvent } from "react";
import { Button } from "@/components/ui/button";
import { api, json } from "@/lib/api/client";
import { useLocale } from "@/lib/locale";
import { useToast } from "@/features/shared/toast";

type Receipt = { reference: string; message: string };

export default function ContactInquiryForm() {
  const { t } = useLocale();
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const [receipt, setReceipt] = useState<Receipt>();
  const [error, setError] = useState("");

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    const target = event.currentTarget;
    const form = new FormData(target);
    const value = (key: string) => String(form.get(key) || "").trim();
    setBusy(true);
    setError("");
    try {
      const result = await api<Receipt>("contact", {
        method: "POST",
        body: json({ name: value("name"), email: value("email"), company: value("company") || null, phone: value("phone") || null, topic: value("topic"), message: value("message"), consent: form.get("consent") === "on" }),
      });
      setReceipt(result);
      target.reset();
      toast.success(t("Enquiry received"), result.message);
    } catch (cause) {
      const detail = cause instanceof Error ? cause.message : t("We could not send your enquiry.");
      setError(t(detail));
      toast.error(t("Enquiry not sent"), t(detail));
    } finally {
      setBusy(false);
    }
  }

  return <section className="container public-content live-contact">
    <div><span className="section-label">{t("BUSINESS SUPPORT")}</span><h1>{t("Talk with the Procura team")}</h1><p>{t("Send a business enquiry and tell us how your organization plans to participate.")}</p><p className="contact-response-note">{t("Your enquiry is stored securely for the platform team to review. This form does not send email or SMS.")}</p></div>
    {receipt ? <div className="notice success" role="status"><strong>{t("Enquiry received")}</strong><p>{t(receipt.message)}</p><small>{t("Reference")}: {receipt.reference}</small><Button type="button" variant="outline" onClick={() => setReceipt(undefined)}>{t("Send another enquiry")}</Button></div> : <form className="panel live-form" onSubmit={submit}>
      <div className="form-grid">
        <label className="field">{t("Your name")}<input name="name" autoComplete="name" required minLength={2} maxLength={150}/></label>
        <label className="field">{t("Business email")}<input name="email" type="email" autoComplete="email" required maxLength={200}/></label>
        <label className="field">{t("Company")}<input name="company" autoComplete="organization" maxLength={220}/></label>
        <label className="field">{t("Mobile number")}<input name="phone" type="tel" autoComplete="tel" maxLength={30}/></label>
        <label className="field">{t("Enquiry type")}<select name="topic" required defaultValue=""><option value="" disabled>{t("Select...")}</option>{[["buyer","Buyer workspace"],["supplier","Supplier participation"],["partnership","Partnership"],["support","Account support"],["other","Other"]].map(([value,label])=><option key={value} value={value}>{t(label)}</option>)}</select></label>
        <label className="field contact-message">{t("How can we help?")}<textarea name="message" required minLength={20} maxLength={5000} rows={6}/></label>
      </div>
      <label className="contact-consent"><input type="checkbox" name="consent" required/><span>{t("I agree that Procura may use these details to respond to this enquiry.")}</span></label>
      {error && <p className="notice" role="alert">{error}</p>}
      <Button disabled={busy} type="submit">{t(busy ? "Sending..." : "Send enquiry")}</Button>
    </form>}
  </section>;
}
