"""General chat with validated tool actions and replay-safe observations."""
import json
import re
from datetime import datetime, timezone, timedelta
from typing import Literal
from pydantic import BaseModel, Field, ConfigDict, model_validator
from sqlalchemy import select, text
from fastapi import HTTPException

from .config import settings
from .db import session, User, Contract, File, Skill, Job, dump, identifier
from .security import own
from .schemas import SkillInput, JobInput
from .services import create_job, write_file
from .web_tools import web_search, read_webpage
from engine import Model, parse, docx_report, RULES, Retrieval
from prompt_loader import load_prompt


class StrictArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SearchArgs(StrictArgs):
    query: str = Field(min_length=1, max_length=400)


class PageArgs(StrictArgs):
    url: str = Field(min_length=10, max_length=2000)


class EmptyArgs(StrictArgs):
    pass


class ContractArgs(StrictArgs):
    contract_id: str = Field(min_length=1, max_length=64)


class ReadContractArgs(ContractArgs):
    start: int = Field(default=0, ge=0)
    limit: int = Field(default=8, ge=1, le=24)
    query: str = Field(default="", max_length=400)


class ReviewArgs(ContractArgs):
    instruction: str = Field(default="", max_length=4000)
    skill_id: str = Field(default="", max_length=64)


class SaveSkillArgs(StrictArgs):
    skill: SkillInput

    @model_validator(mode="before")
    @classmethod
    def migrate_named_checks(cls, value):
        # Preserve custom questions emitted by models using descriptive check keys.
        if not isinstance(value, dict) or not isinstance(value.get("skill"), dict):
            return value
        body = dict(value["skill"])
        checks = body.get("checks", {})
        if not isinstance(checks, dict):
            return value
        aliases = {rule[0]: key for key, rule in RULES.items()}
        normalized, custom = {}, list(body.get("custom_items", []))
        for key, items in checks.items():
            canonical = aliases.get(key, key)
            if canonical in RULES:
                normalized[canonical] = items
            elif isinstance(items, list) and items and all(isinstance(x, str) for x in items):
                custom.append({"title": key, "question": "；".join(items)})
            else:
                normalized[key] = items
        body["checks"], body["custom_items"] = normalized, custom
        return {**value, "skill": body}


class DocumentArgs(StrictArgs):
    name: str = Field(min_length=1, max_length=80)
    content: str = Field(min_length=1, max_length=30000)


TOOL_ARGS = {
    "web_search": SearchArgs, "read_webpage": PageArgs,
    "list_contracts": EmptyArgs, "read_contract": ReadContractArgs,
    "review_contract": ReviewArgs, "list_skills": EmptyArgs,
    "save_skill": SaveSkillArgs, "create_document": DocumentArgs,
}
DESCRIPTIONS = {
    "web_search": "搜索公开互联网，返回来源链接与摘要。外部搜索查询只包含公开关键词，避免上传私人合同内容。",
    "read_webpage": "读取公开 HTTPS 网页正文，包括用户给出的链接。",
    "list_contracts": "列出当前账号拥有的合同及其 ID。",
    "read_contract": "分页读取合同原文及章节索引。使用返回的 next_start 继续读取；不要重复读取相同 start。精确引用段落 ID。",
    "review_contract": "为当前账号的合同创建后台审查任务，可指定 Skill。返回任务 ID 和状态。",
    "list_skills": "列出当前账号可复用的 Skill。",
    "save_skill": "新建并保存 Skill，仅在当前用户明确要求创建/保存模板时使用。",
    "create_document": "将用户需要的文本保存为可下载 Word 文件。",
}


def model_decision(system, payload, model, checkpoint):
    if model.mode == "demo":
        raise ValueError("当前为本地演示模式。通用聊天需要启用 Ollama 或 TACC。可使用 /搜索 关键词、/读取 HTTPS链接、/创建Skill JSON 和 /生成文档 内容。")
    if model.mode == "tacc" and settings.tacc_protocol == "sdk":
        from .tacc_sdk import run_remote
        return run_remote("decision", model.event, checkpoint, auto_start=True, system=system, payload=payload)
    return model.call(system, payload, "assistant")


def compact_schema(value):
    if isinstance(value, dict):
        return {k: ({name: compact_schema(schema) for name, schema in v.items()}
                    if k in ('properties', '$defs', 'definitions') else compact_schema(v))
                for k, v in value.items() if k not in ('title', 'default')}
    if isinstance(value, list):
        return [compact_schema(v) for v in value]
    return value


def tool_signature(name, arguments):
    if name == 'read_contract':
        arguments = ReadContractArgs.model_validate(arguments).model_dump()
    return name + json.dumps(arguments, sort_keys=True)


def relevant_history_excerpt(content, message):
    words = set(re.findall(r'[a-z0-9_]+|[\u4e00-\u9fff]', message.lower()))
    sentences = [s for s in re.split(r'(?<=[。！？.!?\n])\s*', content) if s.strip()]
    if not sentences:
        return content[:160]
    ranked = sorted(range(len(sentences)), key=lambda i: sum(w in sentences[i].lower() for w in words), reverse=True)
    chosen = sorted(set([0, *ranked[:2]]))
    return ' '.join(sentences[i] for i in chosen)[:400]


def fit_chat_payload(system, payload, mode):
    """Budget the complete chat request before submitting a remote job."""
    import copy
    payload = copy.deepcopy(payload)
    limit, output = ((settings.llm_context_limit, settings.llm_max_output_tokens) if mode == 'api' else
                     (settings.ollama_context_limit, settings.ollama_max_output_tokens) if mode == 'ollama' else
                     (settings.tacc_context_limit, settings.tacc_max_output_tokens))
    def fits():
        body = {'messages': [{'role': 'system', 'content': system},
                             {'role': 'user', 'content': json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}]}
        return len(json.dumps(body, ensure_ascii=False).encode()) + output + 768 <= limit
    history = payload.get('history', [])
    payload['history'] = history[-2:]
    if len(history) > 2:
        payload['conversation_summary'] = [
            {'role': item['role'], 'excerpt': relevant_history_excerpt(item['content'], payload['message']),
             'referenced_ids': re.findall(r'\b[a-f0-9]{32}\b', item['content']),
             'compressed': True} for item in history[:-2]][-6:]
    while not fits() and payload.get('conversation_summary'):
        item = payload['conversation_summary'].pop(0)
        memory = payload.setdefault('earlier_context', {'turns_compressed': 0, 'referenced_ids': []})
        memory['turns_compressed'] += 1
        memory['referenced_ids'] = list(dict.fromkeys(memory['referenced_ids'] + item['referenced_ids']))[-8:]
    while not fits() and payload.get('history'):
        payload['history'].pop(0)
        payload['earlier_history_omitted'] = True
    # Observation excerpts are navigation aids. Full, replay-safe results stay in checkpoints.
    while not fits():
        excerpts = [o for o in payload.get('observations', []) if len(o.get('result_excerpt', '')) > 160]
        if excerpts:
            longest = max(excerpts, key=lambda o: len(o['result_excerpt'].encode()))
            longest['result_excerpt'] = longest['result_excerpt'][:max(160, len(longest['result_excerpt']) // 2)]
            longest['excerpt_truncated'] = True
        elif len(payload.get('observations', [])) > 1:
            payload['observations'].pop(0)
            payload['earlier_observations_omitted'] = True
        else:
            raise ValueError('聊天请求超出模型上下文预算；请缩短当前问题或新建对话。尚未提交 TACC 作业。')
    return payload


def observation_context(observations):
    # Keep cursor/coverage metadata outside the text excerpt so it is never clipped away.
    unique = {}
    for item in observations:
        signature = tool_signature(item['tool'], item.get('arguments', {}))
        result = item['result']
        entry = {'tool': item['tool'], 'arguments': item.get('arguments', {}),
                 'result_excerpt': json.dumps(result, ensure_ascii=False)[:2000],
                 'excerpt_truncated': len(json.dumps(result, ensure_ascii=False)) > 2000}
        if isinstance(result, dict):
            entry['coverage'] = {key: result[key] for key in ('contract_id', 'start', 'next_start',
                                'total_paragraphs', 'truncated', 'selection_query', 'error') if key in result}
            if item['tool'] == 'read_contract' and 'next_start' not in entry['coverage']:
                entry['coverage'].update(start=0, next_start=len(result.get('paragraphs', [])) if result.get('truncated') else None)
        unique[signature] = entry
    return list(unique.values())[-3:]


def has_skill_permission(message):
    return bool(re.search(r"(?:创建|新建|写|做|生成|保存|建立).{0,80}(?:skill|技能|模板)|(?:skill|技能|模板).{0,40}(?:保存|创建|生成)|(?:create|save|write|build).{0,80}(?:skill|template)", message, re.I | re.S))


def run_tool(name, arguments, user, run_id, step, request):
    if name not in TOOL_ARGS:
        raise ValueError("未知工具")
    args = TOOL_ARGS[name].model_validate(arguments)
    if name == "web_search":
        if not request.get("allow_web", True):
            raise ValueError("本次对话已关闭联网")
        return web_search(args.query)
    if name == "read_webpage":
        if not request.get("allow_web", True):
            raise ValueError("本次对话已关闭联网")
        return read_webpage(args.url)
    with session() as db:
        if name == "list_contracts":
            return [{"contract_id": c.id, "name": c.name, "company": c.company} for c in
                    db.scalars(select(Contract).where(Contract.owner_id == user.id).limit(100))]
        if name == "list_skills":
            return [{"id": s.id, "version": s.version, **json.loads(s.content)} for s in
                    db.scalars(select(Skill).where(Skill.owner_id == user.id).limit(100))]
        if name == "read_contract":
            contract = own(db.get(Contract, args.contract_id), user)
            paragraphs = json.loads(contract.paragraphs)
            if not paragraphs:
                file = own(db.get(File, contract.file_id), user)
                paragraphs = parse(file.name, (settings.data / file.path).read_bytes())
                contract.paragraphs = dump(paragraphs)
            # Explicit clipping metadata prevents claims of having read omitted text.
            query = args.query or (request.get('message', '')[:400] if args.start == 0 else '')
            ranking = list(range(len(paragraphs)))
            if query:
                lexical = Retrieval(paragraphs).search('custom_question', 8, ('本次问题', [query], []))
                semantic = []
                if settings.contract_vector_search:
                    try:
                        from .retrieval import vector_matches
                        hits = vector_matches(paragraphs, {'categories': []}, query, Model(lambda *_: None, 'api'))
                        lookup = {p['id']: i for i, p in enumerate(paragraphs)}
                        semantic = [lookup[pid] for pid in hits.get('custom_question', []) if pid in lookup]
                    except (ImportError, OSError, RuntimeError, ValueError, TypeError):
                        pass
                ranking = list(dict.fromkeys(semantic + lexical + ranking))
            output, length = [], 0
            for index in ranking[args.start:args.start + args.limit]:
                paragraph = paragraphs[index]
                size = len(json.dumps(paragraph, ensure_ascii=False).encode())
                if output and length + size > 6000:
                    break
                output.append(paragraph)
                length += size
            next_start = args.start + len(output)
            outline = [{'paragraph_id': p['id'], 'heading_excerpt': p['text'][:85]}
                       for p in paragraphs if re.match(r'\s*(?:Section|Article)\s+\d+(?:\.(?!\d))?\s+', p['text'], re.I)][:24]
            return {"contract_id": contract.id, "outline": outline, "paragraphs": output, "total_paragraphs": len(paragraphs),
                    "start": args.start, "next_start": next_start if next_start < len(paragraphs) else None,
                    "truncated": args.start > 0 or next_start < len(paragraphs), "selection_query": query}
        if name == "review_contract":
            db.execute(text("BEGIN IMMEDIATE"))
            job = create_job(db, user, args.contract_id, JobInput(instruction=args.instruction,
                             skill_id=args.skill_id, language=request.get("language", "zh"), request_key=f"agent-{run_id}-{step}",
                             model_mode=request.get("model_mode", ""),
                             ollama_model=request.get("ollama_model", "")),
                             model_snapshot=request.get("model_snapshot"))
            return {"job_id": job.id, "contract_id": job.contract_id, "state": job.state}
        if name == "save_skill":
            if not has_skill_permission(request["message"]):
                raise ValueError("用户当前没有要求创建或保存 Skill，请先展示草稿")
            skill_id = f"agent-{run_id}-{step}"
            stored = db.get(Skill, skill_id)
            if stored:
                own(stored, user)
                return {"skill_id": stored.id, "name": stored.name, "saved": True}
            body = args.skill.model_dump()
            skill = Skill(id=skill_id, owner_id=user.id, name=body["name"], content=dump(body))
            db.add(skill)
            return {"skill_id": skill_id, "name": body["name"], "saved": True}
        if name == "create_document":
            # Stable artifact association makes restart/replay reuse the same output.
            job_key = f"agent-{run_id}-{step}"
            stored = db.scalar(select(File).where(File.job_id == job_key, File.owner_id == user.id))
            if not stored:
                name = re.sub(r'[<>:"/\\|?*]', "_", args.name).rstrip(". ") + ".docx"
                stored = write_file(db, user.id, request.get("contract_id", ""), name,
                                    docx_report(args.content), "draft", job_key)
            return {"file_id": stored.id, "name": stored.name}
    raise ValueError("工具未执行")


def collect_sources(observation, sources):
    entries = observation.get("results", []) if isinstance(observation, dict) else []
    if isinstance(observation, dict) and observation.get("url"):
        entries = [observation, *entries]
    for item in entries:
        url = item.get("url", "")
        if url.startswith("https://") and not any(s["url"] == url for s in sources):
            sources.append({"url": url, "title": item.get("title") or url})


def direct_demo(request):
    message = request["message"].strip()
    for english, chinese in (("/search ", "/搜索 "), ("/read ", "/读取 "), ("/create-skill ", "/创建Skill "), ("/document ", "/生成文档 ")):
        if message.startswith(english):
            message = chinese + message[len(english):]
            break
    if message.startswith("/搜索 "):
        return {"tool": "web_search", "arguments": {"query": message[4:].strip()}}
    if message.startswith("/读取 "):
        return {"tool": "read_webpage", "arguments": {"url": message[4:].strip()}}
    if message.startswith("/创建Skill "):
        return {"tool": "save_skill", "arguments": {"skill": json.loads(message[len("/创建Skill "):])}}
    if message.startswith("/生成文档 "):
        return {"tool": "create_document", "arguments": {"name": "对话文档", "content": message[len("/生成文档 "):]}}
    return None


def run_assistant(user_id, request, run_id, event, checkpoint, decide=None):
    with session() as db:
        user = db.get(User, user_id)
        if not user or user.role != "legal":
            raise ValueError("只有普通法务账号可以使用助手")
        if request.get("contract_id"):
            own(db.get(Contract, request["contract_id"]), user)
    available = {key: {"description": DESCRIPTIONS[key], "arguments_schema": compact_schema(model.model_json_schema())}
                 for key, model in TOOL_ARGS.items() if key in settings.agent_tools.split(",") and (request.get("allow_web", True) or key not in ("web_search", "read_webpage"))}
    system = "\n\n".join(load_prompt(name) for name in ("assistant_system", "tool", "reflection"))
    system += "\n" + load_prompt("output_en" if request.get("language") == "en" else "output_zh")
    observations = checkpoint("observations") or []
    sources, artifacts, reviews = [], [], []
    snapshot = request.get("model_snapshot")
    if not snapshot and request.get("resolved_ollama_model"):
        snapshot = {"model_mode": "ollama", "ollama_model": request["resolved_ollama_model"]}
        request = {**request, "model_snapshot": snapshot}
    snapshot = snapshot or {}
    model = Model(event, snapshot.get("model_mode") or settings.model_mode,
                  ollama_model=snapshot.get("ollama_model") or None)
    if model.mode == "ollama" and request.get("ollama_model"):
        model.require_local_ollama_model()
    model_info = {"mode": model.mode}
    if model.mode == "api":
        model_info["model_name"] = settings.model_name
    if model.mode == "ollama":
        model_info["model_name"] = model.ollama_model
    demo = model.mode == "demo"
    for step in range(7):
        if step < len(observations):
            saved = observations[step]
            collect_sources(saved["result"], sources)
            if saved["tool"] == "create_document" and saved["result"].get("file_id"):
                artifacts.append(saved["result"])
            if saved["tool"] == "save_skill" and saved["result"].get("saved"):
                artifacts.append(saved["result"])
            if saved["tool"] == "review_contract" and saved["result"].get("job_id"):
                reviews.append(saved["result"])
            continue
        decision_key = "decision:" + str(step)
        decision = checkpoint(decision_key)
        if decision is None:
            demo_tool = direct_demo(request) if demo and step == 0 else None
            if demo_tool:
                decision = {"action": "tool", **demo_tool}
            elif demo and step > 0:
                decision = {"action": "final", "answer": "工具执行结果\n" + json.dumps(observations[-1]["result"], ensure_ascii=False, indent=2)[:12000]}
            else:
                if model.mode != "tacc" or settings.tacc_protocol != "sdk":
                    event("model", "理解请求并选择工具", "")
                context = observation_context(observations)
                history = [{"role": item["role"], "content": item["content"]} for item in (request.get("history", []) if settings.agent_short_term_memory else [])[-8:]]
                payload = {"message": request["message"], "history": history,
                           "contract_id": request.get("contract_id", ""), "tools": available,
                           "observations": context, "remaining_tool_calls": 6 - step,
                           "local_date": datetime.now(timezone(timedelta(hours=8))).date().isoformat()}
                if step == 6:
                    payload["tools"] = {}
                    payload["instruction"] = load_prompt("budget")
                payload = fit_chat_payload(system, payload, model.mode)
                def scoped_checkpoint(key, value=None):
                    return checkpoint(f"model:{step}:" + key, value)
                decision = decide(system, payload) if decide else model_decision(system, payload, model, scoped_checkpoint)
            if not isinstance(decision, dict) or decision.get("action") not in ("final", "tool"):
                raise ValueError("模型没有返回有效的聊天或工具动作")
            checkpoint(decision_key, decision)
        if decision["action"] == "final":
            answer = decision.get("answer")
            if not isinstance(answer, str) or not answer.strip():
                raise ValueError("模型没有返回回答内容")
            return {"answer": answer[:30000], "sources": sources, "artifacts": artifacts, "reviews": reviews,
                    **model_info}
        if step == 6:
            break
        name, arguments = decision.get("tool"), decision.get("arguments")
        event("tool", "调用 " + str(name), str(decision.get("reason", ""))[:300])
        try:
            if name not in available:
                raise ValueError("本次对话未启用此工具")
            previous = next((item for item in reversed(observations)
                             if name == 'read_contract' and item['tool'] == name
                             and tool_signature(name, item.get('arguments', {})) == tool_signature(name, arguments)), None)
            if previous:
                result = {**previous['result'], 'reused': True,
                          'instruction': 'This page was already read. Use next_start with the same selection_query to continue, or answer from the evidence. Do not repeat this request.'}
            else:
                result = run_tool(name, arguments, user, run_id, step, request)
            event("observation", str(name) + " 已返回", json.dumps(result, ensure_ascii=False)[:700])
        except HTTPException:
            result = {"error": "记录不存在或当前账号无权访问"}
            event("tool_error", str(name) + " 未完成", result["error"])
        except (ValueError, OSError, TimeoutError) as exc:
            result = {"error": str(exc)[:250] if isinstance(exc, ValueError) else type(exc).__name__}
            event("tool_error", str(name) + " 未完成", result["error"])
        prior_errors = sum(item["tool"] == name and bool(item["result"].get("error"))
                           for item in observations if isinstance(item["result"], dict))
        observations.append({"tool": name, "arguments": arguments, "result": result})
        checkpoint("observations", observations)
        if isinstance(result, dict) and result.get("error") and prior_errors >= 1:
            return {"answer": f"工具 {name} 连续两次执行失败，已停止重试。原因：{result['error']}。本次操作尚未完成。",
                    "sources": sources, "artifacts": artifacts, "reviews": reviews, **model_info}
        collect_sources(result, sources)
        if name in ("create_document", "save_skill") and not result.get("error"):
            artifacts.append(result)
        if name == "review_contract" and result.get("job_id"):
            reviews.append(result)
    return {"answer": "已到达本轮工具调用上限，请根据处理记录继续提问。", "sources": sources,
            "artifacts": artifacts, "reviews": reviews, **model_info}
