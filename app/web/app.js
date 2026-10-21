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
  手动日程: ["#677590", "#eef1f8"],
};
const viewPreferenceKey = "autumn-local-calendar-view";
function savedCalendarView() {
  try {
    return localStorage.getItem(viewPreferenceKey) === "list"
      ? "list"
      : "month";
  } catch {
    return "month";
  }
}
let calendarView = savedCalendarView();
let calendarAnchor = null,
  calendarJump = null;
const emailPageSizes = [10, 20, 50, 100];
let emailPageSize = 20;
try {
  const saved = Number(localStorage.getItem("autumn-local-email-page-size"));
  if (emailPageSizes.includes(saved)) emailPageSize = saved;
} catch {}
let emailPage = 1,
  agendaAnchor = null,
  agendaJump = false;
let contentRevision = 0, settingsRequest = null, settingsCache = null;
function loadSettings() {
  if (!settingsRequest) {
    settingsRequest = api("/api/settings")
      .then((config) => (settingsCache = config))
      .finally(() => { settingsRequest = null; });
  }
  return settingsRequest;
}
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
  if (value !== undefined && ["/api/step", "/api/email", "/api/notes", "/api/manual", "/api/mailbox"].includes(path)) {
    value = { ...value, account: data?.account };
  }
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
    const previousAccount = data?.account;
    data = await api("/api/state");
    if (previousAccount && previousAccount !== data.account) {
      settingsCache = null;
      selected = null;
      dirtyNotes = false;
      $("#detail").close();
      query = "";
      emailPage = 1;
      $("#main").querySelectorAll(".settings").forEach((form) => { form.dataset.dirty = "false"; });
    }
    if (feedback) settingsCache = null;
    render();
    if (selected && !dirtyNotes) renderDetail();
    $("#connection").textContent = "本机已连接";
    if (feedback)
      tell(
        "状态已刷新 · " +
          new Date().toLocaleTimeString("zh-CN", { hour12: false }),
      );
  } catch (e) {
    $("#connection").textContent = data ? "刷新失败，可重试" : "读取失败，请重试";
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
    data = await api(
      item.source === "manual" ? "/api/manual" : "/api/" + kind,
      {
        op: "complete",
        id: item.manual_id || item.id,
        version: item.version,
        done,
      },
    );
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
  emailPage = 1;
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
      emailPage = 1;
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
          emailPage = 1;
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
  const manual = (data.manual_events || []).flatMap((event) =>
    ["start", "deadline"]
      .filter((key) => event[key])
      .map((key) => ({
        id: "manual:" + event.id + ":" + key,
        manual_id: event.id,
        source: "manual",
        version: event.version,
        company: event.title,
        role: "手动日程",
        category: "手动日程",
        label: key === "start" ? "开始" : "截止",
        action: event.notes || event.title,
        at: event[key],
        active: true,
        done: event.done,
        received: event.created_at,
      })),
  );
  return [...data.steps, ...manual]
    .sort((a, b) => b.received.localeCompare(a.received))
    .filter((s) => s.active && (showDone || !s.done) && matches(s));
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
    " · " +
    scheduleSummary(data.schedule);
  renderContent();
  $("#main").setAttribute("aria-busy", "false");
}
function scheduleSummary(schedule) {
  if (!schedule || schedule.error)
    return schedule?.error || "定时详情将在页面服务重启后显示";
  if (!schedule.enabled) return "自动检查未启用";
  return (
    "每 " +
    schedule.interval_hours +
    " 小时检查 · 下次 " +
    fmt(schedule.next_run)
  );
}
function renderContent() {
  const main = $("#main");
  if (page === "settings" && main.querySelector('.settings[data-dirty="true"]'))
    return;
  captureCalendarPosition();
  captureAgendaPosition();
  const revision = ++contentRevision;
  main.replaceChildren();
  main.classList.toggle("settings-page", page === "settings");
  if (page === "calendar") renderCalendar(main);
  else if (page === "steps") renderSteps(main);
  else if (page === "emails") renderEmails(main);
  else if (page === "filtered") renderFiltered(main);
  else if (page === "audits") renderAudits(main);
  else renderSettings(main, revision);
}
function renderCalendar(main) {
  const steps = visibleSteps(),
    bar = controls(steps.filter((s) => s.at).length + " 个时间节点", false),
    left = bar.firstChild;
  left.replaceChildren(
    el(
      "strong",
      calendarView === "month"
        ? month.replace("-", " 年 ") + " 月"
        : "全部日程",
    ),
    button("今天", () => {
      if (calendarView === "list") {
        agendaJump = true;
        renderContent();
        return;
      }
      month = new Date()
        .toLocaleDateString("sv-SE", { timeZone: "Asia/Shanghai" })
        .slice(0, 7);
      calendarJump = new Date().toLocaleDateString("sv-SE", {
        timeZone: "Asia/Shanghai",
      });
      renderContent();
    }),
  );
  const switcher = el("div", null, "view-switch");
  switcher.setAttribute("role", "group");
  switcher.setAttribute("aria-label", "日历展示方式");
  for (const [value, label] of [
    ["month", "月历"],
    ["list", "列表"],
  ]) {
    const option = button(label, () => {
      calendarView = value;
      try {
        localStorage.setItem(viewPreferenceKey, value);
      } catch {
        tell("已切换；浏览器禁止保存偏好，刷新后可能恢复月历", true);
      }
      renderContent();
    });
    option.setAttribute("aria-pressed", String(calendarView === value));
    switcher.append(option);
  }
  bar.lastChild.prepend(switcher);
  bar.lastChild.prepend(button("新建日程", () => openManual(), "primary"));
  main.append(bar);
  const eventsByDay = new Map();
  for (const step of steps) {
    if (!step.at) continue;
    const date = dayKey(step.at);
    if (!eventsByDay.has(date)) eventsByDay.set(date, []);
    eventsByDay.get(date).push(step);
  }
  const calendar = el("div", null, "calendar view-" + calendarView),
    week = el("div", null, "week");
  for (const name of ["一", "二", "三", "四", "五", "六", "日"])
    week.append(el("div", name));
  calendar.append(week);
  if (calendarView === "month") {
    buildScrollingCalendar(
      calendar,
      week,
      eventsByDay,
      left.querySelector("strong"),
    );
  } else {
    buildAgenda(calendar, eventsByDay);
  }
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
function calendarDay(date, eventsByDay, continuous) {
  const today = new Date().toLocaleDateString("sv-SE", {
    timeZone: "Asia/Shanghai",
  });
  const key = date.toISOString().slice(0, 10),
    cell = el(
      "div",
      null,
      "day" +
        (!continuous && !key.startsWith(month) ? " outside" : "") +
        (key === today ? " today" : ""),
    ),
    dateLabel = el("div", null, "date");
  if (date.getUTCDate() === 1)
    dateLabel.append(el("small", date.getUTCMonth() + 1 + "月"));
  dateLabel.append(el("span", date.getUTCDate()));
  cell.append(dateLabel);
  cell.dataset.date = key;
  for (const step of (eventsByDay.get(key) || []).sort((a, b) =>
    a.at.localeCompare(b.at),
  )) {
    const card = color(
        el("div", null, "event" + (step.done ? " done" : "")),
        step.category,
      ),
      title = button("", () => openStep(step));
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
  return cell;
}
function captureCalendarPosition() {
  const scroller = $(".calendar-scroll");
  if (!scroller || calendarJump) return;
  const edge =
    scroller.getBoundingClientRect().top +
    scroller.querySelector(".week").offsetHeight;
  const row = [...scroller.querySelectorAll(".calendar-week")].find(
    (r) => r.getBoundingClientRect().bottom > edge + 1,
  );
  if (row)
    calendarAnchor = {
      day: row.dataset.day,
      offset: row.getBoundingClientRect().top - edge,
      left: scroller.scrollLeft,
    };
}
function buildScrollingCalendar(calendar, week, eventsByDay, title) {
  const scroller = el("div", null, "calendar-scroll");
  scroller.tabIndex = 0;
  scroller.setAttribute("role", "region");
  scroller.setAttribute("aria-label", "连续月历，上下滚动查看日期");
  const target =
    calendarJump ||
    calendarAnchor?.day ||
    new Date().toLocaleDateString("sv-SE", { timeZone: "Asia/Shanghai" });
  const savedOffset = calendarJump ? 0 : calendarAnchor?.offset || 0;
  calendarJump = null;
  const first = new Date(target + "T00:00:00Z");
  first.setUTCDate(first.getUTCDate() - ((first.getUTCDay() + 6) % 7));
  const rows = el("div", null, "calendar-weeks");
  function rowAt(date) {
    const row = el("div", null, "days calendar-week");
    row.dataset.day = date.toISOString().slice(0, 10);
    for (let d = 0; d < 7; d++) {
      const day = new Date(date);
      day.setUTCDate(day.getUTCDate() + d);
      row.append(calendarDay(day, eventsByDay, true));
    }
    return row;
  }
  for (let w = -8; w < 20; w++) {
    const date = new Date(first);
    date.setUTCDate(date.getUTCDate() + w * 7);
    rows.append(rowAt(date));
  }
  scroller.append(week, rows);
  calendar.append(scroller);
  let frame = null;
  function scrollUpdate() {
    if (!scroller.isConnected) return;
    if (scroller.scrollTop < 250) {
      const oldHeight = rows.offsetHeight,
        oldTop = scroller.scrollTop;
      const firstDate = new Date(rows.firstChild.dataset.day + "T00:00:00Z");
      const fragment = document.createDocumentFragment();
      for (let w = -12; w < 0; w++) {
        const date = new Date(firstDate);
        date.setUTCDate(date.getUTCDate() + w * 7);
        fragment.append(rowAt(date));
      }
      rows.prepend(fragment);
      scroller.scrollTop = oldTop + rows.offsetHeight - oldHeight;
      while (rows.children.length > 64) rows.lastChild.remove();
    } else if (
      scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight <
      350
    ) {
      const lastDate = new Date(rows.lastChild.dataset.day + "T00:00:00Z");
      for (let w = 1; w <= 12; w++) {
        const date = new Date(lastDate);
        date.setUTCDate(date.getUTCDate() + w * 7);
        rows.append(rowAt(date));
      }
      let removed = 0;
      while (rows.children.length > 64) {
        removed += rows.firstChild.offsetHeight;
        rows.firstChild.remove();
      }
      scroller.scrollTop -= removed;
    }
    captureCalendarPosition();
    if (calendarAnchor) {
      const date = new Date(calendarAnchor.day + "T00:00:00Z");
      date.setUTCDate(date.getUTCDate() + 3);
      month = date.toISOString().slice(0, 7);
      title.textContent = month.replace("-", " 年 ") + " 月";
    }
  }
  scroller.onscroll = () => {
    if (frame === null)
      frame = requestAnimationFrame(() => {
        frame = null;
        scrollUpdate();
      });
  };
  requestAnimationFrame(() => {
    if (!scroller.isConnected) return;
    const row = rows.querySelector(
      '[data-day="' + first.toISOString().slice(0, 10) + '"]',
    );
    scroller.scrollTop = row.offsetTop - week.offsetHeight - savedOffset;
    scroller.scrollLeft = calendarAnchor?.left || 0;
    scrollUpdate();
  });
}

function captureAgendaPosition() {
  const scroller = $(".agenda-scroll");
  if (!scroller || agendaJump) return;
  const top = scroller.getBoundingClientRect().top;
  const group = [...scroller.querySelectorAll(".agenda-day")].find(
    (g) => g.getBoundingClientRect().bottom > top + 1,
  );
  if (group)
    agendaAnchor = {
      date: group.dataset.date,
      offset: group.getBoundingClientRect().top - top,
    };
}
function buildAgenda(calendar, eventsByDay) {
  const scroller = el("div", null, "agenda-scroll");
  scroller.tabIndex = 0;
  scroller.setAttribute("role", "region");
  scroller.setAttribute("aria-label", "全部日程列表");
  const dates = [...eventsByDay.keys()].sort();
  for (const date of dates) {
    const group = calendarDay(new Date(date + "T00:00:00Z"), eventsByDay, true);
    group.className = "agenda-day";
    const heading = group.querySelector(".date");
    heading.className = "agenda-date";
    const weekday = new Date(date + "T12:00:00+08:00").toLocaleDateString(
      "zh-CN",
      { weekday: "short", timeZone: "Asia/Shanghai" },
    );
    heading.textContent = date + "  " + weekday;
    scroller.append(group);
  }
  const jump = agendaJump;
  agendaJump = false;
  if (!dates.length) {
    scroller.append(el("div", "没有符合条件的日程", "empty"));
    agendaAnchor = null;
  }
  calendar.append(scroller);
  scroller.onscroll = captureAgendaPosition;
  requestAnimationFrame(() => {
    if (!scroller.isConnected || !dates.length) return;
    const target =
      !jump && agendaAnchor
        ? agendaAnchor.date
        : new Date().toLocaleDateString("sv-SE", { timeZone: "Asia/Shanghai" });
    const date = dates.find((d) => d >= target) || dates[dates.length - 1];
    const group = scroller.querySelector('[data-date="' + date + '"]');
    scroller.scrollTop =
      group.offsetTop -
      (!jump && agendaAnchor && date === agendaAnchor.date
        ? agendaAnchor.offset
        : 0);
    captureAgendaPosition();
  });
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
      button(s.company, () => openStep(s), "link"),
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
function emailPageRows(emails, requestedPage, size) {
  const sorted = [...emails].sort(
    (a, b) =>
      Date.parse(b.received) - Date.parse(a.received) ||
      b.id.localeCompare(a.id),
  );
  const pages = Math.max(1, Math.ceil(sorted.length / size));
  const current = Math.min(pages, Math.max(1, Math.trunc(requestedPage) || 1));
  const start = (current - 1) * size;
  return {
    items: sorted.slice(start, start + size),
    total: sorted.length,
    pages,
    current,
    start: sorted.length ? start + 1 : 0,
    end: Math.min(start + size, sorted.length),
  };
}
function renderEmails(main) {
  const emails = data.emails.filter(
    (m) => m.active && (showDone || !m.done) && matches(m),
  );
  main.append(controls(emails.length + " 封邮件 · 按接收时间倒序"));
  const result = emailPageRows(emails, emailPage, emailPageSize);
  emailPage = result.current;
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
  for (const m of result.items) {
    const row = el("tr");
    row.dataset.mailId = m.id;
    row.dataset.received = m.received;
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
  const pager = el("nav", null, "pagination");
  pager.setAttribute("aria-label", "邮件明细分页");
  const pageSizeLabel = el("label", "每页条数"),
    pageSize = el("select");
  pageSize.setAttribute("aria-label", "每页条数");
  for (const value of emailPageSizes) {
    const option = el("option", value + " 条");
    option.value = value;
    pageSize.append(option);
  }
  pageSize.value = emailPageSize;
  pageSize.onchange = () => {
    emailPageSize = Number(pageSize.value);
    emailPage = 1;
    try {
      localStorage.setItem(
        "autumn-local-email-page-size",
        String(emailPageSize),
      );
    } catch {}
    renderContent();
  };
  pageSizeLabel.append(pageSize);
  const previous = button("上一页", () => {
    emailPage--;
    renderContent();
  });
  previous.disabled = emailPage <= 1;
  const next = button("下一页", () => {
    emailPage++;
    renderContent();
  });
  next.disabled = emailPage >= result.pages;
  pager.append(
    pageSizeLabel,
    el("span", result.start + "–" + result.end + " / " + result.total, "count"),
    previous,
    el("span", emailPage + " / " + result.pages, "page-number"),
    next,
  );
  main.append(pager);
  if (!emails.length) main.append(el("div", "没有符合条件的邮件", "empty"));
}
function openStep(step) {
  if (step.source === "manual")
    openManual(data.manual_events.find((e) => e.id === step.manual_id));
  else openMail(step.mail_id);
}
function openManual(event = null) {
  selected = null;
  dirtyNotes = false;
  const requestId = event?.id || crypto.randomUUID();
  $("#detail-title").textContent = event ? "编辑日程" : "新建日程";
  const body = $("#detail-body");
  body.replaceChildren();
  const form = el("form", null, "manual-form"),
    fields = {};
  for (const [key, label, type] of [
    ["title", "标题", "text"],
    ["start", "开始时间（北京时间）", "datetime-local"],
    ["deadline", "截止时间（北京时间）", "datetime-local"],
    ["notes", "备注", "textarea"],
  ]) {
    const row = el("label", label),
      input = el(type === "textarea" ? "textarea" : "input");
    if (type !== "textarea") input.type = type;
    input.setAttribute("aria-label", label);
    input.value =
      key === "start" || key === "deadline"
        ? event?.[key]
          ? fmt(event[key]).replace(" ", "T")
          : ""
        : event?.[key] || "";
    if (key === "title") {
      input.required = true;
      input.maxLength = 200;
    }
    if (key === "notes") input.maxLength = 10000;
    fields[key] = input;
    row.append(input);
    form.append(row);
  }
  form.append(
    el(
      "div",
      "至少填写一个时间；开始和截止会分别显示，完成状态共用。",
      "small",
    ),
  );
  const error = el("div", null, "form-error");
  error.setAttribute("role", "status");
  form.append(error);
  const actions = el("div", null, "detail-buttons"),
    save = el("button", "保存日程", "primary");
  save.type = "submit";
  actions.append(save);
  if (event) {
    const done = el("label", null, "toolbar-group");
    done.append(
      checkbox(
        event.done,
        async (value, input) => {
          input.disabled = true;
          try {
            data = await api("/api/manual", {
              op: "complete",
              id: event.id,
              version: event.version,
              done: value,
            });
            event = data.manual_events.find((e) => e.id === event.id);
            input.disabled = false;
            render();
            tell(value ? "已完成" : "已恢复待办");
          } catch (e) {
            input.checked = !value;
            input.disabled = false;
            error.textContent = e.message;
          }
        },
        "日程已完成",
      ),
      el("span", "已完成"),
    );
    form.append(done);
    actions.append(
      button(
        "删除日程",
        async () => {
          try {
            data = await api("/api/manual", {
              op: "delete",
              id: event.id,
              version: event.version,
            });
            render();
            $("#detail").close();
            tell("日程已删除 ");
            $("#message").append(
              button(
                "撤销",
                async () => {
                  try {
                    data = await api("/api/manual", {
                      op: "restore",
                      id: event.id,
                      version: event.version + 1,
                    });
                    render();
                    tell("日程已恢复");
                  } catch (e) {
                    tell(e.message, true);
                  }
                },
                "link",
              ),
            );
          } catch (e) {
            error.textContent = e.message;
          }
        },
        "danger",
      ),
    );
  }
  form.append(actions);
  form.onsubmit = async (e) => {
    e.preventDefault();
    save.disabled = true;
    const value = {
      op: event ? "update" : "create",
      id: requestId,
      version: event?.version,
      title: fields.title.value,
      notes: fields.notes.value,
    };
    for (const key of ["start", "deadline"])
      value[key] = fields[key].value ? fields[key].value + ":00+08:00" : null;
    try {
      data = await api("/api/manual", value);
      render();
      $("#detail").close();
      tell("日程已保存");
    } catch (err) {
      error.textContent = err.message;
      save.disabled = false;
    }
  };
  body.append(form);
  if (!$("#detail").open) $("#detail").showModal();
  document.body.classList.add("modal-open");
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
  const revision = contentRevision;
  main.append(el("div", "正在读取…", "empty"));
  try {
    const result = await api("/api/filtered?offset=" + filterOffset);
    if (page !== "filtered" || revision !== contentRevision) return;
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
  const revision = contentRevision;
  main.append(el("div", "正在读取…", "empty"));
  try {
    const items = await api("/api/audits");
    if (page !== "audits" || revision !== contentRevision) return;
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
function renderMailboxSettings(main, mailbox) {
  const panel = el("section", null, "panel mailbox-settings");
  panel.append(el("h2", "邮箱"));
  const current = mailbox.accounts.find((item) => item.id === mailbox.active);
  panel.append(el("p", current?.email || "未配置邮箱"));
  function field(form, title, type = "text") {
    const label = el("label", title), input = el("input");
    input.type = type;
    input.setAttribute("aria-label", title);
    input.autocomplete = type === "password" ? "new-password" : "off";
    label.append(input); form.append(label); return input;
  }
  function wire(form, save, payload, secret) {
    form.oninput = () => { form.dataset.dirty = "true"; };
    const status = el("p", "", "small"); status.setAttribute("role", "status");
    form.append(save, status);
    form.onsubmit = async (event) => {
      event.preventDefault(); save.disabled = true; status.textContent = "正在验证邮箱…";
      try {
        await api("/api/mailbox", payload());
        secret.value = ""; form.dataset.dirty = "false"; settingsCache = null;
        status.textContent = "已保存";
        await refresh(); tell("邮箱设置已保存");
      } catch (error) { status.textContent = error.message; }
      finally { secret.value = ""; save.disabled = false; }
    };
  }
  const update = el("form", null, "settings");
  update.append(el("h3", "更新授权码"));
  const code = field(update, "新授权码", "password"); code.required = true;
  const updateButton = button("更新授权码", null, "primary"); updateButton.type = "submit";
  wire(update, updateButton, () => ({op:"credentials", code:code.value}), code);
  const change = el("form", null, "settings");
  change.append(el("h3", "切换邮箱"));
  const savedLabel = el("label", "已有邮箱"), saved = el("select");
  saved.setAttribute("aria-label", "已有邮箱");
  const fresh = el("option", "输入新邮箱"); fresh.value = ""; saved.append(fresh);
  for (const item of mailbox.accounts) {
    if (item.email === "凭证不可读取") continue;
    const option = el("option", item.email); option.value = item.email; saved.append(option);
  }
  savedLabel.append(saved); change.append(savedLabel);
  const address = field(change, "邮箱地址", "email"); address.required = true;
  saved.onchange = () => { address.value = saved.value; change.dataset.dirty = "true"; };
  const newCode = field(change, "授权码（已有邮箱可留空）", "password");
  const switchButton = button("切换邮箱", null, "primary"); switchButton.type = "submit";
  wire(change, switchButton, () => ({op:"switch", email:address.value, code:newCode.value}), newCode);
  change.append(el("p", "每个邮箱独立保存；切回后恢复原记录。", "small"));
  panel.append(update, change); main.append(panel);
}
async function renderSettings(main, revision) {
  const current = () => page === "settings" && revision === contentRevision;
  main.append(el("div", "加载设置…", "small"));
  try {
    const config = settingsCache || await loadSettings();
    if (!current()) return;
    main.replaceChildren();
    if (config.mailbox) renderMailboxSettings(main, config.mailbox);
    const form = el("form", null, "panel settings"),
      fields = {};
    form.oninput = () => {
      form.dataset.dirty = "true";
    };
    form.append(el("h2", "模型设置"));
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
    form.append(save);
    form.onsubmit = async (e) => {
      e.preventDefault();
      try {
        const actual = await api(
          "/api/settings",
          Object.fromEntries(
            Object.entries(fields).map(([k, v]) => [k, v.value]),
          ),
        );
        settingsCache = actual;
        form.dataset.dirty = "false";
        tell("设置已保存");
      } catch (e) {
        tell(e.message, true);
      }
    };
    main.append(form);
    const schedule = config.schedule;
    if (!schedule?.installed || schedule.error) {
      main.append(el("div", schedule?.error || "尚未配置自动检查", "small"));
      return;
    }
    const timerForm = el("form", null, "panel settings scheduler-settings");
    timerForm.oninput = () => {
      timerForm.dataset.dirty = "true";
    };
    timerForm.append(el("h2", "定时扫描"));
    const timerFields = {};
    for (const [key, label, min, max, step] of [
      ["interval_hours", "检查间隔（小时）", 0.25, 168, 0.25],
      ["logon_delay_minutes", "登录后延迟（分钟）", 0, 1440, 1],
      ["retry_minutes", "失败重试间隔（分钟）", 1, 1440, 1],
      ["retry_count", "最多重试次数", 1, 10, 1],
    ]) {
      const labelEl = el("label", label),
        input = el("input");
      input.type = "number";
      input.required = true;
      input.min = min;
      input.max = max;
      input.step = step;
      input.value = schedule[key];
      input.setAttribute("aria-label", label);
      timerFields[key] = input;
      labelEl.append(input);
      timerForm.append(labelEl);
    }
    const timerStatus = el(
      "p",
      "下次检查：" + fmt(schedule.next_run) + "（北京时间）",
    );
    const timerError = el("div", null, "form-error");
    timerError.setAttribute("role", "status");
    const timerSave = el("button", "保存定时设置", "primary");
    timerSave.type = "submit";
    timerForm.append(timerStatus, timerError, timerSave);
    timerForm.onsubmit = async (e) => {
      e.preventDefault();
      timerSave.disabled = true;
      timerSave.textContent = "保存中…";
      timerError.textContent = "";
      try {
        const actual = await api(
          "/api/schedule",
          Object.fromEntries(
            Object.entries(timerFields).map(([key, input]) => [
              key,
              Number(input.value),
            ]),
          ),
        );
        data.schedule = actual;
        if (settingsCache) settingsCache.schedule = actual;
        timerStatus.textContent =
          "下次检查：" + fmt(actual.next_run) + "（北京时间）";
        timerForm.dataset.dirty = "false";
        render();
        tell("定时设置已保存 · 下次 " + fmt(actual.next_run) + "（北京时间）");
      } catch (error) {
        timerError.textContent = error.message;
      } finally {
        timerSave.disabled = false;
        timerSave.textContent = "保存定时设置";
      }
    };
    main.append(timerForm);
  } catch (e) {
    if (!current()) return;
    main.replaceChildren(el("div", "加载失败", "small"), button("重试", () => renderContent()));
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
  stream.onopen = () => {
    $("#connection").textContent = data ? "本机已连接" : "正在读取记录…";
    if (!data && !loading) refresh();
  };
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
