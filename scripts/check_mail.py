"""Verify mailbox logins without sending email, importing attachments or changing flags."""
from pathlib import Path
import smtplib
import ssl
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.config import settings
from app.services import graph_client


def main():
    client = graph_client()
    if not client.enabled:
        raise SystemExit('Mailbox is not configured. Fill the local application password or complete OAuth.')
    if settings.mail_provider == 'gmail' and settings.gmail_transport == 'imap':
        with client.connect() as inbox:
            assert client.number(inbox, 'UIDVALIDITY') > 0
        print('PASS: Gmail IMAP authentication. No message was read or marked.')
        with client.smtp_connection():
            pass
        print('PASS: Gmail SMTP authentication. No email was sent.')
    else:
        client.api('/profile' if settings.mail_provider == 'gmail' else '/mailFolders/inbox?$select=id')
        print('PASS: mailbox API authentication. No email was sent.')


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        raise SystemExit(type(error).__name__ + ': mailbox check failed; check application password and network.')
