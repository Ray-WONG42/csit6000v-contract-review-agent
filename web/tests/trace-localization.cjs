const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const ts = require('typescript');
const filename = path.resolve(__dirname, '../src/traceLocalization.ts');
const compiled = ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, esModuleInterop: true },
}).outputText;
const loaded = new Module(filename, module);
loaded.filename = filename;
loaded.paths = module.paths;
loaded._compile(compiled, filename);
const { localizedTraceTitle: title, localizedTraceDetail: detail } = loaded.exports;

test('stored Chinese traces render in English and switch back without rewriting records', () => {
  for (const [stage, original, english] of [
    ['queue', '提交 TACC 客户端任务', 'Submitting TACC client task'],
    ['queue', 'TACC 客户端已提交', 'TACC client task submitted'],
    ['tool', '调用 save_skill', 'Calling save_skill'],
    ['observation', 'save_skill 已返回', 'save_skill returned'],
    ['validate', '保险 · 批量证据校验完成', 'Insurance · Batch evidence verification completed'],
    ['parse', '正文已解析 · 125 个段落', 'Contract parsed · 125 paragraphs'],
  ]) {
    const event = { stage, title: original };
    assert.equal(title(event, 'en'), english);
    assert.equal(title(event, 'zh'), original);
    assert.equal(event.title, original);
  }
});

test('model details stay generic; identifiers and source content are preserved', () => {
  assert.equal(title({ stage: 'model', title: '调用 DeepSeek API' }, 'en'), 'Calling model');
  assert.equal(detail({ stage: 'model', detail: 'private-model' }, 'en'), '');
  assert.equal(detail({ stage: 'queue', detail: '30997' }, 'en'), '30997');
  const content = '{"name":"合同模板","saved":true}';
  assert.equal(detail({ stage: 'observation', detail: content }, 'en'), content);
  assert.equal(title({ stage: 'queue', title: '未来新增的中文状态' }, 'en'), 'Waiting for remote task');
  assert.equal(title({ stage: 'connection', title: 'Resolved model address before entering course runtime' }, 'zh'), '模型运行地址已就绪');
});
