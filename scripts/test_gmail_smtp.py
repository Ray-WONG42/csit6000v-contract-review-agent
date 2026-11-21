"""Check Gmail STARTTLS authentication, or explicitly send one plain-text test email.

Default: authenticate only. Use --to ADDRESS to send one message.
Credentials: GMAIL_USER (or GMAIL_MAILBOX) and GMAIL_APP_PASSWORD, from env/local .env.
"""
import argparse
import os
from pathlib import Path
import smtplib
import ssl
import sys
from email.message import EmailMessage
from email.utils import parseaddr

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.config import settings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--to', help='Explicit recipient; omission performs authentication only')
    args = parser.parse_args()
    address = os.environ.get('GMAIL_USER') or settings.gmail_mailbox
    password = settings.gmail_app_password.replace(' ', '')
    if not address or not password:
        raise ValueError('Set GMAIL_USER/GMAIL_MAILBOX and GMAIL_APP_PASSWORD in local configuration.')
    if args.to and (parseaddr(args.to)[1] != args.to or '@' not in args.to or any(c in args.to for c in '\r\n')):
        raise ValueError('Specify one valid recipient email address.')
    print('Connecting to smtp.gmail.com:587 with STARTTLS...', flush=True)
    with smtplib.SMTP('smtp.gmail.com', 587, timeout=30) as client:
        client.ehlo()
        client.starttls(context=ssl.create_default_context())
        client.ehlo()
        client.login(address, password)
        if not args.to:
            print('PASS: encrypted connection and authentication. No email sent.')
            return
        message = EmailMessage()
        message['From'] = address
        message['To'] = args.to
        message['Subject'] = 'Contract Review Agent - Gmail SMTP test'
        message['Auto-Submitted'] = 'auto-generated'
        message.set_content('This is a plain-text Gmail SMTP test from Contract Review Agent. No contracts or attachments are included.')
        if client.send_message(message):
            raise RuntimeError('SMTP rejected the recipient.')
        print('PASS: Gmail accepted the test message. Confirm receipt in the recipient inbox.')


if __name__ == '__main__':
    try:
        main()
    except smtplib.SMTPAuthenticationError:
        raise SystemExit('FAILED: Gmail authentication rejected. Check the account and application password.')
    except (smtplib.SMTPException, OSError) as error:
        raise SystemExit('FAILED: ' + type(error).__name__ + '. SMTP connection failed; credentials were not displayed.')
    except ValueError as error:
        raise SystemExit(str(error))
