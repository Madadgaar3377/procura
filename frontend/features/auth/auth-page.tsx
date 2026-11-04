"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";
import {InternationalPhone} from "@/components/ui/international-phone";
import Image from "next/image";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { IconArrowLeft, IconCheck, IconEye, IconEyeOff, IconLock, IconShieldCheck } from "@tabler/icons-react";
import { verificationPath } from "./verification";
import { ApiError } from "@/lib/api/errors";
import { AppLoadingScreen } from "@/features/shared/loading";
import { FieldError } from "@/features/shared/field-error";
import { identifierError, passwordError } from "@/lib/validation";
import { api, json } from "@/lib/api/client";
import { clearApiCache } from "@/lib/api/use-api";
import { Button } from "@/components/ui/button";
import type { Schemas } from "@/lib/api/generated/schema";
import { useLocale } from "@/lib/locale";
import { useToast } from "@/features/shared/toast";
import { ResetSuccess } from "./reset-success";

const ACCOUNT_TYPE = "procura.pending-account-type";
const PENDING_PHONE = "procura.pending-verification-phone";
const VERIFICATION_EMAIL = "procura.pending-verification-email";
const PENDING_EMAIL = "procura.pending-reset-email";

export default function LiveAuth({ type }: { type: string }) {
  const router = useRouter();
  const { t, arabic, setArabic } = useLocale();
  const toast = useToast();
  const autoRequested = useRef(false);
  const [accountType,setAccountType]=useState<"buyer"|"supplier">("buyer");
  useEffect(()=>{if(type==="register"){const role=new URLSearchParams(window.location.search).get("role");if(role==="buyer"||role==="supplier")setAccountType(role);}},[type]);
  const [checkingSession, setCheckingSession] = useState(type === "login");
  const [redirecting, setRedirecting] = useState(false);
  const [verificationReady, setVerificationReady] = useState(!['verify-mobile','verify-email'].includes(type));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [fieldErrors,setFieldErrors]=useState<Record<string,string>>({});
  const [message, setMessage] = useState("");
  const [phone, setPhone] = useState("");
  const [phoneIdentifier,setPhoneIdentifier]=useState(false);
  const [identifierPhone,setIdentifierPhone]=useState("");
  const [emailAddress, setEmailAddress] = useState("");
  const [resetToken, setResetToken] = useState("");
  const resetStep = useRef("");
  const submitting = useRef(false);
  useEffect(() => {
    if (resetStep.current === type) return;
    resetStep.current = type;
    setResetToken("");
    setResetComplete(false);
    if (type === "reset-password") {
      const token = new URLSearchParams(window.location.hash.slice(1)).get("token");
      if (token && /^[a-f0-9]{64}$/.test(token)) setResetToken(token);
      if (window.location.hash) window.history.replaceState(null, "", window.location.pathname);
      const remaining = Math.ceil((Number(window.sessionStorage.getItem("procura.recovery-resend-at")) - Date.now()) / 1000);
      if (remaining > 0) setResendIn(Math.min(900, remaining));
    }
  }, [type]);
  const [resendIn, setResendIn] = useState(0);
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [showConfirmPassword, setShowConfirmPassword] = useState(false);
  const [resetComplete, setResetComplete] = useState(false);

  const passwordScore = password
    ? Number(password.length >= 6) + Number(password.length >= 16) + Number(password.length >= 20)
      + Number([/[a-z]/.test(password), /[A-Z]/.test(password), /[0-9]/.test(password), /[^A-Za-z0-9]/.test(password)].filter(Boolean).length >= 2)
      + Number([/[a-z]/.test(password), /[A-Z]/.test(password), /[0-9]/.test(password), /[^A-Za-z0-9]/.test(password)].filter(Boolean).length >= 3)
    : 0;
  const passwordStrength = password.length < 6 ? "Too short" : passwordScore >= 5 ? "Strong" : passwordScore >= 4 ? "Good" : passwordScore >= 3 ? "Fair" : "Meets minimum";

  const register = type === "register";
  const verify = type === "verify-mobile";
  const forgot = type === "forgot-password";
  const reset = type === "reset-password";
  const email = type === "verify-email";
  useEffect(() => {
    if (type !== "login") return;
    let active = true;
    setCheckingSession(true);
    // Persistent cookies restore the session, including refreshing expired access.
    void (async () => {
      try {
        const user = await api<Schemas["UserOut"]>("auth/me");
        const destination = verificationPath(user);
        if (destination) {
          if (active) router.replace(destination);
          return;
        }
        if (user.is_admin) {
          if (active) router.replace("/app/admin/dashboard");
          return;
        }
        const organizations = await api<Schemas["OrganizationOut"][]>("organizations");
        const buyer = organizations.some(org => org.is_active && org.is_buyer);
        const supplier = organizations.some(org => org.is_active && org.is_supplier);
        if (active) router.replace(buyer && !supplier ? "/app/buyer/dashboard" : supplier && !buyer ? "/app/supplier/dashboard" : "/organizations");
      } catch {
        // Anonymous users and unavailable sessions can still use the login form.
        if (active) setCheckingSession(false);
      }
    })();
    return () => { active = false; };
  }, [type, router]);
  const title = register ? "Create your account" : verify ? "Verify your mobile" : forgot ? "Recover your password" : reset ? "Reset your password" : email ? "Verify your email" : "Welcome back";

  async function requestCode(recipient?: string) {
    if (busy || submitting.current) return;
    const destination = email ? recipient || emailAddress : recipient || phone;
    if (!destination) return;
    const cooldownKey = `procura.verification-resend:${email?'email':'phone'}:${destination}`;
    try {
      const seconds = Math.ceil((Number(sessionStorage.getItem(cooldownKey))-Date.now())/1000);
      if(seconds>0){setResendIn(Math.min(900,seconds));return;}
    } catch { /* Server rate limits still apply when storage is unavailable. */ }
    submitting.current = true;
    setBusy(true);
    setError("");
    setMessage("");
    try {
      const route = email ? "auth/email/verification/request" : `auth/otp/request?phone=${encodeURIComponent(destination)}`;
      const result = await api<{ message: string }>(route, { method: "POST" });
      setMessage(result.message);
      setResendIn(60);
      try { sessionStorage.setItem(cooldownKey,String(Date.now()+60000)); } catch { /* Optional cooldown persistence. */ }
      toast.success(email ? "Verification email requested" : "Verification code requested", result.message);
    } catch (cause) {
      const detail = cause instanceof Error ? cause.message : "Request failed";
      setError(detail);
      if(cause instanceof ApiError&&cause.status===429){
        const seconds=Math.min(900,Math.max(1,Number(cause.retryAfter)||60));setResendIn(seconds);
        try { sessionStorage.setItem(cooldownKey,String(Date.now()+seconds*1000)); } catch { /* Server enforces cooldowns. */ }
      }
      toast.error("We could not request a code", detail);
    } finally {
      submitting.current = false;
      setBusy(false);
    }
  }

  useEffect(() => {
    if (resendIn <= 0) return;
    const timer = window.setTimeout(() => setResendIn((seconds) => Math.max(0, seconds - 1)), 1000);
    return () => window.clearTimeout(timer);
  }, [resendIn]);

  useEffect(() => {
    if (autoRequested.current) return;
    if (verify) {
      autoRequested.current = true;
      let pendingPhone='',pendingEmail='';
      try { pendingPhone=sessionStorage.getItem(PENDING_PHONE)||'';pendingEmail=sessionStorage.getItem(VERIFICATION_EMAIL)||''; } catch { /* Recover signed-in account below. */ }
      if(pendingPhone){
        setEmailAddress(pendingEmail);setPhone(pendingPhone);setVerificationReady(true);void requestCode(pendingPhone);
      } else {
        void api<Schemas['UserOut']>('auth/me').then(user=>{
          if(user.phone_verified){router.replace(verificationPath(user)||'/login');return;}
          setPhone(user.phone);setEmailAddress(user.email||'');setVerificationReady(true);void requestCode(user.phone);
        }).catch(cause=>{
          setVerificationReady(true);
          if(!(cause instanceof ApiError&&cause.status===401))setError(cause instanceof Error?cause.message:'Request failed');
        });
      }
    } else if (email) {
      autoRequested.current = true;
      void api<Schemas["UserOut"]>("auth/me").then(user => {
        const destination=verificationPath(user);
        if(destination!=='/verify-email'){router.replace(destination||'/login');return;}
        setEmailAddress(user.email||'');setVerificationReady(true);void requestCode(user.email||'');
      }).catch(cause=>{
        if(cause instanceof ApiError&&cause.status===401)router.replace('/login');
        else setError(cause instanceof Error?cause.message:'Request failed');
      });
    } else if (reset) {
      const pendingEmail = window.sessionStorage.getItem(PENDING_EMAIL);
      if (pendingEmail) {
        setEmailAddress(pendingEmail);
        
      }
    }
    // Run once when an auth step is opened; verification delivery is queued by the backend.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [type]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy || submitting.current) return;
    const form = new FormData(event.currentTarget);
    const value = (key: string) => String(form.get(key) || "");
    if ((register || (reset && resetToken)) && value("password") !== value("confirm_password")) {
      setFieldErrors({confirm_password:"Your passwords do not match."});
      setError("Your passwords do not match.");
      toast.error("Check your password", "Your passwords do not match.");
      return;
    }
    const errors:Record<string,string>={};
    if(register){
      if(!/^[a-z0-9_][a-z0-9_.-]{2,79}$/i.test(value("username").trim()))errors.username="Username cannot contain @. Use 3-80 letters, digits, dots, hyphens or underscores.";
      const emailIssue=identifierError(value("email"));if(!value("email").includes('@')||emailIssue)errors.email="Invalid email address.";
    }
    if(register||verify){const issue=identifierError(value("phone"));if(issue||value("phone").includes('@'))errors.phone="Use a valid Saudi mobile number (+966).";}
    if(!register&&!verify&&!email){const issue=identifierError(value("identifier"));if(issue&&!resetToken)errors.identifier=issue;}
    if(register||(reset&&resetToken)){const issue=passwordError(value("password"));if(issue)errors.password=issue;}
    if((verify||email||(reset&&!resetToken))&&!/^[0-9]{6}$/.test(value("code")))errors.code="Enter a six-digit verification code.";
    setFieldErrors(errors);if(Object.keys(errors).length){setError("Check the highlighted fields.");return;}
    submitting.current = true;
    setBusy(true);
    setError("");
    setMessage("");
    try {
      if (register) {
        const pendingPhone = value("phone");
        const payload: Schemas["UserRegister"] = {
          username: value("username"),
          phone: pendingPhone,
          phone_country: value("phone_country") || null,
          email: value("email") || null,
          password: value("password"),
        };
        await api("auth/register", { method: "POST", body: json(payload) });
        window.sessionStorage.setItem(PENDING_PHONE, pendingPhone);
        window.sessionStorage.setItem(VERIFICATION_EMAIL, value("email"));
        window.sessionStorage.setItem(ACCOUNT_TYPE,accountType);
        toast.success("Account created", "We are taking you to mobile verification now.");
        router.replace("/verify-mobile");
      } else if (verify) {
        await api("auth/otp/verify", { method: "POST", body: json({ phone: value("phone"), code: value("code") }) });
        try { sessionStorage.removeItem(PENDING_PHONE);sessionStorage.removeItem(VERIFICATION_EMAIL); } catch { /* Verification is persisted by the API. */ }
        toast.success("Account verified", "Sign in to continue to your workspace.");
        clearApiCache();
        try {
          const user=await api<Schemas['UserOut']>('auth/me');
          router.replace(verificationPath(user)||'/login');
        } catch { router.replace('/login'); }
      } else if (forgot) {
        const address = value("identifier").trim();
        const result = await api<{ message: string }>("auth/password/forgot", { method: "POST", body: json({ identifier: address, phone_country: value("identifier_country") || null }) });
        window.sessionStorage.setItem(PENDING_EMAIL, address);
        window.sessionStorage.setItem("procura.recovery-resend-at", String(Date.now() + 60000));
        toast.info("Check your messages", result.message);
        router.replace("/reset-password");
      } else if (reset) {
        if (!resetToken) {
          const result = await api<{reset_token:string}>("auth/password/verify", {method:"POST",body:json({identifier:value("identifier"),phone_country:value("identifier_country") || null,code:value("code")})});
          setResetToken(result.reset_token);
          setMessage("Code verified. Enter your new password.");
          return;
        }
        await api("auth/password/reset", { method: "POST", body: json({ reset_token: resetToken, new_password: value("password"), confirm_password: value("confirm_password") }) });
        window.sessionStorage.removeItem(PENDING_EMAIL);
        toast.success("Password updated", "Sign in with your new password.");
        setResetToken("");
        setResetComplete(true);
      } else if (email) {
        await api("auth/email/verification/confirm", { method: "POST", body: json({ code: value("code") }) });
        toast.success("Email verified", "Your business email is confirmed.");
        const user = await api<Schemas["UserOut"]>("auth/me");
        clearApiCache();
        router.replace(verificationPath(user)||(user.is_admin ? "/app/admin/dashboard" : "/organizations"));
      } else {
        await api("auth/login", { method: "POST", body: json({ identifier: value("identifier"), phone_country: value("identifier_country") || null, password: value("password") }) });
        const user = await api<Schemas["UserOut"]>("auth/me");
        toast.success("Welcome back", "Your secure session is ready.");
        setRedirecting(true);
        const destination = verificationPath(user);
        if (destination) {
          router.replace(destination);
          return;
        }
        if (user.is_admin) {
          router.replace("/app/admin/dashboard");
        } else {
          const organizations = await api<Schemas["OrganizationOut"][]>("organizations");
          const preference=window.sessionStorage.getItem(ACCOUNT_TYPE);
          window.sessionStorage.removeItem(ACCOUNT_TYPE);
          if((preference==="buyer"||preference==="supplier")&&!organizations.some(org=>org.is_active&&(preference==="buyer"?org.is_buyer:org.is_supplier))){router.replace(`/app/${preference}/company`);return;}
          const buyer = organizations.some((org) => org.is_active && org.is_buyer);
          const supplier = organizations.some((org) => org.is_active && org.is_supplier);
          router.replace(buyer && !supplier ? "/app/buyer/dashboard" : supplier && !buyer ? "/app/supplier/dashboard" : "/organizations");
        }
      }
    } catch (cause) {
      setRedirecting(false);
      if (type === "login" && cause instanceof ApiError && cause.status === 403 && cause.detail && typeof cause.detail === "object" && "code" in cause.detail && cause.detail.code === "phone_verification_required" && "phone" in cause.detail && typeof cause.detail.phone === "string") {
        router.replace(verificationPath({phone:cause.detail.phone,email:'email' in cause.detail&&typeof cause.detail.email==='string'?cause.detail.email:null,phone_verified:false,email_verified:false})||'/verify-mobile');
        return;
      }
      const detail = cause instanceof Error ? cause.message : "Request failed";
      setError(detail);
      if(cause instanceof ApiError){
        setFieldErrors(cause.fieldErrors);
        if(cause.status===429)setResendIn(Math.min(900,Math.max(1,Number(cause.retryAfter)||60)));
        if(reset && resetToken && cause.status===400){setResetToken("");setPassword("");}
      }
      toast.error("We could not complete that step", detail);
    } finally {
      submitting.current = false;
      setBusy(false);
    }
  }

  async function resendRecovery() {
    if (busy || submitting.current || resendIn > 0 || !emailAddress) return;
    submitting.current = true;
    setBusy(true); setError("");
    try {
      const result = await api<{message:string}>("auth/password/forgot", {method:"POST",body:json({identifier:emailAddress})});
      setMessage(result.message); setResendIn(60);
      window.sessionStorage.setItem("procura.recovery-resend-at", String(Date.now()+60000));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Request failed");
      if (cause instanceof ApiError && cause.status===429) setResendIn(Math.min(900, Math.max(1, Number(cause.retryAfter)||60)));
    } finally { submitting.current = false; setBusy(false); }
  }

  if (type === "login" && (checkingSession || redirecting)) return <AppLoadingScreen/>;

  if (resetComplete) return <ResetSuccess arabic={arabic}/>;

  return <main className="auth-page auth-page-live">
    <section className="auth-story">
      <div className="auth-story-top">
        <Link href="/" className="brand">Procura<span className="brand-dot">.</span></Link>
        <button className="auth-language" type="button" onClick={() => setArabic(!arabic)}>{arabic ? "English" : "العربية"}</button>
      </div>
      <div className="auth-story-copy">
        <span className="auth-eyebrow"><IconShieldCheck size={15}/> {t("SAUDI BUSINESS PROCUREMENT")}</span>
        <h2>{t("A clearer path from sourcing need to supplier response.")}</h2>
        <p>{t("One secure workspace to describe requirements, connect with capable businesses and review quotations with confidence.")}</p>
        <div className="auth-benefits">
          <span><IconCheck size={15}/>{t("Built for buyer and supplier workflows")}</span>
          <span><IconCheck size={15}/>{t("Private company workspaces")}</span>
          <span><IconLock size={15}/>{t("Protected sign-in and verification")}</span>
        </div>
      </div>
      <div className="auth-visual" aria-label={t("Riyadh and a procurement workflow")}>
        <Image className="auth-visual-image" src="/images/riyadh-skyline.png" alt="Riyadh skyline at sunset" fill priority sizes="(max-width: 900px) 100vw, 55vw" />
        <div className="auth-visual-shade"/>
        <div className="auth-visual-caption"><span>{t("PROCURA WORKSPACE")}</span><strong>{t("From requirement to confident decision.")}</strong></div>
        <div className="auth-flow-card" aria-hidden="true">
          <span className="auth-flow-label">{t("SOURCING WORKFLOW")}</span>
          <div className="auth-flow-step"><i className="complete"><IconCheck size={12}/></i><span><b>{t("Define the need")}</b><small>{t("Clear RFQ requirements")}</small></span><em>{t("Ready")}</em></div>
          <div className="auth-flow-step"><i className="complete"><IconCheck size={12}/></i><span><b>{t("Reach suppliers")}</b><small>{t("Relevant capabilities")}</small></span><em>{t("Matched")}</em></div>
          <div className="auth-flow-step"><i><span/></i><span><b>{t("Review quotations")}</b><small>{t("Compare submitted offers")}</small></span><em>{t("Next")}</em></div>
        </div>
      </div>
      <div className="auth-story-footer"><span>{t("Saudi Arabia")}</span><span>{t("English · العربية")}</span><Link href="/privacy">{t("Privacy")}</Link></div>
    </section>

    <section className="auth-form-side">
      <header className="auth-form-top"><Link href="/" className="auth-back"><IconArrowLeft size={15}/>{t("Back to Procura")}</Link><span><IconLock size={14}/>{t("Secure business access")}</span></header>
      <div className="auth-form-wrap">
        <form className="live-auth" onSubmit={submit} onInvalidCapture={event=>{const input=event.target;if(!(input instanceof HTMLInputElement||input instanceof HTMLSelectElement||input instanceof HTMLTextAreaElement))return;if(input.name)setFieldErrors(previous=>({...previous,[input.name]:input.validity.valueMissing?"This field is required.":input.validationMessage}));}} onChange={event=>{const target=event.target;const name=target instanceof HTMLInputElement||target instanceof HTMLSelectElement||target instanceof HTMLTextAreaElement?target.name:"";if(name)setFieldErrors(previous=>({...previous,[name]:""}));}}>
          <span className="section-label">{t(register ? "CREATE ACCOUNT" : forgot ? "PASSWORD HELP" : verify ? "MOBILE VERIFICATION" : reset ? "RESET PASSWORD" : email ? "EMAIL VERIFICATION" : "BUSINESS ACCESS")}</span>
          <h1>{t(title)}</h1>
          <p className="auth-form-description">{t(register ? "Set up your sign-in details. You will verify your mobile before continuing." : verify ? "Enter the six-digit code sent by SMS to your mobile. Email verification uses a separate code. The SMS code expires in 5 minutes." : forgot ? "Enter the email address or phone number linked to your account. The same recovery code will be sent to your verified phone and email when available." : reset ? (resetToken ? "Choose a new password to complete recovery." : "Enter the recovery code sent to your verified phone and email when available.") : email ? "Confirm your business email to keep account notifications current." : "Sign in to continue to your Procura business workspace.")}</p>
          {verify && phone && <p className="auth-form-description">{t("Verification requested for")}: <strong dir="ltr">{phone}</strong>{emailAddress && <> {t("and")} <strong dir="ltr">{emailAddress}</strong></>}</p>}
          {email && emailAddress && <p className="auth-form-description">{t("Verification requested for")}: <strong dir="ltr">{emailAddress}</strong></p>}
          {verify&&<p className="auth-form-description">{arabic?"إذا لم تصلك الرسالة، تحقق من إتاحة الرسائل الدولية وتواصل مع الدعم. لا يؤكد البريد الإلكتروني ملكية رقم الهاتف.":"If SMS does not arrive, check international SMS availability and contact support. Email verification does not replace phone ownership verification."} <Link href="/contact">{t("Need help?")}</Link></p>}
          {(forgot || reset) && <p className="auth-form-description">{t("If you cannot access a verified recovery contact, contact support for identity review.")} <Link href="/contact">{t("Need help?")}</Link></p>}
          {error && <div className="notice" role="alert">{t(error)}</div>}{register && error.includes("already registered") && <div className="auth-form-links"><Link href="/login">{t("Sign in to your existing account")}</Link><Link href="/verify-mobile">{t("Finish mobile verification using your original signup number")}</Link></div>}
          {(verify||email)&&!verificationReady&&error&&<Button type="button" variant="outline" onClick={()=>window.location.reload()}>{t('Try again')}</Button>}
          {message && <div className="notice success" role="status">{t(message)}</div>}

          {register&&<div className="account-type-picker" role="group" aria-label={t('Account type')}><Button type="button" variant={accountType==='buyer'?'quiet':'outline'} aria-pressed={accountType==='buyer'} disabled={busy} onClick={()=>setAccountType('buyer')}><strong>{t('Buyer account')}</strong><small>{t('Create RFQs and compare quotations')}</small></Button><Button type="button" variant={accountType==='supplier'?'quiet':'outline'} aria-pressed={accountType==='supplier'} disabled={busy} onClick={()=>setAccountType('supplier')}><strong>{t('Supplier account')}</strong><small>{t('Receive RFQs and submit quotations')}</small></Button></div>}
          {!register&&!verify&&!forgot&&!reset&&!email&&<div className="account-create-links"><Link href="/register?role=buyer">{t('Create buyer account')}</Link><Link href="/register?role=supplier">{t('Create supplier account')}</Link></div>}
          {register && <label className="field">{t("Username")}<input name="username" aria-invalid={Boolean(fieldErrors.username)} aria-describedby={fieldErrors.username?"auth-username-error":undefined} required pattern="[A-Za-z0-9_][A-Za-z0-9_.\-]{2,79}" minLength={3} maxLength={80} autoComplete="username"/><FieldError id="auth-username-error" message={fieldErrors.username}/></label>}
          {(register || verify) && <InternationalPhone value={phone} onChange={setPhone} disabled={busy} error={fieldErrors.phone}/>}
          {register && <label className="field">{t("Email")}<input name="email" aria-invalid={Boolean(fieldErrors.email)} aria-describedby={fieldErrors.email?"auth-email-error":undefined} type="email" required autoComplete="email" value={reset ? emailAddress : undefined} onChange={reset ? (event) => setEmailAddress(event.target.value) : undefined}/><FieldError id="auth-email-error" message={fieldErrors.email}/></label>}
          {!phoneIdentifier && (forgot || (reset && !resetToken)) && <label className="field">{t("Email or phone number")}<input name="identifier" aria-invalid={Boolean(fieldErrors.identifier)} aria-describedby={fieldErrors.identifier?"auth-identifier-error":undefined} required autoComplete="username" value={reset ? emailAddress : undefined} onChange={reset ? (event)=>setEmailAddress(event.target.value) : undefined}/><FieldError id="auth-identifier-error" message={fieldErrors.identifier}/></label>}
          {!phoneIdentifier && !register && !verify && !forgot && !reset && !email && <label className="field">{t("Email or phone number")}<input name="identifier" aria-invalid={Boolean(fieldErrors.identifier)} aria-describedby={fieldErrors.identifier?"auth-identifier-error":undefined} required autoComplete="username"/><FieldError id="auth-identifier-error" message={fieldErrors.identifier}/></label>}
          {!register&&!verify&&!email&&(!reset||!resetToken)&&<><button type="button" className="auth-resend" disabled={busy} onClick={()=>setPhoneIdentifier(current=>!current)}>{arabic?(phoneIdentifier?'استخدام البريد الإلكتروني أو رقم دولي':'استخدام رقم محلي مع اختيار البلد'):(phoneIdentifier?'Use email or Saudi phone':'Use Saudi mobile number')}</button>{phoneIdentifier&&<InternationalPhone name="identifier" value={reset?emailAddress:identifierPhone} onChange={reset?setEmailAddress:setIdentifierPhone} disabled={busy} error={fieldErrors.identifier}/>}</>}
          {(verify || (reset && !resetToken) || email) && <label className="field">{t("Verification code")}<input name="code" aria-invalid={Boolean(fieldErrors.code)} aria-describedby={fieldErrors.code?"auth-code-error":undefined} required pattern="[0-9]{6}" inputMode="numeric" autoComplete="one-time-code" maxLength={6}/><FieldError id="auth-code-error" message={fieldErrors.code}/></label>}
          {!verify && !forgot && !email && (!reset || Boolean(resetToken)) && <>
            <label className="field">{t(reset ? "New password" : "Password")}<span className="password-field-control"><input name="password" aria-invalid={Boolean(fieldErrors.password)} aria-describedby={fieldErrors.password?"auth-password-error":undefined} type={showPassword ? "text" : "password"} required minLength={register || reset ? 6 : 1} maxLength={128} autoComplete={register || reset ? "new-password" : "current-password"} value={register || reset ? password : undefined} onChange={register || reset ? (event) => setPassword(event.target.value) : undefined}/><button type="button" className="password-visibility" aria-label={t(showPassword ? "Hide password" : "Show password")} aria-pressed={showPassword} onClick={() => setShowPassword((visible) => !visible)}>{showPassword ? <IconEyeOff size={17}/> : <IconEye size={17}/>}</button></span><FieldError id="auth-password-error" message={fieldErrors.password}/></label>
            {(register || reset) && <>
              <div className={`password-meter password-meter-${password.length < 6 ? "short" : passwordStrength.toLowerCase().replaceAll(" ", "-")}`}>
                <div className="password-meter-head"><span>{t("Password strength")}</span><strong aria-live="polite">{password ? t(passwordStrength) : t("Enter a password")}</strong></div>
                <div className="password-meter-track" role="meter" aria-label={t("Password strength")} aria-valuemin={0} aria-valuemax={5} aria-valuenow={passwordScore} aria-valuetext={t(passwordStrength)}>
                  {Array.from({ length: 5 }, (_, index) => <i key={index} className={password && index < passwordScore ? "filled" : ""}/>) }
                </div>
                <p>{t("Use 6-128 characters. A longer, unique password with letters, numbers and symbols is recommended.")}</p>
                <ul><li className={password.length >= 6 ? "met" : ""}>{t("At least 6 characters (required)")}</li><li className={password.length >= 16 ? "met" : ""}>{t("Longer and unique is stronger")}</li></ul>
              </div>
              <label className="field">{t("Confirm password")}<span className="password-field-control"><input name="confirm_password" aria-invalid={Boolean(fieldErrors.confirm_password)} aria-describedby={fieldErrors.confirm_password?"auth-confirm_password-error":undefined} type={showConfirmPassword ? "text" : "password"} required minLength={6} maxLength={128} autoComplete="new-password"/><button type="button" className="password-visibility" aria-label={t(showConfirmPassword ? "Hide password" : "Show password")} aria-pressed={showConfirmPassword} onClick={() => setShowConfirmPassword((visible) => !visible)}>{showConfirmPassword ? <IconEyeOff size={17}/> : <IconEye size={17}/>}</button></span><FieldError id="auth-confirm_password-error" message={fieldErrors.confirm_password}/></label>
            </>}
          </>}

          {(verify || email) && <button className="auth-resend" type="button" disabled={busy || !verificationReady || resendIn > 0 || (verify && !phone)} onClick={() => void requestCode()}>{resendIn > 0 ? `${t("Resend available in")} ${resendIn}s` : t(verify ? "Resend verification code" : "Send a verification email")}</button>}
          {reset && !resetToken && <button className="auth-resend" type="button" disabled={busy || resendIn>0 || !emailAddress} onClick={()=>void resendRecovery()}>{resendIn>0 ? `${t("Resend available in")} ${resendIn}s` : t("Resend verification code")}</button>}
          <Button type="submit" disabled={busy || !verificationReady}>{t(busy || !verificationReady ? "Please wait..." : register ? "Create account" : verify || email ? "Verify and continue" : forgot ? "Send recovery code" : reset ? (resetToken ? "Reset password" : "Verify code") : "Sign in")}</Button>

          <div className="auth-form-links">{!register && <span>{t("New to Procura?")} <Link href="/register">{t("Create an account")}</Link></span>}{(forgot || reset) && <Link href="/login">{t("Back to Login")}</Link>}{!register && !forgot && <Link href="/forgot-password">{t("Forgot password?")}</Link>}{register && <span>{t("Already have an account?")} <Link href="/login">{t("Sign in")}</Link></span>}{verify && <span>{t("Wrong number?")} <Link href="/register">{t("Start again")}</Link></span>}{reset && <span>{t("Need another code?")} <Link href="/forgot-password">{t("Request a new one")}</Link></span>}</div>
          {register && <p className="auth-consent">{t("By creating an account, you agree to our")} <Link href="/terms">{t("Terms")}</Link> {t("and")} <Link href="/privacy">{t("Privacy")}</Link>.</p>}
        </form>
      </div>
      <footer className="auth-form-footer"><span>{t("© 2026 Procura · Saudi Arabia")}</span><Link href="/contact">{t("Need help?")}</Link></footer>
    </section>
  </main>;
}
