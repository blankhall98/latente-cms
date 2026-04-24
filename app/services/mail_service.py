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
    fields: dict[str, str],
    tenant_name: str,
) -> None:
    """
    Send a contact-form email via SMTP.

    *fields* is the free-form dict from the front-end form — every key/value
    pair is rendered verbatim in the email body, so the template stays
    generic regardless of what fields the form contains.

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
        "fields": fields,
    })

    plain_lines = [f"New contact form message — {tenant_name}", ""]
    plain_lines.append(f"From: {sender_name} <{sender_email}>")
    for key, value in fields.items():
        plain_lines.append(f"{key}: {value}")
    plain_body = "\n".join(plain_lines)

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
