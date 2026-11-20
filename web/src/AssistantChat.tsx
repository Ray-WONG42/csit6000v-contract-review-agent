import { useEffect, useRef, useState } from "react";
import { ArrowUp, Globe, LoaderCircle, Plus, Download, Settings2 } from "lucide-react";
import { api, fileUrl } from "./api";
import { loadLocal, saveLocal, persistChat } from "./storage";

type Source = { url: string; title: string };
type Artifact = { file_id?: string; skill_id?: string; name: string; saved?: boolean };
type Trace = { stage: string; title: string; detail: string };
type Result = { answer: string; sources?: Source[]; artifacts?: Artifact[]; reviews?: { job_id: string; contract_id: string; state: string }[] };
type Message = { id: string; role: "user" | "assistant"; text: string; run?: string; pending?: boolean;
  error?: string; events?: Trace[]; output?: Result };

export default function AssistantChat({ userId, contractId = "", demo, onSkillsChanged, onReview }: {
  userId: string; contractId?: string; demo: boolean; onSkillsChanged: () => void;
  onReview: (contract: string, job: string) => void;
}) {
  const indexKey = "assistant-index:" + userId + ":" + (contractId || "general");
  const [threads, setThreads] = useState<{ id: string; title: string }[]>(() => {
    try { return (persistChat && JSON.parse(localStorage.getItem(indexKey) || "null")) || [{id: "default", title: "对话"}]; }
    catch { return [{id: "default", title: "对话"}]; }
  });
  const [thread, setThread] = useState("default");
  const storageKey = "assistant:" + (contractId || "general") + (thread === "default" ? "" : ":" + thread);
  const [messages, setMessages] = useState<Message[]>([]);
  const [draft, setDraft] = useState("");
  const [allowWeb, setAllowWeb] = useState(true);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState("");
  const [sending, setSending] = useState(false);
  const latest = useRef(messages);
  const active = messages.find(message => message.pending && message.run);
  const scrollEnd = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let cancelled = false;
    loadLocal(userId, storageKey).then(saved => {
      if (!cancelled) {
        const restored = saved.messages as Message[];
        latest.current = restored;
        setMessages(restored); setDraft(saved.draft); setLoaded(true);
      }
    }).catch(() => { if (!cancelled) { setLoaded(true); setError("浏览器记录读取失败"); } });
    return () => { cancelled = true; };
  }, [userId, storageKey]);

  useEffect(() => {
    if (!loaded) return;
    latest.current = messages;
    saveLocal(userId, storageKey, { messages, draft }).catch(() => setError("聊天本地保存失败"));
    scrollEnd.current?.scrollIntoView({ block: "end" });
  }, [messages, draft, loaded, userId, storageKey]);
  useEffect(() => { if (persistChat) localStorage.setItem(indexKey, JSON.stringify(threads)); }, [threads]);

  useEffect(() => {
    if (!active?.run) return;
    let cancelled = false;
    let pendingPoll = false;
    async function poll() {
      if (pendingPoll || cancelled) return;
      pendingPoll = true;
      try {
        const data = await api<{state: string; events: Trace[]; result: Result; error: string}>("/assistant/runs/" + active!.run);
        if (cancelled) return;
        const finished = data.state === "completed" || data.state === "failed";
        const next = latest.current.map(message => message.id === active!.id ? {
          ...message, events: data.events, pending: !finished,
          text: finished ? data.result.answer || data.error || "处理完成" : "",
          output: data.result, error: data.error,
        } : message);
        latest.current = next; setMessages(next);
        if (finished && data.result.artifacts?.some(item => item.skill_id)) onSkillsChanged();
      } catch (err: any) {
        if (!cancelled) setError(err.message);
      } finally { pendingPoll = false; }
    }
    poll();
    const timer = setInterval(poll, 1500);
    return () => { cancelled = true; clearInterval(timer); };
  }, [active?.run]);

  async function send(event?: React.FormEvent) {
    event?.preventDefault();
    if (!draft.trim() || sending || active || !loaded) return;
    setSending(true); setError("");
    const message = draft.trim();
    try {
      const history = messages.filter(item => !item.error && !item.pending && item.text)
        .slice(-8).map(item => ({ role: item.role, content: item.text.slice(0, 1500) }));
      const run = await api<{id: string}>("/assistant/runs", "POST", {
        request_key: crypto.randomUUID(), message, history, contract_id: contractId, allow_web: allowWeb,
      });
      const next: Message[] = [...messages, { id: crypto.randomUUID(), role: "user", text: message },
        { id: crypto.randomUUID(), role: "assistant", text: "", run: run.id, pending: true }];
      latest.current = next; setMessages(next); setDraft("");
      setThreads(current => current.map(item => item.id === thread && !messages.length ? {...item, title: message.slice(0, 35)} : item));
    } catch (err: any) { setError(err.message); }
    finally { setSending(false); }
  }

  return <div className="general-chat">
    <div className="general-heading">
      <span>{contractId ? "可结合当前合同提问" : "自由对话"}</span>
      <select aria-label="切换对话" disabled={!!active} value={thread} onChange={event => {setLoaded(false);setMessages([]);setThread(event.target.value);}}>
        {threads.map(item => <option key={item.id} value={item.id}>{item.title}</option>)}
      </select>
      <button disabled={!!active} onClick={() => {
        const id = crypto.randomUUID(); setLoaded(false); setMessages([]); latest.current = []; setDraft(""); setError("");
        setThreads([...threads, {id, title: "新对话"}]); setThread(id);
      }}><Plus size={14}/> 新对话</button>
    </div>
    <div className="general-messages">
      {!messages.length && <div className="general-intro">
        <h2>有什么想讨论或处理的？</h2>
        <p>可以聊天、查找资料、阅读网页，或为你的工作创建 Skill。</p>
        <div className="suggestions">
          {(demo ? ["/搜索 HKUST TACC 官方文档", '/创建Skill {"name":"保密条款检查","categories":[],"focus":"核对保密主体、范围、例外和存续期","policy":"evidence-only@1"}'] :
            ["帮我创建并保存一个保密条款审查 Skill", "联网查找 TACC 官方使用文档，并列出来源"]).map(text =>
            <button key={text} onClick={() => setDraft(text)}>{text}</button>)}
        </div>
      </div>}
      {messages.map(message => <div key={message.id} className={message.role === "user" ? "user-bubble" : "general-answer"}>
        {message.role === "assistant" && <div className="assistant-name"><div className="small-logo">C</div> 助手 {message.pending && <LoaderCircle className="spin" size={14}/>}</div>}
        {!!message.events?.length && <details className="trace" open={message.pending}>
          <summary>{message.events.length} 条模型与工具记录</summary>
          <div className="trace-list">{message.events.map((event, index) => <div key={index}><span className="trace-dot"/><div>{event.title}<small>{event.detail}</small></div></div>)}</div>
        </details>}
        <p className={message.error ? "error-text" : ""}>{message.text || (message.pending ? "正在处理…" : "")}</p>
        {!!message.output?.sources?.length && <div className="source-list"><strong>本轮获取的来源</strong>{message.output.sources.map(source =>
          <a key={source.url} href={source.url} target="_blank" rel="noopener noreferrer">{source.title}</a>)}</div>}
        {message.output?.artifacts?.map((item, index) => item.file_id ?
          <a key={index} className="secondary" href={fileUrl(item.file_id, true)}><Download size={14}/>{item.name}</a> :
          <div className="skill-saved" key={index}><Settings2 size={14}/>{item.name} · 已保存到我的 Skills</div>)}
        {message.output?.reviews?.map(item => <button key={item.job_id} className="secondary" onClick={() => onReview(item.contract_id, item.job_id)}>打开合同审查任务</button>)}
      </div>)}
      <div ref={scrollEnd}/>
    </div>
    {error && <div className="alert" role="alert">{error}</div>}
    <form className="composer-wrap" onSubmit={send}>
      {demo && <p className="general-mode">演示模式支持显式工具命令。开放问答与自动选择工具需要真实模型。</p>}
      <div className="composer"><textarea aria-label="自由对话消息" placeholder="输入问题、研究任务或 Skill 要求…" value={draft}
        onChange={event => setDraft(event.target.value)}
        onKeyDown={event => { if(event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing){event.preventDefault();send();} }}/>
        <div className="composer-controls"><label className="web-toggle"><input type="checkbox" checked={allowWeb} onChange={event => setAllowWeb(event.target.checked)}/><Globe size={14}/> 联网</label>
          <button type="submit" className="send-button" aria-label="发送自由对话" disabled={!loaded || !draft.trim() || sending || !!active}><ArrowUp size={18}/></button>
        </div>
      </div>
      <div className="composer-note">聊天保存在本浏览器 · 选中的历史消息会作为本轮上下文提交</div>
    </form>
  </div>;
}
