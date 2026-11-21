import { useSyncExternalStore } from 'react';
import translations from './translations.json';
export type Language = 'zh' | 'en';
const dictionary: Record<string, string> = translations;
let language: Language = 'zh';
try { language = localStorage.getItem('clause-language') === 'en' ? 'en' : 'zh'; } catch { /* storage optional */ }
document.documentElement.lang = language === "en" ? "en" : "zh-CN";
const listeners = new Set<() => void>();
export const getLanguage = () => language;
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
  const normalized = value.replace(/\s+/g, ' ').trim();
  return (dictionary[value] ?? dictionary[normalized] ?? value) as T;
}
export function LanguageSwitch() {
  const value = useLanguage();
  return <div style={{ position: 'fixed', right: 16, bottom: 16, zIndex: 10000, display: 'flex', alignItems: 'center', gap: 10, padding: '7px 10px', borderRadius: 8, background: '#fff', color: '#263238', border: '1px solid #cbd5e1' }}>
    <a href={`/api/templates/${value}/payment-skill.json`} download>{value === 'en' ? 'Skill template' : 'Skill 模板'}</a>
    <a href={`/api/templates/${value}/review-request.txt`} download>{value === 'en' ? 'Request template' : '提问模板'}</a>
    <select aria-label="Language / 语言" value={value} onChange={event => setLanguage(event.target.value as Language)}>
    <option value="zh">中文</option><option value="en">English</option>
  </select></div>;
}
