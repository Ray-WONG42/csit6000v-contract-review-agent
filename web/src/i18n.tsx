import { useState, useSyncExternalStore } from 'react';
import { systemText, localizedTraceTitle, localizedTraceDetail } from './traceLocalization';
export type Language = 'zh' | 'en';
let language: Language = 'en';
try { language = localStorage.getItem('clause-language') === 'zh' ? 'zh' : 'en'; } catch { /* storage optional */ }
document.documentElement.lang = language === "en" ? "en" : "zh-CN";
const listeners = new Set<() => void>();
export const getLanguage = () => language;
export function clauseCount(count: number) {
  return language === 'en' ? `${count} ${count === 1 ? 'clause' : 'clauses'}` : `${count} 项条款`;
}
export function setLanguage(value: Language) {
  language = value;
  document.documentElement.lang = value === 'en' ? 'en' : 'zh-CN';
  try { localStorage.setItem('clause-language', value); } catch { /* storage optional */ }
  listeners.forEach(listener => listener());
}
export function useLanguage() {
  return useSyncExternalStore(callback => { listeners.add(callback); return () => { listeners.delete(callback); }; }, getLanguage);
}
export function t<T>(value: T): T {
  if (language !== 'en' || typeof value !== 'string') return value;
  return systemText(value, language) as T;
}
export function traceTitle(event: { stage: string; title: string }) {
  return localizedTraceTitle(event, language);
}
export function traceDetail(event: { stage: string; detail: string }) {
  return localizedTraceDetail(event, language);
}
export function LanguageSwitch({ onChange, floating = false }: {
  onChange: (value: Language) => Promise<void> | void;
  floating?: boolean;
}) {
  const value = useLanguage();
  const [pending, setPending] = useState(false);
  const [error, setError] = useState('');
  const label = value === 'zh' ? '切换为英文' : 'Switch to Chinese';
  async function toggle() {
    setPending(true);
    setError('');
    try { await onChange(value === 'zh' ? 'en' : 'zh'); }
    catch (cause) { setError(cause instanceof Error ? cause.message : 'Language could not be saved'); }
    finally { setPending(false); }
  }
  return <span className={`language-control${floating ? ' language-control-floating' : ''}`}>
    <button type="button" className="language-switch" aria-label={label}
      title={label} disabled={pending} onClick={toggle} aria-busy={pending}>
      <span aria-hidden="true">{value === 'zh' ? '中' : 'EN'}</span>
    </button>
    {error && <span className="language-switch-error" role="alert">{error}</span>}
  </span>;
}
