from __future__ import annotations

import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from app.core.settings import settings

_TEMPLATE_DIR = Path(__file__).parent.parent / "templates" / "email"


def _render(template_name: str, context: dict) -> str:
    env = Environment(loader=FileSystemLoader(str(_TEMPLATE_DIR)), autoescape=True)
    return env.get_template(template_name).render(**context)


def send_contact_email(
    *,
    to_email: str,
    sender_name: str,
    sender_email: str,
    subject: str,
    message: str,
    tenant_name: str,
) -> None:
    """
    Send a contact-form email via SMTP.

    The email arrives at *to_email* (the tenant's contact address) with
    Reply-To set to the visitor's address so the client can reply directly.

    Raises RuntimeError if SMTP credentials are not configured.
    Raises smtplib.SMTPException (or subclass) on delivery failure.
    """
    if not settings.SMTP_USER or not settings.SMTP_PASSWORD:
        raise RuntimeError("SMTP credentials not configured.")

    html_body = _render("contact.html", {
        "tenant_name": tenant_name,
        "sender_name": sender_name,
        "sender_email": sender_email,
        "subject": subject,
        "message": message,
    })
    plain_body = (
        f"New contact form message — {tenant_name}\n\n"
        f"From:    {sender_name} <{sender_email}>\n"
        f"Subject: {subject}\n\n"
        f"{message}"
    )

    msg = MIMEMultipart("alternative")
    msg["From"] = f"{settings.SMTP_FROM_NAME} <{settings.SMTP_USER}>"
    msg["To"] = to_email
    msg["Reply-To"] = f"{sender_name} <{sender_email}>"
    msg["Subject"] = subject

    msg.attach(MIMEText(plain_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT) as smtp:
        smtp.ehlo()
        smtp.starttls()
        smtp.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
        smtp.sendmail(settings.SMTP_USER, to_email, msg.as_string())
