import { t, useLanguage, LanguageSwitch, getLanguage, setLanguage, clauseCount, type Language } from "./i18n";
import React, { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  ArrowUp,
  Plus,
  FileText,
  LibraryBig,
  LayoutDashboard,
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
  FileCheck2,
  Circle,
  LoaderCircle,
  Pencil,
  Paperclip,
  Trash2,
  Eye,
  EyeOff,
} from "lucide-react";
import { api, setCsrf, fileUrl } from "./api";
import { clearContractHistory, loadLocal, pruneReviewHistory, saveLocal, LocalConversation } from "./storage";
import type { components } from "./generated";
import "./style.css";
import "./assistant.css";
import "./review.css";
import "./login.css";
import AssistantChat from "./AssistantChat";
import ReviewPanes from "./ReviewPanes";
import MailPanel from "./MailPanel";

type User = { id: string; email: string; role: string; csrf: string; language: Language };
type Contract = components["schemas"]["ContractView"];
type Evidence = components["schemas"]["EvidenceView"];
type Row = {
  category: string;
  label: string;
  status: string;
  assessment: string;
  explanation: string;
  finding?: string;
  checklist?: { requirement: string; assessment: string }[];
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
  model_mode?: string;
  model_name?: string;
  result?: {
    rows: Row[];
    answer?: string;
    message?: string;
    seconds?: number;
    model_mode?: string;
    model_name?: string;
    calls?: any[];
  };
};
type Trace = { id: number; stage: string; title: string; detail: string };
type HistoryTarget = { kind: "contract" | "job"; id: string; name: string };
type VisibleRetrievalSource = Exclude<NonNullable<Evidence["retrieval_sources"]>[number], "survey">;
const retrievalSourceLabels: Record<VisibleRetrievalSource, string> = {
  keyword: t("关键词检索"),
  vector: t("向量检索"),
  fallback: t("文档开头"),
  adjacent: t("相邻段落"),
};
function modelLabel(mode?: string, name?: string) {
  if (mode === "ollama") return name ? `${name} · Ollama` : t("本地 Ollama");
  if (mode === "api") return name ? `${name} · API` : "DeepSeek · API";
  if (mode === "tacc") return "Qwen · TACC";
  return t("演示模式");
}
const activeStates = [
  "queued",
  "waiting_tacc",
  "tacc_client",
  "parsing",
  "surveying",
  "extracting",
  "validating",
  "rendering",
];
const statusLabel: Record<string, string> = {
  queued: "排队中",
  waiting_tacc: "等待学校 GPU 集群",
  tacc_client: "等待 TACC 回复",
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
  useLanguage();
  const [config, setConfig] = useState<any>(null);
  const [user, setUser] = useState<User | null>(null);
  const [contracts, setContracts] = useState<Contract[]>([]);
  const [selected, setSelected] = useState<Contract | null>(null);
  const [fileState, setFileState] = useState<"loading" | "ready" | "error">("ready");
  const [deskVisit, setDeskVisit] = useState(0);
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
  const [historyTarget, setHistoryTarget] = useState<HistoryTarget | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [filter, setFilter] = useState("");
  const [artifacts, setArtifacts] = useState<any[]>([]);
  const [compact, setCompact] = useState(() => window.matchMedia("(max-width: 900px)").matches);
  const [sidebar, setSidebar] = useState(() => !window.matchMedia("(max-width: 900px)").matches);
  const drawerClose = useRef<HTMLButtonElement>(null);
  const sidebarToggle = useRef<HTMLButtonElement>(null);
  const modalRef = useRef<HTMLDialogElement>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const generation = useRef(0);
  const [general, setGeneral] = useState(true);
  const [modelPreference, setModelPreference] = useState("");
  const [ollamaModels, setOllamaModels] = useState<string[] | null>(null);
  const [ollamaModelsError, setOllamaModelsError] = useState("");
  const canSelectModel = Boolean(config && config.model_mode !== "demo" && user?.role === "legal");
  const preference = canSelectModel ? modelPreference : "";
  const requestedMode = preference === "tacc" ? "tacc" : preference === "api" ? "api" : preference.startsWith("ollama:") ? "ollama" : "";
  const ollamaModel = requestedMode === "ollama" ? preference.slice("ollama:".length) : "";
  const selectedModelOption = preference || (config?.model_mode === "tacc" ? "tacc" : config?.model_mode === "api" ? "api" : "");

  useEffect(() => {
    const query = window.matchMedia("(max-width: 900px)");
    const resize = (event: MediaQueryListEvent) => {
      setCompact(event.matches);
      setSidebar(!event.matches);
    };
    query.addEventListener("change", resize);
    return () => query.removeEventListener("change", resize);
  }, []);
  useEffect(() => {
    if (compact && sidebar) drawerClose.current?.focus();
  }, [compact, sidebar]);
  useEffect(() => {
    if (modal) modalRef.current?.showModal();
  }, [modal]);

  function closeDrawer() {
    if (compact) {
      setSidebar(false);
      requestAnimationFrame(() => sidebarToggle.current?.focus());
    }
  }
  function showDesk() {
    ++generation.current;
    selectedRef.current = "";
    setSelected(null);
    setJob(null);
    setGeneral(true);
    setDeskVisit(value => value + 1);
    closeDrawer();
  }

  async function openAgentReview(contractId: string, jobId: string) {
    const contract = contracts.find(item => item.id === contractId);
    if (!contract) {
      setError(t("这份合同已不在档案中"));
      return;
    }
    await selectContract(contract);
    if (selectedRef.current === contractId) await openJob(jobId).catch(fail);
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
    setLanguage(value.language || "en");
    setUser(value);
    setError("");
    userRef.current = value;
    try {
      const previousOllamaModel = localStorage.getItem("ollama-model:" + value.id);
      setModelPreference(localStorage.getItem("model-selection-v2:" + value.id) ??
        (previousOllamaModel ? `ollama:${previousOllamaModel}` : ""));
    } catch {
      setModelPreference("");
    }
  }
  async function chooseLanguage(language: Language) {
    const current = userRef.current;
    if (current) {
      await api("/auth/language", "PUT", { language });
      if (userRef.current?.id !== current.id) return;
      setUser(previous => previous?.id === current.id ? { ...previous, language } : previous);
    }
    setLanguage(language);
  }
  useEffect(() => {
    if (!canSelectModel) return;
    let cancelled = false;
    setOllamaModels(null);
    setOllamaModelsError("");
    api<string[]>("/ollama/models").then(
      (models) => { if (!cancelled) setOllamaModels(models); },
      (error) => { if (!cancelled) setOllamaModelsError(error.message); },
    );
    return () => { cancelled = true; };
  }, [canSelectModel, user?.id]);

  function chooseModel(model: string, userId: string) {
    setModelPreference(model);
    try {
      localStorage.setItem("model-selection-v2:" + userId, model);
    } catch {
      // The selection still applies within the current browser session.
    }
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
        setError(t("浏览器本地存储不可用，云端任务仍会保存。")),
      );
  }
  async function selectContract(contract: Contract) {
    const version = ++generation.current;
    closeDrawer();
    setGeneral(false);
    setTab("risk");
    selectedRef.current = contract.id;
    setSelected(contract);
    setFileState("loading");
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
        const cleaned = pruneReviewHistory(saved, new Set(data.map(item => item.id)));
        setLocal(cleaned);
        localRef.current = cleaned;
        if (cleaned !== saved) void saveLocal(user.id, contract.id, cleaned).catch(fail);
        setJobs(data);
        setParagraphs(parsed);
        if (data.length) await openJob(data[data.length - 1].id, version);
      }
      if (version === generation.current) setFileState("ready");
    } catch (e) {
      if (version === generation.current) {
        setFileState("error");
        fail(e);
      }
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
                    t("任务已完成。"),
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
        if (!stopped && version === generation.current) fail(e);
      }
    }, 1200);
    return () => {
      stopped = true;
      clearInterval(timer);
    };
  }, [job?.id, selected?.id, user?.id]);

  function askToDelete(target: HistoryTarget) {
    setHistoryTarget(target);
    setDeleteError("");
    setModal("delete");
  }

  async function deleteSelectedHistory() {
    if (!user || !historyTarget || deleting) return;
    const { kind, id } = historyTarget;
    setDeleting(true);
    setDeleteError("");
    try {
      await api((kind === "contract" ? "/contracts/" : "/jobs/") + id, "DELETE");
      setModal("");
      setHistoryTarget(null);
      setError("");
      if (kind === "contract") {
        setContracts(current => current.filter(contract => contract.id !== id));
        if (selectedRef.current === id) showDesk();
        setNotice(t("合同及其历史记录已删除"));
        await clearContractHistory(user.id, id).catch(() =>
          setNotice(t("合同已删除，当前浏览器的本地历史清理失败")),
        );
      } else {
        if (selected) await selectContract(selected);
        setNotice(t("审查记录已删除"));
      }
    } catch (e) {
      setDeleteError(e instanceof Error ? e.message : String(e));
    } finally {
      setDeleting(false);
    }
  }

  async function upload(file: File) {
    setBusy(true);
    setError("");
    try {
      const form = new FormData();
      form.append("file", file);
      form.append("company", selected?.company || t("未归类"));
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
      const text = local.draft.trim() || t("请按所选清单审查这份合同");
      const next = await api<Job>(
        "/contracts/" + selected.id + "/jobs",
        "POST",
        {
          instruction: text,
          skill_id: skill,
          parent_id: job?.id || "",
          request_key: crypto.randomUUID(),
          model_mode: requestedMode,
          ollama_model: ollamaModel,
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
      setFilter("");
      setModal("");
      setNotice("");
      setSkills([]);
      setArtifacts([]);
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
    }, Object.create(null));
  const running = activeStates.includes(job?.state || "");
  const rows = job?.result?.rows || [];
  const terminal = job && !running;
  const demoMode = !config?.model_mode || config.model_mode === "demo";

  if (!user)
    return (
      <>
      <LoginPage
        config={config}
        onLogin={signIn}
        error={error}
        setError={setError}
      />
      <LanguageSwitch onChange={chooseLanguage} floating />
      </>
    );
  return (
    <div className={"app " + (!sidebar ? "collapsed" : "")}>
      {t(compact && sidebar && <button tabIndex={-1} type="button" className="drawer-scrim" aria-label={t("收起导航")} onClick={closeDrawer} />)}
      {t(sidebar && (
        <aside className="sidebar" id="workspace-nav" aria-label={t("工作台导航")}
          onKeyDown={event => { if (event.key === "Escape" && !modal) closeDrawer(); }}>
          <div className="brand">
            <div className="brand-mark">
              <FileCheck2 size={22} />
            </div>
            <span>
              Clause<span className="brand-sub">{t("合同审查工作台")}</span>
            </span>
            {t(compact && <button className="drawer-close" aria-label={t("关闭侧栏")} onClick={closeDrawer} ref={drawerClose}><X size={18}/></button>)}
          </div>
          <button className={"desk-link" + (!selected ? " current" : "")} onClick={showDesk} aria-current={!selected ? "page" : undefined}>
            {t(user.role === "legal" ? <LayoutDashboard size={17}/> : <LibraryBig size={17}/>)}
            {t(user.role === "legal" ? t("工作台首页") : t("合同档案总览"))}
            <ChevronRight size={14}/>
          </button>
          <div className="sidebar-title">
            <span>{t("合同档案")}<small lang="en">Files</small></span>
            <span className="file-count">{contracts.length}</span>
          </div>
          <div className="search">
            <Search size={15} />
            <input
              aria-label={t("搜索合同")}
              placeholder={t("搜索公司或合同")}
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
            />
          </div>
          <nav className="contract-list" aria-label={t("合同与历史记录")}>
            {t(Object.entries(groups).map(([company, items]) => (
              <div className="company" key={company}>
                <div className="company-name">
                  <Folder size={14} />
                  {t(company)}
                </div>
                {t(items.map((c) => (
                  <div key={c.id}>
                    <div className="history-row">
                    <button
                      className={
                        "contract " + (selected?.id === c.id ? "selected" : "")
                      }
                      onClick={() => selectContract(c)}
                      title={c.name}
                      aria-current={selected?.id === c.id ? "page" : undefined}
                    >
                      <FileText size={15} />
                      <span>
                        {c.name}
                        <small>
                          {t(user.role === "admin"
                            ? c.owner_email
                            : new Date(c.created * 1000).toLocaleDateString())}
                        </small>
                      </span>
                    </button>
                    {t(user.role === "legal" && (
                      <button className="history-delete" disabled={busy}
                        aria-label={`删除合同 ${c.name}`} title={t("删除合同")}
                        onClick={() => askToDelete({ kind: "contract", id: c.id, name: c.name })}>
                        <Trash2 size={15} />
                      </button>
                    ))}
                    </div>
                    {t(selected?.id === c.id && jobs.length > 0 && (
                      <div className="job-list">
                        {t(jobs.map((j) => (
                          <div className="history-row" key={j.id}>
                          <button
                            className={job?.id === j.id ? "current" : ""}
                            onClick={() => { closeDrawer(); setGeneral(false); openJob(j.id).catch(fail); }}
                          >
                            <Circle size={7} />
                            <span>{t(j.instruction || t("标准合同审查"))}</span>
                          </button>
                          <button className="history-delete" disabled={busy}
                            aria-label={`删除审查记录 ${j.instruction || t("标准合同审查")}`} title={t("删除审查记录")}
                            onClick={() => askToDelete({ kind: "job", id: j.id, name: j.instruction || t("标准合同审查") })}>
                            <Trash2 size={14} />
                          </button>
                          </div>
                        )))}
                      </div>
                    ))}
                  </div>
                )))}
              </div>
            )))}
            {t(!Object.keys(groups).length && (
              <p className="quiet sidebar-empty">
                {t(filter.trim() ? t("没有匹配的合同") : t("暂无合同档案"))}
              </p>
            ))}
          </nav>
          <div className="sidebar-bottom">
            {t(user.role === "legal" && (
              <>
                <button onClick={() => setModal("skills")}>
                  <Settings2 size={16} /> {t("我的 Skills")}</button>
              </>
            ))}
            <button onClick={() => setModal("mail")}><Mail size={16} /> {t("邮件处理")}</button>
            <div className="account">
              <span className="avatar">
                {t(user.role === "admin" ? (
                  <ShieldCheck size={18} />
                ) : (
                  user.email[0].toUpperCase()
                ))}
              </span>
              <span>
                {t(user.role === "admin" ? t("总账户") : t("法务工作空间"))}
                <small>{user.email}</small>
              </span>
              <button aria-label={t("退出登录")} onClick={logout}>
                <LogOut size={16} />
              </button>
            </div>
          </div>
        </aside>
      ))}
      <main className="workspace" inert={compact && sidebar}>
        <header>
          <button
            className="icon-button"
            aria-label={t("切换侧栏")}
            aria-expanded={sidebar}
            aria-controls={sidebar ? "workspace-nav" : undefined}
            ref={sidebarToggle}
            onClick={() => setSidebar(!sidebar)}
          >
            <PanelLeft size={18} />
          </button>
          <div className="breadcrumb">
            {t(selected ? (
              <>
                <button onClick={showDesk}>{t("合同档案")}</button>
                <ChevronRight size={14} />
                <strong>{selected.name}</strong>
              </>
            ) : (
              <><LibraryBig size={16} /><strong>{t(user.role === "admin" ? t("合同档案") : t("法务工作台"))}</strong></>
            ))}
          </div>
          {t(canSelectModel ? (
            <label className="mode live ollama-selector">
              <span aria-hidden="true" />
              <select aria-label={t("选择对话与审查模型")} value={selectedModelOption}
                onChange={(event) => chooseModel(event.target.value, user.id)}>
                {t(config.model_mode === "ollama" && (
                  <option value="">{t(modelLabel("ollama", config.model_name))}</option>
                ))}
                {t(ollamaModel && !ollamaModels?.includes(ollamaModel) && (
                  <option value={`ollama:${ollamaModel}`}>{t(modelLabel("ollama", ollamaModel))}{t(ollamaModels && t("（不可用）"))}</option>
                ))}
                {t(ollamaModels?.filter((name) => name !== config.model_name || name === ollamaModel).map((name) => (
                  <option key={name} value={`ollama:${name}`}>{t(modelLabel("ollama", name))}</option>
                )))}
                <option value="api" disabled={!config.api_configured}>{modelLabel("api", config.api_model_name)}</option>
                <option value="tacc" disabled={!config.tacc_configured}>
                  {t(modelLabel("tacc"))}{t(!config.tacc_configured && t("（连接未配置）"))}
                </option>
                {t(!ollamaModels?.length && (
                  <option disabled>{t(ollamaModelsError || (ollamaModels ? t("暂无已安装的对话模型") : t("正在读取模型列表…")))}</option>
                ))}
              </select>
            </label>
          ) : (
            <span className={"mode " + (demoMode ? "" : "live")}>
              <span /> {t(modelLabel(config?.model_mode, config?.model_name))}
            </span>
          ))}
          {t(user.role === "legal" && (
            <button
              className="top-upload"
              onClick={() => fileInput.current?.click()}
              disabled={busy}
            >
              <Plus size={15} /> {t("添加合同")}</button>
          ))}
          <LanguageSwitch onChange={chooseLanguage} />
        </header>
        <input
          ref={fileInput}
          aria-label={t("上传合同")}
          type="file"
          accept=".pdf,.docx,.txt"
          hidden
          onChange={(e) => {
            if (e.target.files?.[0]) upload(e.target.files[0]);
            e.target.value = "";
          }}
        />
        {t(error && (
          <div className="alert" role="alert">
            {t(error)}
            <button aria-label={t("关闭错误提示")} onClick={() => setError("")}>
              <X size={15} />
            </button>
          </div>
        ))}
        {t(notice && (
          <div className="notice" role="status">
            {t(notice)}
            <button aria-label={t("关闭通知")} onClick={() => setNotice("")}>
              <X size={15} />
            </button>
          </div>
        ))}
        {t(!selected && user.role === "legal" ? (
          <AssistantChat key={user.id + ":general"} userId={user.id} demo={demoMode}
            deskVisit={deskVisit}
            deskContracts={contracts} onOpenContract={id => {
              const contract = contracts.find(item => item.id === id);
              if (contract) selectContract(contract);
            }}
            onUpload={() => { if (!busy) fileInput.current?.click(); }}
            onOpenSkills={() => setModal("skills")}
            modelMode={requestedMode} ollamaModel={ollamaModel}
            onSkillsChanged={refreshSkills} onReview={openAgentReview}/>
        ) : !selected ? (
          <section className="archive-view" aria-labelledby="archive-title">
            <div className="archive-heading">
              <div>
                <span className="eyebrow" lang="en">Document archive</span>
                <h1 id="archive-title">{t("合同档案总览")}</h1>
                <p>{t("按合同查阅原件与最终审查报告。")}</p>
              </div>
              <LibraryBig size={40} strokeWidth={1.2} aria-hidden="true"/>
            </div>
            <div className="archive-register">
              <div className="register-head"><h2>{t("归档合同")}</h2><span>{contracts.length} {t("份合同")}</span></div>
              {t(Object.values(groups).flat().map(contract => (
                <button className="archive-file" key={contract.id} onClick={() => selectContract(contract)}>
                  <FileText size={20} />
                  <span><strong>{contract.name}</strong><small>{t(contract.company)} · {contract.owner_email}</small></span>
                  <time dateTime={new Date(contract.created * 1000).toISOString()}>{t(new Date(contract.created * 1000).toLocaleDateString(getLanguage() === "en" ? "en-GB" : "zh-CN"))}</time>
                  <ChevronRight size={16}/>
                </button>
              )))}
              {t(!Object.keys(groups).length && <p className="archive-empty">{t(filter ? t("没有匹配的合同") : t("暂无归档合同"))}</p>)}
            </div>
          </section>
        ) : fileState !== "ready" ? (
          <div className="archive-load" role="status">
            {t(fileState === "loading"
              ? <><LoaderCircle className="spin" size={20}/> {t("正在打开合同档案…")}</>
              : <><FileText size={20}/> {t("无法打开合同档案")}<button className="secondary" onClick={() => selectContract(selected)}>{t("重新打开")}</button></>)}
          </div>
        ) : user.role === "admin" ? (
          <section className="archive-view">
            <div className="archive-heading">
              <div><span className="eyebrow" lang="en">Contract file</span>
                <h1>{selected.name}</h1><p>{selected.company} · {selected.owner_email}</p>
              </div>
            </div>
            <div className="archive-register">
              <div className="register-head"><h2>{t("文件目录")}</h2><span>{artifacts.length} {t("份文件")}</span></div>
              {t(artifacts.map(f => (
                <a key={f.id} className="archive-file" href={fileUrl(f.id, true)}>
                  {t(f.kind === "input" ? <FileText size={21}/> : <FileCheck2 size={21}/>)}
                  <span><strong>{f.name}</strong><small>{t(f.kind === "input" ? t("合同原件") : t("最终审查报告"))}</small></span>
                  <time dateTime={new Date(f.created * 1000).toISOString()}>{t(new Date(f.created * 1000).toLocaleDateString(getLanguage() === "en" ? "en-GB" : "zh-CN"))}</time>
                  <Download size={16}/>
                </a>
              )))}
            </div>
          </section>
        ) : (
          <ReviewPanes key={selected.id}>
            <section className="chat-panel">
              <div className="chat-mode-tabs" role="group" aria-label={t("合同处理方式")}>
                <button aria-pressed={!general} className={!general ? "active" : ""} onClick={() => setGeneral(false)}>{t("合同审查")}</button>
                <button aria-pressed={general} className={general ? "active" : ""} onClick={() => setGeneral(true)}>{t("自由对话与工具")}</button>
              </div>
              {t(general ? <AssistantChat key={user.id + ":" + selected.id} userId={user.id} contractId={selected.id}
                demo={demoMode} modelMode={requestedMode} ollamaModel={ollamaModel}
                onSkillsChanged={refreshSkills} onReview={openAgentReview}/> : <>
              <div className="chat-scroll">
                <header className="matter-cover">
                  <div className="matter-cover-heading">
                    <span lang="en"><FileText size={15} aria-hidden="true" /> Contract file</span>
                    <a aria-label={t("下载原件")} title={t("下载原件")} href={fileUrl(selected.file_id, true)}>
                      <Download size={16} />
                    </a>
                  </div>
                  <h1>{selected.name}</h1>
                  <div className="matter-file-meta">
                    <button onClick={() => setModal("company")} aria-label={t("修改公司归类")} title={t("修改公司归类")}>
                      <Folder size={13} /> {selected.company} <Pencil size={11} />
                    </button>
                    <time dateTime={new Date(selected.created * 1000).toISOString()}>
                      {t(new Date(selected.created * 1000).toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "numeric" }))}
                    </time>
                  </div>
                </header>
                {t(!local.messages.length && !job && (
                  <div className="assistant-intro">
                    <h2>{t("选择审查范围")}</h2>
                    <p>{t("选用标准审查或指定条款，也可在下方输入要求。")}</p>
                    <div className="suggestions">
                      {t([
                        t("进行标准合同审查"),
                        t("自动续约需要提前多久通知？"),
                        t("检查责任上限与终止条款"),
                      ].map((text) => (
                        <button
                          key={text}
                          onClick={() => persist({ ...local, draft: text })}
                        >
                          {t(text)}
                          <ChevronRight size={14} />
                        </button>
                      )))}
                    </div>
                  </div>
                ))}
                {t(local.messages.map((message) =>
                  message.role === "user" ? (
                    <div className="user-bubble" key={message.id}>
                      {message.text}
                    </div>
                  ) : message.job !== job?.id ? (
                    <details
                      className="answer-detail prior-answer"
                      key={message.id}
                    >
                      <summary>{t("历史审查回答")}</summary>
                      <p>{message.text}</p>
                      {t(message.job && (
                        <button
                          className="text-button"
                          onClick={() => openJob(message.job!).catch(fail)}
                        >
                          {t("打开结果")}</button>
                      ))}
                    </details>
                  ) : null,
                ))}
                {t(job && (
                  <div className="assistant-message">
                    <div className="assistant-name">
                      <div className="small-logo">C</div> Clause{t(" ")}
                    </div>
                    <div className="trace">
                      <div className="trace-heading">
                        {t(running ? (
                          <LoaderCircle size={15} className="spin" />
                        ) : job.state === "completed" ? (
                          <Check size={15} />
                        ) : (
                          <Circle size={13} />
                        ))}
                        <b>{t(statusLabel[job.state] || job.state)}</b>
                        <span>
                          {t(job.result?.seconds ? job.result.seconds + "s" : "")}
                        </span>
                      </div>
                      <details open={running}>
                        <summary>{t("处理记录 ·")}{events.length} {t("条")}</summary>
                        <div className="trace-list">
                          {t(events.map((e) => (
                            <div key={e.id}>
                              <span
                                className={
                                  "trace-dot " +
                                  (e.stage === "error" ? "red" : "")
                                }
                              />
                              <div>
                                {e.title}
                                {t(e.detail && <small>{e.detail}</small>)}
                              </div>
                            </div>
                          )))}
                        </div>
                      </details>
                    </div>
                    {t(job.state === "awaiting_confirmation" && (
                      <>
                        <p>
                          {t("邮箱合同等待你确认归属。确认后开始审查并回传报告。")}</p>
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
                          {t("确认并开始审查")}</button>
                      </>
                    ))}
                    {t(terminal && job.result && (
                      <>
                        <p className="result-summary">
                          {t(job.result.message ||
                            (rows.length
                              ? <>
                                <span className="wide-summary">{t("已完成")}{rows.length} {t("类条款检查，引用与清单判断见右侧。")}</span>
                                <span className="compact-summary">{t("已完成")}{rows.length} {t("类条款检查。可在「审查底稿」查看引文与清单判断。")}</span>
                              </>
                              : t("处理已结束。")))}
                        </p>
                        <div className="summary-chips">
                          <span>
                            {rows.filter((r) => r.status === "found").length}{t(" ")}
                            {t("类有证据")}</span>
                          <span>
                            {
                              rows.filter((r) => r.assessment === "待核验")
                                .length
                            }{t(" ")}
                            {t("类待核验")}</span>
                        </div>
                        {t(job.result.answer && (
                          <details className="answer-detail">
                            <summary>{t("查看本次回答")}</summary>
                            <p>{job.result.answer}</p>
                          </details>
                        ))}
                      </>
                    ))}
                    {t(job.error && <p className="error-text">{job.error}</p>)}
                    {t(job.report_file && (
                      <a
                        className="report-card"
                        href={fileUrl(job.report_file, true)}
                      >
                        <div className="file-symbol green">
                          <FileCheck2 size={22} />
                        </div>
                        <span>
                          <strong>{t("合同审查报告")}</strong>
                          <small>{t("Word 文档 · 原文证据与风险表")}</small>
                        </span>
                        <Download size={18} />
                      </a>
                    ))}
                    {t(job.delivery && (
                      <div className="delivery">
                        <Mail size={14} />
                        {
                          t((
                            {
                              pending: t("邮件等待发送"),
                              sending: t("邮件发送中"),
                              accepted: t("已提交邮箱发送"),
                              demo: t("结果已保存到演示发件箱"),
                              failed: t("邮件发送失败"),
                              uncertain: t("发送状态待核对"),
                            } as Record<string, string>
                          )[job.delivery])
                        }
                        {t(["failed", "uncertain"].includes(job.delivery) && (
                          <button
                            onClick={async () => {
                              await api("/jobs/" + job.id + "/resend", "POST");
                              await openJob(job.id);
                            }}
                          >
                            {t("重发")}</button>
                        ))}
                      </div>
                    ))}
                  </div>
                ))}
              </div>
              <div className="composer-wrap">
                <div className="composer">
                  <textarea
                    aria-label={t("审查要求")}
                    placeholder={t("询问这份合同，或输入审查要求…")}
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
                      aria-label={t("添加新合同")}
                      onClick={() => fileInput.current?.click()}
                    >
                      <Paperclip size={18} />
                    </button>
                    <select
                      aria-label={t("选择 Skill")}
                      value={skill}
                      onChange={(e) => setSkill(e.target.value)}
                    >
                      <option value="">{t("标准审查 · 8 类")}</option>
                      {t(skills.map((s) => (
                        <option key={s.id} value={s.id}>
                          {s.name}
                        </option>
                      )))}
                    </select>
                    <button
                      className="send-button"
                      aria-label={t("开始审查")}
                      onClick={submit}
                      disabled={busy || running}
                    >
                      {t(running ? (
                        <LoaderCircle className="spin" size={17} />
                      ) : (
                        <ArrowUp size={19} />
                      ))}
                      <span>{t("开始审查")}</span>
                    </button>
                  </div>
                </div>
                <div className="composer-note">
                  {t("聊天保存在本浏览器 · 结果与文件保存到服务器")}</div>
              </div>
              </>)}
            </section>
            <section className="document-panel">
              <div className="document-tabs" role="group" aria-label={t("审查底稿视图")}>
                {t([
                  ["risk", t("风险表")],
                  ["text", t("原文证据")],
                  ["original", t("原件")],
                  ["edit", t("编辑报告")],
                ].map(([key, label]) => (
                  <button
                    key={key}
                    aria-pressed={tab === key}
                    className={tab === key ? "active" : ""}
                    onClick={() => setTab(key)}
                  >
                    {t(label)}
                  </button>
                )))}
              </div>
              <div className="document-body">
                {t(tab === "risk" && (
                  <>
                    <div className="document-title">
                      <h2 lang="en">Review findings</h2>
                      <p>{t("合同风险与证据")}{rows.length > 0 && ` · ${clauseCount(rows.length)}`}</p>
                    </div>
                    {t(!rows.length ? (
                      <div className="empty-state">
                        <div className="empty-folio" aria-hidden="true"><FileCheck2 size={30} strokeWidth={1.4} /></div>
                        <p>
                          {t(running
                            ? t(statusLabel[job!.state] || "审查处理中")
                            : job?.error || job?.result?.message || t("提交审查后，可在这里逐条核对判断与原文。"))}
                        </p>
                      </div>
                    ) : (
                      <div className="risk-list">
                        {t(rows.map((row, i) => (
                          <article className="risk-row" key={row.category}>
                            <div className="risk-top">
                              <span className="risk-number">
                                {t(String(i + 1).padStart(2, "0"))}
                              </span>
                              <h3>{t(row.label)}</h3>
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
                            {t(row.finding && (
                              <p className="finding">{row.finding}</p>
                            ))}
                            {t(row.explanation && <p>{row.explanation}</p>)}
                            {t(row.evidence.map((e, index) => {
                              const sourceLabel = e.retrieval_sources
                                ?.filter((source) => source !== "survey")
                                .map((source) => retrievalSourceLabels[source])
                                .join(" + ");
                              return (
                                <button
                                  className="quote"
                                  key={index}
                                  onClick={() => evidence(e)}
                                >
                                  <span>
                                    <b className="reference-id">{e.paragraph_id}</b>
                                    {t(e.page ? t(" · 第 ") + e.page + t(" 页") : "")}
                                    {t(sourceLabel && (
                                      <span className="quote-source">{t(sourceLabel)}</span>
                                    ))}
                                    <ChevronRight size={14} className="quote-arrow" aria-hidden="true" />
                                  </span>
                                  <q>{e.quote}</q>
                                </button>
                              );
                            }))}
                            {t(row.status === "not_found" && (
                              <div className="not-found">
                                {t("本次未检出对应原文")}</div>
                            ))}
                            {t(row.missing.length > 0 && (
                              <small>{t("待确认 ·")}{t(row.missing.join(" / "))}</small>
                            ))}
                            {t(!!row.checklist?.length && (
                              <details className="audit-checklist">
                                <summary>{t("审计核对项 ·")}{row.checklist.length} {t("项")}</summary>
                                <dl>
                                  {t(row.checklist.map((item, index) => (
                                    <div key={index}>
                                      <dt>{item.requirement}</dt>
                                      <dd>{item.assessment}</dd>
                                    </div>
                                  )))}
                                </dl>
                              </details>
                            ))}
                          </article>
                        )))}
                      </div>
                    ))}
                  </>
                ))}
                {t(tab === "text" && (
                  <>
                    <div className="document-title">
                      <h2 lang="en">Source evidence</h2>
                      <p>
                        {t("合同正文 ·")}{paragraphs.length} {t("个段落")}</p>
                    </div>
                    {t(paragraphs.length === 0 && (
                      <div className="empty-state">{t("解析完成后显示正文")}</div>
                    ))}
                    {t(paragraphs.map((p) => {
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
                            {t(p.page ? " / PAGE " + p.page : "")}
                          </small>
                          <p>
                            {t(index >= 0 && highlight ? (
                              <>
                                {t(p.text.slice(0, index))}
                                <mark>{highlight.quote}</mark>
                                {t(p.text.slice(index + highlight.quote.length))}
                              </>
                            ) : (
                              p.text
                            ))}
                          </p>
                        </div>
                      );
                    }))}
                  </>
                ))}
                {t(tab === "original" &&
                  (selected.name.toLowerCase().endsWith(".pdf") ? (
                    <iframe
                      title={t("合同原件")}
                      className="pdf-frame"
                      src={fileUrl(selected.file_id)}
                    />
                  ) : (
                    <div className="empty-state">
                      <FileText size={32} />
                      <p>{t("在「原文证据」阅读可定位的合同段落。")}</p>
                      <a
                        className="secondary"
                        href={fileUrl(selected.file_id, true)}
                      >
                        <Download size={16} /> {t("下载原始文件")}</a>
                    </div>
                  )))}
                {t(tab === "edit" && (
                  <>
                    <div className="document-title">
                      <h2 lang="en">Report draft</h2>
                      <p>{t("编辑审查报告 · 保存为个人 Word 编辑稿")}</p>
                    </div>
                    <textarea
                      className="report-editor"
                      aria-label={t("报告内容")}
                      value={editor}
                      placeholder={t("审查完成后可编辑报告")}
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
                          setNotice(t("编辑稿已保存并开始下载。"));
                        } catch (e) {
                          fail(e);
                        }
                      }}
                    >
                      <Download size={16} /> {t("保存并下载编辑稿")}</button>
                  </>
                ))}
              </div>
            </section>
          </ReviewPanes>
        ))}
      </main>
      {t(modal && (
        <dialog ref={modalRef} className="work-dialog"
          onCancel={event => { if (deleting) event.preventDefault(); else setModal(""); }}
          aria-label={{skills: t("我的 Skills"), mail: t("邮件处理"), company: t("合同归类"), delete: t("确认删除历史记录")}[modal]}
          onClick={event => { if (!deleting && event.target === event.currentTarget) setModal(""); }}>
          <div className="modal">
            <button
              className="modal-close"
              aria-label={t("关闭")}
              disabled={deleting}
              onClick={() => setModal("")}
            >
              <X size={20} />
            </button>
            {t(modal === "delete" && historyTarget && (
              <>
                <h2>{t(historyTarget.kind === "contract" ? t("删除合同") : t("删除审查记录"))}</h2>
                <p className="delete-item">{historyTarget.name}</p>
                <p className="quiet">{t(historyTarget.kind === "contract"
                  ? t("合同原件、关联审查记录、报告和合同对话将从工作台删除。此操作无法撤销。")
                  : t("这次审查的结果、处理记录、报告和编辑稿将被删除。此操作无法撤销。"))}</p>
                {t(deleteError && <p className="error-text" role="alert">{t(deleteError)}</p>)}
                <div className="delete-actions">
                  <button className="secondary" autoFocus disabled={deleting} onClick={() => setModal("")}>{t("取消")}</button>
                  <button className="primary danger" disabled={deleting} onClick={deleteSelectedHistory}>
                    {t(deleting ? <LoaderCircle className="spin" size={15}/> : <Trash2 size={15}/>)}
                    {t(deleting ? t("正在删除…") : t("确认删除"))}
                  </button>
                </div>
              </>
            ))}
            {t(modal === "company" && (
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
            ))}
            {t(modal === "skills" && (
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
                  setNotice(t("Skill 已保存，可在输入框下方选择。"));
                  setModal("");
                }}
              />
            ))}
            {modal === "mail" && <MailPanel key={user.id} role={user.role} mailbox={config?.mailbox || ""} />}
          </div>
        </dialog>
      ))}
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
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [code, setCode] = useState("");
  const [info, setInfo] = useState("");
  const [busy, setBusy] = useState(false);
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      if (register) {
        await api("/auth/register", "POST", { email, password, code });
        setRegister(false);
        setInfo(t("注册成功，请登录。"));
      } else onLogin(await api("/auth/login", "POST", { email, password }));
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <main className="entry-page">
      <section className="entry-cover" aria-labelledby="entry-title">
        <img className="entry-architecture" src="/images/chambers.webp" alt="" />
        <div className="entry-brand">
          <FileCheck2 size={27} strokeWidth={1.6} aria-hidden="true" />
          <span lang="en">Clause</span>
        </div>
        <div className="entry-cover-title">
          <h1 id="entry-title" lang="en">Contract<br /> Review<span>.</span></h1>
          <p>{t("合同审查与证据核验")}</p>
        </div>
        <div className="entry-cover-footer">
          <span>CSIT6000V</span>
          <span lang="en">Course Project</span>
        </div>
      </section>
      <section className="entry-form-wrap" aria-labelledby="entry-form-title">
        <form className="entry-form" onSubmit={submit}>
          <p className="entry-section-label" lang="en">{t(register ? "Registration" : "Workspace access")}</p>
          <h2 id="entry-form-title">{t(register ? t("创建法务账户") : t("登录工作空间"))}</h2>
          <p className="entry-form-intro">
            {t(register ? t("使用邮箱验证码开通你的法务工作空间。") : t("使用你的邮箱访问合同、审查清单与报告。"))}
          </p>
          <label>
            {t("邮箱")}<input
              type="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              autoComplete="email"
              placeholder="name@company.com"
            />
          </label>
          <label>
            {t("密码")}<span className="entry-password-field"><input
              type={showPassword ? "text" : "password"}
              required
              minLength={register ? 10 : 1}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete={register ? "new-password" : "current-password"}
              placeholder={register ? t("至少 10 个字符") : t("输入密码")}
            /><button type="button" className="entry-password-toggle"
                aria-label={t(showPassword ? "隐藏密码" : "显示密码")} aria-pressed={showPassword}
                onClick={() => setShowPassword(value => !value)}>
                {showPassword ? <EyeOff size={18} aria-hidden="true" /> : <Eye size={18} aria-hidden="true" />}
              </button></span>
          </label>
          {t(register && (
            <div className="entry-code">
              <label htmlFor="entry-code">{t("邮箱验证码")}</label>
              <div className="entry-code-line">
                <input
                  id="entry-code"
                  autoComplete="one-time-code"
                  inputMode="numeric"
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
                          ? t("演示验证码：") + r.demo_code
                          : t("验证码已发送"),
                      );
                    } catch (e: any) {
                      setError(e.message);
                    }
                  }}
                >
                  {t("发送验证码")}</button>
              </div>
            </div>
          ))}
          {t(error && (
            <div className="form-error" role="alert">
              {t(error)}
            </div>
          ))}
          {t(info && <div className="form-info" role="status">{t(info)}</div>)}
          <button className="entry-submit" disabled={busy}>
            {t(busy ? t("请稍候…") : register ? t("注册") : t("登录"))}
            <ChevronRight size={17} />
          </button>
          <button
            type="button"
            className="entry-switch"
            onClick={() => {
              setRegister(!register);
              setError("");
              setInfo("");
            }}
          >
            {t(register ? t("已有账户？返回登录") : t("使用邮箱验证码注册"))}
          </button>
          {t(config?.login_accounts?.length > 0 && (
            <div className="entry-demo">
              <div className="entry-demo-title"><ShieldCheck size={15} aria-hidden="true" />{t("快捷账户")}</div>
              <div className="entry-demo-options">
                {t(config.login_accounts.map(({label, email: address}: {label: string; email: string}) => (
                  <button
                    type="button"
                    key={label}
                    title={address}
                    aria-pressed={!register && email === address}
                    onClick={() => {
                      setEmail(address);
                      setRegister(false);
                    }}
                  >
                    {t(label)}
                  </button>
                )))}
              </div>
              <small>{t("仅填写邮箱，密码由用户自行输入。")}</small>
            </div>
          ))}
        </form>
      </section>
    </main>
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
      <h2>{t("合同归类")}</h2>
      <label>
        {t("公司或项目名称")}<input
          required
          maxLength={100}
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
      </label>
      <button className="primary">{t("保存归类")}</button>
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
  const standardCategories: { id: string; label: string }[] = config?.categories || [];
  const editingLegacySkill = skills.find((skill) => skill.id === id)?.categories?.includes("liquidated_damages");
  const categoryOptions = editingLegacySkill
    ? [...standardCategories, { id: "liquidated_damages", label: t("约定赔偿（旧版模板）") }]
    : standardCategories;
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
      <h2>{t("我的审查模板")}</h2>
      <label>
        {t("编辑已有模板")}<select
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
          <option value="">{t("创建新 Skill")}</option>
          {t(skills.map((s) => (
            <option key={s.id} value={s.id}>
              {s.name} · v{s.version}
            </option>
          )))}
        </select>
      </label>
      <label>
        {t("模板名称")}<input
          required
          maxLength={60}
          placeholder={t("例如：供应商续约检查")}
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
      </label>
      <div className="category-options">
        {t(categoryOptions.map((c) => (
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
            {t(c.label)}
          </label>
        )))}
      </div>
      <details>
        <summary>{t("自定义每类核对项")}</summary>
        {t(categoryOptions
          .filter((item) => categories.includes(item.id))
          .map((item) => (
            <label key={item.id}>
              {t(item.label)}
              <input
                value={checks[item.id] || ""}
                onChange={(event) =>
                  setChecks({ ...checks, [item.id]: event.target.value })
                }
                placeholder={t("用逗号分隔，留空使用默认核对项")}
                maxLength={700}
              />
            </label>
          )))}
      </details>
      <label>
        {t("模板 Prompt")}<textarea
          value={focus}
          onChange={(e) => setFocus(e.target.value)}
          maxLength={1500}
          placeholder={t("例如：总结保密义务的主体、范围、例外和存续期。可单独使用，无需勾选内置类别。")}
        />
      </label>
      <div className="custom-items">
        {t(customItems.map((item, index) => (
          <div key={index}>
            <label>
              {t("自定义项标题")}<input
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
              {t("审查要求")}<textarea
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
              {t("移除此项")}</button>
          </div>
        )))}
        <button
          className="secondary"
          type="button"
          disabled={customItems.length >= 6}
          onClick={() =>
            setCustomItems([...customItems, { title: "", question: "" }])
          }
        >
          <Plus size={14} /> {t("添加自定义审查项")}</button>
      </div>
      <label>
        {t("审计清单")}<select value={policy} onChange={(e) => setPolicy(e.target.value)}>
          <option value="supplier-demo@1">
            {t("供应商示例清单 v1 · 续约通知至少 30 个自然日")}</option>
          <option value="evidence-only@1">
            {t("证据提取清单 · 由人工判断符合性")}</option>
        </select>
      </label>
      {t(error && <p className="error-text">{t(error)}</p>)}
      <button
        className="primary"
        disabled={!categories.length && !customItems.length && !focus.trim()}
      >
        {t("保存 Skill")}</button>
    </form>
  );
}

createRoot(document.getElementById("root")!).render(<App />);
