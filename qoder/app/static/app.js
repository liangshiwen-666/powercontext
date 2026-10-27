const $ = (sel) => document.querySelector(sel);

const state = {
  user: localStorage.getItem("study_user") || "demo",
  lastAnswer: null,
};

function userKey() {
  return state.user.trim() || "demo";
}

async function api(path, options = {}) {
  const resp = await fetch(path, options);
  const text = await resp.text();
  let data;
  try {
    data = text ? JSON.parse(text) : {};
  } catch {
    data = { raw: text };
  }
  if (!resp.ok) {
    const detail = data.detail || data.error || resp.statusText;
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return data;
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function show(node) {
  node.classList.remove("hidden");
}

// ---- tabs ----
document.querySelectorAll(".tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
    document.querySelectorAll(".panel").forEach((p) => p.classList.remove("active"));
    tab.classList.add("active");
    $(`#panel-${tab.dataset.panel}`).classList.add("active");
    if (tab.dataset.panel === "docs") loadDocs();
    if (tab.dataset.panel === "plan") loadMemories();
    if (tab.dataset.panel === "evidence") loadEvidence();
  });
});

// ---- health ----
async function loadHealth() {
  try {
    const health = await api("/api/health");
    $("#dotSeek").className = "dot " + (health.seekdb?.ok ? "ok" : "bad");
    $("#dotMemory").className = "dot " + (health.powercontext?.ok ? "ok" : "bad");
    $("#dotLlm").className = "dot " + (health.llm ? "ok" : "bad");
    $("#dotSeek").title = health.seekdb?.ok
      ? `${health.seekdb.collection} · ${health.seekdb.embedding} · ${health.seekdb.chunks} 片段`
      : health.seekdb?.error || "不可用";
    $("#dotMemory").title = health.powercontext?.ok
      ? `PowerContext ${health.powercontext.version} · Scope 可用`
      : health.powercontext?.error || "不可用";
    $("#dotLlm").title = health.llm ? `模型：${health.llm_model}` : "未配置模型，将使用模板降级";
  } catch (err) {
    $("#dotSeek").className = "dot bad";
    $("#dotLlm").className = "dot bad";
  }
}

// ---- ask ----
function renderAnswer(payload) {
  const wrap = el("div", "stack");
  const answer = el("div", "answer");
  const meta = el("div", "meta");
  meta.append(el("span", "pill", payload.llm_used ? `模型：${payload.llm_model}` : "模板降级"));
  meta.append(el("span", "pill", `检索 ${payload.retrieval.latency_ms} ms`));
  meta.append(el("span", "pill", `总耗时 ${payload.latency_ms} ms`));
  if (payload.memories?.length) meta.append(el("span", "pill mem", `召回记忆 ${payload.memories.length} 条`));
  if (payload.retrieval?.keywords?.length)
    meta.append(el("span", "pill", `关键词：${payload.retrieval.keywords.join(" / ")}`));
  answer.append(meta, el("div", "", payload.answer));

  const cites = el("div", "");
  payload.citations.forEach((c) => {
    const row = el("div", "cite");
    row.append(el("div", "idx", `[${c.index}]`));
    const body = el("div", "body");
    const where = [c.title, c.section, c.page ? `第 ${c.page} 页` : ""].filter(Boolean).join(" · ");
    body.append(el("div", "where", where || c.doc_id));
    body.append(el("div", "snippet", c.snippet + "…"));
    body.append(el("div", "snippet", `片段 ${c.chunk_id} · RRF 得分 ${c.score}`));
    const flag = el("button", "", "标记没懂");
    flag.addEventListener("click", () => flagConcept(c, flag));
    body.append(flag);
    row.append(body);
    cites.append(row);
  });
  if (payload.citations?.length) {
    const box = el("div", "answer");
    box.append(el("div", "where", "资料来源"), cites);
    wrap.append(answer, box);
  } else {
    wrap.append(answer);
  }

  if (payload.memories?.length) {
    const mem = el("div", "answer");
    mem.append(el("div", "where", "本次召回的 PowerContext 记忆"));
    payload.memories.forEach((m) => {
      mem.append(el("div", "snippet", `${m.artifact_id}@r${m.revision} · ${m.text}`));
    });
    wrap.append(mem);
  }
  if (payload.memory_error) {
    const warn = el("div", "answer");
    warn.append(el("div", "snippet", `记忆服务不可用：${payload.memory_error}`));
    wrap.append(warn);
  }

  const evidence = el("details", "answer");
  evidence.append(el("summary", "", "查看检索证据"));
  const pre = el("pre", "output", JSON.stringify(payload.retrieval, null, 2));
  evidence.append(pre);
  wrap.append(evidence);
  return wrap;
}

async function ask() {
  const question = $("#question").value.trim();
  if (!question) return;
  const btn = $("#askBtn");
  btn.disabled = true;
  btn.textContent = "检索中…";
  const area = $("#answerArea");
  area.replaceChildren();
  try {
    const payload = await api("/api/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ user_key: userKey(), question }),
    });
    state.lastAnswer = payload;
    area.append(renderAnswer(payload));
  } catch (err) {
    area.append(el("div", "answer", `出错了：${err.message}`));
  } finally {
    btn.disabled = false;
    btn.textContent = "提问";
  }
}

function flagConcept(citation, button) {
  if (button.dataset.open) return;
  button.dataset.open = "1";

  const box = el("div", "flagbox");
  const input = el("input");
  input.placeholder = "哪个概念没懂？会写进你的记忆档案";
  input.value = citation.section || citation.title;
  const save = el("button", "primary", "保存到记忆");
  const cancel = el("button", "", "取消");

  cancel.addEventListener("click", () => {
    box.remove();
    delete button.dataset.open;
  });

  save.addEventListener("click", async () => {
    const concept = input.value.trim();
    if (!concept) {
      input.focus();
      return;
    }
    save.disabled = true;
    save.textContent = "写入中…";
    try {
      await api("/api/flag", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          user_key: userKey(),
          concept,
          detail: `来自《${citation.title}》的片段，需要复习。`,
          citation: [citation.title, citation.section, citation.page ? `第 ${citation.page} 页` : ""]
            .filter(Boolean)
            .join(" · "),
        }),
      });
      box.replaceWith(el("div", "snippet", `已记下「${concept}」，下次会话会被召回。`));
      button.textContent = "已记下";
      button.disabled = true;
    } catch (err) {
      save.disabled = false;
      save.textContent = "保存到记忆";
      alert(`写入失败：${err.message}`);
    }
  });

  box.append(input, save, cancel);
  button.after(box);
  input.focus();
}

$("#askBtn").addEventListener("click", ask);
$("#question").addEventListener("keydown", (e) => {
  if ((e.metaKey || e.ctrlKey) && e.key === "Enter") ask();
});

// ---- docs ----
async function loadDocs() {
  try {
    const data = await api("/api/documents");
    const list = $("#docList");
    list.replaceChildren();
    if (!data.documents.length) {
      list.append(el("div", "empty", "还没有资料，点上面的「灌入样例资料」先试试。"));
      return;
    }
    data.documents.forEach((doc) => {
      const item = el("div", "item");
      const head = el("div", "head");
      head.append(el("div", "where", doc.title), el("div", "sub", `${doc.chunk_count} 片段`));
      item.append(head, el("div", "sub", `${doc.source} · ${doc.created_at}`));
      list.append(item);
    });
  } catch (err) {
    $("#docList").replaceChildren(el("div", "empty", err.message));
  }
}

async function upload() {
  const file = $("#fileInput").files[0];
  if (!file) {
    alert("先选一个文件");
    return;
  }
  const form = new FormData();
  form.append("file", file);
  form.append("user_key", userKey());
  $("#uploadBtn").disabled = true;
  try {
    const result = await api("/api/ingest", { method: "POST", body: form });
    const out = $("#ingestResult");
    out.textContent = `已入库：${result.title}，${result.chunks} 个片段（doc_id=${result.doc_id}）`;
    show(out);
    loadDocs();
  } catch (err) {
    alert(`入库失败：${err.message}`);
  } finally {
    $("#uploadBtn").disabled = false;
  }
}

async function seed() {
  const form = new FormData();
  form.append("user_key", userKey());
  $("#seedBtn").disabled = true;
  try {
    const result = await api("/api/seed", { method: "POST", body: form });
    const out = $("#ingestResult");
    out.textContent = result.ingested.map((r) => `${r.title}: ${r.chunks} 片段`).join("\n");
    show(out);
    loadDocs();
  } catch (err) {
    alert(`入库失败：${err.message}`);
  } finally {
    $("#seedBtn").disabled = false;
  }
}

$("#uploadBtn").addEventListener("click", upload);
$("#seedBtn").addEventListener("click", seed);

// ---- plan ----
async function loadPlan() {
  const out = $("#planOut");
  out.replaceChildren(el("div", "empty", "生成中…"));
  try {
    const data = await api(`/api/plan?user_key=${encodeURIComponent(userKey())}`);
    out.replaceChildren();
    const card = el("div", "answer");
    const meta = el("div", "meta");
    meta.append(el("span", "pill", data.llm_used ? "模型生成" : "模板降级"));
    meta.append(el("span", "pill", `记忆 ${data.memory_total} 条`));
    meta.append(el("span", "pill", `耗时 ${data.latency_ms} ms`));
    card.append(meta, el("div", "", data.plan));
    out.append(card);
    if (data.weak_points.length) {
      const list = el("div", "answer");
      list.append(el("div", "where", "计划引用的记忆"));
      data.weak_points.forEach((w) => list.append(el("div", "snippet", `${w.artifact_id}@r${w.revision} · ${w.text}`)));
      out.append(list);
    }
    loadMemories();
  } catch (err) {
    out.replaceChildren(el("div", "empty", err.message));
  }
}

async function loadMemories() {
  const list = $("#memoryList");
  try {
    const data = await api(`/api/memories?user_key=${encodeURIComponent(userKey())}`);
    list.replaceChildren();
    if (!data.entries.length) {
      list.append(el("div", "empty", "这个使用者还没有记忆。去问答页点「标记没懂」。"));
      return;
    }
    data.entries.forEach((entry) => {
      const item = el("div", "item");
      const head = el("div", "head");
      head.append(
        el("div", "where", entry.kind),
        el("div", "sub", `${entry.artifact.artifact_id}@r${entry.artifact.revision}`)
      );
      item.append(head, el("div", "", entry.text));
      list.append(item);
    });
  } catch (err) {
    list.replaceChildren(el("div", "empty", err.message));
  }
}

$("#planBtn").addEventListener("click", loadPlan);

// ---- evidence ----
async function loadEvidence() {
  try {
    const data = await api("/api/evidence?limit=12");
    const stats = $("#statCards");
    stats.replaceChildren();
    const entries = [
      ["累计提问", data.stats.questions],
      ["检索次数", data.stats.retrievals],
      ["写入记忆", data.stats.memories_written],
      ["使用者", data.stats.active_users],
      ["平均检索 ms", data.stats.avg_retrieval_ms],
      ["近 7 天提问", data.stats.week_questions],
    ];
    entries.forEach(([label, value]) => {
      const card = el("div", "stat");
      card.append(el("b", "", String(value ?? 0)), el("span", "", label));
      stats.append(card);
    });

    const list = $("#retrievalList");
    list.replaceChildren();
    if (!data.retrievals.length) {
      list.append(el("div", "empty", "还没有检索记录。"));
      return;
    }
    data.retrievals.forEach((row) => {
      const item = el("div", "item");
      const head = el("div", "head");
      head.append(el("div", "where", row.question), el("div", "sub", `${row.latency_ms} ms`));
      item.append(
        head,
        el("div", "sub", `${row.user_key} · 关键词 ${row.keywords.join(" / ") || "无"} · 命中 ${row.hit_ids.length} 段`),
        el("div", "sub", `top score ${row.top_score} · ${row.created_at}`)
      );
      list.append(item);
    });
  } catch (err) {
    $("#retrievalList").replaceChildren(el("div", "empty", err.message));
  }
}

$("#prepareBtn").addEventListener("click", async () => {
  const out = $("#prepareOut");
  try {
    const data = await api(`/api/powercontext/prepare?user_key=${encodeURIComponent(userKey())}`);
    out.textContent = JSON.stringify(data, null, 2);
    show(out);
  } catch (err) {
    out.textContent = err.message;
    show(out);
  }
});

// ---- user ----
const userInput = $("#userKey");
userInput.value = state.user;
userInput.addEventListener("change", () => {
  state.user = userInput.value.trim() || "demo";
  localStorage.setItem("study_user", state.user);
  loadHealth();
  loadMemories();
});

loadHealth();
loadDocs();
