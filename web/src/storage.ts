export interface LocalMessage {
  id: string;
  role: "user" | "assistant";
  text: string;
  job?: string;
}
export interface LocalConversation {
  messages: LocalMessage[];
  draft: string;
  reportDraft?: string;
  draftJobId?: string;
  result?: unknown;
}
let connection: Promise<IDBDatabase> | undefined;
export const persistChat = import.meta.env.VITE_PERSIST_CHAT !== "0";
const transient = new Map<string, LocalConversation>();
function open() {
  connection ??= new Promise((resolve, reject) => {
    const request = indexedDB.open("clause-workbench", 1);
    request.onupgradeneeded = () =>
      request.result.createObjectStore("conversations");
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
  return connection;
}
export async function loadLocal(
  user: string,
  contract: string,
): Promise<LocalConversation> {
  if (!persistChat) return transient.get(user + ":" + contract) || { messages: [], draft: "" };
  const db = await open();
  return new Promise((resolve, reject) => {
    const request = db
      .transaction("conversations")
      .objectStore("conversations")
      .get(user + ":" + contract);
    request.onsuccess = () =>
      resolve(request.result || { messages: [], draft: "" });
    request.onerror = () => reject(request.error);
  });
}
export async function saveLocal(
  user: string,
  contract: string,
  value: LocalConversation,
) {
  if (!persistChat) { transient.set(user + ":" + contract, value); return; }
  const db = await open();
  return new Promise<void>((resolve, reject) => {
    const transaction = db.transaction("conversations", "readwrite");
    transaction.objectStore("conversations").put(value, user + ":" + contract);
    transaction.oncomplete = () => resolve();
    transaction.onerror = () => reject(transaction.error);
  });
}

export async function clearContractHistory(user: string, contract: string) {
  const reviewKey = user + ":" + contract;
  const chatKey = user + ":assistant:" + contract;
  const threadPrefix = chatKey + ":";
  if (!persistChat) {
    for (const key of transient.keys()) {
      if (key === reviewKey || key === chatKey || key.startsWith(threadPrefix)) transient.delete(key);
    }
    return;
  }
  const db = await open();
  await new Promise<void>((resolve, reject) => {
    const transaction = db.transaction("conversations", "readwrite");
    const conversations = transaction.objectStore("conversations");
    conversations.delete(reviewKey);
    conversations.delete(chatKey);
    conversations.delete(IDBKeyRange.bound(threadPrefix, chatKey + ";", false, true));
    transaction.oncomplete = () => resolve();
    transaction.onabort = () => reject(transaction.error);
  });
  localStorage.removeItem("assistant-index:" + user + ":" + contract);
}

export function pruneReviewHistory(saved: LocalConversation, jobIds: Set<string>): LocalConversation {
  const messages = saved.messages.filter(message => !message.job || jobIds.has(message.job));
  const hasRemovedDraft = saved.draftJobId !== undefined && !jobIds.has(saved.draftJobId);
  if (messages.length === saved.messages.length && !hasRemovedDraft) return saved;
  return {
    messages,
    draft: saved.draft,
    ...(hasRemovedDraft ? {} : { draftJobId: saved.draftJobId, reportDraft: saved.reportDraft }),
  };
}
