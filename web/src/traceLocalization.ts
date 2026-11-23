import translations from './translations.json';

const dictionary: Record<string, string> = translations;
const reverse = Object.fromEntries(Object.entries(dictionary).map(([zh, en]) => [en, zh]));

// Translate application-owned text, never contract quotations or user content.
export function systemText(value: string, language: 'en' | 'zh'): string {
  const normalized = value.replace(/\s+/g, ' ').trim();
  if (language === 'zh') return reverse[value] ?? reverse[normalized] ?? value;
  const exact = dictionary[value] ?? dictionary[normalized];
  if (exact) return exact;
  if (normalized.includes(', ') && !/^[{[]/.test(normalized)) {
    const parts = normalized.split(', ');
    if (parts.every(part => dictionary[part])) return parts.map(part => dictionary[part]).join(', ');
  }
  const patterns: [RegExp, (match: RegExpMatchArray) => string][] = [
    [/^调用 (\S+)$/, m => `Calling ${m[1]}`],
    [/^(\S+) 已返回$/, m => `${m[1]} returned`],
    [/^(\S+) 未完成$/, m => `${m[1]} did not complete`],
    [/^正文已解析 · (\d+) 个段落$/, m => `Contract parsed · ${m[1]} paragraphs`],
    [/^通读合同 · 分块 (\d+)$/, m => `Reading contract · Chunk ${m[1]}`],
    [/^审查范围已确定 · (\d+) 项$/, m => `Review scope confirmed · ${m[1]} items`],
    [/^检索(.+)$/, m => `Retrieving ${systemText(m[1], 'en')}`],
    [/^(.+) · (批量证据校验完成|校验完成|等待模型|恢复已完成结果|待人工复核|需要复查)$/, m =>
      `${systemText(m[1], 'en')} · ${systemText(m[2], 'en')}`],
  ];
  for (const [pattern, format] of patterns) {
    const match = normalized.match(pattern);
    if (match) return format(match);
  }
  return value;
}

const stages: Record<string, [string, string]> = {
  queue: ['等待远端任务', 'Waiting for remote task'],
  connection: ['连接服务', 'Connecting to service'],
  parse: ['解析合同', 'Parsing contract'],
  scope: ['确定审查范围', 'Determining review scope'],
  survey: ['通读合同', 'Reading contract'],
  survey_error: ['通读未完成，使用检索继续', 'Full read incomplete; continuing with retrieval'],
  retrieve: ['检索合同证据', 'Retrieving contract evidence'],
  expand: ['扩展证据检索', 'Expanding evidence retrieval'],
  validate: ['验证证据', 'Verifying evidence'],
  review: ['审查条款', 'Reviewing clauses'],
  render: ['生成报告', 'Generating report'],
  cache: ['恢复已保存结果', 'Restoring saved results'],
  retry: ['重试处理', 'Retrying processing'],
  tool: ['调用工具', 'Calling tool'],
  observation: ['工具已返回', 'Tool returned'],
  tool_error: ['工具未完成', 'Tool did not complete'],
  done: ['任务处理结束', 'Task processing finished'],
};

export function localizedTraceTitle(event: { stage: string; title: string }, language: 'en' | 'zh') {
  if (event.stage === 'model') return language === 'en' ? 'Calling model' : '调用模型';
  const title = systemText(event.title, language);
  // Older tasks can contain untranslated server messages. Keep the stage useful
  // without exposing a different-language system label in the selected UI.
  if (language === 'en' && /[\u3400-\u9fff]/u.test(title)) {
    return (stages[event.stage] ?? ['处理任务', 'Processing task'])[1];
  }
  return title;
}

export function localizedTraceDetail(event: { stage: string; detail: string }, language: 'en' | 'zh') {
  if (event.stage === 'model') return '';
  return systemText(event.detail, language);
}
