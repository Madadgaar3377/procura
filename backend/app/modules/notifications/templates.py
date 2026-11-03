"""Small escaped transactional templates shared by SMS and SMTP delivery."""
from html import escape


def render_message(template: str, context: dict, secret: str | None = None) -> tuple[str, str, str]:
    code = secret or ''
    if template == 'phone_otp':
        subject = 'Your Saudi B2B Procurement verification code'
        plain = f'Your verification code is {code}. It expires in 5 minutes. Do not share it.'
        heading = 'Verify your phone number'
    elif template == 'email_otp':
        subject = 'Verify your business email'
        plain = f'Your verification code is {code}. It expires in 10 minutes. Do not share it.'
        heading = 'Verify your business email'
    elif template == 'password_reset':
        subject = 'Reset your Saudi B2B Procurement password'
        plain = f'Your password reset code is {code}. It expires in 10 minutes. If you did not request this, ignore this message.'
        heading = 'Password reset'
    else:
        reference = str(context.get('reference', ''))
        title = str(context.get('title', ''))
        messages = {
            'rfq_invitation': ('New RFQ invitation', f'You have been invited to respond to RFQ {reference}: {title}.'),
            'quotation_submitted': ('New quotation received', f'A supplier submitted a quotation for RFQ {reference}: {title}.'),
            'verification_updated': ('Company verification update', f'Your company verification status is now {context.get("status", "updated")}.'),
            'deadline_reminder': ('RFQ deadline reminder', f'RFQ {reference}: {title} is approaching its response deadline.'),
        }
        heading, plain = messages.get(template, ('Account notification', 'You have a new account notification.'))
        subject = f'{heading} | Saudi B2B Procurement'

    html_body = (
        '<!doctype html><html><body style="margin:0;background:#f3f6f8;font-family:Arial,sans-serif;color:#17212b">'
        '<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="padding:28px 12px">'
        '<tr><td align="center"><table role="presentation" width="600" cellspacing="0" cellpadding="0" '
        'style="max-width:600px;background:#fff;border-radius:10px;padding:30px">'
        '<tr><td><div style="font-size:12px;letter-spacing:1px;color:#52736a;font-weight:bold">SAUDI B2B PROCUREMENT</div>'
        f'<h1 style="font-size:23px;margin:18px 0 12px">{escape(heading)}</h1>'
        f'<p style="font-size:15px;line-height:1.6">{escape(plain)}</p>'
        '<p style="font-size:12px;color:#64716d;margin-top:28px">This is an automated transactional message.</p>'
        '</td></tr></table></td></tr></table></body></html>'
    )
    return subject, plain, html_body
