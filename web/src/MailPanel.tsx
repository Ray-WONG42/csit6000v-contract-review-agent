import { useEffect, useState } from 'react';
import { api } from './api';
import { getLanguage, t } from './i18n';

type Mail = {id: string; subject: string; sender: string; sender_name: string; received_at: number;
  unread: boolean; intake_state: string; intake_note: string};
type Inbox = {items: Mail[]; next_page: string; demo: boolean};

export default function MailPanel({role, mailbox}: {role: string; mailbox: string}) {
  const [items, setItems] = useState<Mail[]>([]);
  const [page, setPage] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [demo, setDemo] = useState(false);
  async function load(next = '') {
    setLoading(true); setError('');
    try {
      const data = await api<Inbox>('/mail/inbox' + (next ? '?page=' + encodeURIComponent(next) : ''));
      setItems(old => next ? [...new Map([...old, ...data.items].map(m => [m.id, m])).values()] : data.items);
      setPage(data.next_page); setDemo(data.demo);
    } catch (e: any) { setError(e.message); }
    finally { setLoading(false); }
  }
  useEffect(() => { if (role === 'admin') void load(); }, [role]);
  const statuses: Record<string, string> = {accepted: '已进入审查', ignored: '未登记发件人', rejected: '附件不符合要求'};
  return <>
    <span className="eyebrow">MAIL</span>
    <h2>{t('邮件处理')}</h2>
    <p>{t('主账户邮箱')} · <a href={'mailto:' + mailbox}>{mailbox || t('尚未配置邮箱')}</a></p>
    {role === 'admin' && <>
      <div className="mail-inbox-toolbar"><h3>{t('近 24 小时收到的邮件')}</h3>
        <button className="secondary" disabled={loading} onClick={() => load()}>{t('刷新')}</button></div>
      {loading && <p role="status">{t('正在读取邮件…')}</p>}
      {error && <p role="alert" className="form-error">{error}</p>}
      {!loading && !error && !items.length && <p className="quiet">{t(demo ? '当前为演示邮箱，未读取真实来件。' : '近 24 小时没有收到邮件。')}</p>}
      <div className="mail-inbox-list">{items.map(m => <article className="mail-card" key={m.id}>
        <strong>{m.subject || t('无主题')}{m.unread && <span className="mail-unread"> · {t('未读')}</span>}</strong>
        <p>{m.sender_name ? m.sender_name + ' · ' : ''}{m.sender}</p>
        <small>{new Date(m.received_at * 1000).toLocaleString(getLanguage() === 'en' ? 'en-GB' : 'zh-CN')}
          {m.intake_state && ' · ' + t(statuses[m.intake_state] || m.intake_state)}</small>
      </article>)}</div>
      {page && <button className="secondary" disabled={loading} onClick={() => load(page)}>{t('加载更多')}</button>}
    </>}
  </>;
}
