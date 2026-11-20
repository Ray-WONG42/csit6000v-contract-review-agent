import React, { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  ArrowUp,
  Plus,
  FileText,
  PanelLeft,
  ChevronRight,
  Check,
  Folder,
  Search,
  X,
  Settings2,
  Mail,
  Download,
  LogOut,
  ShieldCheck,
  Sparkles,
  FileCheck2,
  Circle,
  LoaderCircle,
  Pencil,
  ExternalLink,
  Paperclip,
  Copy,
} from "lucide-react";
import { api, setCsrf, fileUrl } from "./api";
import { loadLocal, saveLocal, LocalConversation } from "./storage";
import type { components } from "./generated";
import "./style.css";
import "./assistant.css";
import AssistantChat from "./AssistantChat";

type User = { id: string; email: string; role: string; csrf: string };
type Contract = components["schemas"]["ContractView"];
type Evidence = components["schemas"]["EvidenceView"];
type Row = {
  category: string;
  label: string;
  status: string;
  assessment: string;
  explanation: string;
  finding?: string;
  evidence: Evidence[];
  question: string;
  missing: string[];
};
type Job = {
  id: string;
  contract_id: string;
  state: string;
  instruction: string;
  source: string;
  report: string;
  report_file: string;
  created: number;
  error: string;
  delivery: string;
  result?: {
    rows: Row[];
    answer?: string;
    message?: string;
    seconds?: number;
    model_mode?: string;
    calls?: any[];
  };
};
type Trace = { id: number; stage: string; title: string; detail: string };
const activeStates = [
  "queued",
  "parsing",
  "surveying",
  "extracting",
  "validating",
  "rendering",
];
const statusLabel: Record<string, string> = {
  queued: "排队中",
  parsing: "解析合同",
  surveying: "通读合同",
  extracting: "提取条款",
  validating: "验证证据",
  rendering: "生成报告",
  completed: "已完成",
  partial: "部分待复核",
  failed: "处理失败",
  needs_input: "待补充",
  awaiting_confirmation: "来件待确认",
};

function App() {
  const [config, setConfig] = useState<any>(null);
  const [user, setUser] = useState<User | null>(null);
  const [contracts, setContracts] = useState<Contract[]>([]);
  const [selected, setSelected] = useState<Contract | null>(null);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [job, setJob] = useState<Job | null>(null);
  const [events, setEvents] = useState<Trace[]>([]);
  const [paragraphs, setParagraphs] = useState<any[]>([]);
  const [local, setLocal] = useState<LocalConversation>({
    messages: [],
    draft: "",
  });
  const localRef = useRef(local);
  const selectedRef = useRef("");
  const userRef = useRef<User | null>(null);
  const [skills, setSkills] = useState<any[]>([]);
  const [skill, setSkill] = useState("");
  const [tab, setTab] = useState("risk");
  const [highlight, setHighlight] = useState<Evidence | null>(null);
  const [editor, setEditor] = useState("");
  const [modal, setModal] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [filter, setFilter] = useState("");
  const [artifacts, setArtifacts] = useState<any[]>([]);
  const [localMails, setLocalMails] = useState<any[]>([]);
  const [ticket, setTicket] = useState<any>(null);
  const [sidebar, setSidebar] = useState(true);
  const fileInput = useRef<HTMLInputElement>(null);
  const generation = useRef(0);
  const [general, setGeneral] = useState(true);

  async function openAgentReview(contractId: string, jobId: string) {
    const contract = contracts.find(item => item.id === contractId);
    if (!contract) return;
    await selectContract(contract);
    setGeneral(false);
    await openJob(jobId);
  }

  useEffect(() => {
    api("/config")
      .then(setConfig)
      .catch((e) => setError(e.message));
    api<User>("/auth/me")
      .then(signIn)
      .catch(() => {});
  }, []);
  useEffect(() => {
    localRef.current = local;
  }, [local]);
  useEffect(() => {
    userRef.current = user;
  }, [user]);
  function signIn(value: User) {
    setCsrf(value.csrf);
    setUser(value);
    setError("");
    userRef.current = value;
  }
  useEffect(() => {
    if (user) {
      refreshContracts();
      if (user.role === "legal") refreshSkills();
    }
  }, [user?.id]);
  async function refreshContracts() {
    const currentUser = userRef.current;
    if (!currentUser) return;
    const data = await api<Contract[]>(
      currentUser.role === "admin" ? "/admin/contracts" : "/contracts",
    );
    if (userRef.current?.id === currentUser.id) setContracts(data);
  }
  async function refreshSkills() {
    setSkills(await api("/skills"));
  }
  function fail(e: any) {
    setError(e.message || String(e));
  }
  async function persist(value: LocalConversation) {
    setLocal(value);
    localRef.current = value;
    if (user && selectedRef.current)
      await saveLocal(user.id, selectedRef.current, value).catch(() =>
        setError("浏览器本地存储不可用，云端任务仍会保存。"),
      );
  }
  async function selectContract(contract: Contract) {
    const version = ++generation.current;
    selectedRef.current = contract.id;
    setSelected(contract);
    setJob(null);
    setJobs([]);
    setEvents([]);
    setParagraphs([]);
    setHighlight(null);
    setEditor("");
    setError("");
    setArtifacts([]);
    setSkill("");
    try {
      if (user?.role === "admin") {
        const data = await api(
          "/admin/contracts/" + contract.id + "/artifacts",
        );
        if (version === generation.current) setArtifacts(data);
      } else if (user) {
        const [saved, data, parsed] = await Promise.all([
          loadLocal(user.id, contract.id),
          api<Job[]>("/contracts/" + contract.id + "/jobs"),
          api<any[]>("/contracts/" + contract.id + "/paragraphs"),
        ]);
        if (version !== generation.current) return;
        setLocal(saved);
        localRef.current = saved;
        setJobs(data);
        setParagraphs(parsed);
        if (data.length) await openJob(data[data.length - 1].id, version);
      }
    } catch (e) {
      fail(e);
    }
  }
  async function openJob(id: string, version = generation.current) {
    const [data, trace] = await Promise.all([
      api<Job>("/jobs/" + id),
      api<Trace[]>("/jobs/" + id + "/events"),
    ]);
    if (version !== generation.current) return;
    setJob(data);
    setEvents(trace);
    const stored = localRef.current;
    setEditor(
      stored.draftJobId === id
        ? (stored.reportDraft ?? data.report)
        : data.report || "",
    );
  }
  useEffect(() => {
    if (!job || !selected || !user || user.role !== "legal") return;
    const id = job.id,
      contractId = selected.id,
      version = generation.current;
    let stopped = false;
    const timer = window.setInterval(async () => {
      if (stopped || version !== generation.current) return;
      try {
        const [next, trace] = await Promise.all([
          api<Job>("/jobs/" + id),
          api<Trace[]>("/jobs/" + id + "/events"),
        ]);
        if (stopped || version !== generation.current) return;
        setJob(next);
        setEvents(trace);
        if (!activeStates.includes(next.state)) {
          const [list, parsed] = await Promise.all([
            api<Job[]>("/contracts/" + contractId + "/jobs"),
            api<any[]>("/contracts/" + contractId + "/paragraphs"),
          ]);
          if (stopped || version !== generation.current) return;
          setJobs(list);
          setParagraphs(parsed);
          if (next.report) setEditor((previous) => previous || next.report);
          const current = localRef.current;
          if (!current.messages.some((m) => m.id === "answer-" + id)) {
            await persist({
              ...current,
              result: next.result,
              messages: [
                ...current.messages,
                {
                  id: "answer-" + id,
                  role: "assistant",
                  text:
                    next.result?.answer ||
                    next.result?.message ||
                    next.error ||
                    "任务已完成。",
                  job: id,
                },
              ],
            });
          }
          if (next.delivery !== "pending" && next.delivery !== "sending") {
            stopped = true;
            clearInterval(timer);
          }
        }
      } catch (e) {
        fail(e);
      }
    }, 1200);
    return () => {
      stopped = true;
      clearInterval(timer);
    };
  }, [job?.id, selected?.id, user?.id]);

  async function upload(file: File) {
    setBusy(true);
    setError("");
    try {
      const form = new FormData();
      form.append("file", file);
      form.append("company", selected?.company || "未归类");
      const contract = await api<Contract>("/contracts", "POST", form);
      await refreshContracts();
      await selectContract(contract);
    } catch (e) {
      fail(e);
    } finally {
      setBusy(false);
    }
  }
  async function submit() {
    if (!selected || busy || activeStates.includes(job?.state || "")) return;
    setBusy(true);
    setError("");
    try {
      const text = local.draft.trim() || "请按所选清单审查这份合同";
      const next = await api<Job>(
        "/contracts/" + selected.id + "/jobs",
        "POST",
        {
          instruction: text,
          skill_id: skill,
          parent_id: job?.id || "",
          request_key: crypto.randomUUID(),
        },
      );
      await persist({
        ...local,
        draft: "",
        messages: [
          ...local.messages,
          { id: crypto.randomUUID(), role: "user", text, job: next.id },
        ],
      });
      setJob(next);
      setJobs((previous) => [...previous, next]);
      setEvents([]);
      setEditor("");
      setTab("risk");
    } catch (e) {
      fail(e);
    } finally {
      setBusy(false);
    }
  }
  async function logout() {
    try {
      await api("/auth/logout", "POST");
      ++generation.current;
      selectedRef.current = "";
      userRef.current = null;
      setUser(null);
      setSelected(null);
      setContracts([]);
      setLocal({ messages: [], draft: "" });
      setJob(null);
      setCsrf("");
    } catch (e) {
      fail(e);
    }
  }
  async function evidence(item: Evidence) {
    setHighlight(item);
    setTab("text");
    setTimeout(
      () =>
        document
          .getElementById("paragraph-" + item.paragraph_id)
          ?.scrollIntoView({ behavior: "smooth", block: "center" }),
      100,
    );
  }
  const groups = contracts
    .filter((c) =>
      (c.name + c.company + c.owner_email)
        .toLowerCase()
        .includes(filter.toLowerCase()),
    )
    .reduce<Record<string, Contract[]>>((result, c) => {
      (result[c.company] ||= []).push(c);
      return result;
    }, {});
  const running = activeStates.includes(job?.state || "");
  const rows = job?.result?.rows || [];
  const terminal = job && !running;

  if (!user)
    return (
      <LoginPage
        config={config}
        onLogin={signIn}
        error={error}
        setError={setError}
      />
    );
  return (
    <div className={"app " + (!sidebar ? "collapsed" : "")}>
      {sidebar && (
        <aside className="sidebar">
          <div className="brand">
            <div className="brand-mark">
              <FileCheck2 size={22} />
            </div>
            <span>
              Clause<span className="brand-sub">合同审查工作台</span>
            </span>
          </div>
          <button
            className="new-contract"
            onClick={() => fileInput.current?.click()}
            disabled={user.role === "admin" || busy}
          >
            <Plus size={17} /> 添加合同 <span>⌘ N</span>
          </button>
          {user.role === "legal" && <button className="new-chat" onClick={() => {
            ++generation.current; selectedRef.current = ""; setSelected(null); setJob(null); setGeneral(true);
          }}><Plus size={15}/> 返回自由对话</button>}
          <div className="search">
            <Search size={15} />
            <input
              aria-label="搜索合同"
              placeholder="搜索公司或合同"
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
            />
          </div>
          <div className="sidebar-title">
            {user.role === "admin" ? "全部合同 · 输入与输出" : "合同与历史记录"}{" "}
            <span>{contracts.length}</span>
          </div>
          <nav className="contract-list">
            {Object.entries(groups).map(([company, items]) => (
              <div className="company" key={company}>
                <div className="company-name">
                  <Folder size={14} />
                  {company}
                </div>
                {items.map((c) => (
                  <div key={c.id}>
                    <button
                      className={
                        "contract " + (selected?.id === c.id ? "selected" : "")
                      }
                      onClick={() => selectContract(c)}
                    >
                      <FileText size={15} />
                      <span>
                        {c.name}
                        <small>
                          {user.role === "admin"
                            ? c.owner_email
                            : new Date(c.created * 1000).toLocaleDateString()}
                        </small>
                      </span>
                    </button>
                    {selected?.id === c.id && jobs.length > 0 && (
                      <div className="job-list">
                        {jobs.map((j) => (
                          <button
                            key={j.id}
                            className={job?.id === j.id ? "current" : ""}
                            onClick={() => openJob(j.id).catch(fail)}
                          >
                            <Circle size={7} />
                            <span>{j.instruction || "标准合同审查"}</span>
                          </button>
                        ))}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            ))}
            {!contracts.length && (
              <p className="quiet sidebar-empty">
                添加合同后，将在这里按公司整理。
              </p>
            )}
          </nav>
          <div className="sidebar-bottom">
            {user.role === "legal" && (
              <>
                <button onClick={() => setModal("skills")}>
                  <Settings2 size={16} /> 我的 Skills
                </button>
                <button
                  onClick={async () => {
                    setModal("mail");
                    if (config?.demo) setLocalMails(await api("/mail/local"));
                  }}
                >
                  <Mail size={16} /> 邮件处理
                </button>
              </>
            )}
            <div className="account">
              <span className="avatar">
                {user.role === "admin" ? (
                  <ShieldCheck size={18} />
                ) : (
                  user.email[0].toUpperCase()
                )}
              </span>
              <span>
                {user.role === "admin" ? "总账户" : "法务工作空间"}
                <small>{user.email}</small>
              </span>
              <button aria-label="退出登录" onClick={logout}>
                <LogOut size={16} />
              </button>
            </div>
          </div>
        </aside>
      )}
      <main className="workspace">
        <header>
          <button
            className="icon-button"
            aria-label="切换侧栏"
            onClick={() => setSidebar(!sidebar)}
          >
            <PanelLeft size={18} />
          </button>
          <div className="breadcrumb">
            {selected ? (
              <>
                <span>{selected.company}</span>
                <ChevronRight size={14} />
                <strong>{selected.name}</strong>
              </>
            ) : (
              <strong>工作空间</strong>
            )}
          </div>
          <span
            className={"mode " + (config?.model_mode === "tacc" ? "live" : "")}
          >
            <span />{" "}
            {config?.model_mode === "tacc" ? "Qwen · TACC" : "演示模式"}
          </span>
          {user.role === "legal" && (
            <button
              className="top-upload"
              onClick={() => fileInput.current?.click()}
            >
              <Plus size={15} /> 添加合同
            </button>
          )}
        </header>
        <input
          ref={fileInput}
          aria-label="上传合同"
          type="file"
          accept=".pdf,.docx,.txt"
          hidden
          onChange={(e) => {
            if (e.target.files?.[0]) upload(e.target.files[0]);
            e.target.value = "";
          }}
        />
        {error && (
          <div className="alert" role="alert">
            {error}
            <button onClick={() => setError("")}>
              <X size={15} />
            </button>
          </div>
        )}
        {notice && (
          <div className="notice">
            {notice}
            <button onClick={() => setNotice("")}>
              <X size={15} />
            </button>
          </div>
        )}
        {!selected && user.role === "legal" ? (
          <AssistantChat key={user.id + ":general"} userId={user.id} demo={config?.model_mode !== "tacc"}
            onSkillsChanged={refreshSkills} onReview={openAgentReview}/>
        ) : !selected ? (
          <div className="welcome">
            <div className="welcome-icon">
              <FileCheck2 size={34} />
            </div>
            <span className="eyebrow">CONTRACT REVIEW</span>
            <h1>
              {user.role === "admin"
                ? "所有合同，清晰可查。"
                : "让每一个判断，都有依据。"}
            </h1>
            <p>
              {user.role === "admin"
                ? "从左侧选择合同，查看原件与最终审查报告。"
                : "添加一份合同，选择审查范围。条款、证据与结果会在同一个工作空间呈现。"}
            </p>
            {user.role === "legal" && (
              <>
                <button
                  className="primary"
                  onClick={() => fileInput.current?.click()}
                >
                  <Plus size={18} /> 添加第一份合同
                </button>
                <div className="sample-link">
                  <a href="/api/demo/sample">
                    <Download size={14} /> 下载示例合同
                  </a>
                  <span>PDF / Word / TXT · 最大 12 MB</span>
                </div>
                <div className="welcome-cards">
                  <div>
                    <FileText size={20} />
                    <b>原文可追溯</b>
                    <p>每条证据都能定位回合同段落</p>
                  </div>
                  <div>
                    <Settings2 size={20} />
                    <b>按你的清单审查</b>
                    <p>保存 Skill，下次继续使用</p>
                  </div>
                  <div>
                    <Mail size={20} />
                    <b>网页与邮箱同步</b>
                    <p>处理结果统一归档</p>
                  </div>
                </div>
              </>
            )}
          </div>
        ) : user.role === "admin" ? (
          <div className="admin-body">
            <h2>{selected.name}</h2>
            <p className="quiet">
              {selected.owner_email} · {selected.company}
            </p>
            <div className="artifact-grid">
              {artifacts.map((f) => (
                <a key={f.id} className="artifact" href={fileUrl(f.id, true)}>
                  <FileText size={26} />
                  <div>
                    <small>
                      {f.kind === "input" ? "合同原件" : "最终审查报告"}
                    </small>
                    <strong>{f.name}</strong>
                    <span>{new Date(f.created * 1000).toLocaleString()}</span>
                  </div>
                  <Download size={18} />
                </a>
              ))}
            </div>
          </div>
        ) : (
          <div className="panels">
            <section className="chat-panel">
              <div className="chat-mode-tabs">
                <button className={general ? "active" : ""} onClick={() => setGeneral(true)}>自由对话与工具</button>
                <button className={!general ? "active" : ""} onClick={() => setGeneral(false)}>合同审查</button>
              </div>
              {general ? <AssistantChat key={user.id + ":" + selected.id} userId={user.id} contractId={selected.id}
                demo={config?.model_mode !== "tacc"} onSkillsChanged={refreshSkills} onReview={openAgentReview}/> : <>
              <div className="panel-heading">
                <span>
                  <Sparkles size={16} /> 合同审查
                </span>
                <button
                  onClick={() => setModal("company")}
                  title="修改公司归类"
                >
                  <Folder size={15} />
                </button>
              </div>
              <div className="chat-scroll">
                <div className="contract-card">
                  <div className="file-symbol">
                    <FileText size={23} />
                  </div>
                  <div>
                    <strong>{selected.name}</strong>
                    <span>{selected.company} · 原件已保存</span>
                  </div>
                  <a
                    aria-label="下载原件"
                    href={fileUrl(selected.file_id, true)}
                  >
                    <Download size={17} />
                  </a>
                </div>
                {!local.messages.length && !job && (
                  <div className="assistant-intro">
                    <div className="assistant-name">
                      <div className="small-logo">C</div> Clause
                    </div>
                    <p>
                      合同已就绪。你可以进行八类标准审查，或直接提出关注的问题。
                    </p>
                    <div className="suggestions">
                      {[
                        "进行标准合同审查",
                        "自动续约需要提前多久通知？",
                        "检查责任上限与终止条款",
                      ].map((text) => (
                        <button
                          key={text}
                          onClick={() => persist({ ...local, draft: text })}
                        >
                          {text}
                          <ChevronRight size={14} />
                        </button>
                      ))}
                    </div>
                  </div>
                )}
                {local.messages.map((message) =>
                  message.role === "user" ? (
                    <div className="user-bubble" key={message.id}>
                      {message.text}
                    </div>
                  ) : message.job !== job?.id ? (
                    <details
                      className="answer-detail prior-answer"
                      key={message.id}
                    >
                      <summary>历史审查回答</summary>
                      <p>{message.text}</p>
                      {message.job && (
                        <button
                          className="text-button"
                          onClick={() => openJob(message.job!).catch(fail)}
                        >
                          打开结果
                        </button>
                      )}
                    </details>
                  ) : null,
                )}
                {job && (
                  <div className="assistant-message">
                    <div className="assistant-name">
                      <div className="small-logo">C</div> Clause{" "}
                      <span>
                        {config?.model_mode === "tacc"
                          ? "Qwen"
                          : "本地规则演示"}
                      </span>
                    </div>
                    <div className="trace">
                      <div className="trace-heading">
                        {running ? (
                          <LoaderCircle size={15} className="spin" />
                        ) : (
                          <Check size={15} />
                        )}
                        <b>{statusLabel[job.state] || job.state}</b>
                        <span>
                          {job.result?.seconds ? job.result.seconds + "s" : ""}
                        </span>
                      </div>
                      <details open={running}>
                        <summary>{events.length} 条处理记录</summary>
                        <div className="trace-list">
                          {events.map((e) => (
                            <div key={e.id}>
                              <span
                                className={
                                  "trace-dot " +
                                  (e.stage === "error" ? "red" : "")
                                }
                              />
                              <div>
                                {e.title}
                                {e.detail && <small>{e.detail}</small>}
                              </div>
                            </div>
                          ))}
                        </div>
                      </details>
                    </div>
                    {job.state === "awaiting_confirmation" && (
                      <>
                        <p>
                          邮箱合同等待你确认归属。确认后开始审查并回传报告。
                        </p>
                        <button
                          className="primary"
                          onClick={async () => {
                            try {
                              await api("/jobs/" + job.id + "/confirm", "POST");
                              await openJob(job.id);
                            } catch (e) {
                              fail(e);
                            }
                          }}
                        >
                          确认并开始审查
                        </button>
                      </>
                    )}
                    {terminal && job.result && (
                      <>
                        <p className="result-summary">
                          {job.result.message ||
                            (rows.length
                              ? "已完成 " +
                                rows.length +
                                " 类条款检查，引用与清单判断见右侧。"
                              : "处理已结束。")}
                        </p>
                        <div className="summary-chips">
                          <span>
                            {rows.filter((r) => r.status === "found").length}{" "}
                            类有证据
                          </span>
                          <span>
                            {
                              rows.filter((r) => r.assessment === "待核验")
                                .length
                            }{" "}
                            类待核验
                          </span>
                        </div>
                        {job.result.answer && (
                          <details className="answer-detail">
                            <summary>查看本次回答</summary>
                            <p>{job.result.answer}</p>
                          </details>
                        )}
                      </>
                    )}
                    {job.error && <p className="error-text">{job.error}</p>}
                    {job.report_file && (
                      <a
                        className="report-card"
                        href={fileUrl(job.report_file, true)}
                      >
                        <div className="file-symbol green">
                          <FileCheck2 size={22} />
                        </div>
                        <span>
                          <strong>合同审查报告</strong>
                          <small>Word 文档 · 原文证据与风险表</small>
                        </span>
                        <Download size={18} />
                      </a>
                    )}
                    {job.delivery && (
                      <div className="delivery">
                        <Mail size={14} />
                        {
                          (
                            {
                              pending: "邮件等待发送",
                              sending: "邮件发送中",
                              accepted: "已提交 Outlook 发送",
                              demo: "结果已保存到演示发件箱",
                              failed: "邮件发送失败",
                              uncertain: "发送状态待核对",
                            } as Record<string, string>
                          )[job.delivery]
                        }
                        {["failed", "uncertain"].includes(job.delivery) && (
                          <button
                            onClick={async () => {
                              await api("/jobs/" + job.id + "/resend", "POST");
                              await openJob(job.id);
                            }}
                          >
                            重发
                          </button>
                        )}
                      </div>
                    )}
                  </div>
                )}
              </div>
              <div className="composer-wrap">
                <div className="composer">
                  <textarea
                    aria-label="审查要求"
                    placeholder="询问这份合同，或输入审查要求…"
                    value={local.draft}
                    onChange={(e) =>
                      persist({ ...local, draft: e.target.value })
                    }
                    onKeyDown={(e) => {
                      if (
                        e.key === "Enter" &&
                        !e.shiftKey &&
                        !e.nativeEvent.isComposing
                      ) {
                        e.preventDefault();
                        submit();
                      }
                    }}
                  />
                  <div className="composer-controls">
                    <button
                      className="icon-button"
                      aria-label="添加新合同"
                      onClick={() => fileInput.current?.click()}
                    >
                      <Paperclip size={18} />
                    </button>
                    <select
                      aria-label="选择 Skill"
                      value={skill}
                      onChange={(e) => setSkill(e.target.value)}
                    >
                      <option value="">标准审查 · 8 类</option>
                      {skills.map((s) => (
                        <option key={s.id} value={s.id}>
                          {s.name}
                        </option>
                      ))}
                    </select>
                    <button
                      className="send-button"
                      aria-label="开始审查"
                      onClick={submit}
                      disabled={busy || running}
                    >
                      {running ? (
                        <LoaderCircle className="spin" size={17} />
                      ) : (
                        <ArrowUp size={19} />
                      )}
                    </button>
                  </div>
                </div>
                <div className="composer-note">
                  聊天保存在本浏览器 · 结果与文件保存到服务器
                </div>
              </div>
              </>}
            </section>
            <section className="document-panel">
              <div className="document-tabs">
                {[
                  ["risk", "风险表"],
                  ["text", "原文证据"],
                  ["original", "原件"],
                  ["edit", "编辑报告"],
                ].map(([key, label]) => (
                  <button
                    key={key}
                    className={tab === key ? "active" : ""}
                    onClick={() => setTab(key)}
                  >
                    {label}
                  </button>
                ))}
              </div>
              <div className="document-body">
                {tab === "risk" && (
                  <>
                    <div className="document-title">
                      <span className="eyebrow">REVIEW FINDINGS</span>
                      <h2>合同风险与证据</h2>
                      <p>按所选审计清单标注 · 示例规则 v1</p>
                    </div>
                    {!rows.length ? (
                      <div className="empty-state">
                        <FileCheck2 size={32} />
                        <p>
                          {running
                            ? "正在读取合同，审查结果即将呈现。"
                            : "开始审查后，在这里查看风险表。"}
                        </p>
                      </div>
                    ) : (
                      <div className="risk-list">
                        {rows.map((row, i) => (
                          <article className="risk-row" key={row.category}>
                            <div className="risk-top">
                              <span className="risk-number">
                                {String(i + 1).padStart(2, "0")}
                              </span>
                              <h3>{row.label}</h3>
                              <span
                                className={
                                  "tag " +
                                  (row.assessment === "符合"
                                    ? "pass"
                                    : row.assessment === "不符合"
                                      ? "fail"
                                      : "review")
                                }
                              >
                                {row.assessment}
                              </span>
                            </div>
                            {row.finding && (
                              <p className="finding">{row.finding}</p>
                            )}
                            <p>{row.explanation}</p>
                            {row.evidence.map((e, index) => (
                              <button
                                className="quote"
                                key={index}
                                onClick={() => evidence(e)}
                              >
                                <span>
                                  {e.paragraph_id}
                                  {e.page ? " · 第 " + e.page + " 页" : ""}
                                  <ExternalLink size={12} />
                                </span>
                                <q>{e.quote}</q>
                              </button>
                            ))}
                            {row.status === "not_found" && (
                              <div className="not-found">
                                本次未检出对应原文
                              </div>
                            )}
                            {row.missing.length > 0 && (
                              <small>待确认 · {row.missing.join(" / ")}</small>
                            )}
                          </article>
                        ))}
                      </div>
                    )}
                  </>
                )}
                {tab === "text" && (
                  <>
                    <div className="document-title">
                      <span className="eyebrow">SOURCE EVIDENCE</span>
                      <h2>合同正文</h2>
                      <p>
                        {paragraphs.length} 个稳定段落 · 点击风险表引用可定位
                      </p>
                    </div>
                    {paragraphs.length === 0 && (
                      <div className="empty-state">解析完成后显示正文</div>
                    )}
                    {paragraphs.map((p) => {
                      const index =
                        highlight?.paragraph_id === p.id
                          ? p.text.indexOf(highlight?.quote || "")
                          : -1;
                      return (
                        <div
                          id={"paragraph-" + p.id}
                          key={p.id}
                          className={
                            "paragraph " +
                            (highlight?.paragraph_id === p.id
                              ? "highlight"
                              : "")
                          }
                        >
                          <small>
                            {p.id}
                            {p.page ? " / PAGE " + p.page : ""}
                          </small>
                          <p>
                            {index >= 0 && highlight ? (
                              <>
                                {p.text.slice(0, index)}
                                <mark>{highlight.quote}</mark>
                                {p.text.slice(index + highlight.quote.length)}
                              </>
                            ) : (
                              p.text
                            )}
                          </p>
                        </div>
                      );
                    })}
                  </>
                )}
                {tab === "original" &&
                  (selected.name.toLowerCase().endsWith(".pdf") ? (
                    <iframe
                      title="合同原件"
                      className="pdf-frame"
                      src={fileUrl(selected.file_id)}
                    />
                  ) : (
                    <div className="empty-state">
                      <FileText size={32} />
                      <p>在「原文证据」阅读可定位的合同段落。</p>
                      <a
                        className="secondary"
                        href={fileUrl(selected.file_id, true)}
                      >
                        <Download size={16} /> 下载原始文件
                      </a>
                    </div>
                  ))}
                {tab === "edit" && (
                  <>
                    <div className="document-title">
                      <span className="eyebrow">REPORT EDITOR</span>
                      <h2>编辑审查报告</h2>
                      <p>保存会生成一份独立的个人编辑稿。</p>
                    </div>
                    <textarea
                      className="report-editor"
                      aria-label="报告内容"
                      value={editor}
                      placeholder="审查完成后可编辑报告"
                      disabled={!job?.report_file}
                      onChange={(e) => {
                        setEditor(e.target.value);
                        persist({
                          ...localRef.current,
                          reportDraft: e.target.value,
                          draftJobId: job?.id,
                        });
                      }}
                    />
                    <button
                      className="primary"
                      disabled={!job?.report_file || !editor.trim()}
                      onClick={async () => {
                        try {
                          const data = await api(
                            "/jobs/" + job?.id + "/report-versions",
                            "POST",
                            { content: editor },
                          );
                          const link = document.createElement("a");
                          link.href = fileUrl(data.file_id, true);
                          link.click();
                          setNotice("编辑稿已保存并开始下载。");
                        } catch (e) {
                          fail(e);
                        }
                      }}
                    >
                      <Download size={16} /> 保存并下载编辑稿
                    </button>
                  </>
                )}
              </div>
            </section>
          </div>
        )}
      </main>
      {modal && (
        <div className="modal-backdrop" onClick={() => setModal("")}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <button
              className="modal-close"
              aria-label="关闭"
              onClick={() => setModal("")}
            >
              <X size={20} />
            </button>
            {modal === "company" && (
              <CompanyForm
                value={selected?.company || ""}
                onSave={async (company) => {
                  try {
                    const updated = await api<Contract>(
                      "/contracts/" + selected?.id,
                      "PATCH",
                      { company },
                    );
                    setSelected(updated);
                    await refreshContracts();
                    setModal("");
                  } catch (e) {
                    fail(e);
                  }
                }}
              />
            )}
            {modal === "skills" && (
              <SkillForm
                config={config}
                skills={skills}
                onSave={async (body, id) => {
                  await api(
                    "/skills" + (id ? "/" + id : ""),
                    id ? "PUT" : "POST",
                    body,
                  );
                  await refreshSkills();
                  setNotice("Skill 已保存，可在输入框下方选择。");
                  setModal("");
                }}
              />
            )}
            {modal === "mail" && (
              <>
                <span className="eyebrow">MAIL INTEGRATION</span>
                <h2>邮件处理</h2>
                <p className="quiet">
                  使用已注册邮箱发送合同。生成的提交凭据可让来件自动开始处理，有效期
                  24 小时且仅使用一次。
                </p>
                <button
                  className="secondary"
                  onClick={async () => {
                    try {
                      setTicket(await api("/mail/ticket", "POST"));
                    } catch (e) {
                      fail(e);
                    }
                  }}
                >
                  生成邮件提交主题
                </button>
                {ticket && (
                  <div className="ticket">
                    <small>
                      收件邮箱 · {ticket.mailbox || "尚未配置 Outlook"}
                    </small>
                    <code>{ticket.subject}</code>
                    <button
                      onClick={() =>
                        navigator.clipboard.writeText(ticket.subject)
                      }
                    >
                      <Copy size={14} /> 复制
                    </button>
                  </div>
                )}
                {config?.demo && (
                  <>
                    <h3>演示邮箱</h3>
                    <p className="quiet">
                      模拟已授权发件。结果保存到下面的本地发件箱。
                    </p>
                    <input
                      aria-label="模拟邮箱上传"
                      type="file"
                      accept=".pdf,.docx,.txt"
                      onChange={async (e) => {
                        const file = e.target.files?.[0];
                        if (!file) return;
                        try {
                          const f = new FormData();
                          f.append("file", file);
                          f.append("instruction", "标准合同审查");
                          const r = await api("/demo/mail", "POST", f);
                          await refreshContracts();
                          const list = await api<Contract[]>("/contracts");
                          const c = list.find((x) => x.id === r.contract_id);
                          if (c) await selectContract(c);
                          setModal("");
                        } catch (err) {
                          fail(err);
                        }
                      }}
                    />
                    <button
                      className="text-button"
                      onClick={async () =>
                        setLocalMails(await api("/mail/local"))
                      }
                    >
                      刷新发件箱
                    </button>
                    {localMails.map((m) => (
                      <div className="mail-card" key={m.id}>
                        <strong>{m.subject}</strong>
                        <p>{m.body.slice(0, 250)}</p>
                        {m.file_id && (
                          <a href={fileUrl(m.file_id, true)}>下载附件</a>
                        )}
                      </div>
                    ))}
                  </>
                )}
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

function LoginPage({
  config,
  onLogin,
  error,
  setError,
}: {
  config: any;
  onLogin: (user: User) => void;
  error: string;
  setError: (s: string) => void;
}) {
  const [register, setRegister] = useState(false);
  const [email, setEmail] = useState("legal@example.com");
  const [password, setPassword] = useState("LegalDemo2026!");
  const [code, setCode] = useState("");
  const [info, setInfo] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (config && !config.demo) {
      setEmail("");
      setPassword("");
    }
  }, [config]);
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      if (register) {
        await api("/auth/register", "POST", { email, password, code });
        setRegister(false);
        setInfo("注册成功，请登录。");
      } else onLogin(await api("/auth/login", "POST", { email, password }));
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="login-page">
      <div className="login-story">
        <div className="brand">
          <div className="brand-mark">
            <FileCheck2 size={23} />
          </div>
          <span>Clause</span>
        </div>
        <div>
          <span className="eyebrow">YOUR CONTRACT WORKSPACE</span>
          <h1>
            从合同原文，
            <br />
            到有据可查的判断。
          </h1>
          <p>
            为法务团队构建的合同审查工作台。
            <br />
            集中管理合同、审查清单和每一次处理结果。
          </p>
          <div className="login-proof">
            <ShieldCheck size={18} /> 账户隔离 <span /> 原文证据 <span />{" "}
            审查留痕
          </div>
        </div>
        <small>Contract Review Agent · Course Project</small>
      </div>
      <div className="login-form-wrap">
        <form className="login-form" onSubmit={submit}>
          <h2>{register ? "创建法务账户" : "登录工作空间"}</h2>
          <p>使用你的邮箱继续</p>
          <label>
            邮箱
            <input
              type="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              autoComplete="email"
            />
          </label>
          <label>
            密码
            <input
              type="password"
              required
              minLength={register ? 10 : 1}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete={register ? "new-password" : "current-password"}
            />
          </label>
          {register && (
            <label>
              邮箱验证码
              <div className="code-line">
                <input
                  value={code}
                  onChange={(e) => setCode(e.target.value)}
                  required
                  maxLength={6}
                />
                <button
                  type="button"
                  className="secondary"
                  onClick={async () => {
                    try {
                      const r = await api("/auth/send-code", "POST", { email });
                      setInfo(
                        r.demo_code
                          ? "演示验证码：" + r.demo_code
                          : "验证码已发送",
                      );
                    } catch (e: any) {
                      setError(e.message);
                    }
                  }}
                >
                  发送验证码
                </button>
              </div>
            </label>
          )}
          {error && (
            <div className="form-error" role="alert">
              {error}
            </div>
          )}
          {info && <div className="form-info">{info}</div>}
          <button className="primary full" disabled={busy}>
            {busy ? "请稍候…" : register ? "注册" : "登录"}
            <ChevronRight size={17} />
          </button>
          <button
            type="button"
            className="text-button full"
            onClick={() => {
              setRegister(!register);
              setError("");
              setInfo("");
            }}
          >
            {register ? "已有账户？返回登录" : "使用邮箱验证码注册"}
          </button>
          {config?.demo && (
            <div className="demo-accounts">
              <span>本地演示账号</span>
              <div>
                {[
                  ["法务", "legal@example.com", "LegalDemo2026!"],
                  ["另一法务", "other@example.com", "OtherDemo2026!"],
                  ["总账户", "admin@example.com", "AdminDemo2026!"],
                ].map(([label, address, pass]) => (
                  <button
                    type="button"
                    key={label}
                    onClick={() => {
                      setEmail(address);
                      setPassword(pass);
                      setRegister(false);
                    }}
                  >
                    {label}
                  </button>
                ))}
              </div>
              <small>演示模式使用本地规则和发件箱。</small>
            </div>
          )}
        </form>
      </div>
    </div>
  );
}

function CompanyForm({
  value,
  onSave,
}: {
  value: string;
  onSave: (s: string) => void;
}) {
  const [name, setName] = useState(value);
  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        onSave(name);
      }}
    >
      <h2>合同归类</h2>
      <label>
        公司或项目名称
        <input
          required
          maxLength={100}
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
      </label>
      <button className="primary">保存归类</button>
    </form>
  );
}
function SkillForm({
  config,
  skills,
  onSave,
}: {
  config: any;
  skills: any[];
  onSave: (body: any, id: string) => Promise<void>;
}) {
  const [id, setId] = useState(""),
    [name, setName] = useState(""),
    [categories, setCategories] = useState<string[]>(
      config?.categories.map((x: any) => x.id) || [],
    ),
    [focus, setFocus] = useState(""),
    [customItems, setCustomItems] = useState<
      { title: string; question: string }[]
    >([]),
    [checks, setChecks] = useState<Record<string, string>>({}),
    [policy, setPolicy] = useState("supplier-demo@1"),
    [error, setError] = useState("");
  async function save(event: React.FormEvent) {
    event.preventDefault();
    try {
      const items = Object.fromEntries(
        categories.flatMap((category) => {
          const fields = (checks[category] || "")
            .split(/[,，\n]/)
            .map((x) => x.trim())
            .filter(Boolean);
          return fields.length ? [[category, fields]] : [];
        }),
      );
      await onSave(
        {
          name,
          categories,
          focus,
          policy,
          checks: items,
          custom_items: customItems,
        },
        id,
      );
    } catch (err: any) {
      setError(err.message);
    }
  }
  return (
    <form onSubmit={save}>
      <span className="eyebrow">REUSABLE REVIEW SKILLS</span>
      <h2>我的审查模板</h2>
      <label>
        编辑已有模板
        <select
          value={id}
          onChange={(e) => {
            setId(e.target.value);
            const s = skills.find((s) => s.id === e.target.value);
            setName(s?.name || "");
            setCategories(
              s?.categories || config.categories.map((x: any) => x.id),
            );
            setFocus(s?.focus || "");
            setCustomItems(s?.custom_items || []);
            setPolicy(s?.policy || "supplier-demo@1");
            setChecks(
              Object.fromEntries(
                Object.entries(s?.checks || {}).map(([key, value]) => [
                  key,
                  (value as string[]).join("，"),
                ]),
              ),
            );
          }}
        >
          <option value="">创建新 Skill</option>
          {skills.map((s) => (
            <option key={s.id} value={s.id}>
              {s.name} · v{s.version}
            </option>
          ))}
        </select>
      </label>
      <label>
        模板名称
        <input
          required
          maxLength={60}
          placeholder="例如：供应商续约检查"
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
      </label>
      <div className="category-options">
        {config?.categories.map((c: any) => (
          <label key={c.id}>
            <input
              type="checkbox"
              checked={categories.includes(c.id)}
              onChange={(e) =>
                setCategories(
                  e.target.checked
                    ? [...categories, c.id]
                    : categories.filter((x) => x !== c.id),
                )
              }
            />
            {c.label}
          </label>
        ))}
      </div>
      <details>
        <summary>自定义每类核对项</summary>
        {config?.categories
          .filter((item: { id: string }) => categories.includes(item.id))
          .map((item: { id: string; label: string }) => (
            <label key={item.id}>
              {item.label}
              <input
                value={checks[item.id] || ""}
                onChange={(event) =>
                  setChecks({ ...checks, [item.id]: event.target.value })
                }
                placeholder="用逗号分隔，留空使用默认核对项"
                maxLength={700}
              />
            </label>
          ))}
      </details>
      <label>
        模板 Prompt
        <textarea
          value={focus}
          onChange={(e) => setFocus(e.target.value)}
          maxLength={1500}
          placeholder="例如：总结保密义务的主体、范围、例外和存续期。可单独使用，无需勾选内置类别。"
        />
      </label>
      <div className="custom-items">
        {customItems.map((item, index) => (
          <div key={index}>
            <label>
              自定义项标题
              <input
                required
                maxLength={80}
                value={item.title}
                onChange={(e) =>
                  setCustomItems(
                    customItems.map((current, i) =>
                      i === index
                        ? { ...current, title: e.target.value }
                        : current,
                    ),
                  )
                }
              />
            </label>
            <label>
              审查要求
              <textarea
                required
                maxLength={1000}
                value={item.question}
                onChange={(e) =>
                  setCustomItems(
                    customItems.map((current, i) =>
                      i === index
                        ? { ...current, question: e.target.value }
                        : current,
                    ),
                  )
                }
              />
            </label>
            <button
              className="text-button"
              type="button"
              onClick={() =>
                setCustomItems(customItems.filter((_, i) => i !== index))
              }
            >
              移除此项
            </button>
          </div>
        ))}
        <button
          className="secondary"
          type="button"
          disabled={customItems.length >= 6}
          onClick={() =>
            setCustomItems([...customItems, { title: "", question: "" }])
          }
        >
          <Plus size={14} /> 添加自定义审查项
        </button>
      </div>
      <label>
        审计清单
        <select value={policy} onChange={(e) => setPolicy(e.target.value)}>
          <option value="supplier-demo@1">
            供应商示例清单 v1 · 续约通知至少 30 个自然日
          </option>
          <option value="evidence-only@1">
            证据提取清单 · 由人工判断符合性
          </option>
        </select>
      </label>
      {error && <p className="error-text">{error}</p>}
      <button
        className="primary"
        disabled={!categories.length && !customItems.length && !focus.trim()}
      >
        保存 Skill
      </button>
    </form>
  );
}

createRoot(document.getElementById("root")!).render(<App />);
