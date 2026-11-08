"""Escaped bilingual Procumaterial templates for existing notification events."""

from datetime import UTC
from html import escape
from urllib.parse import urlsplit

TAGLINE = "Procurement Without Complexity"


def render_message(template: str, context: dict, secret: str | None = None) -> tuple[str, str, str]:
    if template == 'phone_changed':
        plain = 'Procumaterial Security: Your registered phone number was changed. If this was not you, contact Procumaterial support immediately.'
        if context.get('language') == 'ar':
            plain = 'أمان بروكيورا: تم تغيير رقم هاتف حسابك. إذا لم تقم بهذا التغيير، تواصل مع الدعم فوراً.'
        return 'Procumaterial | Phone number changed', plain, '<p>'+escape(plain)+'</p>'
    if template == "password_changed":
        from datetime import datetime

        from app.config import get_settings
        settings=get_settings()
        support=settings.support_url or settings.frontend_url.rstrip('/')+'/contact'
        parsed=urlsplit(support)
        if parsed.scheme not in {'https','http'} or not parsed.netloc or parsed.username or parsed.password:
            raise ValueError('Support URL must be an absolute application URL')
        subject='Procumaterial | Your Password Was Changed'
        if context.get('channel')=='sms':
            plain="Procumaterial Security: Your account password was changed. If this wasn't you, contact Procumaterial support immediately."
        else:
            when=context.get('changed_at') or datetime.now(UTC).isoformat()
            plain=("Hello,\n\nYour Procumaterial account password was successfully changed.\n\n"
                "If you made this change, no further action is required.\n\n"
                "If you did not make this change, contact Procumaterial support and use the secure account recovery process immediately.\n\n"
                f"Date/time (UTC): {when}\nSupport: {support}\n\nProcumaterial Team\nProcurement Without Complexity.")
        return subject,plain,'<html><body><h1>Your password was changed</h1><p>'+escape(plain).replace('\n','<br>')+'</p></body></html>'
    arabic = context.get("language") == "ar"
    sms = context.get("channel") == "sms"
    ref = str(context.get("reference", ""))
    title = str(context.get("title", ""))
    deadline = str(context.get("deadline", ""))
    security = template in {"phone_change_otp", "phone_otp", "email_otp", "password_reset"}
    if security:
        minutes = context.get('minutes', 5 if template in {"phone_change_otp", "phone_otp"} else 10)
        heading = (
            ("استعادة كلمة المرور" if template == "password_reset" else "التحقق من حسابك")
            if arabic
            else ("Password recovery" if template == "password_reset" else "Verify your account")
        )
        plain = (
            f"Procumaterial: رمز التحقق {secret or ''}. تنتهي صلاحيته خلال {minutes} دقائق. لا تشاركه."
            if arabic
            else f"Procumaterial verification code: {secret or ''}. Expires in {minutes} minutes. Do not share this code."
        )
    else:
        messages = {
            "registration_welcome": (
                "Welcome to Procumaterial",
                "مرحباً بك في Procumaterial",
                "Welcome to Procumaterial. Complete your company profile and verify your business details to begin.",
                "مرحباً بك في Procumaterial. أكمل ملف شركتك وتحقق من بياناتها للبدء.",
            ),
            "membership_expiration": (
                "Membership expiration",
                "انتهاء العضوية",
                f"Your company membership expires on {deadline}. Review your membership in your dashboard.",
                f"تنتهي عضوية شركتك في {deadline}. راجع العضوية عبر لوحة التحكم.",
            ),
            "rfq_invitation": (
                "New RFQ Invitation",
                "دعوة جديدة لطلب عرض سعر",
                f"Hello {context.get('supplier_name', '')},\n\nA new procurement request matching your company's supply categories is available.\n\nRFQ: {ref}\nProject: {title}\nCategory: {context.get('categories', '')}\nDelivery Location: {context.get('delivery_location', '')}\nDeadline: {deadline}\n\nReview the requirements and submit your quotation through Procumaterial.",
                f"مرحباً {context.get('supplier_name', '')}،\n\nيتوفر طلب شراء جديد يتوافق مع فئات توريد شركتكم.\nطلب عرض السعر: {ref}\nالمشروع: {title}\nالفئات: {context.get('categories', '')}\nموقع التسليم: {context.get('delivery_location', '')}\nالموعد النهائي: {deadline}\n\nراجع المواد المطلوبة وقدم عرضك عبر لوحة المورد.",
            ),
            "deadline_reminder": (
                "RFQ deadline reminder",
                "تذكير بالموعد النهائي",
                f"Reminder to respond to RFQ {ref} before {deadline}. Open your supplier dashboard.",
                f"تذكير بالرد على طلب عرض السعر {ref} قبل {deadline}. افتح لوحة المورد.",
            ),
            "quotation_submitted": (
                "New quotation received",
                "تم استلام عرض سعر جديد",
                f"A supplier submitted a quotation for RFQ {ref}: {title}. Review it in your buyer dashboard.",
                f"قدم مورد عرض سعر لطلب {ref}: {title}. راجعه عبر لوحة المشتري.",
            ),
            "quotation_confirmation": (
                "Quotation submitted",
                "تم تقديم عرض السعر",
                f"Your quotation for RFQ {ref}: {title} was submitted successfully. Track its status in your supplier dashboard.",
                f"تم تقديم عرضك لطلب {ref}: {title} بنجاح. تابع حالته عبر لوحة المورد.",
            ),
            "verification_updated": (
                "Company verification update",
                "تحديث التحقق من الشركة",
                f"Your company verification status is now {context.get('status', 'updated')}. Review your company profile for details.",
                "تم تحديث حالة التحقق من شركتك. راجع ملف الشركة للاطلاع على التفاصيل.",
            ),
        }
        en, ar, english, translated = messages.get(
            template,
            (
                "Account notification",
                "إشعار الحساب",
                "You have a new account notification.",
                "لديك إشعار جديد في حسابك.",
            ),
        )
        heading = ar if arabic else en
        plain = translated if arabic else english
        if sms:
            plain = "Procumaterial: " + plain
            if template == "rfq_invitation":
                plain = (
                    f"Procumaterial: طلب عرض سعر جديد {ref} يناسب فئات توريدك. راجع لوحة المورد."
                    if arabic
                    else f"Procumaterial: New RFQ {ref} matches your supply categories. Open your supplier dashboard to review."
                )
    if template in {"quotation_shortlisted", "quotation_awarded", "quotation_rejected"}:
        decision = template.removeprefix("quotation_")
        decision_labels = {
            "shortlisted": "تم إدراج عرضك في القائمة المختصرة",
            "awarded": "تمت ترسية الطلب على عرضك",
            "rejected": "لم يتم اختيار عرضك",
        }
        heading = decision_labels[decision] if arabic else "Quotation " + decision
        plain = (
            f"Procumaterial: {decision_labels[decision]} — {ref}. راجع لوحة المورد."
            if arabic
            else f"Procumaterial: Your quotation for RFQ {ref} was {decision}. Review your supplier dashboard."
        )
    if template == "verification_updated":
        status = str(context.get("status", "updated"))
        status_ar = {
            "verified": "معتمدة",
            "approved": "معتمدة",
            "rejected": "مرفوضة",
            "pending": "قيد المراجعة",
        }.get(status, "محدثة")
        plain = (
            f"Procumaterial: حالة التحقق من شركتك {status_ar}. راجع ملف الشركة للاطلاع على التفاصيل."
            if arabic
            else f"Procumaterial: Your company verification status is {status}. Review your company profile for details."
        )
    if template == 'password_reset':
        heading = 'إعادة تعيين كلمة المرور' if arabic else 'Reset Your Password'
        if not sms:
            plain = (
                f"مرحباً،\n\nتلقينا طلباً لإعادة تعيين كلمة مرور حسابك في Procumaterial. استخدم الرابط الآمن أدناه أو الرمز {secret or ''}. تنتهي صلاحيته خلال {minutes} دقائق.\n\nإذا لم تطلب ذلك، يمكنك تجاهل هذه الرسالة."
                if arabic else
                f"Hello,\n\nWe received a request to reset your Procumaterial account password.\n\nUse the secure button below to set a new password, or enter code {secret or ''}. This link expires in {minutes} minutes.\n\nIf you did not request this, you can ignore this email."
            )
        elif not arabic:
            plain = f"Procumaterial: Your password reset code is {secret or ''}. Expires in {minutes} minutes. Do not share it."
    if template == 'password_changed':
        heading = 'تم تغيير كلمة المرور' if arabic else 'Password changed'
        plain = 'تم تغيير كلمة مرور حسابك في Procumaterial. إذا لم تقم بذلك، تواصل مع فريق الدعم فوراً.' if arabic else 'Your Procumaterial password was changed. If this was not you, contact support immediately.'
    subject = f"Procumaterial | {heading}" + (f" — {ref}" if ref else "")
    url = str(context.get("url", "")) if not security or template=='password_reset' else ""
    parsed = urlsplit(url)
    if parsed.scheme not in {"https", "http"} or not parsed.netloc or parsed.username or parsed.password:
        url = ""
    cta = (
        ("عرض طلب السعر" if arabic else "View RFQ")
        if template == "rfq_invitation"
        else ("فتح لوحة التحكم" if arabic else "Open dashboard")
    )
    if template=='password_reset':
        cta='إعادة تعيين كلمة المرور' if arabic else 'Reset Password'
    link = (
        f'<p style="margin:28px 0"><a href="{escape(url, quote=True)}" style="display:inline-block;background:#2563EB;color:#FFFFFF;padding:14px 24px;border-radius:6px;text-decoration:none;font-weight:bold">{cta}</a></p>'
        if url
        else ""
    )
    paragraphs = "".join(
        f'<p style="font-size:16px;line-height:1.7;margin:0 0 16px">{escape(part).replace(chr(10), "<br>")}</p>'
        for part in plain.split("\n\n")
    )
    html = (
        f'<!doctype html><html lang="{"ar" if arabic else "en"}" dir="{"rtl" if arabic else "ltr"}"><head><meta name="viewport" content="width=device-width,initial-scale=1"></head>'
        '<body style="margin:0;background:#F8FAFC;color:#0F172A;font-family:Arial,Tahoma,sans-serif">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td style="padding:24px 12px" align="center">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:600px;background:#FFFFFF;border:1px solid #E2E8F0">'
        f'<tr><td style="padding:28px;background:#0F172A;color:#FFFFFF"><div style="font-size:28px;font-weight:bold">Procumaterial</div><div style="font-size:13px;margin-top:8px">{TAGLINE}</div></td></tr>'
        f'<tr><td style="padding:28px;text-align:{"right" if arabic else "left"}"><h1 style="font-size:23px;line-height:1.4;margin:0 0 20px">{escape(heading)}</h1>{paragraphs}{link}'
        f'<p style="font-size:14px;line-height:1.6;margin-top:28px">{"فريق Procumaterial" if arabic else "Procumaterial Team"}<br>{TAGLINE}</p>'
        "</td></tr></table></td></tr></table></body></html>"
    )
    if not sms:
        plain = (
            f"Procumaterial — {TAGLINE}\n\n{plain}"
            + (f"\n\n{cta}: {url}" if url else "")
            + f"\n\nProcumaterial Team\n{TAGLINE}"
        )
    return subject, plain, html
