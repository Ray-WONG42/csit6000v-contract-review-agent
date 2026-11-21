# Gmail and language setup / Gmail 与中英文配置

## Language / 语言

Use the bottom-right **中文 / English** selector. The preference is saved in this browser.
It changes interface text and the language requested for new assistant replies and reports.
Existing conversations, uploaded originals and saved reports are not rewritten.
Source quotations always retain the original contract language.

使用右下角 **中文 / English** 切换。选择保存在当前浏览器，并随新聊天和审查任务提交。
界面、预置任务和新生成报告随之切换；历史记录、原合同和已保存报告保持原样。
证据引用保留合同原文，不翻译原文证据。

Download built-in Skill and request files from the links beside the selector.
Editable source files live in `templates/en/` and `templates/zh/`.
通过语言开关旁边的链接下载预置 Skill 和提问模板。源文件位于对应语言的 templates 目录。

## Gmail without Cloud / 不使用 Google Cloud

The default local setup can use Gmail IMAP/SMTP with a Google application password:

```dotenv
MAIL_PROVIDER=gmail
GMAIL_TRANSPORT=imap
GMAIL_MAILBOX=your-project-mailbox@gmail.com
GMAIL_APP_PASSWORD=
MAIL_POLL_SECONDS=60
MAIL_AUTO_REVIEW=1
MAIL_DEMO=1
```

Enable two-step verification yourself and generate an application password at https://myaccount.google.com/apppasswords . Store it only in your private `.env`, never your normal login password. Run `python scripts/check_mail.py` with the project interpreter. It verifies IMAP and SMTP logins without sending mail. After both pass, set `MAIL_DEMO=0` and restart the workbench.

开启 Google 两步验证，亲自生成应用专用密码，仅填入本机 `.env`。使用项目解释器执行 `scripts/check_mail.py` 验证收件和发件登录；不会发送测试邮件。验证通过后将 `MAIL_DEMO=0` 并重启工作台。

The first successful inbox poll establishes a new-mail baseline and skips existing mail. Send a **new** message after startup from a registered legal account, with exactly one PDF/DOCX/TXT contract (maximum 12 MB). The worker checks every 60 seconds, uses the default standard checklist, saves evidence and a Word risk report, then replies to the original registered sender. The app must remain running. Message UIDs prevent duplicate imports; delivery receipts prevent routine replay. Ambiguous SMTP delivery is marked uncertain instead of being retried automatically.

首次连接建立新邮件起点，不批量处理历史邮件。启动完成后，由已注册法务邮箱发送一封新邮件，附一份不超过 12 MB 的 PDF/DOCX/TXT 合同。系统每 60 秒检查新来件，按默认清单审查并保存风险 Word 文件，随后回邮原发件人。程序必须保持运行；UID 和发送记录用于去重。发件结果不确定时标为待核对，不盲目重复发送。

Unregistered senders are not automatically provisioned or reviewed. Automatic responses are excluded to prevent loops. With `MAIL_AUTO_REVIEW=0`, the original ticket/confirmation flow remains available. Application-password options depend on Google account policy; if unavailable, use the optional OAuth route below.

未注册邮箱不会自动开通账户或进入审阅；自动回复邮件会排除以避免循环。关闭 `MAIL_AUTO_REVIEW` 可保留原凭据/页面确认流程。若 Google 账号不提供应用专用密码，使用下方可选 OAuth 方式。

## Optional Google OAuth / 可选 Google OAuth 配置

1. Sign in to Google Cloud with the intended project mailbox. Create/select a project.
2. Enable **Gmail API** in APIs & Services.
3. In **Google Auth Platform**, configure Branding and Audience. For a personal course demo, keep the app in Testing and add the mailbox as a test user.
4. Create an OAuth client of type **Desktop app**. Download its client JSON to a private local folder.
5. In the local `.env`, set `MAIL_PROVIDER=gmail`, `GMAIL_TRANSPORT=oauth` and `GMAIL_MAILBOX` to the account you will authorize. Keep `MAIL_DEMO=1` during setup. HTTPS API mode does not use SMTP or the application password.
6. From the project directory run:

```powershell
.\.venv\Scripts\python.exe scripts/gmail_login.py --client "PATH_TO_DOWNLOADED_CLIENT_JSON"
```

7. Complete the Google consent screen yourself. The script requests Gmail modification access to read contract attachments, mark processed messages as read and send verification codes/results. It never asks for a mailbox password. The callback uses localhost, state validation and PKCE.
8. The script verifies the authorized mailbox matches `GMAIL_MAILBOX`, then stores `.gmail-client.json` and `.gmail-token.json` under ignored `data/`. Do not publish the downloaded client JSON or token files.
9. Run `python scripts/check_connections.py --live` with the project interpreter. `mail_read_test: true` confirms read access only; it does not prove sending or attachment ingestion.
10. Set `MAIL_DEMO=0` after successful authorization and restart the API/worker to enable real mail. `APP_DEMO` independently controls local demo accounts. Keep local demo credentials for local demonstrations only.

中文步骤：使用项目邮箱登录 Google Cloud，创建项目并启用 Gmail API；配置 Google Auth Platform，测试模式加入该邮箱为测试用户；创建“桌面应用”客户端并下载 JSON。在本机设置 `GMAIL_TRANSPORT=oauth`，执行上方授权脚本，在浏览器亲自同意授权。脚本核对邮箱后把凭据写入被 Git 忽略的 data 目录。此模式通过 HTTPS API 收发，不走 SMTP，也不使用应用专用密码。授权成功后，将 `MAIL_DEMO=0` 并重启服务，才会真实收发。

## Mail behavior / 邮件行为

- Only registered legal users can submit contracts. A one-time submission ticket is bound to its owner and expires after 24 hours.
- When automatic review is disabled, intake without a valid ticket waits for confirmation in the workbench. The sender address alone never authorizes automatic processing.
- The worker accepts one PDF, DOCX or TXT contract per message, with a 12 MB limit. A stable Gmail message ID prevents duplicate imports.
- Replies go to the registered owner recorded by the application. A model cannot override the recipient.
- English submission subjects include `[LANG=en]`; replies and newly generated reports use English. Original evidence is preserved.
- With `MAIL_DEMO=1`, messages stay in the local demo outbox. No real email is sent.

仅处理已注册法务用户的来件。`MAIL_AUTO_REVIEW=1` 时自动进行默认审查；关闭时需要绑定用户的一次性提交凭据，无凭据时需在页面确认。每封邮件一份合同，最多 12 MB。Gmail 消息 ID 用于去重；回邮收件人来自已登记用户，不能由模型指定。英文提交主题含 `[LANG=en]`。`MAIL_DEMO=1` 只写本地演示发件箱。

Google Testing-mode authorizations may expire; repeat authorization when Google reports that the grant is invalid. Real email acceptance requires a controlled attachment email, observed ingestion, review completion and a received reply. Unit tests with synthetic responses are not real email acceptance.

Google 测试模式授权可能过期；失效后重新授权。真实邮件验收需要实际附件来件、成功导入、完成审查和收到回邮，不能用模拟测试替代。

If HTTPS requests are unstable behind an existing local accelerator, set `GMAIL_HTTPS_PROXY`
to its HTTP CONNECT proxy address (for example `http://127.0.0.1:7890`, only when that is your
actual proxy). This setting affects Gmail API calls only. TLS certificate verification stays enabled;
it does not change the machine's global proxy or TUN settings. Sending is never retried automatically
after an uncertain response.

现有加速器环境下 HTTPS 不稳定时，可把 `GMAIL_HTTPS_PROXY` 设置为它实际提供的 HTTP
代理地址。只影响 Gmail API 请求，不改全局网络、不关闭加速器或证书校验。发件响应不确定时
不自动重发，应先核对 Gmail 已发送邮件。
