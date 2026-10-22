"use strict";
const $ = (id) => document.getElementById(id);
let processing = false;
let dragDepth = 0;
const dropZone = $("csv-drop-zone");
function resetDrag() {
  dragDepth = 0;
  dropZone.classList.remove("drag-over");
}
function receiveFiles(files) {
  if (processing) return;
  if (files.length !== 1 || !files[0].name.toLowerCase().endsWith(".csv")) {
    $("error").textContent = files.length !== 1 ? "Choose one CSV file at a time." : "Choose a CSV file ending in .csv.";
    $("error").hidden = false;
    $("progress").textContent = "";
    $("csv-file").value = "";
    return;
  }
  analyse(files[0]);
}
function node(tag, text, className) {
  const element = document.createElement(tag);
  if (text !== undefined) element.textContent = text;
  if (className) element.className = className;
  return element;
}
function link(text, href, className) {
  const element = node("a", text, className);
  try {
    const url = new URL(href);
    if (["http:", "https:"].includes(url.protocol)) {
      element.href = url.href;
      element.target = "_blank";
      element.rel = "noopener noreferrer";
    }
  } catch (_) { /* Invalid source links remain plain text. */ }
  return element;
}
const VISIBLE_ROWS = 10;
const plural = (count, word) => `${count.toLocaleString("en-US")} ${word}${count === 1 ? "" : "s"}`;
function render(data, filename) {
  $("accounts").replaceChildren();
  // Display only: the ranked order is unchanged and the results CSV keeps every row.
  data.rows.slice(0, VISIBLE_ROWS).forEach((row) => {
    const tr = node("tr");
    const account = node("td");
    account.append(link(row.account, row.website, "account-name"), node("span", row.account_id, "account-id"));
    const priority = node("td");
    const priorityLabel = row.priority === "Research needed" ? "Confirm details" : row.priority;
    priority.append(node("span", String(row.rank).padStart(2, "0"), "rank"), node("span", priorityLabel, "priority-chip " + row.priority_band));
    const evidence = node("td");
    const commercial = row.simulated_commercial;
    const usd = (value) => value === null ? "Unknown" : new Intl.NumberFormat("en-US", {style:"currency", currency:"USD", minimumFractionDigits:value % 1 ? 2 : 0, maximumFractionDigits:2}).format(value);
    evidence.append(node("span", `Estimated annual fees: ${usd(commercial.illustrative_annual_gross_fee_revenue_usd)}`, "evidence-title"));
    evidence.append(node("span", `Annual GMV: ${usd(commercial.annual_ticketing_gmv_usd)}`, "volume-note"));
    const inputs = commercial.inputs || {};
    {
      const details = node("details", undefined, "sources");
      details.append(node("summary", "Inputs and sources"));
      details.append(node("p", commercial.origin === "synthetic" ? "Commercial inputs are fictional test assumptions." : "No commercial inputs supplied."));
      details.append(node("p", commercial.annual_ticketing_gmv_usd === null
        ? commercial.assumptions
        : `${plural(inputs.simulated_annual_paid_events, "event")} x ${plural(inputs.simulated_paid_tickets_per_event, "ticket")} x ${usd(inputs.simulated_average_ticket_price_usd)} average price. Switching difficulty: ${commercial.switching_difficulty}.`));
      details.append(node("p", row.reviewed ? "Public evidence: supplied field review." : "Public evidence has not been verified."));
      details.append(node("p", commercial.fee_assumptions_text + ". " + commercial.fee_limitations));
      details.append(node("p", `${commercial.opportunity} GMV band. These configurable GMV bands are unvalidated demonstration assumptions and do not determine fee ranking.`));
      details.append(node("p", row.evidence + ". Actual annual paid-ticket volume: " + row.annual_volume + "."));
      const list = node("ul");
      row.offers.forEach((offer) => {
        const li = node("li");
        li.append(link(offer.name, offer.source), node("span", offer.date || "Date not established", "source-date"));
        list.append(li);
      });
      if (!row.offers.length) row.sources.forEach((url) => {
        const li = node("li");
        li.append(link("Public source", url));
        list.append(li);
      });
      if (list.childNodes.length) details.append(list);
      evidence.append(details);
    }
    const action = node("td");
    // Wording only. The API, export, task, safeguards and ranked order stay unchanged.
    const actionLabel = row.next_action.replace(/^Verify organiser identity/, "Confirm organiser identity");
    action.append(node("span", actionLabel));
    const why = node("td");
    why.append(node("p", row.explanation.replace("Evidence needed: ", "Check needed: ").replace(" Simulated commercial values do not verify public facts.", ""), "why"));
    const crm = node("div", undefined, "crm-context");
    const history = node("details", undefined, "sources");
    history.append(node("summary", "CRM history"), node("p", row.crm_label + ". " + row.crm_context));
    crm.append(history);
    why.append(crm);
    tr.append(account, priority, evidence, action, why);
    $("accounts").append(tr);
  });
  const truncated = data.rows.length > VISIBLE_ROWS;
  $("account-count").textContent = truncated ? `Top ${VISIBLE_ROWS} of ${data.rows.length}` : data.rows.length;
  $("batch-meta").textContent = `${filename}. ${data.rows.length} accounts processed.` +
    (truncated ? ` Showing the top ${VISIBLE_ROWS}; download the full list below.` : "");
  const origins = [...new Set(data.rows.map((r) => r.crm_label))];
  const crmNote = origins.length === 1 && origins[0] === "Simulated CRM" ? "CRM histories are simulated." : "Uploaded CRM information is unverified.";
  $("limitation").textContent = `${crmNote} Fee estimates are illustrative. Exports require human review.`;
  const assumptions = data.rows[0].simulated_commercial;
  $("commercial-rule-detail").textContent = assumptions.fee_assumptions_text + ". Reprocess the file after changing fee settings.";
  $("download-results").href = data.download_results;
  $("download-handoff").href = data.download_handoff;
  $("draft-note").textContent = `HubSpot draft includes ${data.task_drafts} proposed tasks and supporting records, selected within available demo capacity. Review tasks and map owners before importing. No live CRM connection or outreach.`;
  $("issue-list").replaceChildren();
  data.issues.forEach((issue) => $("issue-list").append(node("li", `Row ${issue.input_row || "unknown"}, ${issue.account_id || "unknown account"}: ${issue.reason}`)));
  $("input-issues").hidden = !data.issues.length;
  $("issue-count").textContent = `${data.issues.length} input warning(s). ${data.rows.length} of ${data.input_rows} organiser rows retained.`;
  $("empty").hidden = true;
  $("results").hidden = false;
  document.body.classList.add("has-results");
  $("file-help").open = false;
  $("results").scrollIntoView({behavior: "smooth", block: "start"});
}
async function analyse(file) {
  if (processing || !file) return;
  processing = true;
  resetDrag();
  dropZone.setAttribute("aria-busy", "true");
  $("error").hidden = true;
  $("progress").textContent = "Checking records and prioritising next actions...";
  $("csv-file").disabled = true;
  $("results").hidden = true;
  try {
    if (file.size > 10 * 1024 * 1024) throw new Error("Choose a CSV smaller than 10 MB.");
    const response = await fetch("/api/analyse", {
      method: "POST", headers: {"Content-Type": "text/csv"}, body: file
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Could not process this file.");
    render(data, file.name);
    $("progress").textContent = "";
  } catch (error) {
    $("error").textContent = error.message;
    $("error").hidden = false;
    $("progress").textContent = "";
    $("empty").hidden = false;
    document.body.classList.remove("has-results");
  } finally {
    processing = false;
    dropZone.setAttribute("aria-busy", "false");
    $("csv-file").disabled = false;
    $("csv-file").value = "";
  }
}
$("csv-file").addEventListener("change", (event) => {
  if (event.target.files.length) receiveFiles(event.target.files);
});
const isFileDrag = (event) => Array.from(event.dataTransfer?.types || []).includes("Files");
dropZone.addEventListener("dragenter", (event) => {
  if (!isFileDrag(event)) return;
  event.preventDefault();
  if (!processing) {
    dragDepth += 1;
    dropZone.classList.add("drag-over");
  }
});
dropZone.addEventListener("dragover", (event) => {
  if (!isFileDrag(event)) return;
  event.preventDefault();
  event.dataTransfer.dropEffect = processing ? "none" : "copy";
});
dropZone.addEventListener("dragleave", () => {
  dragDepth = Math.max(0, dragDepth - 1);
  if (!dragDepth) resetDrag();
});
dropZone.addEventListener("drop", (event) => {
  if (!isFileDrag(event)) return;
  event.preventDefault();
  resetDrag();
  receiveFiles(event.dataTransfer.files);
});
window.addEventListener("dragover", (event) => {
  if (!isFileDrag(event)) return;
  event.preventDefault();
  if (!dropZone.contains(event.target)) event.dataTransfer.dropEffect = "none";
});
window.addEventListener("drop", (event) => {
  if (!isFileDrag(event)) return;
  event.preventDefault();
  resetDrag();
});
window.addEventListener("dragend", resetDrag);
