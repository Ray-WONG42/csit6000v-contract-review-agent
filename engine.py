"""Contract parsing, retrieval, model adapter and bounded review workflow."""
import io
import json
import math
import os
import re
import time
import unicodedata
import urllib.request
import zipfile
from prompt_loader import load_prompt
from collections import Counter


RULES = {
    "termination": ("终止", ("termination", "terminate", "cancellation", "for convenience"), ("终止事由", "通知期限")),
    "liability_cap": ("责任上限", ("aggregate liability", "liability cap", "shall not exceed", "maximum liability"), ("上限金额或计算基准",)),
    "governing_law": ("适用法律", ("governing law", "governed by", "construed in accordance", "laws of"), ("适用法律",)),
    "automatic_renewal": ("自动续约", ("automatic renewal", "automatically renew", "successive term", "non-renewal", "unless either party"), ("续约期限", "通知期限", "通知方式")),
    "audit_rights": ("审计权", ("audit", "inspect records", "access to records", "examine books"), ("可审计范围", "通知要求")),
    "assignment": ("转让限制", ("assignment", "assign", "transfer", "prior written consent"), ("转让条件", "同意要求")),
    "insurance": ("保险", ("insurance", "coverage", "insured", "policy limit"), ("保险类型", "保险金额")),
    "liquidated_damages": ("约定赔偿", ("liquidated damages", "agreed damages", "per day of delay", "delay damages"), ("金额或计算标准", "触发条件")),
}
TASK_STAGES = {"found", "not_found", "review"}
WORD = re.compile(r"[a-z0-9]+", re.I)
STANDARD_REQUESTS = {"", "标准审查", "标准合同审查", "进行标准合同审查", "请按所选清单审查这份合同",
                     "标准合同审查报告", "全面审查", "全面审查合同", "full review", "standard review"}
MAX_FILE = 12 * 1024 * 1024
DEFAULT_SKILL = {"name": "标准合同审查", "categories": list(RULES), "checks": {}, "focus": ""}


def normalize(value):
    return " ".join(unicodedata.normalize("NFKC", value).split())


def safe_zip(raw):
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        if "word/document.xml" not in archive.namelist() or sum(item.file_size for item in archive.infolist()) > 30 * 1024 * 1024:
            raise ValueError("Word 文件格式无效或解包后过大。")


def parse(name, raw):
    lower = name.lower()
    records = []
    if lower.endswith(".pdf"):
        from pypdf import PdfReader
        pdf = PdfReader(io.BytesIO(raw))
        if pdf.is_encrypted or len(pdf.pages) > 300:
            raise ValueError("请使用未加密且不超过 300 页的 PDF。")
        for number, page in enumerate(pdf.pages, 1):
            records.extend((number, text) for text in split_blocks(page.extract_text() or ""))
    elif lower.endswith(".docx"):
        from docx import Document
        from docx.table import Table
        from docx.text.paragraph import Paragraph
        safe_zip(raw)
        doc = Document(io.BytesIO(raw))
        for block in doc.iter_inner_content():
            if isinstance(block, Paragraph):
                records.append((None, block.text))
            elif isinstance(block, Table):
                records.extend((None, " | ".join(cell.text for cell in row.cells)) for row in block.rows)
    elif lower.endswith(".txt"):
        records = [(None, text) for text in split_blocks(raw.decode("utf-8-sig"))]
    else:
        raise ValueError("请上传文字版 PDF、DOCX 或 UTF-8 TXT。")
    result = []
    for page, raw_text in records:
        text = normalize(raw_text)
        start = 0
        while start < len(text):
            end = min(start + 1800, len(text))
            if end < len(text):
                split = max(text.rfind(". ", start + 900, end), text.rfind(" ", start + 900, end))
                if split > start:
                    end = split + 1
            part = text[start:end].strip()
            if part:
                result.append({"id": f"P{len(result) + 1:04d}", "page": page, "text": part,
                               "block_offset": start, "block_end": end})
            start = end
    if not result:
        raise ValueError("无法提取正文，请上传文字版 PDF 或 DOCX。")
    if sum(len(p["text"]) for p in result) > 700_000:
        raise ValueError("合同文本超出此原型的处理上限。")
    return result


def split_blocks(text):
    # PDF line wraps are retained within paragraphs unless a heading or blank line starts a block.
    return re.split(r"\n\s*\n|\n(?=\s*(?:\d+(?:\.\d+)*[.\)]\s|(?:section|article)\s+\d))", text, flags=re.I)


class Retrieval:
    def __init__(self, paragraphs):
        self.paragraphs = paragraphs
        self.documents = [Counter(WORD.findall(p["text"].lower())) for p in paragraphs]
        self.lengths = [sum(doc.values()) for doc in self.documents]
        self.average = sum(self.lengths) / len(self.lengths) or 1
        self.frequency = Counter(token for doc in self.documents for token in doc)

    def search(self, category, limit=4, rule=None):
        terms = set(WORD.findall(" ".join((rule or RULES[category])[1]).lower()))
        scores = []
        total = len(self.documents)
        for index, doc in enumerate(self.documents):
            score = 0
            for term in terms.intersection(doc):
                count = doc[term]
                idf = math.log(1 + (total - self.frequency[term] + .5) / (self.frequency[term] + .5))
                score += idf * (count * 2.5) / (count + 1.5 * (.25 + .75 * self.lengths[index] / self.average))
            if score > 0:
                scores.append((index, score))
        ranked = sorted(scores, key=lambda item: (-item[1], item[0]))
        return [index for index, _ in ranked[:limit]]


def custom_definitions(skill, instruction):
    rules = dict(RULES)
    for index, item in enumerate(skill.get("custom_items", []), 1):
        rules[f"custom_{index}"] = (item["title"], (item["question"],), ())
    focus = skill.get("focus", "").strip()
    if focus and skill.get("explicit"):
        rules["custom_focus"] = ("Skill 专项要求", (focus,), ())
    if instruction.strip().lower().rstrip("。.!！") not in STANDARD_REQUESTS:
        rules["custom_question"] = ("本次问题", (instruction.strip(),), ())
    return rules


def scope_plan(skill, instruction, model, definitions):
    if re.search(r"(?:比较|对比)(?:这)?两份合同|compare\s+(?:(?:two|both|the)\s+)?contracts|\bredline\b|修改原合同|重写合同", instruction, re.I):
        return [], "目前支持单份合同的审查。请提交要检查的类别或问题。"
    categories = skill["categories"]
    custom = [key for key in definitions if key.startswith("custom_") and key != "custom_question"]
    standard = instruction.strip().lower().rstrip("。.!！") in STANDARD_REQUESTS
    if standard:
        return categories + custom, ""
    if model.mode != "demo":
        return (categories if skill.get("explicit") else []) + custom + ["custom_question"], ""
    aliases = {
        "termination": r"终止|解约|terminat",
        "liability_cap": r"责任上限|赔偿上限|liability cap",
        "governing_law": r"适用法|准据法|governing law",
        "automatic_renewal": r"续约|续签|续期|更期|renewal",
        "audit_rights": r"审计|audit",
        "assignment": r"转让|assignment",
        "insurance": r"保险|insurance",
        "liquidated_damages": r"约定赔偿|违约金|liquidated damages",
    }
    matches = [key for key in categories if re.search(aliases[key], instruction, re.I)]
    if matches:
        return matches + custom, ""
    if re.search(r"^(那|这个|它|上述|这项|how (?:long|much)|what about|and )", instruction.strip(), re.I):
        previous = [key for key in skill.get("previous_categories", []) if key in categories]
        if previous:
            return previous + custom, ""
    return (categories if skill.get("explicit") else []) + custom + ["custom_question"], ""


class Model:
    def __init__(self, event, mode=None):
        self.event = event
        self.mode = mode or os.getenv("MODEL_MODE", "demo")
        self.calls = []

    @property
    def signature(self):
        from app.config import settings
        import hashlib
        source = f"survey-v2|{self.mode}|{settings.tacc_model}|{settings.tacc_url}|{settings.tacc_context_limit}"
        return hashlib.sha256(source.encode()).hexdigest()

    def call(self, system, payload, phase):
        from app.config import settings
        start = time.perf_counter()
        usage = {}
        error = ""
        try:
            if self.mode != "tacc":
                raise RuntimeError("当前没有启用真实 TACC 模型。")
            url = settings.tacc_url
            if not url and getattr(settings, 'llm_base_url', ''):
                base = settings.llm_base_url.rstrip('/')
                url = base if base.endswith('/chat/completions') else base + '/chat/completions'
            model_name = settings.tacc_model or getattr(settings, 'model_name', '')
            if not url or not model_name:
                raise RuntimeError("请配置 TACC_URL 和 TACC_MODEL。")
            body = {"model": model_name, "temperature": getattr(settings, 'llm_temperature', 0), "max_tokens": settings.tacc_max_output_tokens,
                    "messages": [{"role": "system", "content": system},
                                 {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}]}
            # Conservative UTF-8 byte bound; never silently truncate the contract.
            if len(json.dumps(body, ensure_ascii=False).encode()) + settings.tacc_max_output_tokens > settings.tacc_context_limit:
                raise ValueError("请求超出配置的保守上下文预算，请调小分块或确认课程上下文上限。")
            if settings.tacc_json_mode:
                body["response_format"] = {"type": "json_object"}
            if getattr(settings, "tacc_disable_thinking", False):
                body["chat_template_kwargs"] = {"enable_thinking": False}
            headers = {"Content-Type": "application/json"}
            if key := (settings.tacc_api_key or getattr(settings, 'llm_api_key', '')):
                headers["Authorization"] = f"Bearer {key}"
            self.event("model", "调用 TACC 模型", phase)
            request = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers, method="POST")
            with urllib.request.urlopen(request, timeout=settings.tacc_timeout) as response:
                data = json.load(response)
            if settings.tacc_protocol == "queue":
                if not settings.tacc_poll_url or "{job_id}" not in settings.tacc_poll_url:
                    raise ValueError("队列模式需要包含 {job_id} 的 TACC_POLL_URL。")
                from urllib.parse import quote
                remote_id = str(data["job_id"])
                deadline = time.monotonic() + settings.tacc_timeout
                while data.get("status") not in ("completed", "failed"):
                    if time.monotonic() >= deadline:
                        raise TimeoutError("TACC 排队超时")
                    self.event("queue", "TACC 请求排队中", remote_id)
                    time.sleep(2)
                    poll = urllib.request.Request(settings.tacc_poll_url.replace("{job_id}", quote(remote_id, safe="")), headers=headers)
                    with urllib.request.urlopen(poll, timeout=30) as response:
                        data = json.load(response)
                if data["status"] == "failed":
                    raise RuntimeError("TACC 队列任务失败")
                data = data["result"]
            usage = data.get("usage") or {}
            content = data["choices"][0]["message"]["content"]
            if not isinstance(content, str):
                raise ValueError("TACC 未返回文本 JSON。")
            clean = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", content, flags=re.I).strip()
            return json.loads(clean)
        except Exception as exc:
            error = type(exc).__name__
            raise
        finally:
            self.calls.append({"phase": phase, "input_tokens": usage.get("prompt_tokens"),
                               "output_tokens": usage.get("completion_tokens"),
                               "seconds": round(time.perf_counter() - start, 3), "error": error})
            if getattr(self, "save_calls", None):
                self.save_calls(self.calls)


SYSTEM = load_prompt("contract_system")


def model_rows(model, categories, skill, instruction, paragraphs, phase, evaluation=False, overview=None, definitions=None):
    definitions = definitions or RULES
    if model.mode == "demo":
        return {key: demo_row(key, paragraphs) for key in categories if key in RULES}
    payload = {
        "requested": [{"category": key, "definition": definitions[key][1][0],
                       "checklist": skill.get("checks", {}).get(key) or definitions[key][2]} for key in categories],
        "focus": skill.get("focus", ""), "request": instruction, "paragraphs": paragraphs,
        "previous_question": skill.get("previous_question", ""),
    }
    if evaluation:
        payload["evaluation_scope"] = "Termination is termination for convenience; automatic renewal requires explicit automatic extension."
    if overview:
        payload["navigation_only"] = overview
    response = model.call(SYSTEM, payload, phase)
    if not isinstance(response, dict) or not isinstance(response.get("rows"), list):
        raise ValueError("结构化响应缺少 rows。")
    rows = {}
    for row in response["rows"]:
        if isinstance(row, dict) and isinstance(row.get("category"), str):
            if row["category"] in rows:
                raise ValueError("响应包含重复类别。")
            rows[row["category"]] = row
    return rows


def demo_row(category, paragraphs):
    patterns = {
        "termination": r"\bterminat(?:e|ion)\b", "liability_cap": r"liability.{0,65}(?:shall not exceed|limited to)|liability cap",
        "governing_law": r"governed by|construed in accordance", "automatic_renewal": r"automatic(?:ally)? renew|renew automatic",
        "audit_rights": r"may audit|right to audit|inspect (?:the |its )?records",
        "assignment": r"may not assign|shall not assign|assignment requires|neither party may assign",
        "insurance": r"maintain.{0,65}insurance|insurance coverage",
        "liquidated_damages": r"liquidated damages",
    }
    for i, p in enumerate(paragraphs):
        sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z])", p["text"])
        for sentence in sentences:
            if not re.search(patterns[category], sentence, re.I):
                continue
            evidence = [{"paragraph_id": p["id"], "quote": sentence[:550]}]
            if category == "automatic_renewal" and not re.search(r"\d+\s+(?:(?:calendar|business)\s+)?days?", sentence, re.I):
                next_para = next((item for item in paragraphs[i + 1:]
                                  if int(item["id"][1:]) == int(p["id"][1:]) + 1
                                  and re.search(r"\d+\s+(?:(?:calendar|business)\s+)?days?", item["text"], re.I)), None)
                if next_para:
                    evidence.append({"paragraph_id": next_para["id"], "quote": next_para["text"][:350]})
                else:
                    return {"status": "needs_context", "evidence": evidence}
            return {"status": "found", "evidence": evidence, "missing": [],
                    "question": "请核对引用及适用条件。"}
    return {"status": "not_found", "evidence": []}


def validate(category, row, candidates, definitions=None):
    if not isinstance(row, dict) or row.get("status") not in {"found", "not_found", "needs_context"}:
        raise ValueError("模型未返回有效类别状态。")
    if row["status"] == "needs_context":
        raise ValueError("现有证据需要更多上下文。")
    evidence = row.get("evidence")
    if not isinstance(evidence, list) or len(evidence) > 5 or bool(evidence) != (row["status"] == "found"):
        raise ValueError("证据字段与类别状态不一致。")
    allowed = {p["id"]: p for p in candidates}
    verified = []
    for entry in evidence:
        if not isinstance(entry, dict):
            raise ValueError("引用格式无效。")
        pid, quote = entry.get("paragraph_id"), entry.get("quote")
        if not isinstance(pid, str) or not isinstance(quote, str) or pid not in allowed:
            raise ValueError("引用所用段落未被实际读取。")
        phrase = normalize(quote)
        source = allowed[pid]["text"]
        if not phrase or len(phrase) > 1200 or phrase not in source:
            raise ValueError("引用无法与合同原文对应。")
        start = source.index(phrase)
        verified.append({"paragraph_id": pid, "quote": source[start:start + len(phrase)], "page": allowed[pid]["page"]})
    missing = row.get("missing", [])
    question = row.get("question", "")
    finding = row.get("finding")
    return {"category": category, "label": (definitions or RULES)[category][0], "status": row["status"],
            "finding": finding[:2000] if isinstance(finding, str) and row["status"] == "found" else "",
            "evidence": verified, "missing": [x[:150] for x in missing[:10] if isinstance(x, str)] if isinstance(missing, list) else [],
            "question": question[:350] if isinstance(question, str) else ""}


def semantic_chunks(paragraphs, budget=10500):
    """Group contiguous paragraphs around headings, with a document-size-independent budget."""
    current, size = [], 0
    for paragraph in paragraphs:
        text = paragraph["text"]
        offset = 0
        while offset < len(text):
            end = min(offset + budget, len(text))
            if end < len(text):
                split = max(text.rfind(". ", offset + budget // 2, end), text.rfind(" ", offset + budget // 2, end))
                if split > offset:
                    end = split + 1
            part = text[offset:end]
            heading = re.match(r"^(?:section|article|schedule|appendix)\s+\w+|^\d+\.\s", part, re.I)
            if current and (size + len(part) > budget or heading and size >= 2800):
                yield current
                current, size = [], 0
            current.append({**paragraph, "text": part, "start": offset, "end": end})
            size += len(part)
            offset = end
    if current:
        yield current


def survey_document(paragraphs, model, categories, definitions):
    from app.config import settings
    hints = {key: [] for key in categories}
    partial = False
    budget = max(400, min(8000, (settings.tacc_context_limit - settings.tacc_max_output_tokens - 2000) // 3))
    for n, chunk in enumerate(semantic_chunks(paragraphs, budget), 1):
        model.event("survey", f"通读合同 · 分块 {n}", f"{chunk[0]['id']}–{chunk[-1]['id']}")
        if model.mode == "demo":
            continue
        try:
            reply = model.call(
                load_prompt("survey"),
                {"categories": {key: definitions[key][1][0] for key in categories}, "paragraphs": chunk}, "survey")
            if not isinstance(reply, dict):
                raise ValueError("Invalid survey.")
            allowed = {p["id"] for p in chunk}
            for key in categories:
                values = reply.get(key)
                if isinstance(values, list):
                    hints[key].extend(p for p in values[:2] if isinstance(p, str) and p in allowed)
        except (ValueError, KeyError, TypeError, OSError, RuntimeError):
            partial = True
            model.event("survey_error", "该分块通读失败，继续使用关键词检索", "")
    return hints, partial


def annotate(row, skill, definitions=None):
    fields = skill.get("checks", {}).get(row["category"]) or (definitions or RULES)[row["category"]][2]
    missing = set(row["missing"])
    checklist = []
    for item in fields:
        if row["status"] != "found":
            label = "待核验"
        elif item in missing:
            label = "未确认"
        else:
            label = "证据已提取 · 待人工确认"
        checklist.append({"requirement": item, "assessment": label})
    row["checklist"] = checklist
    row["assessment"] = "待核验"
    row["explanation"] = "已定位证据，需依据所选审计清单进一步核验。"
    row["policy"] = skill.get("policy", "supplier-demo@1")
    if row["status"] != "found":
        row["explanation"] = "现有证据不足以作出符合性判断。"
    if row.get("reason"):
        row["explanation"] = row["reason"]
    if row["category"] == "automatic_renewal" and row["status"] == "found" and row["policy"] == "supplier-demo@1":
        values = []
        for evidence in row["evidence"]:
            # Only explicit notice periods with stated calendar units meet this demonstration rule.
            matches = re.findall(r"(\d+)\s+calendar days?\s+(?:prior\s+)?(?:written\s+)?notice|(?:notice|notification).{0,45}?(\d+)\s+calendar days?", evidence["quote"], re.I)
            values.extend(int(a or b) for a, b in matches)
        if len(set(values)) == 1:
            days = values[0]
            row["assessment"] = "符合" if days >= 30 else "不符合"
            row["facts"] = {"notice_days": days, "unit": "calendar_days"}
            row["explanation"] = f"示例供应商审计清单 v1 要求至少提前 30 个自然日。引用明确为 {days} 个自然日，请人工确认适用场景。"
        else:
            row["explanation"] = "示例清单需明确的自然日通知期，当前证据存在缺项或冲突。"
    return row


def review(paragraphs, skill, instruction, model, method="retrieval", evaluation=False, checkpoint=None):
    started = time.perf_counter()
    event = model.event
    definitions = custom_definitions(skill, instruction)
    categories, clarify = scope_plan(skill, instruction, model, definitions)
    if clarify:
        return {"state": "needs_input", "message": clarify, "rows": [], "calls": model.calls}
    event("scope", f"审查范围已确定 · {len(categories)} 项", ", ".join(definitions[key][0] for key in categories))
    retrieval = Retrieval(paragraphs)
    results = []
    def unresolved(key, detail):
        return {"category": key, "label": definitions[key][0], "status": "review",
                "evidence": [], "missing": [], "question": "", "reason": detail}

    if method == "full":
        rows = model_rows(model, categories, skill, instruction, paragraphs, "full", evaluation, definitions=definitions)
        for key in categories:
            try:
                results.append(validate(key, rows.get(key), paragraphs, definitions))
            except ValueError as exc:
                results.append(unresolved(key, str(exc)))
    else:
        cached = checkpoint("survey") if checkpoint and method == "retrieval" else None
        if cached and cached.get("signature") == model.signature and not any(k.startswith("custom_") for k in categories) and all(key in cached["hints"] for key in categories):
            hints, survey_partial = cached["hints"], cached["partial"]
            event("cache", "复用已完成的合同通读", "")
        else:
            hints, survey_partial = survey_document(paragraphs, model, categories, definitions) if method == "retrieval" else ({}, False)
            if checkpoint and method == "retrieval":
                checkpoint("survey", {"hints": hints, "partial": survey_partial, "signature": model.signature})
        by_id = {p["id"]: i for i, p in enumerate(paragraphs)}
        for key in categories:
            label = definitions[key][0]
            if key.startswith("custom_") and model.mode == "demo":
                results.append(unresolved(key, "此自定义审查项需要连接 TACC 模型后执行。"))
                event("review", label + " · 等待模型", "连接 TACC 后重新审查")
                continue
            cached_result = checkpoint("category:" + key) if checkpoint else None
            if cached_result:
                results.append(cached_result)
                event("cache", label + " · 恢复已完成结果", "")
                continue
            indexes = retrieval.search(key, 4 if model.mode != "demo" else 1, definitions[key])
            # Read a small fallback sample for terminology not present in the keyword index.
            if not indexes:
                indexes = list(range(min(3, len(paragraphs))))
            suggested = [by_id[x] for x in hints.get(key, [])]
            ranked = suggested + indexes if key.startswith("custom_") else indexes + suggested
            indexes = list(dict.fromkeys(ranked))[:6]
            for attempt in range(2):
                candidates = [paragraphs[i] for i in sorted(indexes)]
                event("retrieve" if not attempt else "expand",
                      "检索" + label if not attempt else "补查相邻段落", ", ".join(p["id"] for p in candidates))
                try:
                    rows = model_rows(model, [key], skill, instruction, candidates, key, evaluation, definitions=definitions)
                    result = validate(key, rows.get(key), candidates, definitions)
                    event("validate", label + " · 校验完成", result["status"])
                    break
                except (ValueError, KeyError, TypeError, OSError, RuntimeError) as exc:
                    detail = str(exc) if isinstance(exc, ValueError) and "line " not in str(exc) else type(exc).__name__
                    more = sorted({i + delta for i in indexes for delta in (-1, 0, 1) if 0 <= i + delta < len(paragraphs)})
                    if attempt:
                        result = unresolved(key, detail)
                        event("review", label + " · 待人工复核", detail)
                        break
                    event("retry", label + " · 需要复查", detail)
                    indexes = more
            results.append(result)
            if checkpoint:
                checkpoint("category:" + key, result)
    results = [annotate(row, skill, definitions) for row in results]
    state = "partial" if any(row["status"] == "review" for row in results) or method != "full" and survey_partial else "complete"
    if results and all(r["status"] == "review" for r in results):
        state = "failed"
    answer = []
    for row in results:
        if row["status"] == "found":
            detail = "；".join(item["quote"] for item in row["evidence"])
            answer.append(f"{row['label']}：{row.get('finding') or detail}\n{row['explanation']}")
        else:
            answer.append(row["label"] + "：" + ("未检出支持原文。" if row["status"] == "not_found" else "需要人工复核。"))
    return {"state": state, "rows": results, "calls": model.calls, "answer": "\n\n".join(answer),
            "seconds": round(time.perf_counter() - started, 3), "model_mode": model.mode}


def report_text(name, result):
    lines = ["# 合同审查报告", "", f"审查对象：{name}", "",
             "本报告供人工复核。未找到对应原文表示本次未检出。",
             f"执行模式：{'TACC' if result.get('model_mode') == 'tacc' else '本地规则演示'}", ""]
    for row in result["rows"]:
        state = {"found": "找到证据", "not_found": "未找到对应原文", "review": "待人工复核"}[row["status"]]
        lines.extend([f"## {row['label']}", state])
        if row.get("finding"):
            lines.append(row["finding"])
        lines.extend(f"{item['paragraph_id']} · {item['quote']}" for item in row["evidence"])
        lines.append("清单判断：" + row.get("assessment", "待核验"))
        lines.append(row.get("explanation", ""))
        lines.extend(f"核对项 · {item['requirement']}：{item['assessment']}" for item in row.get("checklist", []))
        if row["missing"]:
            lines.append("待核对：" + "、".join(row["missing"]))
        if row["question"]:
            lines.append("建议问题：" + row["question"])
        lines.append("")
    return "\n".join(lines)


def docx_report(content, result=None):
    from docx import Document
    from docx.shared import Pt, Inches, RGBColor
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    doc = Document()
    doc.styles["Normal"].font.size = Pt(11)
    doc.styles["Normal"].font.name = "Calibri"
    doc.styles["Normal"].element.rPr.rFonts.set(qn("w:eastAsia"), "Microsoft YaHei")
    doc.sections[0].top_margin = Inches(.7)
    doc.sections[0].bottom_margin = Inches(.7)
    if result:
        doc.add_heading("合同风险表", 0)
        doc.add_paragraph("按所选审计清单核对。后附原文引用与待核验项。")
        table = doc.add_table(rows=1, cols=4)
        table.style = "Light Shading Accent 1"
        for cell, value in zip(table.rows[0].cells, ["类别", "提取状态", "清单判断", "段落证据"]):
            cell.text = value
        header = OxmlElement("w:tblHeader")
        table.rows[0]._tr.get_or_add_trPr().append(header)
        for row in result["rows"]:
            cells = table.add_row().cells
            values = [row["label"], {"found": "已定位", "not_found": "未检出", "review": "待复核"}[row["status"]],
                      row.get("assessment", "待核验"), ", ".join(e["paragraph_id"] for e in row["evidence"]) or "—"]
            for cell, value in zip(cells, values):
                cell.text = value
        doc.add_page_break()
    for line in content.splitlines():
        if line.startswith("# "):
            doc.add_heading(line[2:], 0)
        elif line.startswith("## "):
            doc.add_heading(line[3:], 1)
        elif line:
            doc.add_paragraph(line)
    buffer = io.BytesIO()
    doc.save(buffer)
    return buffer.getvalue()
