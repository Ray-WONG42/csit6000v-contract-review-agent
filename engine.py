"""Contract parsing, retrieval, model adapter and bounded review workflow."""
import io
import json
import math
import os
import re
import time
import unicodedata
import urllib.error
import urllib.request
from urllib.parse import urlsplit
import zipfile
from prompt_loader import load_prompt
from collections import Counter
from itertools import zip_longest


RULES = {
    "termination": ("终止", ("termination", "terminate", "cancellation", "for convenience"), ("终止事由", "通知期限")),
    "liability_cap": ("责任上限", ("aggregate liability", "liability cap", "shall not exceed", "maximum liability"), ("上限金额或计算基准",)),
    "governing_law": ("适用法律", ("governing law", "governed by", "construed in accordance", "laws of"), ("适用法律",)),
    "automatic_renewal": ("自动续约", ("automatic renewal", "automatically renew", "successive term", "non-renewal", "unless either party"), ("续约期限", "通知期限", "通知方式")),
    "audit_rights": ("审计权", ("audit", "inspect records", "access to records", "examine books"), ("可审计范围", "通知要求")),
    "assignment": ("转让限制", ("assignment", "assign", "transfer", "prior written consent"), ("转让条件", "同意要求")),
    "insurance": ("保险", ("insurance", "coverage", "insured", "policy limit"), ("保险类型", "保险金额")),
    "agreement_date": ("协议日期", ("agreement date", "contract date", "dated", "made as of", "entered into", "executed on", "signed on"), ("协议订立或签署日期",)),
}
LEGACY_RULES = {
    "liquidated_damages": ("约定赔偿", ("liquidated damages", "agreed damages", "per day of delay", "delay damages"), ("金额或计算标准", "触发条件")),
}
MONTH = r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?"
DAY = r"(?:0?[1-9]|[12]\d|3[01])(?:st|nd|rd|th)?"
AGREEMENT_DATE_EVIDENCE = re.compile(
    r"(?:\b(?:agreement|contract|execution)\s+date\b(?:\s+is)?"
    r"|\b(?:this\s+)?(?:agreement|contract)\s+(?:(?:is|was)\s+)?"
    r"(?:dated|made(?:\s+and\s+entered\s+into)?|entered\s+into|executed|signed)(?:\s+(?:as\s+of|on))?"
    r"|(?:协议|合同|签署|订立)日期|本(?:协议|合同)(?:(?:签署|签订|订立)?于))"
    r"\s*[:|]?\s*(?:(?:this|the)\s+)?(?P<date>"
    rf"{MONTH}\s+{DAY},?\s+\d{{4}}|{DAY}\s+(?:day\s+of\s+)?{MONTH},?\s+\d{{4}}"
    r"|\d{4}[-/.]\d{1,2}[-/.]\d{1,2}|\d{1,2}[/.-]\d{1,2}[/.-]\d{4}|\d{4}年\s*\d{1,2}月\s*\d{1,2}日)(?!\d)",
    re.I,
)
TASK_STAGES = {"found", "not_found", "review"}
WORD = re.compile(r"[a-z0-9]+", re.I)
STANDARD_REQUESTS = {"review this contract using the selected checklist", "run standard contract review","", "标准审查", "标准合同审查", "进行标准合同审查", "请按所选清单审查这份合同",
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


def is_standard_request(instruction):
    return instruction.strip().lower().rstrip("。.!！") in STANDARD_REQUESTS


def custom_definitions(skill, instruction):
    rules = {**RULES, **{key: LEGACY_RULES[key] for key in skill["categories"] if key in LEGACY_RULES}}
    for index, item in enumerate(skill.get("custom_items", []), 1):
        rules[f"custom_{index}"] = (item["title"], (item["question"],), ())
    focus = skill.get("focus", "").strip()
    if focus and skill.get("explicit"):
        rules["custom_focus"] = ("Skill 专项要求", (focus,), ())
    if not is_standard_request(instruction):
        rules["custom_question"] = ("本次问题", (instruction.strip(),), ())
    return rules


def scope_plan(skill, instruction, model, definitions):
    if re.search(r"(?:比较|对比)(?:这)?两份合同|compare\s+(?:(?:two|both|the)\s+)?contracts|\bredline\b|修改原合同|重写合同", instruction, re.I):
        return [], "目前支持单份合同的审查。请提交要检查的类别或问题。"
    categories = skill["categories"]
    custom = [key for key in definitions if key.startswith("custom_") and key != "custom_question"]
    if is_standard_request(instruction):
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
        "agreement_date": r"(?:协议|合同)(?:日期|签订|签署|订立)|签署日期|订立日期|agreement date|contract date|execution date",
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


class ContextBudgetExceeded(ValueError):
    """The full request cannot fit; callers may split source paragraphs safely."""


class Model:
    def __init__(self, event, mode=None, ollama_model=None):
        self.event = event
        self.mode = mode or os.getenv("MODEL_MODE", "demo")
        self.calls = []
        self._ollama_model = ollama_model

    @property
    def ollama_model(self):
        from app.config import settings
        return self._ollama_model if self._ollama_model is not None else settings.ollama_model

    @property
    def signature(self):
        from app.config import settings
        import hashlib
        if self.mode == "ollama":
            source = ["survey-v2", self.mode, self.ollama_model, settings.ollama_url,
                      settings.ollama_context_limit, settings.ollama_max_output_tokens,
                      settings.llm_temperature, load_prompt("survey"), load_prompt("contract_system")]
            return hashlib.sha256(json.dumps(source).encode()).hexdigest()
        source = f"survey-v2|{self.mode}|{settings.tacc_model}|{settings.tacc_url}|{settings.tacc_context_limit}"
        source += '|' + load_prompt('survey') + '|' + load_prompt('contract_system')
        source += '|' + str(getattr(settings, 'llm_base_url', '')) + '|' + str(getattr(settings, 'model_name', ''))
        source += '|' + str(getattr(settings, 'llm_temperature', 0)) + '|' + str(settings.tacc_max_output_tokens)
        source += '|' + str(getattr(settings, 'llm_context_limit', '')) + '|' + str(getattr(settings, 'llm_max_output_tokens', ''))
        return hashlib.sha256(source.encode()).hexdigest()

    @staticmethod
    def _ollama_json(settings, path, timeout, body=None):
        request = urllib.request.Request(settings.ollama_url.rstrip("/") + path,
                                         data=json.dumps(body, ensure_ascii=False).encode() if body is not None else None,
                                         headers={"Content-Type": "application/json"})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(request, timeout=timeout) as response:
            return json.load(response)

    @classmethod
    def local_ollama_models(cls, selected=""):
        from app.config import settings
        try:
            catalog = cls._ollama_json(settings, "/api/tags", 4)["models"]
            names = set()
            for item in catalog:
                name = item.get("model") or item.get("name")
                if not name or (selected and name != selected) or item.get("remote_host") or item.get("remote_model"):
                    continue
                if not item.get("capabilities"):
                    try:
                        item = cls._ollama_json(settings, "/api/show", 4, {"model": name})
                    except urllib.error.HTTPError as exc:
                        if exc.code != 404:
                            raise
                        continue
                if "completion" in (item.get("capabilities") or []) and not (item.get("remote_host") or item.get("remote_model")):
                    names.add(name)
            return sorted(names, key=str.casefold)
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
            raise ValueError("无法读取 Ollama 模型列表，请检查本机 Ollama 服务。") from exc

    def require_local_ollama_model(self):
        if self.ollama_model not in self.local_ollama_models(self.ollama_model):
            raise ValueError("所选模型当前不在 Ollama 的本地对话模型列表中，请重新选择。")

    def _ollama_response(self, settings, system, payload, phase):
        if not settings.ollama_url or not self.ollama_model:
            raise ValueError("请配置 OLLAMA_URL 和 OLLAMA_MODEL。")
        body = {
            "model": self.ollama_model,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
            "stream": False,
            "format": "json",
            "think": False,
            "options": {"temperature": settings.llm_temperature,
                        "num_predict": settings.ollama_max_output_tokens,
                        "num_ctx": settings.ollama_context_limit},
        }
        raw = json.dumps(body, ensure_ascii=False).encode()
        if len(raw) + settings.ollama_max_output_tokens > settings.ollama_context_limit:
            raise ValueError("请求超出 Ollama 的保守上下文预算，请调小分块或调整 OLLAMA_CONTEXT_LIMIT。")
        self.event("model", "调用本地 Ollama 模型", phase)
        try:
            data = self._ollama_json(settings, "/api/chat", settings.ollama_timeout, body)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise ValueError("Ollama 找不到配置的模型，请检查 OLLAMA_MODEL 和 ollama list。") from exc
            raise ValueError(f"Ollama 请求失败（HTTP {exc.code}），请检查本机 Ollama 日志。") from exc
        except (TimeoutError, urllib.error.URLError) as exc:
            raise ValueError("Ollama 连接失败或超时，请检查本机服务和 OLLAMA_TIMEOUT。") from exc
        if data.get("done_reason") == "length":
            raise ValueError("Ollama 的输出达到上限，请调整 OLLAMA_MAX_OUTPUT_TOKENS。")
        usage = {"prompt_tokens": data.get("prompt_eval_count"), "completion_tokens": data.get("eval_count")}
        return data["message"]["content"], usage

    def embed(self, texts):
        from app.config import settings
        if not settings.ollama_url or not settings.ollama_embedding_model:
            raise ValueError("请配置 Ollama 向量模型和地址。")
        body = {"model": settings.ollama_embedding_model, "input": texts, "truncate": False}
        data = self._ollama_json(settings, "/api/embed", settings.ollama_embedding_timeout, body)
        embeddings = data.get("embeddings") if isinstance(data, dict) else None
        if not isinstance(embeddings, list) or len(embeddings) != len(texts):
            raise ValueError("Ollama 返回的向量数量不正确。")
        return embeddings

    @staticmethod
    def _parse_json(content):
        if not isinstance(content, str):
            raise ValueError("模型未返回文本 JSON。")
        clean = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", content, flags=re.I).strip()
        return json.loads(clean)

    def call(self, system, payload, phase):
        from app.config import settings
        start = time.perf_counter()
        if getattr(self, "output_language", "zh") == "en":
            system += "\n" + load_prompt("output_en")
        usage = {}
        error = ""
        try:
            if self.mode == "ollama":
                content, usage = self._ollama_response(settings, system, payload, phase)
                return self._parse_json(content)
            if self.mode not in ("tacc", "api"):
                raise RuntimeError("当前没有启用真实 TACC 模型。")
            api = self.mode == "api"
            context_limit = settings.llm_context_limit if api else settings.tacc_context_limit
            max_output = settings.llm_max_output_tokens if api else settings.tacc_max_output_tokens
            timeout = settings.llm_timeout if api else settings.tacc_timeout
            url = "" if api else settings.tacc_url
            if not url and getattr(settings, 'llm_base_url', ''):
                base = settings.llm_base_url.rstrip('/')
                url = base if base.endswith('/chat/completions') else base + '/chat/completions'
            model_name = settings.model_name if api else settings.tacc_model or getattr(settings, 'model_name', '')
            if not url or not model_name:
                raise RuntimeError("请配置 TACC_URL 和 TACC_MODEL。")
            body = {"model": model_name, "temperature": getattr(settings, 'llm_temperature', 0), "max_tokens": max_output,
                    "messages": [{"role": "system", "content": system},
                                 {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}]}
            # Conservative UTF-8 byte bound; never silently truncate the contract.
            if len(json.dumps(body, ensure_ascii=False).encode()) + max_output > context_limit:
                raise ContextBudgetExceeded("请求超出配置的保守上下文预算，请调小分块或确认课程上下文上限。")
            if settings.tacc_json_mode:
                body["response_format"] = {"type": "json_object"}
            if urlsplit(url).hostname == "api.deepseek.com":
                body["thinking"] = {"type": "disabled"}
            elif getattr(settings, "tacc_disable_thinking", False):
                body["chat_template_kwargs"] = {"enable_thinking": False}
            headers = {"Content-Type": "application/json"}
            if key := (settings.llm_api_key if api else settings.tacc_api_key or getattr(settings, 'llm_api_key', '')):
                headers["Authorization"] = f"Bearer {key}"
            self.event("model", "调用 DeepSeek API" if urlsplit(url).hostname == "api.deepseek.com" else "调用 TACC 模型", phase)
            request = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers, method="POST")
            with urllib.request.urlopen(request, timeout=timeout) as response:
                data = json.load(response)
            if not api and settings.tacc_protocol == "queue":
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
            try:
                return self._parse_json(data["choices"][0]["message"]["content"])
            except json.JSONDecodeError:
                if getattr(self, "_retry_json", False):
                    raise ValueError("模型连续两次返回无效 JSON，请重新发送。")
                self._retry_json = True
                try:
                    self.event("retry", "模型格式有误，重新生成一次", phase)
                    return self.call(system + "\n" + load_prompt("json_retry"), payload, phase)
                finally:
                    self._retry_json = False
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
        return {key: demo_row(key, paragraphs) for key in categories if key in RULES or key in LEGACY_RULES}
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
    try:
        response = model.call(SYSTEM, payload, phase)
    except ContextBudgetExceeded:
        if len(paragraphs) < 2:
            raise  # Metadata or a single source paragraph still cannot fit: report it explicitly.
        midpoint = len(paragraphs) // 2
        chunks = (paragraphs[:midpoint], paragraphs[midpoint:])
        model.event("chunk", "候选段落超出预算，分批审阅并合并证据", str(len(paragraphs)))
        pieces = [model_rows(model, categories, skill, instruction, chunk, phase,
                             evaluation, overview, definitions) for chunk in chunks]
        merged = {}
        for key in categories:
            rows = []
            for piece, chunk in zip(pieces, chunks):
                row = piece.get(key)
                if not isinstance(row, dict) or row.get("status") == "needs_context":
                    rows = []
                    break
                rows.append(validate(key, row, chunk, definitions))
            if not rows:
                merged[key] = {"category": key, "status": "needs_context", "evidence": []}
                continue
            evidence = list({(e["paragraph_id"], e["quote"]): e
                             for row in rows for e in row["evidence"]}.values())
            if len(evidence) > 5:
                merged[key] = {"category": key, "status": "needs_context", "evidence": []}
                continue  # Never drop evidence silently to fit the report schema.
            merged[key] = {"category": key, "status": "found" if evidence else "not_found",
                           "evidence": evidence,
                           "finding": "；".join(dict.fromkeys(r["finding"] for r in rows if r.get("finding"))),
                           "missing": list(dict.fromkeys(m for r in rows for m in r.get("missing", []))),
                           "question": "；".join(dict.fromkeys(r["question"] for r in rows if r.get("question")))}
        return merged
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
    pattern = AGREEMENT_DATE_EVIDENCE if category == "agreement_date" else re.compile(patterns[category], re.I)
    for i, p in enumerate(paragraphs):
        sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z])", p["text"])
        for sentence in sentences:
            match = pattern.search(sentence)
            if not match:
                continue
            if category == "agreement_date":
                return {"status": "found", "evidence": [{"paragraph_id": p["id"], "quote": match.group(0)}],
                        "finding": f"协议日期为 {match['date']}。", "missing": []}
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


def validate(category, row, candidates, definitions=None, retrieval_sources=None):
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
        verified.append({"paragraph_id": pid, "quote": source[start:start + len(phrase)], "page": allowed[pid]["page"],
                         "retrieval_sources": (retrieval_sources or {}).get(pid, [])})
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
    if model.mode == "ollama":
        context_limit, max_output = settings.ollama_context_limit, settings.ollama_max_output_tokens
    else:
        context_limit, max_output = settings.tacc_context_limit, settings.tacc_max_output_tokens
    budget = max(400, min(8000, (context_limit - max_output - 2000) // 3))
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


def review(paragraphs, skill, instruction, model, method="retrieval", evaluation=False, checkpoint=None,
           vector_hits=None):
    started = time.perf_counter()
    model.output_language = skill.get("language", "zh")
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
        # Batch normal built-in categories while keeping candidate budgets and
        # quotation validation separate for each category.
        # A failed/missing row falls through to the existing isolated retry path.
        batched_tacc = model.mode in ("tacc", "api") and method == "retrieval"
        cached = checkpoint("survey") if checkpoint and method == "retrieval" and not batched_tacc else None
        if cached and cached.get("signature") == model.signature and not any(k.startswith("custom_") for k in categories) and all(key in cached["hints"] for key in categories):
            hints, survey_partial = cached["hints"], cached["partial"]
            event("cache", "复用已完成的合同通读", "")
        elif batched_tacc:
            # The bounded candidate union replaces the model survey in this path.
            hints, survey_partial = {}, False
            event("retrieve", "使用关键词和向量检索构建批量候选" if vector_hits else "使用本地 BM25 构建批量候选", "")
        else:
            hints, survey_partial = survey_document(paragraphs, model, categories, definitions) if method == "retrieval" else ({}, False)
            if checkpoint and method == "retrieval":
                checkpoint("survey", {"hints": hints, "partial": survey_partial, "signature": model.signature})
        by_id = {p["id"]: i for i, p in enumerate(paragraphs)}
        vector_hits = vector_hits or {}

        def candidates_for(key, keyword_limit, fallback_limit, limit):
            # Specific questions over short contracts use complete source coverage.
            from app.config import settings
            budget = settings.ollama_context_limit if model.mode == "ollama" else settings.llm_context_limit if model.mode == "api" else settings.tacc_context_limit
            if key == "custom_question" and not vector_hits and len(json.dumps(paragraphs, ensure_ascii=False).encode()) < min(12000, budget // 2):
                event("retrieve", "本次问题读取完整短合同", str(len(paragraphs)) + " paragraphs")
                return {i: [] for i in range(len(paragraphs))}
            keyword = retrieval.search(key, keyword_limit, definitions[key])
            vector = [by_id[pid] for pid in vector_hits.get(key, []) if pid in by_id]
            survey = [by_id[pid] for pid in hints.get(key, []) if pid in by_id]
            # Alternate the two rankings so each can contribute at a fixed budget.
            ranked = [i for pair in zip_longest(keyword, vector) for i in pair if i is not None]
            fallback = list(range(min(fallback_limit, len(paragraphs)))) if not ranked else []
            ranked = ranked + fallback
            custom = key.startswith("custom_")
            if vector and survey:
                offset = 0 if custom else 2
                ordered = ranked[:offset] + survey[:1] + ranked[offset:] + survey[1:]
            else:
                ordered = survey + ranked if custom else ranked + survey
            sources = (("keyword", keyword), ("vector", vector), ("survey", survey), ("fallback", fallback))
            return {i: [source for source, matches in sources if i in matches]
                    for i in list(dict.fromkeys(ordered))[:limit]}

        def verify_candidates(key, row, selected):
            return validate(key, row, [paragraphs[i] for i in selected], definitions,
                            {paragraphs[i]["id"]: sources for i, sources in selected.items()})

        batch_rows = {}
        batch_indexes = {}
        batch_categories = [key for key in categories
                            if not key.startswith("custom_")
                            and not (checkpoint and checkpoint("category:" + key))]
        if batch_categories and model.mode in ("tacc", "api"):
            for key in batch_categories:
                batch_indexes[key] = candidates_for(key, 3, 2, 4)
            # Bound output size so a batch fits the model timeout and token budget.
            for offset in range(0, len(batch_categories), 3):
                group = batch_categories[offset:offset + 3]
                union = sorted({index for key in group for index in batch_indexes[key]})
                batch_candidates = [paragraphs[index] for index in union]
                if batch_candidates:
                    try:
                        batch_rows.update(model_rows(model, group, skill, instruction,
                                                     batch_candidates, "batch_extract", evaluation,
                                                     definitions=definitions))
                    except (ValueError, KeyError, TypeError, OSError, RuntimeError) as exc:
                        event("retry", "该批次提取失败，仅补查受影响类别", type(exc).__name__)
        for key in categories:
            label = definitions[key][0]
            if key.startswith("custom_") and model.mode == "demo":
                results.append(unresolved(key, "此自定义审查项需要启用 Ollama 或 TACC 模型后执行。"))
                event("review", label + " · 等待模型", "启用模型后重新审查")
                continue
            cached_result = checkpoint("category:" + key) if checkpoint else None
            if cached_result:
                results.append(cached_result)
                event("cache", label + " · 恢复已完成结果", "")
                continue
            if key in batch_categories and key in batch_rows:
                try:
                    result = verify_candidates(key, batch_rows[key], batch_indexes[key])
                except (ValueError, KeyError, TypeError):
                    pass
                else:
                    results.append(result)
                    event("validate", label + " · 批量证据校验完成", result["status"])
                    if checkpoint:
                        checkpoint("category:" + key, result)
                    continue
            indexes = candidates_for(key, 1 if model.mode == "demo" else 4, 3, 6)
            for attempt in range(2):
                candidates = [paragraphs[i] for i in sorted(indexes)]
                event("retrieve" if not attempt else "expand",
                      "检索" + label if not attempt else "补查相邻段落", ", ".join(p["id"] for p in candidates))
                try:
                    rows = model_rows(model, [key], skill, instruction, candidates, key, evaluation, definitions=definitions)
                    result = verify_candidates(key, rows.get(key), indexes)
                    event("validate", label + " · 校验完成", result["status"])
                    break
                except (ValueError, KeyError, TypeError, OSError, RuntimeError) as exc:
                    detail = str(exc) if isinstance(exc, ValueError) and "line " not in str(exc) else type(exc).__name__
                    if attempt:
                        result = unresolved(key, detail)
                        event("review", label + " · 待人工复核", detail)
                        break
                    event("retry", label + " · 需要复查", detail)
                    more = sorted({i + delta for i in indexes for delta in (-1, 0, 1) if 0 <= i + delta < len(paragraphs)})
                    indexes = {i: indexes.get(i, ["adjacent"]) for i in more}
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
    result = {"state": state, "rows": results, "calls": model.calls, "answer": "\n\n".join(answer),
              "seconds": round(time.perf_counter() - started, 3), "model_mode": model.mode}
    result["language"] = skill.get("language", "zh")
    if model.mode == "api":
        from app.config import settings
        result["model_name"] = settings.model_name
    if model.mode == "ollama":
        result["model_name"] = model.ollama_model
    return result


def report_text(name, result):
    from app.localization import text_for
    tr = lambda value: text_for(value, result.get("language", "zh"))
    mode = {"tacc": "TACC", "api": "API", "ollama": "本地 Ollama", "demo": "本地规则演示"}.get(
        result.get("model_mode"), "本地规则演示")
    if result.get("model_name"):
        mode += " · " + result["model_name"]
    lines = ["# 合同审查报告", "", f"审查对象：{name}", "",
             "本报告供人工复核。未找到对应原文表示本次未检出。",
             f"执行模式：{tr(mode)}", ""]
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
    if result.get("language") == "en":
        lines = [tr(line) for line in lines]
        lines = [line.replace("# 合同审查报告", "# Contract Review Report").replace("审查对象：", "Contract: ").replace("执行模式：", "Execution mode: ").replace("## ", "## ").replace("清单判断：", "Assessment: ").replace("待核对：", "To verify: ").replace("建议问题：", "Suggested question: ").replace("核对项 · ", "Checklist item · ") for line in lines]
        for index, line in enumerate(lines):
            if line.startswith("## "): lines[index] = "## " + tr(line[3:])
            elif line.startswith("Assessment: "): lines[index] = "Assessment: " + tr(line[len("Assessment: "):])
    return "\n".join(lines)


def docx_report(content, result=None):
    from app.localization import text_for
    tr = lambda value: text_for(value, (result or {}).get("language", "zh"))
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
        doc.add_heading(tr("合同风险表"), 0)
        doc.add_paragraph(tr("按所选审计清单核对。后附原文引用与待核验项。"))
        table = doc.add_table(rows=1, cols=4)
        table.style = "Light Shading Accent 1"
        for cell, value in zip(table.rows[0].cells, ["类别", "提取状态", "清单判断", "段落证据"]):
            cell.text = tr(value)
        header = OxmlElement("w:tblHeader")
        table.rows[0]._tr.get_or_add_trPr().append(header)
        for row in result["rows"]:
            cells = table.add_row().cells
            values = [row["label"], {"found": "已定位", "not_found": "未检出", "review": "待复核"}[row["status"]],
                      row.get("assessment", "待核验"), ", ".join(e["paragraph_id"] for e in row["evidence"]) or "—"]
            for cell, value in zip(cells, values):
                cell.text = tr(value)
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
