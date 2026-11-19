"""Mail delivery. `file` backend writes .eml files to an outbox (development/test);
`smtp` backend delivers through a real SMTP server (production)."""
from __future__ import annotations

import logging
import smtplib
import uuid
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

from app.config import get_settings

log = logging.getLogger(__name__)


def send_mail(to: str, subject: str, body: str) -> str:
    s = get_settings()
    msg = EmailMessage()
    msg["From"] = s.mail_from
    msg["To"] = to
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=False)
    msg["Message-ID"] = make_msgid(domain="scriptorium")
    msg.set_content(body)

    if s.mail_backend == "file":
        s.mail_outbox_dir.mkdir(parents=True, exist_ok=True)
        path = s.mail_outbox_dir / f"{uuid.uuid4()}.eml"
        path.write_bytes(bytes(msg))
        log.info("mail written to outbox", extra={"path": str(path), "subject": subject})
        return str(path)

    with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=30) as smtp:
        if s.smtp_starttls:
            smtp.starttls()
        if s.smtp_username:
            smtp.login(s.smtp_username, s.smtp_password or "")
        smtp.send_message(msg)
    log.info("mail sent", extra={"subject": subject})
    return msg["Message-ID"]
