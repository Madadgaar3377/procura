"use client";

import { useRef, useState, type FormEvent } from "react";
import { Button } from "@/components/ui/button";
import { api, json } from "@/lib/api/client";
import { useLocale } from "@/lib/locale";
import { useToast } from "@/features/shared/toast";
import { ApiError } from "@/lib/api/errors";

type Receipt = { reference: string; message: string };

export default function ContactInquiryForm() {
  const { t } = useLocale();
  const toast = useToast();
  const busyRef = useRef(false);
  const [busy, setBusy] = useState(false);
  const [receipt, setReceipt] = useState<Receipt>();
  const [error, setError] = useState("");
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const submissionKey = useRef<string | undefined>(undefined);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busyRef.current) return;
    busyRef.current = true;
    const target = event.currentTarget;
    const form = new FormData(target);
    const value = (key: string) => String(form.get(key) || "").trim();
    setBusy(true);
    setError("");
    setFieldErrors({});
    submissionKey.current ||= crypto.randomUUID();
    try {
      const result = await api<Receipt>("contact", {
        method: "POST",
        body: json({ name: value("name"), email: value("email"), company: value("company") || null, phone: value("phone") || null, topic: value("user_type"), user_type: value("user_type"), category: value("category"), subject: value("subject"), message: value("message"), consent: form.get("consent") === "on", website: value("website") || null, submission_key: submissionKey.current }),
      });
      setReceipt(result);
      submissionKey.current = undefined;
      target.reset();
      toast.success(t("Enquiry received"), result.message);
    } catch (cause) {
      const detail = cause instanceof Error ? cause.message : t("We could not send your enquiry.");
      if (cause instanceof ApiError) setFieldErrors(cause.fieldErrors);
      setError(t(detail));
      toast.error(t("Enquiry not sent"), t(detail));
    } finally {
      busyRef.current = false;
      setBusy(false);
    }
  }
  const fieldError = (name: string) => fieldErrors[name] ? <small className="field-error" role="alert">{t(fieldErrors[name])}</small> : null;

  return <section className="container public-content live-contact">
    <div><span className="section-label">{t("BUSINESS SUPPORT")}</span><h1>{t("Talk with the Procumaterial team")}</h1><p>{t("Contact our team for help with your account, procurement requests, supplier participation, or a general business enquiry.")}</p><div className="contact-details"><a href="mailto:support@procumaterial.com">support@procumaterial.com</a><a href="tel:0501496516">0501496516</a></div><p className="contact-response-note">{t("Your enquiry is stored securely and sent to our support team. We will send a confirmation to your email.")}</p></div>
    {receipt ? <div className="notice success" role="status"><strong>{t("Enquiry received")}</strong><p>{t(receipt.message)}</p><small>{t("Reference")}: {receipt.reference}</small><Button type="button" variant="outline" onClick={() => setReceipt(undefined)}>{t("Send another enquiry")}</Button></div> : <form className="panel live-form" onSubmit={submit}>
      <div className="form-grid">
        <label className="contact-honeypot" aria-hidden="true">Website<input name="website" tabIndex={-1} autoComplete="off"/></label>
        <label className="field">{t("Full name")}<input name="name" autoComplete="name" required minLength={2} maxLength={150} aria-invalid={!!fieldErrors.name}/>{fieldError("name")}</label>
        <label className="field">{t("Email address")}<input name="email" type="email" autoComplete="email" required maxLength={200} aria-invalid={!!fieldErrors.email}/>{fieldError("email")}</label>
        <label className="field">{t("Phone number (optional)")}<input name="phone" type="tel" autoComplete="tel" maxLength={30} pattern="[+0-9() .-]{7,30}" title={t("Enter a valid phone number")} aria-invalid={!!fieldErrors.phone}/>{fieldError("phone")}</label>
        <label className="field">{t("Company name (optional)")}<input name="company" autoComplete="organization" maxLength={220}/></label>
        <label className="field">{t("I am a")}<select name="user_type" required defaultValue="other"><option value="buyer">{t("Buyer")}</option><option value="supplier">{t("Supplier")}</option><option value="other">{t("Other")}</option></select></label>
        <label className="field">{t("Inquiry category")}<select name="category" required defaultValue="general_inquiry">{[["general_inquiry","General Inquiry"],["buyer_support","Buyer Support"],["supplier_support","Supplier Support"],["account_verification","Account & Verification"],["rfq_quotations","RFQ & Quotations"],["technical_support","Technical Support"],["other","Other"]].map(([value,label])=><option key={value} value={value}>{t(label)}</option>)}</select></label>
        <label className="field contact-message">{t("Subject")}<input name="subject" required minLength={3} maxLength={200} aria-invalid={!!fieldErrors.subject}/>{fieldError("subject")}</label>
        <label className="field contact-message">{t("Message")}<textarea name="message" required minLength={20} maxLength={5000} rows={6} aria-invalid={!!fieldErrors.message}/>{fieldError("message")}</label>
      </div>
      <label className="contact-consent"><input type="checkbox" name="consent" required/><span>{t("I agree that Procumaterial may use these details to respond to this enquiry.")}</span></label>
      {error && <p className="notice" role="alert">{error}</p>}
      <Button disabled={busy} type="submit" aria-busy={busy}>{t(busy ? "Sending..." : "Send enquiry")}</Button>
    </form>}
  </section>;
}
