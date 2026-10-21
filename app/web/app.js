"use strict";
const $ = (s) => document.querySelector(s),
  token = $("meta[name=local-token]").content;
const pages = {
  calendar: "待办日历",
  steps: "待办步骤",
  emails: "邮件明细",
  filtered: "已过滤邮件",
  audits: "判断审计",
  settings: "设置",
};
const colors = {
  测评: ["#9460b0", "#f4edf9"],
  笔试: ["#d18b32", "#fff4e5"],
  面试: ["#3b7bc3", "#eaf3fc"],
  AI面试: ["#398e85", "#e9f6f3"],
  补充材料: ["#a96c64", "#fbefec"],
  录用: ["#628849", "#f0f6e9"],
};
let data = null,
  page = "calendar",
  month = new Date()
    .toLocaleDateString("sv-SE", { timeZone: "Asia/Shanghai" })
    .slice(0, 7),
  showDone = false,
  query = "",
  selected = null,
  dirtyNotes = false,
  stream = null,
  loading = false,
  filterOffset = 0;
function el(tag, text, cls) {
  const e = document.createElement(tag);
  if (text !== undefined && text !== null) e.textContent = text;
  if (cls) e.className = cls;
  return e;
}
function button(text, fn, cls) {
  const b = el("button", text, cls);
  b.type = "button";
  b.onclick = fn;
  return b;
}
function fmt(value) {
  if (!value) return "—";
  const d = new Date(value);
  return isNaN(d)
    ? String(value)
    : d.toLocaleString("sv-SE", { timeZone: "Asia/Shanghai" }).slice(0, 16);
}
function dayKey(value) {
  return fmt(value).slice(0, 10);
}
function color(node, type) {
  const [a, b] = colors[type] || ["#71829a", "#eef1f6"];
  node.style.setProperty("--accent", a);
  node.style.setProperty("--tint", b);
  return node;
}
function tag(type) {
  return color(el("span", type, "tag"), type);
}
function tell(text, error = false) {
  $("#message").textContent = text;
  $("#message").className = error ? "error" : "";
}
async function api(path, value) {
  const response = await fetch(
    path,
    value === undefined
      ? { cache: "no-store" }
      : {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-Local-Token": token,
          },
          body: JSON.stringify(value),
        },
  );
  const result = await response.json();
  if (!response.ok) throw Error(result.error || "请求失败");
  return result;
}
async function refresh(feedback = false) {
  if (loading) return;
  loading = true;
  $("#refresh").disabled = true;
  try {
    data = await api("/api/state");
    render();
    if (selected && !dirtyNotes) renderDetail();
    $("#connection").textContent = "本机已连接";
    if (feedback)
      tell(
        "状态已刷新 · " +
          new Date().toLocaleTimeString("zh-CN", { hour12: false }),
      );
  } catch (e) {
    tell(e.message, true);
  } finally {
    loading = false;
    $("#refresh").disabled = false;
  }
}
function checkbox(done, fn, label) {
  const c = el("input");
  c.type = "checkbox";
  c.checked = done;
  c.setAttribute("aria-label", label);
  c.onchange = () => fn(c.checked, c);
  return c;
}
async function change(kind, item, done, input) {
  input.disabled = true;
  try {
    data = await api("/api/" + kind, {
      id: item.id,
      version: item.version,
      done,
    });
    render();
    if (selected && !dirtyNotes) renderDetail();
    tell(done ? "已完成" : "已恢复待办");
  } catch (e) {
    input.checked = !done;
    input.disabled = false;
    tell(e.message, true);
    await refresh();
  }
}
function navigate(name) {
  page = name;
  query = "";
  filterOffset = 0;
  render();
}
function controls(count, search = true) {
  const bar = el("div", null, "toolbar"),
    left = el("div", null, "toolbar-group");
  left.append(el("span", count, "count"));
  bar.append(left);
  const right = el("div", null, "toolbar-group");
  if (search) {
    const input = el("input", null, "search");
    input.placeholder = "搜索公司、岗位、待办";
    input.value = query;
    input.oninput = () => {
      const caret = input.selectionStart;
      query = input.value;
      renderContent();
      const replacement = $(".search");
      replacement?.focus();
      replacement?.setSelectionRange(caret, caret);
    };
    right.append(input);
  }
  if (["calendar", "steps", "emails"].includes(page)) {
    const label = el("label");
    label.append(
      checkbox(
        showDone,
        (v) => {
          showDone = v;
          renderContent();
        },
        "显示已完成",
      ),
      el("span", "显示已完成"),
    );
    right.append(label);
  }
  bar.append(right);
  return bar;
}
function matches(item) {
  return (
    !query ||
    [item.company, item.role, item.action, item.label].some((v) =>
      String(v || "")
        .toLowerCase()
        .includes(query.toLowerCase()),
    )
  );
}
function visibleSteps() {
  return data.steps.filter(
    (s) => s.active && (showDone || !s.done) && matches(s),
  );
}
function render() {
  if (!data) return;
  $("#title").textContent = pages[page];
  $("#nav").replaceChildren(
    ...Object.entries(pages).map(([key, title]) =>
      button(title, () => navigate(key), key === page ? "selected" : ""),
    ),
  );
  $("#scan").disabled = data.job.running;
  $("#scan").textContent = data.job.running ? "扫描中…" : "扫描邮件";
  $("#footer").textContent =
    "北京时间 · " +
    (data.last_scan
      ? "最近扫描 " + fmt(data.last_scan)
      : "导入本机快照 " + fmt(data.imported_at)) +
    " · 独立版未启用定时检查";
  renderContent();
  $("#main").setAttribute("aria-busy", "false");
}
function renderContent() {
  const main = $("#main");
  main.replaceChildren();
  if (page === "calendar") renderCalendar(main);
  else if (page === "steps") renderSteps(main);
  else if (page === "emails") renderEmails(main);
  else if (page === "filtered") renderFiltered(main);
  else if (page === "audits") renderAudits(main);
  else renderSettings(main);
}
function renderCalendar(main) {
  const steps = visibleSteps(),
    bar = controls(steps.filter((s) => s.at).length + " 个时间节点", false),
    left = bar.firstChild;
  left.replaceChildren(
    button("‹", () => moveMonth(-1)),
    el("strong", month.replace("-", " 年 ") + " 月"),
    button("›", () => moveMonth(1)),
    button("今天", () => {
      month = new Date()
        .toLocaleDateString("sv-SE", { timeZone: "Asia/Shanghai" })
        .slice(0, 7);
      renderContent();
    }),
  );
  main.append(bar);
  const calendar = el("div", null, "calendar"),
    week = el("div", null, "week");
  for (const name of ["一", "二", "三", "四", "五", "六", "日"])
    week.append(el("div", name));
  calendar.append(week);
  const days = el("div", null, "days"),
    first = new Date(month + "-01T00:00:00Z"),
    start = new Date(first);
  start.setUTCDate(1 - ((first.getUTCDay() + 6) % 7));
  const today = new Date().toLocaleDateString("sv-SE", {
    timeZone: "Asia/Shanghai",
  });
  for (let i = 0; i < 42; i++) {
    const date = new Date(start);
    date.setUTCDate(start.getUTCDate() + i);
    const key = date.toISOString().slice(0, 10),
      cell = el(
        "div",
        null,
        "day" +
          (key.startsWith(month) ? "" : " outside") +
          (key === today ? " today" : ""),
      ),
      dateLabel = el("div", null, "date");
    dateLabel.append(el("span", date.getUTCDate()));
    cell.append(dateLabel);
    for (const step of steps
      .filter((s) => s.at && dayKey(s.at) === key)
      .sort((a, b) => a.at.localeCompare(b.at))) {
      const card = color(
          el("div", null, "event" + (step.done ? " done" : "")),
          step.category,
        ),
        title = button("", () => openMail(step.mail_id));
      title.append(
        el(
          "span",
          step.company + "｜" + step.label + (step.estimated ? "（估算）" : ""),
          "event-title",
        ),
        el("span", fmt(step.at).slice(11), "time"),
      );
      title.title = step.role + " · " + step.action;
      card.append(
        checkbox(
          step.done,
          (v, c) => change("step", step, v, c),
          "完成 " + step.company + " " + step.label,
        ),
        title,
      );
      cell.append(card);
    }
    days.append(cell);
  }
  calendar.append(days);
  main.append(calendar);
  const legend = el("div", null, "legend");
  for (const type of Object.keys(colors)) {
    const item = color(el("span"), type);
    item.append(el("i", null, "dot"), el("span", type));
    legend.append(item);
  }
  main.append(legend);
  const undated = steps.filter((s) => !s.at).length;
  if (undated)
    main.append(
      button(
        undated + " 项时间待核实 → 查看待办步骤",
        () => navigate("steps"),
        "link",
      ),
    );
}
function moveMonth(delta) {
  const d = new Date(month + "-01T00:00:00Z");
  d.setUTCMonth(d.getUTCMonth() + delta);
  month = d.toISOString().slice(0, 7);
  renderContent();
}
function table(headers) {
  const panel = el("div", null, "panel"),
    t = el("table", null, "table"),
    thead = el("thead"),
    tr = el("tr"),
    tbody = el("tbody");
  headers.forEach((h) => tr.append(el("th", h)));
  thead.append(tr);
  t.append(thead, tbody);
  panel.append(t);
  return { panel, tbody };
}
function td(node, cls) {
  const cell = el("td", null, cls);
  cell.append(typeof node === "string" ? el("span", node) : node);
  return cell;
}
function renderSteps(main) {
  const steps = visibleSteps();
  main.append(controls(steps.length + " 个步骤"));
  const { panel, tbody } = table([
    "已完成",
    "公司 / 岗位",
    "通知类型",
    "待办步骤",
    "时间",
    "接收时间",
  ]);
  for (const s of steps) {
    const row = el("tr"),
      company = el("div");
    company.append(
      button(s.company, () => openMail(s.mail_id), "link"),
      el("div", s.role, "small"),
    );
    const action = el("div");
    action.append(el("div", s.label), el("div", s.action, "small"));
    row.append(
      td(
        checkbox(s.done, (v, c) => change("step", s, v, c), "完成 " + s.label),
      ),
      td(company),
      td(tag(s.category)),
      td(action, "wrap"),
      td(fmt(s.at) + (s.estimated ? "（估算）" : "")),
      td(fmt(s.received)),
    );
    tbody.append(row);
  }
  main.append(panel);
  if (!steps.length) main.append(el("div", "没有符合条件的待办", "empty"));
}
function renderEmails(main) {
  const emails = data.emails.filter(
    (m) => m.active && (showDone || !m.done) && matches(m),
  );
  main.append(controls(emails.length + " 封邮件 · 按接收时间倒序"));
  const { panel, tbody } = table([
    "公司",
    "岗位",
    "通知类型",
    "待办事项",
    "开始时间",
    "截止时间",
    "已完成",
    "备注",
    "接收时间",
  ]);
  for (const m of emails) {
    const row = el("tr");
    row.append(
      td(button(m.company, () => openMail(m.id), "link")),
      td(m.role),
      td(tag(m.category)),
      td(m.action, "wrap"),
      td(fmt(m.start)),
      td(fmt(m.deadline)),
      td(
        checkbox(
          m.done,
          (v, c) => change("email", m, v, c),
          "完成该邮件全部步骤",
        ),
      ),
      td(
        button(m.notes || "添加备注", () => openMail(m.id), "link"),
        "wrap",
      ),
      td(fmt(m.received)),
    );
    tbody.append(row);
  }
  main.append(panel);
  if (!emails.length) main.append(el("div", "没有符合条件的邮件", "empty"));
}
function openMail(id) {
  selected = id;
  dirtyNotes = false;
  renderDetail();
  $("#detail").showModal();
  document.body.classList.add("modal-open");
}
function renderDetail() {
  const m = data.emails.find((x) => x.id === selected);
  if (!m) return;
  $("#detail-title").textContent = m.company;
  const body = $("#detail-body");
  body.replaceChildren();
  const meta = el("div", null, "detail-meta");
  meta.append(
    tag(m.category),
    el("div", m.role),
    el("div", m.subject, "small"),
    el("div", "接收于 " + fmt(m.received), "small"),
  );
  body.append(meta, el("div", m.action, "detail-action"));
  const all = el("label", null, "toolbar-group");
  all.append(
    checkbox(m.done, (v, c) => change("email", m, v, c), "完成全部步骤"),
    el("span", "完成全部步骤"),
  );
  body.append(all);
  for (const s of data.steps.filter((x) => x.mail_id === m.id && x.active)) {
    const row = el("label", null, "step-row"),
      content = el("div");
    content.append(
      el("strong", s.label),
      el(
        "div",
        fmt(s.at) + (s.estimated ? " · 估算，以系统为准" : ""),
        "small",
      ),
      el("div", s.action, "small"),
    );
    row.append(
      checkbox(s.done, (v, c) => change("step", s, v, c), "完成 " + s.label),
      content,
    );
    body.append(row);
  }
  const label = el("label", "备注", "notes-label"),
    notes = el("textarea");
  notes.value = m.notes;
  notes.setAttribute("aria-label", "备注");
  notes.oninput = () => {
    dirtyNotes = true;
  };
  body.append(label, notes);
  const buttons = el("div", null, "detail-buttons");
  buttons.append(
    button("保存备注", async () => {
      try {
        data = await api("/api/notes", {
          id: m.id,
          version: m.version,
          notes: notes.value,
        });
        dirtyNotes = false;
        render();
        renderDetail();
        tell("备注已保存");
      } catch (e) {
        tell(e.message + "；输入内容仍保留", true);
      }
    }),
    button("查看原邮件", async () => {
      try {
        const value = await api("/api/mail/" + m.id),
          pre = el("pre", value.body);
        pre.setAttribute("aria-label", "原邮件正文");
        body.querySelector("pre")?.remove();
        body.append(pre);
      } catch (e) {
        tell(e.message, true);
      }
    }),
  );
  body.append(buttons);
}
async function renderFiltered(main) {
  main.append(el("div", "正在读取…", "empty"));
  try {
    const result = await api("/api/filtered?offset=" + filterOffset);
    if (page !== "filtered") return;
    main.replaceChildren(controls(result.total + " 封本机过滤记录", false));
    const { panel, tbody } = table(["主题", "过滤原因", "接收时间"]);
    for (const item of result.items) {
      const row = el("tr");
      row.append(
        td(
          button(
            item.subject,
            async () => {
              try {
                const value = await api("/api/filtered/" + item.id);
                showText(
                  item.subject,
                  value.reason +
                    "\n" +
                    fmt(value.received) +
                    "\n\n" +
                    value.body,
                );
              } catch (e) {
                tell(e.message, true);
              }
            },
            "link",
          ),
          "wrap",
        ),
        td(item.reason),
        td(fmt(item.received)),
      );
      tbody.append(row);
    }
    main.append(panel);
    const nav = el("div", null, "toolbar-group");
    if (filterOffset)
      nav.append(
        button("上一页", () => {
          filterOffset -= 50;
          renderContent();
        }),
      );
    if (result.has_more)
      nav.append(
        button("下一页", () => {
          filterOffset += 50;
          renderContent();
        }),
      );
    main.append(nav);
  } catch (e) {
    tell(e.message, true);
  }
}
function showText(title, text) {
  selected = null;
  dirtyNotes = false;
  $("#detail-title").textContent = title;
  $("#detail-body").replaceChildren(el("pre", text));
  if (!$("#detail").open) $("#detail").showModal();
  document.body.classList.add("modal-open");
}
async function renderAudits(main) {
  main.append(el("div", "正在读取…", "empty"));
  try {
    const items = await api("/api/audits");
    if (page !== "audits") return;
    main.replaceChildren(
      controls(items.length + " 条模型判断 · 包含导入的历史记录", false),
    );
    const { panel, tbody } = table([
      "时间",
      "实际模型",
      "判断",
      "持久化结果",
      "详情",
    ]);
    for (const a of items) {
      const row = el("tr");
      row.append(
        td(fmt(a.started_at)),
        td(a.actual_model || "未记录"),
        td(a.model_status || "—"),
        td(a.sync_status || "—"),
        td(
          button(
            "查看输入 / 输出",
            async () => {
              try {
                const result = await api("/api/audit/" + a.audit_id);
                selected = null;
                $("#detail-title").textContent = "判断审计";
                $("#detail-body").replaceChildren(
                  ...Object.entries(result)
                    .filter(([, value]) => value)
                    .map(([name, value]) => {
                      const box = el("details");
                      box.append(el("summary", name), el("pre", value));
                      return box;
                    }),
                );
                $("#detail").showModal();
              } catch (e) {
                tell(e.message, true);
              }
            },
            "link",
          ),
        ),
      );
      tbody.append(row);
    }
    main.append(panel);
  } catch (e) {
    tell(e.message, true);
  }
}
async function renderSettings(main) {
  try {
    const config = await api("/api/settings");
    if (page !== "settings") return;
    const form = el("form", null, "panel settings"),
      fields = {};
    for (const [key, title] of [
      ["model", "首选模型"],
      ["fallback", "后备模型"],
      ["effort", "思考强度"],
    ]) {
      const label = el("label", title),
        input = el(key === "effort" ? "select" : "input");
      if (key === "effort")
        for (const [value, text] of [
          ["low", "轻度"],
          ["medium", "中等"],
          ["high", "高"],
        ]) {
          const option = el("option", text);
          option.value = value;
          input.append(option);
        }
      input.value = config[key];
      fields[key] = input;
      label.append(input);
      form.append(label);
    }
    const save = el("button", "保存设置", "primary");
    save.type = "submit";
    form.append(
      save,
      el(
        "p",
        "CodeBuddy CN CLI · 邮箱凭证" +
          (config.credentials_saved ? "已保存在本机。" : "未配置。"),
      ),
      el(
        "p",
        "独立版未启用自动检查，原项目的定时任务未改动。关闭全部页面后空闲 2 分钟自动退出。",
      ),
    );
    form.onsubmit = async (e) => {
      e.preventDefault();
      try {
        await api(
          "/api/settings",
          Object.fromEntries(
            Object.entries(fields).map(([k, v]) => [k, v.value]),
          ),
        );
        tell("设置已保存");
      } catch (e) {
        tell(e.message, true);
      }
    };
    main.append(form);
  } catch (e) {
    tell(e.message, true);
  }
}
$("#refresh").onclick = () => refresh(true);
$("#scan").onclick = async () => {
  try {
    const result = await api("/api/scan", {});
    tell(result.started ? "已开始扫描" : "正在扫描，请稍候");
    await refresh();
  } catch (e) {
    tell(e.message, true);
  }
};
$("#close-detail").onclick = () => $("#detail").close();
$("#detail").addEventListener("close", () => {
  selected = null;
  dirtyNotes = false;
  document.body.classList.remove("modal-open");
});
function connect() {
  if (stream) return;
  stream = new EventSource("/api/session?token=" + encodeURIComponent(token));
  stream.onmessage = (e) => {
    if (data && e.data !== data.version) {
      const wasRunning = data.job.running;
      refresh().then(() => {
        if (wasRunning && data && !data.job.running)
          tell(data.job.message, data.job.message !== "扫描完成");
      });
    }
  };
  stream.onerror = () => {
    $("#connection").textContent = "连接中断，正在重连";
  };
}
window.addEventListener("pagehide", () => {
  stream?.close();
  stream = null;
});
window.addEventListener("pageshow", connect);
refresh();
connect();
