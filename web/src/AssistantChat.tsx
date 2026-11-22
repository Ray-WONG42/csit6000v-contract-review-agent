import { t, useLanguage, LanguageSwitch, getLanguage } from "./i18n";
import { useEffect, useRef, useState } from "react";
import {
  ArrowLeft, ArrowRight, ArrowUp, ArrowUpRight, BookOpen, Download,
  FileCheck2, FileSearch, FileText, FolderOpen, Globe, LoaderCircle,
  MessageSquareText, Plus, Settings2, Upload,
} from "lucide-react";
import { api, fileUrl } from "./api";
import { loadLocal, saveLocal, persistChat } from "./storage";

type Source = { url: string; title: string };
type Artifact = { file_id?: string; skill_id?: string; name: string; saved?: boolean };
type Trace = { stage: string; title: string; detail: string };
type Result = { answer: string; sources?: Source[]; artifacts?: Artifact[]; reviews?: { job_id: string; contract_id: string; state: string }[] };
type Message = { id: string; role: "user" | "assistant"; text: string; run?: string; pending?: boolean;
  error?: string; events?: Trace[]; output?: Result };
type DeskContract = { id: string; name: string; company: string; created: number };

const fileDate = { format: (value: number) => new Intl.DateTimeFormat(getLanguage() === "en" ? "en-GB" : "zh-HK", { month: "short", day: "numeric" }).format(value) };
const skillPrompt = t("帮我创建并保存一个保密条款审查 Skill，核对保密主体、范围、例外和存续期。");
const demoSkillPrompt = t("/创建Skill {\"name\":\"保密条款检查\",\"categories\":[],\"focus\":\"核对保密主体、范围、例外和存续期\",\"policy\":\"evidence-only@1\"}");

export default function AssistantChat({
  userId, contractId = "", demo, modelMode, ollamaModel, onSkillsChanged, onReview,
  deskContracts = [], deskVisit, onOpenContract, onUpload, onOpenSkills,
}: {
  userId: string; contractId?: string; demo: boolean; modelMode: "" | "ollama" | "tacc" | "api";
  ollamaModel: string; onSkillsChanged: () => void;
  onReview: (contract: string, job: string) => void;
  deskContracts?: DeskContract[]; deskVisit?: number; onOpenContract?: (id: string) => void;
  onUpload?: () => void; onOpenSkills?: () => void;
}) {
  const indexKey = "assistant-index:" + userId + ":" + (contractId || "general");
  const [threads, setThreads] = useState<{ id: string; title: string }[]>(() => {
    try { return (persistChat && JSON.parse(localStorage.getItem(indexKey) || "null")) || [{id: "default", title: t("对话")}]; }
    catch { return [{id: "default", title: t("对话")}]; }
  });
  const [thread, setThread] = useState("default");
  const storageKey = "assistant:" + (contractId || "general") + (thread === "default" ? "" : ":" + thread);
  const [messages, setMessages] = useState<Message[]>([]);
  const [draft, setDraft] = useState("");
  const [allowWeb, setAllowWeb] = useState(true);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState("");
  const [sending, setSending] = useState(false);
  const [overview, setOverview] = useState(!contractId);
  const latest = useRef(messages);
  const active = messages.find(message => message.pending && message.run);
  const chatLog = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLTextAreaElement>(null);
  useEffect(() => {
    if (deskVisit !== undefined) setOverview(true);
  }, [deskVisit]);

  useEffect(() => {
    let cancelled = false;
    loadLocal(userId, storageKey).then(async saved => {
      const restored = saved.messages as Message[];
      // Reconcile failed local messages with tasks recovered on the server.
      await Promise.allSettled(restored.filter(message => message.run && (message.pending || message.error)).slice(-5).map(async message => {
        const data = await api<{state: string; events: Trace[]; result: Result; error: string}>("/assistant/runs/" + message.run);
        const finished = data.state === "completed" || data.state === "failed";
        Object.assign(message, {events: data.events, pending: !finished, output: data.result, error: data.error,
          text: finished ? data.result.answer || data.error || t("处理完成") : ""});
      }));
      if (!cancelled) {
        latest.current = restored;
        setMessages(restored); setDraft(saved.draft); setLoaded(true);
        if (restored.some(message => message.pending && message.run)) setOverview(false);
      }
    }).catch(() => { if (!cancelled) { setLoaded(true); setError(t("浏览器记录读取失败")); } });
    return () => { cancelled = true; };
  }, [userId, storageKey]);

  useEffect(() => {
    if (!loaded) return;
    latest.current = messages;
    saveLocal(userId, storageKey, { messages, draft }).catch(() => setError(t("聊天本地保存失败")));
  }, [messages, draft, loaded, userId, storageKey]);

  useEffect(() => {
    const log = chatLog.current;
    if (log) log.scrollTop = log.scrollHeight;
  }, [messages, overview]);

  useEffect(() => { if (persistChat) localStorage.setItem(indexKey, JSON.stringify(threads)); }, [threads, indexKey]);

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
          text: finished ? data.result.answer || data.error || t("处理完成")
            : data.state === "waiting_tacc" ? t("等待学校 GPU 集群…") : "",
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
        model_mode: modelMode,
        ollama_model: ollamaModel,
      });
      const next: Message[] = [...messages, { id: crypto.randomUUID(), role: "user", text: message },
        { id: crypto.randomUUID(), role: "assistant", text: "", run: run.id, pending: true }];
      latest.current = next; setMessages(next); setDraft(""); setOverview(false);
      setThreads(current => current.map(item => item.id === thread && !messages.length ? {...item, title: message.slice(0, 35)} : item));
    } catch (err: any) { setError(err.message); }
    finally { setSending(false); }
  }

  function openThread(id: string) {
    setOverview(false);
    if (id === thread) return;
    setLoaded(false); setMessages([]); latest.current = []; setDraft(""); setError(""); setThread(id);
  }

  const recent = [...deskContracts].sort((a, b) => b.created - a.created).slice(0, 3);
  const prompts = [
    { icon: BookOpen, title: t("拟定保密条款核查清单"), text: demo ? demoSkillPrompt : skillPrompt },
    contractId && !demo
      ? { icon: FileSearch, title: t("查找协议日期与责任主体"), text: t("请从当前合同中找出协议日期和签约主体，并引用原文段落。") }
      : { icon: Globe, title: t("检索公开资料与来源"), text: demo ? t("/搜索 香港 公司注册处 查册") : t("查找香港公司注册处介绍公司查册程序的官方资料，并列出来源。") },
  ];
  const promptButtons = prompts.map(({ icon: Icon, title, text }) => (
    <button type="button" key={title} disabled={!loaded || sending} onClick={() => { setDraft(text); input.current?.focus(); }}>
      <Icon size={17} aria-hidden="true" /><span>{t(title)}</span><ArrowUpRight size={14} aria-hidden="true" />
    </button>
  ));
  const threadControls = (
    <div className="thread-controls">
      {t(overview && messages.length > 0 && <button type="button" onClick={() => setOverview(false)}>{t("打开对话")}<ArrowRight size={13}/></button>)}
      <select aria-label={t("切换对话")} disabled={!!active || sending} value={thread} onChange={event => openThread(event.target.value)}>
        {t(threads.map(item => <option key={item.id} value={item.id}>{item.title}</option>))}
      </select>
      <button type="button" disabled={!!active || sending} onClick={() => {
        const id = crypto.randomUUID();
        setThreads([...threads, {id, title: t("新对话")}]); openThread(id);
      }}><Plus size={14}/> {t("新对话")}</button>
    </div>
  );

  return <div className={`general-chat ${overview ? "desk-overview" : "discussion"}`}>
    {t(overview ? <div className="desk-content">
      <section className="desk-cover" aria-labelledby="desk-title">
        <div className="desk-cover-copy">
          <p className="desk-kicker">{t("合同审查工作台")}</p>
          <h1 id="desk-title" lang="en">Review desk</h1>
          <p className="desk-lede">{t("审阅合同条款，核对原文证据，编制审查报告。")}</p>
          <div className="desk-cover-actions">
            <button className="desk-upload" type="button" onClick={onUpload}><Upload size={16}/> {t("上传新合同")}</button>
            <button className="desk-skills" type="button" onClick={onOpenSkills}>{t("我的审查模板")}<ArrowUpRight size={15}/></button>
          </div>
        </div>
        <div className="desk-cover-image" aria-hidden="true"/>
      </section>
      <div className="desk-columns">
        <section className="desk-register">
          <h2><FolderOpen size={17} aria-hidden="true"/> {t("最近合同")}</h2>
          {t(recent.length > 0 ? <ul>{t(recent.map(contract => <li key={contract.id}>
            <button type="button" onClick={() => onOpenContract?.(contract.id)}>
              <FileText size={18}/><span className="desk-file-title"><strong>{contract.name}</strong>{t(contract.company && <small>{t(contract.company)}</small>)}</span>
              <time dateTime={new Date(contract.created * 1000).toISOString()}>{t(fileDate.format(contract.created * 1000))}</time>
              <ArrowUpRight size={14}/>
            </button>
          </li>))}</ul> : <div className="desk-empty-register">
            <FileText size={25} strokeWidth={1.35} aria-hidden="true"/>
            <div><strong>{t("尚未添加合同")}</strong><p>{t("支持 PDF、DOCX 和 TXT，单份最大 12 MB。")}</p></div>
          </div>)}
        </section>
        <section className="desk-tools">
          <h2><FileCheck2 size={17} aria-hidden="true"/> {t("常用审查任务")}</h2>
          <div className="desk-prompts">{t(promptButtons)}</div>
        </section>
      </div>
    </div> : <>
      <div className="general-heading">
        <span className="chat-section-heading"><MessageSquareText size={17}/>{t(contractId ? t("合同对话") : t("法务助手"))}</span>
        <div className="chat-heading-actions">
          {t(!contractId && <button type="button" className="back-to-desk" onClick={() => setOverview(true)}><ArrowLeft size={14}/> {t("工作桌")}</button>)}
          {t(threadControls)}
        </div>
      </div>
      <div className="general-messages" ref={chatLog}>
        {t(!messages.length && <section className="general-intro">
          <MessageSquareText size={23} strokeWidth={1.4}/>
          <h2 lang="en">{t(contractId ? "Contract notes" : "Counsel’s desk")}</h2>
          <p>{t(contractId ? t("就这份合同提问，或选择一项审查任务。") : t("输入法务问题，或选择一项审查任务。"))}</p>
          <div className="desk-prompts">{t(promptButtons)}</div>
        </section>)}
        {t(messages.map(message => <div key={message.id} className={message.role === "user" ? "user-bubble" : "general-answer"}>
          {t(message.role === "assistant" && <div className="assistant-name"><div className="small-logo">C</div> {t("法务助手")}{t(message.pending && <LoaderCircle className="spin" size={14}/>)}</div>)}
          {t(!!message.events?.length && <details className="trace" open={message.pending}>
            <summary>{message.events.length} {t("条模型与工具记录")}</summary>
            <div className="trace-list">{t(message.events.map((event, index) => <div key={index}><span className="trace-dot"/><div>{event.title}<small>{event.detail}</small></div></div>))}</div>
          </details>)}
          <p className={message.error ? "error-text" : ""}>{t(message.text || (message.pending ? t("正在处理…") : ""))}</p>
          {t(!!message.output?.sources?.length && <div className="source-list"><strong>{t("本轮获取的来源")}</strong>{t(message.output.sources.map(source =>
            <a key={source.url} href={source.url} target="_blank" rel="noopener noreferrer">{source.title}</a>))}</div>)}
          {t(message.output?.artifacts?.map((item, index) => item.file_id ?
            <a key={index} className="secondary" href={fileUrl(item.file_id, true)}><Download size={14}/>{item.name}</a> :
            <div className="skill-saved" key={index}><Settings2 size={14}/>{item.name} {t("· 已保存到我的 Skills")}</div>))}
          {t(message.output?.reviews?.map(item => <button key={item.job_id} className="secondary" onClick={() => onReview(item.contract_id, item.job_id)}>{t("打开合同审查任务")}</button>))}
        </div>))}
      </div>
    </>)}
    {t(error && <div className="alert" role="alert">{t(error)}</div>)}
    <form className="composer-wrap" onSubmit={send}>
      {t(overview && <div className="desk-chat-heading">
        <h2 className="chat-section-heading"><MessageSquareText size={17}/> {t("法务助手")}</h2>{t(threadControls)}
      </div>)}
      {t(demo && <p className="general-mode">{t("演示模式支持显式工具命令。开放问答与自动选择工具需要真实模型。")}</p>)}
      <div className="composer"><textarea ref={input} aria-label={t("自由对话消息")} disabled={!loaded || sending} placeholder={contractId ? t("输入关于当前合同的问题…") : t("输入法务问题或审查要求…")} value={draft}
        onChange={event => setDraft(event.target.value)}
        onKeyDown={event => { if(event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing){event.preventDefault();send();} }}/>
        <div className="composer-controls"><label className="web-toggle"><input type="checkbox" checked={allowWeb} onChange={event => setAllowWeb(event.target.checked)}/><Globe size={14}/> {t("联网")}</label>
          <button type="submit" className="send-button" aria-label={t("发送自由对话")} disabled={!loaded || !draft.trim() || sending || !!active}><ArrowUp size={18}/></button>
        </div>
      </div>
      <div className="composer-note">{t(persistChat ? t("对话保存在本浏览器") : t("对话仅保留在本次页面会话"))} {t("· 近期消息会随问题一并提交")}</div>
    </form>
  </div>;
}
