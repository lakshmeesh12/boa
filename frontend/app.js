/* Bank of America — Banking Simulator App Logic */
(function () {
"use strict";

// Defer all DOM interaction until the document is fully parsed
document.addEventListener("DOMContentLoaded", function() { _boot(); });

const API = "/api/v1";

// ── Helpers ───────────────────────────────────────────────────────────────
const $ = id => document.getElementById(id);
const fmtMoney = v => "$" + Number(v||0).toLocaleString("en-US",{minimumFractionDigits:2,maximumFractionDigits:2});
const fmtDate  = iso => iso ? new Date(iso).toLocaleDateString("en-US",{year:"numeric",month:"short",day:"numeric"}) : "—";
const fmtShort = iso => iso ? new Date(iso).toLocaleDateString("en-US",{month:"short",day:"numeric"}) : "—";

async function apiFetch(path, opts={}) {
  const r = await fetch(API + path, {headers:{"Content-Type":"application/json"}, ...opts});
  let body = null; try { body = await r.json(); } catch(_) {}
  if (!r.ok) { const e = new Error((body&&body.detail)||`HTTP ${r.status}`); e.status=r.status; throw e; }
  return body;
}

function toast(msg, ok=true) {
  const t = $("toast"); if(!t) return;
  t.textContent = msg;
  t.style.background = ok ? "#1A7F37" : "#C5221F";
  t.style.display = "block";
  setTimeout(() => { t.style.display = "none"; }, 3500);
}

// ── State ─────────────────────────────────────────────────────────────────
let _portfolio = null;
let _currentCardId    = null;
let _currentDepositId = null;
let _currentAccountId = null;
let _ccCard = null;   // currently open credit card object
let _minPayment = 25;
let _stmtBalance = 0;
let _currentBalance = 0;
let _allCustomers = [];

// ── Navigation ────────────────────────────────────────────────────────────
let _activeNav = "overview";

window.navTo = function(id, btn) {
  document.querySelectorAll(".subnav-item").forEach(b => b.classList.remove("active"));
  if (btn) btn.classList.add("active");
  showView("view-" + id);
  _activeNav = id;
};

function showView(id) {
  document.querySelectorAll(".view").forEach(v => v.classList.remove("active"));
  const el = $(id); if(el) el.classList.add("active");
}

window.switchTab = function(id, btn) {
  const parent = btn.closest(".tabs");
  if (parent) parent.querySelectorAll(".tab-btn").forEach(b => b.classList.remove("active"));
  if (btn) btn.classList.add("active");
  const container = document.querySelector(".view.active");
  if (container) container.querySelectorAll(".tab-panel").forEach(p => p.classList.remove("active"));
  const panel = $(id); if (panel) panel.classList.add("active");
};

// ── Bootstrap ─────────────────────────────────────────────────────────────
async function init() {
  await loadCustomerList();
}

async function loadCustomerList() {
  try {
    const data = await apiFetch("/customers?limit=50");
    _allCustomers = data.customers || [];
    const sel = $("customer-selector");
    if (!sel) return;
    sel.innerHTML = _allCustomers.map((c,i) =>
      `<option value="${c._id}">${c.display_name || "Customer " + (i+1)} — ${c.kyc_status}</option>`
    ).join("");
    if (_allCustomers.length) {
      await loadPortfolio(_allCustomers[0]._id);
    }
  } catch(e) {
    console.error("Failed to load customers:", e);
  }
}

window.switchCustomer = async function(id) {
  if (!id) return;
  await loadPortfolio(id);
};

async function loadPortfolio(customerId) {
  try {
    _portfolio = await apiFetch(`/customers/${customerId}/portfolio`);
    renderSidebar(_portfolio);
    renderOverview(_portfolio);
    // Always show the demo user name regardless of API data
    if ($("customer-name"))   $("customer-name").textContent   = DEMO_USER_NAME;
    if ($("customer-avatar")) $("customer-avatar").textContent = DEMO_USER_INITIALS;
  } catch(e) {
    console.error("Portfolio load failed:", e);
  }
}
function setText(id, v) { const e=$(id); if(e) e.textContent = v; }

// ── SIDEBAR ───────────────────────────────────────────────────────────────
function renderSidebar(portfolio) {
  const {accounts=[], credit_cards=[], fixed_deposits=[]} = portfolio;
  let html = "";

  // Banking
  const checking = accounts.filter(a=>a.account_type==="CHECKING");
  const savings   = accounts.filter(a=>a.account_type==="SAVINGS");
  if (checking.length || savings.length) {
    html += `<div class="sidebar-section-header">Banking</div>`;
    checking.forEach(a => {
      html += sidebarAccountItem(a._id, "💳", "acct-icon-check", "Adv. Plus Banking™", a.account_number, a.balance, a.status, "account");
    });
    savings.forEach(a => {
      html += sidebarAccountItem(a._id, "🏦", "acct-icon-save", "Rewards Savings", a.account_number, a.balance, a.status, "account");
    });
  }

  // Credit Cards
  if (credit_cards.length) {
    html += `<div class="sidebar-section-header">Credit Cards</div>`;
    credit_cards.forEach(c => {
      html += sidebarAccountItem(c._id, "💳", "acct-icon-card", "BofA Cash Rewards", c.card_number_masked, c.current_balance, c.status, "credit-card");
    });
  }

  // Fixed Deposits
  if (fixed_deposits.length) {
    html += `<div class="sidebar-section-header">CDs &amp; IRAs</div>`;
    fixed_deposits.forEach(fd => {
      html += sidebarAccountItem(fd._id, "📈", "acct-icon-fd", `${fd.tenure_months}-Month CD`, fd.deposit_number, fd.principal_amount, fd.status, "deposit");
    });
  }

  // Placeholder sections
  html += `<div class="sidebar-section-header">Loans &amp; Lines</div>`;
  html += `<div class="sidebar-account-item" style="opacity:.5;cursor:default;">
    <div class="sidebar-acct-icon acct-icon-loan">🏠</div>
    <div class="sidebar-acct-info">
      <div class="sidebar-acct-name">Home Equity Line</div>
      <div class="sidebar-acct-num">No accounts</div>
    </div>
  </div>`;

  html += `<div class="sidebar-section-header">Investments</div>`;
  html += `<div class="sidebar-account-item" style="opacity:.5;cursor:default;">
    <div class="sidebar-acct-icon acct-icon-save">📊</div>
    <div class="sidebar-acct-info">
      <div class="sidebar-acct-name">Merrill Lynch</div>
      <div class="sidebar-acct-num">Not linked</div>
    </div>
  </div>`;

  const sc = $("sidebar-content");
  if (sc) sc.innerHTML = html;

  // Sidebar click events are handled via inline onclick (avoids innerHTML race)
}

function sidebarAccountItem(id, icon, iconClass, name, number, balance, status, type) {
  const masked = formatMasked(number);
  const bal = fmtMoney(balance);
  const fnMap = {
    "account":     `openAccountView('${id}')`,
    "credit-card": `openCreditCardView('${id}')`,
    "deposit":     `openDepositView('${id}')`,
  };
  const onclick = fnMap[type] || "";
  return `<div class="sidebar-account-item" data-id="${id}" data-type="${type}"
    onclick="document.querySelectorAll('.sidebar-account-item').forEach(e=>e.classList.remove('active'));this.classList.add('active');${onclick}">
    <div class="sidebar-acct-icon ${iconClass}">${icon}</div>
    <div class="sidebar-acct-info">
      <div class="sidebar-acct-name">${name}</div>
      <div class="sidebar-acct-num">${masked}</div>
    </div>
    <div class="sidebar-acct-bal">${bal}</div>
  </div>`;
}

function formatMasked(num) {
  if (!num) return "";
  if (num.includes("XXXX")) return num;
  const s = String(num);
  return "••••" + s.slice(-4);
}

// ── OVERVIEW ──────────────────────────────────────────────────────────────
function renderOverview(portfolio) {
  const {accounts=[], credit_cards=[], fixed_deposits=[], customer={}} = portfolio;
  showView("view-overview");

  // Page subtitle
  const sub = $("overview-subtitle");
  if (sub) sub.textContent = `Good day, ${(customer.display_name||"").split(".").pop()?.trim()||"customer"} — here's a snapshot of your accounts.`;

  // Build cards
  let html = "";

  accounts.forEach(a => {
    const status = a.status;
    const isActive = status === "ACTIVE";
    html += `<div class="acct-summary-card${a.account_type==="SAVINGS"?" ":""}" onclick="openAccountView('${a._id}')">
      <div class="acct-card-top">
        <div>
          <div class="acct-card-name">${a.account_type==="CHECKING"?"Adv. Plus Banking™":"Rewards Savings"}</div>
          <div class="acct-card-num">••••${String(a.account_number||"").slice(-4)}</div>
        </div>
        <span class="${status==="ACTIVE"?"tag-active":status==="FROZEN"?"tag-frozen":"tag-blocked"}">${status}</span>
      </div>
      <div class="acct-card-bal">${fmtMoney(a.balance)}</div>
      <div class="acct-card-bal-lbl">Available Balance</div>
      <div class="acct-card-actions">
        <button class="btn btn-navy btn-sm" onclick="event.stopPropagation();openAccountView('${a._id}')">View Account</button>
        <button class="btn btn-ghost btn-sm" onclick="event.stopPropagation();">Transfer</button>
      </div>
    </div>`;
  });

  credit_cards.forEach(c => {
    const avail = Number(c.available_credit||0);
    const limit = Number(c.credit_limit||1);
    const used  = Number(c.current_balance||0);
    const pct   = Math.min(100, Math.round(used/limit*100));
    html += `<div class="acct-summary-card cc-card" onclick="openCreditCardView('${c._id}')">
      <div class="acct-card-top">
        <div>
          <div class="acct-card-name">BofA Cash Rewards Credit Card</div>
          <div class="acct-card-num">${c.card_number_masked}</div>
        </div>
        <span class="${c.status==="ACTIVE"?"tag-active":c.status==="BLOCKED"?"tag-blocked":"tag-pending"}">${c.status}</span>
      </div>
      <div style="display:flex;gap:20px;margin-bottom:10px;">
        <div>
          <div style="font-size:11px;color:#666;">Available Credit</div>
          <div style="font-size:20px;font-weight:700;color:#1A7F37;">${fmtMoney(avail)}</div>
        </div>
        <div>
          <div style="font-size:11px;color:#666;">Balance</div>
          <div style="font-size:20px;font-weight:700;">${fmtMoney(used)}</div>
        </div>
      </div>
      <div class="util-bar-track">
        <div class="util-bar-fill" style="width:${pct}%;background:${pct>80?"#E31837":pct>50?"#F59E0B":"#1A7F37"};"></div>
      </div>
      <div style="font-size:11px;color:#666;margin-top:4px;">${pct}% utilized of ${fmtMoney(limit)}</div>
      <div class="acct-card-actions">
        <button class="btn btn-red btn-sm" onclick="event.stopPropagation();openCreditCardView('${c._id}')">View Card</button>
        <button class="btn btn-ghost btn-sm" onclick="event.stopPropagation();openPaymentModal('${c._id}')">Pay</button>
      </div>
    </div>`;
  });

  fixed_deposits.forEach(fd => {
    const matDate = fd.maturity_date ? new Date(fd.maturity_date) : null;
    const daysLeft = matDate ? Math.max(0, Math.round((matDate-Date.now())/86400000)) : "—";
    html += `<div class="acct-summary-card fd-card" onclick="openDepositView('${fd._id}')">
      <div class="acct-card-top">
        <div>
          <div class="acct-card-name">${fd.tenure_months}-Month CD</div>
          <div class="acct-card-num">••••${String(fd.deposit_number||"").slice(-4)}</div>
        </div>
        <span class="${fd.status==="ACTIVE"?"tag-active":fd.status==="MATURED"?"tag-matured":"tag-blocked"}">${fd.status}</span>
      </div>
      <div class="acct-card-bal">${fmtMoney(fd.principal_amount)}</div>
      <div class="acct-card-bal-lbl">Principal · ${Number(fd.interest_rate_apy||0).toFixed(2)}% APY</div>
      <div style="font-size:12px;color:#666;margin-top:8px;">Matures: ${fmtDate(fd.maturity_date)} ${daysLeft!=="—"?"("+daysLeft+" days)":""}</div>
      <div class="acct-card-actions">
        <button class="btn btn-ghost btn-sm" style="border-color:#F59E0B;color:#7C5700;" onclick="event.stopPropagation();openDepositView('${fd._id}')">View CD</button>
      </div>
    </div>`;
  });

  const grid = $("overview-grid");
  if (grid) grid.innerHTML = html || "<div style='color:#999;padding:20px;'>No accounts found.</div>";

  // Recent activity (last 10 transactions across accounts)
  renderOverviewActivity(accounts);
}

async function renderOverviewActivity(accounts) {
  const actEl = $("overview-activity");
  if (!actEl) return;
  if (!accounts.length) { actEl.innerHTML = "<div style='color:#999;text-align:center;padding:20px;'>No recent activity.</div>"; return; }

  try {
    const acct = accounts.find(a=>a.status==="ACTIVE") || accounts[0];
    const data = await apiFetch(`/accounts/${acct._id}/statement?limit=8`);
    const txns = data.transactions || [];
    if (!txns.length) { actEl.innerHTML = "<div style='color:#999;text-align:center;padding:20px;'>No recent transactions.</div>"; return; }
    actEl.innerHTML = `<table class="txn-table" style="width:100%;">
      <thead><tr><th>Date</th><th>Description</th><th class="text-right">Amount</th><th class="text-right">Status</th></tr></thead>
      <tbody>${txns.map(t=>txnRow(t)).join("")}</tbody>
    </table>`;
  } catch(_) {
    actEl.innerHTML = "<div style='color:#999;text-align:center;padding:20px;'>No transaction history available.</div>";
  }
}

function txnRow(t) {
  const isCredit = t.type==="CREDIT"||t.type==="INTEREST_ACCRUAL";
  const amt = isCredit
    ? `<span class="amount-positive">+${fmtMoney(t.amount)}</span>`
    : `<span class="amount-negative">-${fmtMoney(t.amount)}</span>`;
  const cats = {CREDIT:"Deposit",DEBIT:"Withdrawal",FEE:"Fee",INTEREST_ACCRUAL:"Interest"};
  return `<tr>
    <td style="white-space:nowrap;color:#666;">${fmtShort(t.timestamp)}</td>
    <td><div class="txn-desc">${t.description||t.type}</div><div class="txn-cat">${cats[t.type]||t.type}</div></td>
    <td class="text-right">${amt}</td>
    <td class="text-right"><span class="${t.status==="COMPLETED"?"tag-active":t.status==="FAILED"?"tag-blocked":"tag-pending"}">${t.status}</span></td>
  </tr>`;
}

// ─────────────────────────────────────────────────────────────────────────
// FIXED CUSTOMER NAME
// ─────────────────────────────────────────────────────────────────────────
const DEMO_USER_NAME     = "Lakshmeesh H N";
const DEMO_USER_INITIALS = "LH";

// ── ACCOUNT DETAIL ────────────────────────────────────────────────────────
window.openAccountView = openAccountView;
async function openAccountView(accountId) {
  _currentAccountId = accountId;
  showView("view-account");
  // Reset tabs
  document.querySelectorAll("#view-account .tab-btn").forEach((b,i)=>{b.classList.toggle("active",i===0);});
  document.querySelectorAll("#view-account .tab-panel").forEach((p,i)=>{p.classList.toggle("active",i===0);});

  // Find account in portfolio
  const acct = (_portfolio?.accounts||[]).find(a=>a._id===accountId) || {};
  const typeLabel = acct.account_type==="CHECKING" ? "Adv. Plus Banking™ Checking" : "Rewards Savings";

  // Hero
  const heroArea = $("account-hero-area");
  if (heroArea) {
    heroArea.innerHTML = `
    <div class="acct-hero" style="margin-bottom:20px;">
      <div class="acct-hero-name">${typeLabel} ••••${String(acct.account_number||"").slice(-4)}</div>
      <div class="acct-hero-bal">${fmtMoney(acct.balance)}</div>
      <div class="acct-hero-sub">Available Balance · ${acct.status==="ACTIVE"?"Account is Active":acct.status}</div>
      <div class="acct-hero-row">
        <div class="acct-hero-stat"><div class="acct-hero-stat-lbl">Account Type</div><div class="acct-hero-stat-val">${acct.account_type||"—"}</div></div>
        <div class="acct-hero-stat"><div class="acct-hero-stat-lbl">Account Number</div><div class="acct-hero-stat-val mono">••••${String(acct.account_number||"").slice(-4)}</div></div>
        <div class="acct-hero-stat"><div class="acct-hero-stat-lbl">Routing Number</div><div class="acct-hero-stat-val mono">026009593</div></div>
        <button class="btn btn-outline" style="color:#fff;border-color:rgba(255,255,255,.5);" onclick="openTransferModal()">Transfer Money</button>
      </div>
    </div>`;
  }

  // Info panel
  const infoPanel = $("acct-info-panel");
  if (infoPanel) {
    infoPanel.innerHTML = `
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;">
      <div><div style="font-size:11px;color:#666;font-weight:700;text-transform:uppercase;">Account Nickname</div><div style="margin-top:4px;">${typeLabel}</div></div>
      <div><div style="font-size:11px;color:#666;font-weight:700;text-transform:uppercase;">Account Type</div><div style="margin-top:4px;">${acct.account_type}</div></div>
      <div><div style="font-size:11px;color:#666;font-weight:700;text-transform:uppercase;">Account Number</div><div style="margin-top:4px;font-family:monospace;">••••${String(acct.account_number||"").slice(-4)}</div></div>
      <div><div style="font-size:11px;color:#666;font-weight:700;text-transform:uppercase;">Routing Number</div><div style="margin-top:4px;font-family:monospace;">026009593</div></div>
      <div><div style="font-size:11px;color:#666;font-weight:700;text-transform:uppercase;">Status</div><div style="margin-top:4px;"><span class="${acct.status==="ACTIVE"?"tag-active":"tag-blocked"}">${acct.status}</span></div></div>
      <div><div style="font-size:11px;color:#666;font-weight:700;text-transform:uppercase;">Opened</div><div style="margin-top:4px;">${fmtDate(acct.opened_at)}</div></div>
    </div>`;
  }

  // Transactions
  const tbody = $("acct-txn-body");
  if (tbody) tbody.innerHTML = `<tr><td colspan="5" style="text-align:center;padding:20px;color:#999;">Loading…</td></tr>`;

  try {
    const data = await apiFetch(`/accounts/${accountId}/statement?limit=20`);
    const txns = data.transactions || [];
    if (!tbody) return;
    if (!txns.length) {
      tbody.innerHTML = `<tr><td colspan="5" style="text-align:center;padding:32px;color:#999;">No transactions found.</td></tr>`;
      return;
    }
    let runBal = Number(acct.balance||0);
    tbody.innerHTML = txns.map(t => {
      const isCredit = t.type==="CREDIT"||t.type==="INTEREST_ACCRUAL";
      const amt = Number(t.amount||0);
      const amtStr = isCredit
        ? `<span class="amount-positive">+${fmtMoney(amt)}</span>`
        : `<span class="amount-negative">-${fmtMoney(amt)}</span>`;
      const balStr = fmtMoney(runBal);
      if (!isCredit) runBal += amt; else runBal -= amt;
      const cats={CREDIT:"Deposit/Credit",DEBIT:"Purchase/Withdrawal",FEE:"Fee",INTEREST_ACCRUAL:"Interest Earned"};
      return `<tr>
        <td style="color:#666;white-space:nowrap;font-size:12px;">${fmtDate(t.timestamp)}</td>
        <td><div class="txn-desc">${t.description||t.type}</div><div class="txn-cat">${cats[t.type]||t.type}</div></td>
        <td class="text-right"><div class="txn-cat">ACH / EFT</div></td>
        <td class="text-right">${amtStr}</td>
        <td class="text-right" style="font-family:monospace;">${balStr}</td>
      </tr>`;
    }).join("");
  } catch(_) {
    if (tbody) tbody.innerHTML = `<tr><td colspan="5" style="text-align:center;padding:32px;color:#999;">Could not load transactions.</td></tr>`;
  }
}

// ── CREDIT CARD VIEW — fully functional 7-tab module ─────────────────────
let _ccActiveTab = "summary";

window.openCreditCardView = openCreditCardView;
async function openCreditCardView(cardId) {
  _currentCardId = cardId;
  _ccActiveTab   = "summary";
  showView("view-credit-card");
  const container = $("cc-content");
  if (!container) return;
  container.innerHTML = `<div class="skel" style="height:160px;border-radius:10px;margin-bottom:16px;"></div>
    <div class="skel" style="height:40px;border-radius:6px;margin-bottom:16px;"></div>
    <div class="skel" style="height:280px;border-radius:10px;"></div>`;
  try {
    const card = await apiFetch(`/credit-cards/${cardId}`);
    renderCreditCard(card);
  } catch(e) {
    container.innerHTML = `<div class="alert alert-danger">Failed to load card: ${e.message}</div>`;
  }
}

// ─────────────────────────────────────────────────────────────────────────
// CC TAB SWITCHER
// ─────────────────────────────────────────────────────────────────────────
window.ccTab = async function(tab) {
  _ccActiveTab = tab;
  // highlight active tab button
  document.querySelectorAll(".cc-nav-btn").forEach(b => {
    b.classList.toggle("cc-nav-active", b.dataset.tab === tab);
  });
  const panel = $("cc-panel");
  if (!panel) return;
  panel.innerHTML = `<div style="display:flex;align-items:center;justify-content:center;height:180px;">
    <div style="width:32px;height:32px;border:3px solid #e2e8f0;border-top-color:#E31837;border-radius:50%;animation:spin .8s linear infinite;"></div>
  </div>`;
  try {
    switch(tab) {
      case "summary":     await renderCCSummary();     break;
      case "txns":        await renderCCTransactions(); break;
      case "pay":         await renderCCPayment();      break;
      case "statements":  await renderCCStatements();   break;
      case "rewards":     await renderCCRewards();      break;
      case "disputes":    await renderCCDisputes();     break;
      case "controls":    await renderCCControls();     break;
    }
  } catch(err) {
    if(panel) panel.innerHTML = `<div class="alert alert-danger">Error: ${err.message}</div>`;
  }
};

// ─────────────────────────────────────────────────────────────────────────
// CC HERO + SHELL (called once on card open)
// ─────────────────────────────────────────────────────────────────────────
function renderCreditCard(card) {
  const avail   = Number(card.available_credit||0);
  const limit   = Number(card.credit_limit||1);
  const bal     = Number(card.current_balance||0);
  const pct     = Math.min(100, Math.round(bal/limit*100));
  const barColor  = pct>80?"#E31837":pct>50?"#F59E0B":"#1A7F37";
  const isBlocked = card.status==="BLOCKED";
  const billingDay = card.billing_cycle_day||15;
  const today = new Date();
  let dueDate = new Date(today.getFullYear(), today.getMonth(), billingDay);
  if (dueDate <= today) dueDate.setMonth(dueDate.getMonth()+1);
  const daysLeft = Math.max(0, Math.round((dueDate-today)/86400000));
  const minPay = Math.max(25, Math.round(bal*0.02*100)/100);
  _minPayment = minPay; _stmtBalance = bal*0.85; _currentBalance = bal;

  // Store card in closure-accessible variable for sub-tab functions
  _ccCard = card;

  const container = $("cc-content");
  if (!container) return;

  container.innerHTML = `
  <!-- Card visual -->
  <div class="cc-hero">
    <div class="cc-logo-mark">
      <div class="cc-logo-circle cc-logo-c1"></div>
      <div class="cc-logo-circle cc-logo-c2"></div>
    </div>
    <div class="cc-hero-chip"></div>
    <div class="cc-hero-type">Bank of America®</div>
    <div class="cc-hero-name">Customized Cash Rewards Credit Card</div>
    <div class="cc-hero-number">${card.card_number_masked}</div>
    <div class="cc-hero-bottom">
      <div><div class="cc-hero-exp">EXPIRES</div><div style="font-size:13px;font-weight:700;">${card.expiry_date||"••/••"}</div></div>
      ${isBlocked ? `<span style="background:#E31837;color:#fff;padding:4px 12px;border-radius:3px;font-size:12px;font-weight:700;letter-spacing:.05em;">🔒 LOCKED</span>` : ""}
    </div>
  </div>

  <!-- Tab navigation -->
  <div style="display:flex;gap:2px;margin-top:16px;margin-bottom:20px;border-bottom:2px solid #e2e8f0;flex-wrap:wrap;">
    <button class="cc-nav-btn cc-nav-active" data-tab="summary"    onclick="ccTab('summary')">Summary</button>
    <button class="cc-nav-btn"               data-tab="txns"       onclick="ccTab('txns')">Transactions</button>
    <button class="cc-nav-btn"               data-tab="pay"        onclick="ccTab('pay')">Make a Payment</button>
    <button class="cc-nav-btn"               data-tab="statements" onclick="ccTab('statements')">Statements</button>
    <button class="cc-nav-btn"               data-tab="rewards"    onclick="ccTab('rewards')">Rewards</button>
    <button class="cc-nav-btn"               data-tab="disputes"   onclick="ccTab('disputes')">Disputes</button>
    <button class="cc-nav-btn"               data-tab="controls"   onclick="ccTab('controls')">Card Controls</button>
  </div>

  <!-- Dynamic tab panel -->
  <div id="cc-panel"></div>
  `;

  // Load summary tab immediately
  ccTab("summary");
}

// ─────────────────────────────────────────────────────────────────────────
// TAB 1 — SUMMARY
// ─────────────────────────────────────────────────────────────────────────
async function renderCCSummary() {
  const card = _ccCard; if(!card) return;
  const panel = $("cc-panel"); if(!panel) return;
  const avail = Number(card.available_credit||0);
  const limit = Number(card.credit_limit||1);
  const bal   = Number(card.current_balance||0);
  const pct   = Math.min(100, Math.round(bal/limit*100));
  const barCol= pct>80?"#E31837":pct>50?"#F59E0B":"#1A7F37";
  const billingDay = card.billing_cycle_day||15;
  const today = new Date();
  let dueDate = new Date(today.getFullYear(), today.getMonth(), billingDay);
  if (dueDate <= today) dueDate.setMonth(dueDate.getMonth()+1);
  const daysLeft = Math.max(0,Math.round((dueDate-today)/86400000));
  const minPay = Math.max(25,Math.round(bal*0.02*100)/100);

  panel.innerHTML = `
  ${bal>0?`<div class="payment-info-card">
    <div><div class="pay-due-label">💰 Payment Due</div>
      <div class="pay-due-date">${dueDate.toLocaleDateString("en-US",{month:"long",day:"numeric",year:"numeric"})}</div>
      <div class="pay-due-days">${daysLeft} days remaining</div>
    </div>
    <div class="pay-min"><div class="pay-min-lbl">Minimum Payment</div><div class="pay-min-val">${fmtMoney(minPay)}</div></div>
    <button class="btn btn-red" onclick="ccTab('pay')">Make a Payment →</button>
  </div>`:`<div class="alert alert-success">✓ No payment due — your balance is $0.00.</div>`}

  <div class="cc-stats-grid">
    <div class="cc-stat-box"><div class="cc-stat-lbl">Current Balance</div><div class="cc-stat-val">${fmtMoney(bal)}</div><div class="cc-stat-sub">As of today</div></div>
    <div class="cc-stat-box"><div class="cc-stat-lbl">Available Credit</div><div class="cc-stat-val" style="color:#1A7F37;">${fmtMoney(avail)}</div><div class="cc-stat-sub">Ready to use</div></div>
    <div class="cc-stat-box"><div class="cc-stat-lbl">Credit Limit</div><div class="cc-stat-val">${fmtMoney(limit)}</div><div class="cc-stat-sub">Total credit line</div></div>
    <div class="cc-stat-box"><div class="cc-stat-lbl">Statement Balance</div><div class="cc-stat-val">${fmtMoney(bal*0.85)}</div><div class="cc-stat-sub">Last statement</div></div>
    <div class="cc-stat-box"><div class="cc-stat-lbl">Minimum Payment</div><div class="cc-stat-val">${fmtMoney(minPay)}</div><div class="cc-stat-sub">Due ${dueDate.toLocaleDateString("en-US",{month:"short",day:"numeric"})}</div></div>
    <div class="cc-stat-box"><div class="cc-stat-lbl">APR (Variable)</div><div class="cc-stat-val">19.99%</div><div class="cc-stat-sub">Cash advance: 29.99%</div></div>
  </div>

  <div class="card" style="padding:16px 20px;margin-bottom:20px;">
    <div style="display:flex;justify-content:space-between;font-size:13px;color:#555;margin-bottom:8px;">
      <span style="font-weight:700;">Credit Utilization</span>
      <span style="font-weight:700;color:${barCol};">${pct}% used of ${fmtMoney(limit)}</span>
    </div>
    <div class="util-bar-track" style="height:10px;">
      <div class="util-bar-fill" style="width:${pct}%;background:${barCol};"></div>
    </div>
    <div style="font-size:11px;color:#888;margin-top:6px;">
      ${pct<=30?"✓ Excellent utilization. Keeping below 30% maximises your credit score.":pct<=50?"⚠ Moderate utilization. Consider paying down your balance soon.":"⚠ High utilization. This is likely impacting your credit score."}
    </div>
  </div>

  <div style="display:flex;gap:10px;flex-wrap:wrap;margin-bottom:20px;">
    <button class="btn btn-red" onclick="ccTab('pay')">💳 Make a Payment</button>
    <button class="btn btn-outline" onclick="ccTab('txns')">📋 View Transactions</button>
    <button class="btn btn-outline" onclick="ccTab('disputes')">🏷 Dispute a Charge</button>
    <button class="btn btn-ghost" onclick="ccTab('controls')">⚙ Card Controls</button>
  </div>

  <div class="card">
    <div class="card-header"><span class="card-title">Recent Transactions</span>
      <button style="font-size:12px;color:#004EBC;background:none;border:none;cursor:pointer;" onclick="ccTab('txns')">View All →</button></div>
    <div style="padding:0;" id="summary-txn-area"><div style="text-align:center;padding:20px;color:#999;">Loading…</div></div>
  </div>`;

  // Load recent transactions for preview
  try {
    const data = await apiFetch(`/credit-cards/${card._id}/transactions?limit=6`);
    const area = $("summary-txn-area"); if(!area) return;
    const txns = data.transactions||[];
    if(!txns.length){area.innerHTML=`<div style="text-align:center;padding:24px;color:#999;">No transactions found.</div>`;return;}
    area.innerHTML = `<table class="txn-table" style="width:100%;"><thead><tr>
      <th>Date</th><th>Merchant</th><th>Category</th><th class="text-right">Amount</th>
    </tr></thead><tbody>${txns.map(t=>`<tr>
      <td style="color:#666;font-size:12px;white-space:nowrap;">${fmtShort(t.timestamp)}</td>
      <td><div class="txn-desc">${t.icon||""} ${t.merchant_name}</div></td>
      <td><div class="txn-cat">${t.category}</div></td>
      <td class="text-right" style="font-family:monospace;font-weight:600;">
        ${t.type==="CREDIT"?`<span class="amount-positive">+${fmtMoney(t.amount)}</span>`:`<span class="amount-negative">-${fmtMoney(t.amount)}</span>`}
      </td>
    </tr>`).join("")}</tbody></table>`;
  } catch(_){}
}

// ─────────────────────────────────────────────────────────────────────────
// TAB 2 — TRANSACTIONS
// ─────────────────────────────────────────────────────────────────────────
async function renderCCTransactions() {
  const panel = $("cc-panel"); if(!panel||!_ccCard) return;
  panel.innerHTML = `
  <div class="card" style="padding:16px 20px;margin-bottom:16px;">
    <div style="display:flex;gap:10px;flex-wrap:wrap;align-items:flex-end;">
      <div style="flex:1;min-width:120px;"><label style="font-size:11px;font-weight:700;text-transform:uppercase;color:#666;display:block;margin-bottom:4px;">Start Date</label>
        <input type="date" id="txn-start" style="padding:7px 10px;font-size:13px;" /></div>
      <div style="flex:1;min-width:120px;"><label style="font-size:11px;font-weight:700;text-transform:uppercase;color:#666;display:block;margin-bottom:4px;">End Date</label>
        <input type="date" id="txn-end" style="padding:7px 10px;font-size:13px;" /></div>
      <div style="min-width:130px;"><label style="font-size:11px;font-weight:700;text-transform:uppercase;color:#666;display:block;margin-bottom:4px;">Category</label>
        <select id="txn-cat" style="padding:7px 10px;font-size:13px;">
          <option value="">All Categories</option>
          <option>Groceries</option><option>Gas & EV</option><option>Dining</option>
          <option>Shopping</option><option>Travel</option><option>Streaming</option>
          <option>Transportation</option><option>Payment</option>
        </select></div>
      <div style="min-width:120px;"><label style="font-size:11px;font-weight:700;text-transform:uppercase;color:#666;display:block;margin-bottom:4px;">Type</label>
        <select id="txn-type" style="padding:7px 10px;font-size:13px;">
          <option value="">All Types</option><option value="DEBIT">Purchases</option><option value="CREDIT">Payments & Credits</option>
        </select></div>
      <button class="btn btn-navy" onclick="loadCCTransactions()">🔍 Search</button>
    </div>
  </div>
  <div class="card"><div class="card-body" style="padding:0;" id="txn-table-area">
    <div style="text-align:center;padding:24px;color:#999;">Loading transactions…</div>
  </div></div>`;
  await loadCCTransactions();
}

async function loadCCTransactions() {
  const area = $("txn-table-area"); if(!area||!_ccCard) return;
  area.innerHTML = `<div style="text-align:center;padding:24px;color:#999;">Loading…</div>`;
  const start = $("txn-start")?.value||"";
  const end   = $("txn-end")?.value||"";
  const cat   = $("txn-cat")?.value||"";
  const type  = $("txn-type")?.value||"";
  let qs = "?limit=50";
  if(start) qs+=`&start_date=${start}`;
  if(end)   qs+=`&end_date=${end}`;
  if(cat)   qs+=`&category=${encodeURIComponent(cat)}`;
  if(type)  qs+=`&txn_type=${type}`;
  try {
    const data = await apiFetch(`/credit-cards/${_ccCard._id}/transactions${qs}`);
    const txns = data.transactions||[];
    if(!txns.length){area.innerHTML=`<div style="text-align:center;padding:32px;color:#999;">No transactions match your filters.</div>`;return;}
    let total_debits=0, total_credits=0;
    txns.forEach(t=>{ if(t.type==="DEBIT") total_debits+=Number(t.amount); else total_credits+=Number(t.amount); });
    area.innerHTML = `
    <div style="display:flex;gap:24px;padding:12px 16px;background:#f8f8fb;border-bottom:1px solid #e0e0e0;font-size:13px;">
      <span>${txns.length} transactions</span>
      <span style="color:#C5221F;margin-left:auto;">Charges: ${fmtMoney(total_debits)}</span>
      <span style="color:#1A7F37;">Payments: ${fmtMoney(total_credits)}</span>
    </div>
    <table class="txn-table" style="width:100%;"><thead><tr>
      <th>Date</th><th>Merchant</th><th>Category</th><th class="text-right">Amount</th><th>Status</th>
    </tr></thead>
    <tbody>${txns.map(t=>`<tr>
      <td style="color:#666;font-size:12px;white-space:nowrap;">${fmtDate(t.timestamp)}</td>
      <td><div class="txn-desc">${t.icon||"💳"} ${t.merchant_name}</div></td>
      <td><span class="tag-active" style="font-size:10px;">${t.category}</span></td>
      <td class="text-right" style="font-family:monospace;font-weight:700;">
        ${t.type==="CREDIT"?`<span class="amount-positive">+${fmtMoney(t.amount)}</span>`:`<span class="amount-negative">-${fmtMoney(t.amount)}</span>`}
      </td>
      <td><span class="${t.status==="COMPLETED"?"tag-active":"tag-pending"}" style="font-size:10px;">${t.status}</span></td>
    </tr>`).join("")}</tbody></table>`;
  } catch(e) { area.innerHTML=`<div class="alert alert-danger" style="margin:16px;">Error: ${e.message}</div>`; }
}
window.loadCCTransactions = loadCCTransactions;

// ─────────────────────────────────────────────────────────────────────────
// TAB 3 — MAKE A PAYMENT
// ─────────────────────────────────────────────────────────────────────────
async function renderCCPayment() {
  const panel = $("cc-panel"); if(!panel||!_ccCard) return;
  const card  = _ccCard;
  const bal   = Number(card.current_balance||0);
  const minP  = Math.max(25, Math.round(bal*0.02*100)/100);
  const stmtB = Math.round(bal*0.85*100)/100;
  const accts = _portfolio?.accounts?.filter(a=>a.status==="ACTIVE")||[];
  const today = new Date().toISOString().slice(0,10);

  panel.innerHTML = `
  <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;max-width:800px;">
    <div class="card" style="padding:20px;">
      <div style="font-weight:700;font-size:15px;color:#012169;margin-bottom:14px;">Payment Details</div>

      <div style="font-size:11px;font-weight:700;text-transform:uppercase;color:#666;margin-bottom:6px;">Payment Amount</div>
      <div style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:10px;">
        <button class="btn btn-ghost btn-sm" onclick="setPay('min')" id="pay-min-btn">Min. Payment (${fmtMoney(minP)})</button>
        <button class="btn btn-ghost btn-sm" onclick="setPay('stmt')" id="pay-stmt-btn">Statement Balance (${fmtMoney(stmtB)})</button>
        <button class="btn btn-ghost btn-sm" onclick="setPay('full')" id="pay-full-btn">Current Balance (${fmtMoney(bal)})</button>
      </div>
      <div style="display:flex;align-items:center;gap:6px;margin-bottom:14px;border:1px solid #e0e0e0;border-radius:6px;overflow:hidden;padding:8px 12px;">
        <span style="font-size:20px;color:#555;">$</span>
        <input type="number" id="cc-pay-amount" placeholder="0.00" step="0.01" value=""
          style="border:none;outline:none;font-size:20px;font-weight:700;flex:1;padding:0;" />
      </div>

      <div style="font-size:11px;font-weight:700;text-transform:uppercase;color:#666;margin-bottom:6px;">Pay From Account</div>
      <select id="cc-pay-from" style="margin-bottom:14px;padding:8px 10px;font-size:13px;">
        ${accts.map(a=>`<option value="${a._id}">Checking ••••${String(a.account_number||"").slice(-4)} (${fmtMoney(a.balance)})</option>`).join("")||"<option>No accounts linked</option>"}
      </select>

      <div style="font-size:11px;font-weight:700;text-transform:uppercase;color:#666;margin-bottom:6px;">Payment Date</div>
      <input type="date" id="cc-pay-date" value="${today}" style="margin-bottom:16px;padding:8px 10px;font-size:13px;" />

      <button class="btn btn-red" style="width:100%;padding:11px;" onclick="submitCCPayment()">Submit Payment</button>
      <div id="cc-pay-result" style="display:none;" class="alert alert-success" style="margin-top:12px;"></div>
      <div id="cc-pay-error"  style="display:none;" class="alert alert-danger"  style="margin-top:12px;"></div>
    </div>

    <div>
      <div class="card" style="padding:16px 18px;margin-bottom:12px;">
        <div style="font-weight:700;color:#012169;margin-bottom:8px;font-size:13px;">Account Summary</div>
        <div style="display:flex;flex-direction:column;gap:8px;font-size:13px;">
          <div style="display:flex;justify-content:space-between;"><span style="color:#666;">Current Balance</span><strong>${fmtMoney(bal)}</strong></div>
          <div style="display:flex;justify-content:space-between;"><span style="color:#666;">Minimum Payment</span><strong>${fmtMoney(minP)}</strong></div>
          <div style="display:flex;justify-content:space-between;"><span style="color:#666;">Statement Balance</span><strong>${fmtMoney(stmtB)}</strong></div>
          <div style="display:flex;justify-content:space-between;"><span style="color:#666;">Available Credit</span><strong style="color:#1A7F37;">${fmtMoney(Number(card.available_credit||0))}</strong></div>
        </div>
      </div>
      <div class="alert alert-info">
        ℹ Payments posted before 5:00 PM ET are applied the same business day.
        Standard payments take 2–3 days to free up credit.
      </div>
      <div class="alert alert-warn" style="margin-top:8px;">
        ⚠ <strong>AQE Test Note:</strong> This endpoint has an intentional vulnerability —
        try submitting a <em>negative amount</em> (e.g. -100) to test LOGIC-001.
      </div>
    </div>
  </div>`;
}

window.setPay = function(type) {
  const inp = $("cc-pay-amount"); if(!inp||!_ccCard) return;
  const bal  = Number(_ccCard.current_balance||0);
  const minP = Math.max(25, Math.round(bal*0.02*100)/100);
  if(type==="min")  inp.value = minP.toFixed(2);
  if(type==="stmt") inp.value = Math.round(bal*0.85*100)/100;
  if(type==="full") inp.value = bal.toFixed(2);
};

window.submitCCPayment = async function() {
  const amt  = parseFloat($("cc-pay-amount")?.value||"");
  const date = $("cc-pay-date")?.value||"";
  const res  = $("cc-pay-result"); const err = $("cc-pay-error");
  if(res) res.style.display="none"; if(err) err.style.display="none";
  if(!_ccCard){toast("No card loaded",false);return;}
  try {
    const r = await apiFetch(`/credit-cards/${_ccCard._id}/payment`,{
      method:"POST", body:JSON.stringify({amount:amt, payment_date:date, payment_type:"online"})
    });
    if(res){res.style.display="";res.textContent=`✓ Payment of ${fmtMoney(r.amount_paid)} ${r.status}. Ref: ${r.payment_ref.slice(0,8).toUpperCase()}`;}
    toast(`Payment of ${fmtMoney(r.amount_paid)} submitted!`);
    // Refresh card data
    const updated = await apiFetch(`/credit-cards/${_ccCard._id}`);
    _ccCard = updated;
  } catch(e) {
    if(err){err.style.display="";err.textContent="Error: "+e.message;}
  }
};

// ─────────────────────────────────────────────────────────────────────────
// TAB 4 — STATEMENTS
// ─────────────────────────────────────────────────────────────────────────
async function renderCCStatements() {
  const panel = $("cc-panel"); if(!panel||!_ccCard) return;
  panel.innerHTML = `<div style="text-align:center;padding:24px;color:#999;">Loading statements…</div>`;
  try {
    const now  = new Date();
    const data = await apiFetch(`/credit-cards/${_ccCard._id}/statements?year=${now.getFullYear()}&month=${now.getMonth()+1}`);
    const s    = data.statement;
    const periods = data.available_periods||[];
    panel.innerHTML = `
    <div style="display:grid;grid-template-columns:220px 1fr;gap:16px;">
      <div class="card" style="padding:14px;">
        <div style="font-weight:700;color:#012169;font-size:13px;margin-bottom:10px;">Statement Period</div>
        <div style="display:flex;flex-direction:column;gap:2px;">
          ${periods.map(p=>`<button onclick="loadStatement(${p.year},${p.month})" style="text-align:left;padding:8px 10px;border-radius:4px;border:none;background:${p.selected?"#E31837":""};color:${p.selected?"#fff":"#333"};font-size:13px;cursor:pointer;" class="${p.selected?"":"hover-gray"}">${p.label}</button>`).join("")}
        </div>
      </div>
      <div>
        <div class="acct-hero" style="margin-bottom:16px;">
          <div class="acct-hero-name">Statement — ${new Date(s.year,s.month-1,1).toLocaleDateString("en-US",{month:"long",year:"numeric"})}</div>
          <div class="acct-hero-bal">${fmtMoney(s.closing_balance)}</div>
          <div class="acct-hero-sub">Closing Balance · ${s.transactions_count} transactions</div>
          <div class="acct-hero-row">
            <div class="acct-hero-stat"><div class="acct-hero-stat-lbl">Opening Balance</div><div class="acct-hero-stat-val">${fmtMoney(s.opening_balance)}</div></div>
            <div class="acct-hero-stat"><div class="acct-hero-stat-lbl">Purchases</div><div class="acct-hero-stat-val">${fmtMoney(s.total_purchases)}</div></div>
            <div class="acct-hero-stat"><div class="acct-hero-stat-lbl">Payments & Credits</div><div class="acct-hero-stat-val">${fmtMoney(s.total_credits)}</div></div>
          </div>
        </div>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;">
          <div class="card" style="padding:16px;"><div style="font-size:11px;text-transform:uppercase;color:#666;font-weight:700;">Fees Charged</div><div style="font-size:22px;font-weight:700;margin-top:4px;">${fmtMoney(s.fees)}</div></div>
          <div class="card" style="padding:16px;"><div style="font-size:11px;text-transform:uppercase;color:#666;font-weight:700;">Interest Charged</div><div style="font-size:22px;font-weight:700;margin-top:4px;color:#C5221F;">${fmtMoney(s.interest_charged)}</div></div>
          <div class="card" style="padding:16px;"><div style="font-size:11px;text-transform:uppercase;color:#666;font-weight:700;">Minimum Payment Due</div><div style="font-size:22px;font-weight:700;margin-top:4px;">${fmtMoney(s.minimum_payment_due)}</div></div>
          <div class="card" style="padding:16px;"><div style="font-size:11px;text-transform:uppercase;color:#666;font-weight:700;">Payment Due Date</div><div style="font-size:22px;font-weight:700;margin-top:4px;">${s.payment_due_date}</div></div>
        </div>
        <div style="margin-top:14px;display:flex;gap:10px;">
          <button class="btn btn-outline" onclick="toast('Statement downloaded as PDF')">⬇ Download PDF</button>
          <button class="btn btn-ghost"   onclick="toast('Statement emailed to you')">✉ Email Statement</button>
        </div>
      </div>
    </div>`;
  } catch(e) { $("cc-panel").innerHTML=`<div class="alert alert-danger">${e.message}</div>`; }
}

window.loadStatement = async function(year,month) {
  const panel = $("cc-panel"); if(!panel||!_ccCard) return;
  const data = await apiFetch(`/credit-cards/${_ccCard._id}/statements?year=${year}&month=${month}`);
  const s = data.statement;
  const periods = data.available_periods||[];
  // Update just the content (same template as above with new data)
  panel.querySelector("[data-stmt]")?.remove();
  // Re-render full tab
  await renderCCStatements();
};

// ─────────────────────────────────────────────────────────────────────────
// TAB 5 — REWARDS
// ─────────────────────────────────────────────────────────────────────────
async function renderCCRewards() {
  const panel = $("cc-panel"); if(!panel||!_ccCard) return;
  panel.innerHTML = `<div style="text-align:center;padding:24px;color:#999;">Loading rewards…</div>`;
  try {
    const r = await apiFetch(`/credit-cards/${_ccCard._id}/rewards`);
    const cp = r.current_period;
    panel.innerHTML = `
    <div class="rewards-card" style="margin-bottom:20px;">
      <div style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:12px;">
        <div>
          <div class="rewards-lbl">Total Cash Back Available to Redeem</div>
          <div class="rewards-pts">${fmtMoney(r.available_to_redeem)}</div>
          <div class="rewards-sub">Lifetime earned: ${fmtMoney(r.lifetime_earned)}</div>
        </div>
        <div style="display:flex;gap:8px;flex-wrap:wrap;">
          <button class="btn" style="background:rgba(255,255,255,.2);border:1px solid rgba(255,255,255,.4);color:#fff;" onclick="toast('Cash back applied as statement credit!')">Statement Credit</button>
          <button class="btn" style="background:rgba(255,255,255,.15);border:1px solid rgba(255,255,255,.3);color:#fff;" onclick="toast('Direct deposit initiated — arrives in 2 days.')">Direct Deposit</button>
        </div>
      </div>
    </div>

    <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin-bottom:20px;">
      ${cp.categories.map(cat=>`
      <div class="card" style="padding:18px;text-align:center;">
        <div style="font-size:36px;margin-bottom:6px;">${cat.icon}</div>
        <div style="font-weight:700;font-size:14px;color:#012169;">${cat.name}</div>
        <div style="font-size:26px;font-weight:800;color:#E31837;margin:6px 0;">${cat.rate}</div>
        <div style="font-size:12px;color:#666;">Spent: ${fmtMoney(cat.spend)}</div>
        <div style="font-size:13px;font-weight:700;color:#1A7F37;margin-top:4px;">Earned: ${fmtMoney(cat.earned)}</div>
      </div>`).join("")}
    </div>

    <div class="card" style="padding:18px;">
      <div style="font-weight:700;color:#012169;margin-bottom:12px;">Redemption Options</div>
      <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:10px;">
        ${r.redemption_options.map(opt=>`
        <div style="border:1px solid #e0e0e0;border-radius:6px;padding:12px;text-align:center;">
          <div style="font-weight:700;font-size:13px;">${opt.type}</div>
          <div style="font-size:11px;color:#666;margin-top:2px;">Minimum: ${opt.minimum||r.redemption_minimum}</div>
          <button class="btn btn-outline btn-sm" style="margin-top:8px;" onclick="toast('${opt.type} redemption initiated!')">Redeem</button>
        </div>`).join("")}
      </div>
    </div>`;
  } catch(e){$("cc-panel").innerHTML=`<div class="alert alert-danger">${e.message}</div>`;}
}

// ─────────────────────────────────────────────────────────────────────────
// TAB 6 — DISPUTES
// ─────────────────────────────────────────────────────────────────────────
async function renderCCDisputes() {
  const panel = $("cc-panel"); if(!panel||!_ccCard) return;
  // Load recent transactions for the dispute selector
  let txnOptions = "";
  try {
    const d = await apiFetch(`/credit-cards/${_ccCard._id}/transactions?limit=15&txn_type=DEBIT`);
    txnOptions = (d.transactions||[]).map(t=>
      `<option value="${t.transaction_ref}">${fmtShort(t.timestamp)} — ${t.merchant_name} (${fmtMoney(t.amount)})</option>`
    ).join("");
  } catch(_){}

  panel.innerHTML = `
  <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;max-width:800px;">
    <div class="card" style="padding:20px;">
      <div style="font-weight:700;font-size:15px;color:#012169;margin-bottom:14px;">File a Dispute</div>

      <div class="form-group" style="margin-bottom:12px;">
        <label style="font-size:11px;font-weight:700;text-transform:uppercase;color:#666;display:block;margin-bottom:5px;">Select Transaction</label>
        <select id="disp-txn" style="padding:8px 10px;font-size:13px;">
          <option value="">Select a transaction…</option>${txnOptions}
          <option value="other">Other / not listed</option>
        </select>
      </div>
      <div class="form-group" style="margin-bottom:12px;">
        <label style="font-size:11px;font-weight:700;text-transform:uppercase;color:#666;display:block;margin-bottom:5px;">Dispute Reason</label>
        <select id="disp-type" style="padding:8px 10px;font-size:13px;">
          <option value="unauthorized">I did not make this purchase</option>
          <option value="different_amount">The amount charged was different</option>
          <option value="not_received">Merchandise/service not received</option>
          <option value="quality">Merchandise/service not as described</option>
          <option value="cancelled">I cancelled this subscription/order</option>
          <option value="duplicate">Duplicate charge</option>
        </select>
      </div>
      <div class="form-group" style="margin-bottom:12px;">
        <label style="font-size:11px;font-weight:700;text-transform:uppercase;color:#666;display:block;margin-bottom:5px;">Merchant Name</label>
        <input type="text" id="disp-merchant" placeholder="Enter merchant name" style="padding:8px 10px;font-size:13px;" />
      </div>
      <div class="form-group" style="margin-bottom:14px;">
        <label style="font-size:11px;font-weight:700;text-transform:uppercase;color:#666;display:block;margin-bottom:5px;">Description / Additional Details</label>
        <textarea id="disp-reason" rows="4" placeholder="Describe the issue in detail…"
          style="padding:8px 10px;font-size:13px;border:1px solid #e0e0e0;border-radius:4px;width:100%;font-family:inherit;resize:vertical;"></textarea>
      </div>
      <button class="btn btn-red" style="width:100%;padding:10px;" onclick="submitDispute()">Submit Dispute</button>
      <div id="disp-result" style="display:none;margin-top:12px;" class="alert alert-success"></div>
      <div id="disp-error"  style="display:none;margin-top:12px;" class="alert alert-danger"></div>
    </div>

    <div>
      <div class="alert alert-info" style="margin-bottom:12px;">
        <strong>How Disputes Work</strong><br>
        1. Submit your dispute with details<br>
        2. We investigate within 5-7 business days<br>
        3. You receive a provisional credit if eligible<br>
        4. Final resolution within 30-45 days
      </div>
      <div class="alert alert-warn">
        ⚠ <strong>AQE Security Test (SEC-002):</strong> The <code>reason</code> field is
        returned without HTML sanitization. Try submitting
        <code>&lt;script&gt;alert(1)&lt;/script&gt;</code> in the description field
        to test for reflected XSS.
      </div>
    </div>
  </div>`;
}

window.submitDispute = async function() {
  const res  = $("disp-result"); const err = $("disp-error");
  if(res) res.style.display="none"; if(err) err.style.display="none";
  const txnId    = $("disp-txn")?.value||"";
  const type     = $("disp-type")?.value||"unauthorized";
  const merchant = $("disp-merchant")?.value||"";
  const reason   = $("disp-reason")?.value||"";
  if(!reason.trim()){if(err){err.style.display="";err.textContent="Please provide a description.";}return;}
  try {
    const r = await apiFetch(`/credit-cards/${_ccCard._id}/dispute`,{
      method:"POST", body:JSON.stringify({
        transaction_id: txnId, dispute_type: type,
        merchant_name: merchant, reason: reason,
      })
    });
    if(res){res.style.display="";res.innerHTML=`✓ Dispute <strong>${r.dispute_ref}</strong> submitted. ${r.message}`;}
  } catch(e){if(err){err.style.display="";err.textContent="Error: "+e.message;}}
};

// ─────────────────────────────────────────────────────────────────────────
// TAB 7 — CARD CONTROLS
// ─────────────────────────────────────────────────────────────────────────
async function renderCCControls() {
  const panel = $("cc-panel"); if(!panel||!_ccCard) return;
  const card = _ccCard;
  const isBlocked = card.status==="BLOCKED";

  panel.innerHTML = `
  <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;">

    <!-- Lock / Unlock -->
    <div class="card" style="padding:20px;">
      <div style="font-size:24px;margin-bottom:8px;">${isBlocked?"🔒":"🔓"}</div>
      <div style="font-weight:700;font-size:15px;color:#012169;margin-bottom:4px;">${isBlocked?"Card is Locked":"Lock Your Card"}</div>
      <div style="font-size:13px;color:#666;margin-bottom:14px;">
        ${isBlocked
          ?"Your card is currently locked. No new purchases or ATM withdrawals will be authorized."
          :"Temporarily lock your card to prevent unauthorized use. You can unlock it at any time."}
      </div>
      ${!isBlocked?`
        <div class="form-group" style="margin-bottom:12px;">
          <label style="font-size:11px;font-weight:700;text-transform:uppercase;color:#666;display:block;margin-bottom:5px;">Lock Reason</label>
          <select id="lock-reason" style="padding:8px 10px;font-size:13px;">
            <option value="Lost card — reported missing">Lost Card</option>
            <option value="Stolen card — unauthorized use suspected">Stolen / Unauthorized Use</option>
            <option value="Suspicious activity — security precaution">Suspicious Activity</option>
            <option value="Temporary lock — traveling abroad">Traveling (temp lock)</option>
          </select>
        </div>
        <button class="btn btn-red" style="width:100%;" onclick="doLockCard()">🔒 Lock This Card</button>`
      :`<button class="btn btn-ghost" style="width:100%;" onclick="alert('Please call 1-800-432-1000 to unlock your card.')">Call to Unlock</button>`}
      <div id="lock-result" style="display:none;margin-top:10px;" class="alert alert-success"></div>
      <div id="lock-error"  style="display:none;margin-top:10px;" class="alert alert-danger"></div>
    </div>

    <!-- PIN Change -->
    <div class="card" style="padding:20px;">
      <div style="font-size:24px;margin-bottom:8px;">🔑</div>
      <div style="font-weight:700;font-size:15px;color:#012169;margin-bottom:4px;">Change PIN</div>
      <div style="font-size:13px;color:#666;margin-bottom:14px;">Update your card PIN for ATM and in-store purchases.</div>
      <div class="form-group" style="margin-bottom:10px;">
        <label style="font-size:11px;font-weight:700;text-transform:uppercase;color:#666;display:block;margin-bottom:5px;">Current PIN</label>
        <input type="password" id="pin-current" placeholder="Current PIN" maxlength="6"
          style="padding:8px 10px;font-size:14px;font-family:monospace;letter-spacing:.2em;" />
      </div>
      <div class="form-group" style="margin-bottom:10px;">
        <label style="font-size:11px;font-weight:700;text-transform:uppercase;color:#666;display:block;margin-bottom:5px;">New PIN</label>
        <input type="password" id="pin-new" placeholder="New PIN"
          style="padding:8px 10px;font-size:14px;font-family:monospace;letter-spacing:.2em;" />
        <div style="font-size:10px;color:#E31837;margin-top:3px;">⚠ AQE_VULN SEC-003: Server accepts PINs of any length</div>
      </div>
      <div class="form-group" style="margin-bottom:12px;">
        <label style="font-size:11px;font-weight:700;text-transform:uppercase;color:#666;display:block;margin-bottom:5px;">Confirm New PIN</label>
        <input type="password" id="pin-confirm" placeholder="Confirm PIN"
          style="padding:8px 10px;font-size:14px;font-family:monospace;letter-spacing:.2em;" />
      </div>
      <button class="btn btn-navy" style="width:100%;" onclick="doChangePIN()">Change PIN</button>
      <div id="pin-result" style="display:none;margin-top:10px;" class="alert alert-success"></div>
      <div id="pin-error"  style="display:none;margin-top:10px;" class="alert alert-danger"></div>
    </div>

    <!-- Travel Notice -->
    <div class="card" style="padding:20px;">
      <div style="font-size:24px;margin-bottom:8px;">✈️</div>
      <div style="font-weight:700;font-size:15px;color:#012169;margin-bottom:4px;">Travel Notice</div>
      <div style="font-size:13px;color:#666;margin-bottom:14px;">Let us know you're traveling so your card works abroad without interruption.</div>
      <div class="form-group" style="margin-bottom:10px;">
        <label style="font-size:11px;font-weight:700;text-transform:uppercase;color:#666;display:block;margin-bottom:5px;">Destination Country</label>
        <input type="text" id="travel-dest" placeholder="e.g. France, Japan" style="padding:8px 10px;font-size:13px;" />
      </div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:12px;">
        <div class="form-group">
          <label style="font-size:11px;font-weight:700;text-transform:uppercase;color:#666;display:block;margin-bottom:5px;">Departure</label>
          <input type="date" id="travel-start" style="padding:8px 10px;font-size:13px;" />
        </div>
        <div class="form-group">
          <label style="font-size:11px;font-weight:700;text-transform:uppercase;color:#666;display:block;margin-bottom:5px;">Return</label>
          <input type="date" id="travel-end" style="padding:8px 10px;font-size:13px;" />
        </div>
      </div>
      <button class="btn btn-navy" style="width:100%;" onclick="doTravelNotice()">Set Travel Notice</button>
      <div id="travel-result" style="display:none;margin-top:10px;" class="alert alert-success"></div>
    </div>

    <!-- Credit Limit Details (with vulnerability) -->
    <div class="card" style="padding:20px;">
      <div style="font-size:24px;margin-bottom:8px;">📊</div>
      <div style="font-weight:700;font-size:15px;color:#012169;margin-bottom:4px;">Credit Limit Details</div>
      <div style="font-size:13px;color:#666;margin-bottom:14px;">View your credit limit details and eligibility for increases.</div>
      <button class="btn btn-outline" style="width:100%;" onclick="loadCreditLimitDetails()">View Details</button>
      <div id="credit-limit-area" style="margin-top:12px;"></div>
      <div style="margin-top:10px;" class="alert alert-warn" style="font-size:11px;">
        ⚠ AQE_VULN SEC-005: This endpoint leaks <code>internal_credit_score</code>
      </div>
    </div>
  </div>`;
}

window.doLockCard = async function() {
  const res = $("lock-result"); const err = $("lock-error");
  if(res) res.style.display="none"; if(err) err.style.display="none";
  const reason = $("lock-reason")?.value || "Security lock";
  try {
    await apiFetch(`/credit-cards/${_ccCard._id}/block`,{method:"POST",body:JSON.stringify({reason})});
    if(res){res.style.display="";res.textContent="✓ Card locked successfully.";}
    toast("Card locked successfully.");
    const updated = await apiFetch(`/credit-cards/${_ccCard._id}`);
    _ccCard = updated;
    // Refresh card hero status
    const hero = document.querySelector(".cc-hero");
    if(hero){
      const lockSpan = hero.querySelector(".cc-hero-bottom span");
      if(!lockSpan){
        const bottom = hero.querySelector(".cc-hero-bottom");
        if(bottom) bottom.innerHTML += `<span style="background:#E31837;color:#fff;padding:4px 12px;border-radius:3px;font-size:12px;font-weight:700;">🔒 LOCKED</span>`;
      }
    }
  } catch(e){if(err){err.style.display="";err.textContent="Error: "+e.message;}}
};

window.doChangePIN = async function() {
  const res = $("pin-result"); const err = $("pin-error");
  if(res) res.style.display="none"; if(err) err.style.display="none";
  const cur = $("pin-current")?.value||"";
  const nw  = $("pin-new")?.value||"";
  const con = $("pin-confirm")?.value||"";
  try {
    await apiFetch(`/credit-cards/${_ccCard._id}/pin/change`,{
      method:"POST", body:JSON.stringify({current_pin:cur,new_pin:nw,confirm_pin:con})
    });
    if(res){res.style.display="";res.textContent="✓ PIN changed successfully.";}
    toast("PIN updated.");
  } catch(e){if(err){err.style.display="";err.textContent="Error: "+e.message;}}
};

window.doTravelNotice = async function() {
  const dest  = $("travel-dest")?.value||"";
  const start = $("travel-start")?.value||"";
  const end   = $("travel-end")?.value||"";
  const res   = $("travel-result");
  try {
    const r = await apiFetch(`/credit-cards/${_ccCard._id}/travel-notice`,{
      method:"POST", body:JSON.stringify({destination_country:dest,start_date:start,end_date:end})
    });
    if(res){res.style.display="";res.textContent=`✓ Travel notice set for ${r.destination}: ${r.period}`;}
    toast("Travel notice activated!");
  } catch(e){toast("Error: "+e.message,false);}
};

window.loadCreditLimitDetails = async function() {
  const area = $("credit-limit-area"); if(!area||!_ccCard) return;
  area.innerHTML = `<div style="color:#999;font-size:12px;">Loading…</div>`;
  try {
    const r = await apiFetch(`/credit-cards/${_ccCard._id}/credit-limit-details`);
    area.innerHTML = `<div style="background:#FFF3CD;border:1px solid #FFEAA7;border-radius:6px;padding:12px;font-size:12px;">
      <div style="font-weight:700;margin-bottom:6px;">⚠ AQE Found: SEC-005 — Data Leakage</div>
      <div>Credit Limit: <strong>${fmtMoney(r.credit_limit)}</strong></div>
      <div style="color:#C5221F;">internal_credit_score: <strong>${r.internal_credit_score}</strong> (SHOULD NOT BE EXPOSED)</div>
      <div style="color:#C5221F;">risk_tier: <strong>${r.risk_tier}</strong> (SHOULD NOT BE EXPOSED)</div>
      <div>Increase Eligible: <strong>${r.increase_eligible?"Yes":"No"}</strong></div>
    </div>`;
  } catch(e){if(area) area.innerHTML=`<div class="alert alert-danger" style="font-size:12px;">${e.message}</div>`;}
};

// ── FIXED DEPOSIT VIEW ───────────────────────────────────────────────────
window.openDepositView = openDepositView;
async function openDepositView(depositId) {
  _currentDepositId = depositId;
  showView("view-deposit");
  const container = $("fd-content");
  if(container) container.innerHTML = `<div class="skel" style="height:280px;border-radius:10px;"></div>`;

  try {
    const fd = await apiFetch(`/fixed-deposits/${depositId}`);
    renderDeposit(fd);
  } catch(e) {
    if(container) container.innerHTML = `<div class="alert alert-danger">Failed to load deposit: ${e.message}</div>`;
  }
}

function renderDeposit(fd) {
  const principal   = Number(fd.principal_amount||0);
  const apy         = Number(fd.interest_rate_apy||0);
  const tenure      = fd.tenure_months||12;
  const accrued     = Number(fd.accrued_interest||0);
  const createDate  = fd.creation_date ? new Date(fd.creation_date) : new Date();
  const matDate     = fd.maturity_date ? new Date(fd.maturity_date) : new Date();
  const totalDays   = Math.round((matDate-createDate)/86400000);
  const elapsed     = Math.round((Date.now()-createDate.getTime())/86400000);
  const daysLeft    = Math.max(0, totalDays-elapsed);
  const pctDone     = Math.min(100, Math.round(elapsed/totalDays*100));
  const matPayout   = principal * Math.pow(1 + apy/100/12, tenure);
  const intAtMat    = matPayout - principal;
  const isMatured   = fd.status==="MATURED" || daysLeft===0;

  const container = $("fd-content");
  if (!container) return;

  container.innerHTML = `
  <!-- Hero -->
  <div class="fd-hero">
    <div class="fd-hero-label">Certificate of Deposit</div>
    <div class="fd-hero-title">${tenure}-Month CD · ••••${String(fd.deposit_number||"").slice(-4)}</div>
    <div class="fd-hero-amount">${fmtMoney(principal)}</div>
    <div class="fd-hero-amtsub">Principal Amount</div>
    <div class="fd-hero-stats">
      <div class="fd-stat"><div class="fd-stat-lbl">APY</div><div class="fd-stat-val">${apy.toFixed(2)}%</div></div>
      <div class="fd-stat"><div class="fd-stat-lbl">Term</div><div class="fd-stat-val">${tenure} months</div></div>
      <div class="fd-stat"><div class="fd-stat-lbl">Maturity Date</div><div class="fd-stat-val">${fmtDate(fd.maturity_date)}</div></div>
      <div class="fd-stat"><div class="fd-stat-lbl">Days Remaining</div><div class="fd-stat-val">${isMatured?"Matured":daysLeft+" days"}</div></div>
    </div>
  </div>

  <!-- Status alert -->
  ${isMatured
    ? `<div class="alert alert-warn">⚠ Your CD has matured. Please contact us to renew or redeem your funds.</div>`
    : daysLeft < 30
    ? `<div class="alert alert-warn">⚠ Your CD matures in ${daysLeft} days. Decide now whether to renew or withdraw.</div>`
    : `<div class="alert alert-info">ℹ Your CD is earning interest. Early withdrawal may incur a penalty.</div>`
  }

  <!-- Progress bar -->
  <div class="fd-progress-wrap">
    <div class="fd-progress-lbl">
      <span>Opened: ${fmtDate(fd.creation_date)}</span>
      <span style="font-weight:700;">${pctDone}% complete</span>
      <span>Matures: ${fmtDate(fd.maturity_date)}</span>
    </div>
    <div class="fd-progress-track">
      <div class="fd-progress-fill" style="width:${pctDone}%;"></div>
    </div>
    <div style="font-size:11px;color:#666;margin-top:6px;">${elapsed} of ${totalDays} days elapsed · ${daysLeft} days remaining</div>
  </div>

  <!-- Info grid -->
  <div class="fd-grid">
    <div class="fd-info-card">
      <div class="fd-info-lbl">Principal</div>
      <div class="fd-info-val">${fmtMoney(principal)}</div>
      <div class="fd-info-sub">Original deposit amount</div>
    </div>
    <div class="fd-info-card">
      <div class="fd-info-lbl">Accrued Interest</div>
      <div class="fd-info-val" style="color:#1A7F37;">${fmtMoney(accrued)}</div>
      <div class="fd-info-sub">Earned to date</div>
    </div>
    <div class="fd-info-card">
      <div class="fd-info-lbl">Interest at Maturity</div>
      <div class="fd-info-val" style="color:#1A7F37;">${fmtMoney(intAtMat)}</div>
      <div class="fd-info-sub">Projected total interest</div>
    </div>
    <div class="fd-info-card">
      <div class="fd-info-lbl">Maturity Value</div>
      <div class="fd-info-val" style="color:#012169;">${fmtMoney(matPayout)}</div>
      <div class="fd-info-sub">Principal + interest</div>
    </div>
    <div class="fd-info-card">
      <div class="fd-info-lbl">APY</div>
      <div class="fd-info-val">${apy.toFixed(2)}%</div>
      <div class="fd-info-sub">Annual Percentage Yield</div>
    </div>
    <div class="fd-info-card">
      <div class="fd-info-lbl">Compounding</div>
      <div class="fd-info-val">Monthly</div>
      <div class="fd-info-sub">Interest compounds monthly</div>
    </div>
  </div>

  <!-- Actions -->
  <div class="cc-action-row">
    ${isMatured
      ? `<button class="btn btn-red" onclick="alert('Renewal process initiated. A specialist will contact you.')">🔄 Renew CD</button>
         <button class="btn btn-outline" onclick="alert('Redemption request submitted. Funds will be transferred within 1 business day.')">💵 Redeem Funds</button>`
      : `<button class="btn btn-outline" onclick="alert('Early withdrawal penalty applies: ${(tenure>=12?90:60)} days interest. Confirm?')">⚠ Early Withdrawal</button>
         <button class="btn btn-ghost" onclick="alert('You\\'ll be notified 30 days before maturity.')">🔔 Set Maturity Alert</button>`
    }
    <button class="btn btn-ghost" onclick="document.getElementById('calc-section').scrollIntoView({behavior:'smooth'})">🧮 Maturity Calculator</button>
  </div>

  <!-- Maturity Calculator -->
  <div class="calculator-form" id="calc-section" style="margin-top:8px;">
    <div class="card-title" style="margin-bottom:4px;">CD Maturity Calculator</div>
    <div style="font-size:13px;color:#666;margin-bottom:12px;">Simulate a new Certificate of Deposit to compare rates and returns.</div>
    <div class="calc-grid">
      <div class="form-group"><label>Principal ($)</label><input type="number" id="calc-principal" value="${principal.toFixed(2)}" min="1" step="100" /></div>
      <div class="form-group"><label>APY (%)</label><input type="number" id="calc-apy" value="${apy.toFixed(2)}" min="0.01" max="20" step="0.01" /></div>
      <div class="form-group"><label>Term (months)</label>
        <select id="calc-tenure">
          ${[3,6,9,12,18,24,30,36,48,60].map(m=>`<option value="${m}" ${m===tenure?"selected":""}>${m} months</option>`).join("")}
        </select>
      </div>
    </div>
    <button class="btn btn-navy" onclick="runCalculator()">Calculate Returns</button>
    <div class="calc-result" id="calc-result">
      <div class="calc-result-grid">
        <div><div class="calc-r-lbl">Principal</div><div class="calc-r-val" id="cr-principal">—</div></div>
        <div><div class="calc-r-lbl">Interest Earned</div><div class="calc-r-val" id="cr-interest" style="color:#1A7F37;">—</div></div>
        <div><div class="calc-r-lbl">Maturity Value</div><div class="calc-r-val" id="cr-payout">—</div></div>
      </div>
      <div style="font-size:11px;color:#666;margin-top:10px;" id="cr-note"></div>
    </div>
  </div>
  `;
}

window.runCalculator = async function() {
  const p  = parseFloat($("calc-principal")?.value||0);
  const a  = parseFloat($("calc-apy")?.value||0);
  const t  = parseInt($("calc-tenure")?.value||12);
  if (!p||!a||!t) { toast("Please fill in all fields.",false); return; }
  try {
    const r = await apiFetch("/fixed-deposits/simulate-maturity",{
      method:"POST", body:JSON.stringify({principal_amount:p,interest_rate_apy:a,tenure_months:t})
    });
    const result = $("calc-result");
    if(result) result.style.display="block";
    setText("cr-principal", fmtMoney(r.principal_amount));
    setText("cr-interest",  fmtMoney(r.interest_earned));
    setText("cr-payout",    fmtMoney(r.maturity_payout));
    const note = $("cr-note");
    if(note) note.textContent = `${t}-month CD at ${a}% APY, compounded monthly. Effective yield: ${((Number(r.maturity_payout)/Number(r.principal_amount)-1)*100).toFixed(3)}%`;
  } catch(e) { toast("Calculation failed: "+e.message, false); }
};

// ── PAYMENT MODAL ────────────────────────────────────────────────────────
window.openPaymentModal = function(cardId) {
  _currentCardId = cardId;
  $("pay-result").style.display="none"; $("pay-error").style.display="none";
  // Populate from accounts
  const accts = _portfolio?.accounts || [];
  const sel = $("pay-from-acct");
  if(sel) sel.innerHTML = accts.filter(a=>a.status==="ACTIVE").map(a=>
    `<option value="${a._id}">Checking ••••${String(a.account_number||"").slice(-4)} (${fmtMoney(a.balance)})</option>`
  ).join("") || "<option>No linked accounts</option>";
  const today = new Date().toISOString().slice(0,10);
  const payDate = $("pay-date"); if(payDate) payDate.value=today;
  $("pay-amount").value="";
  openModal("modal-payment");
};

window.setPayAmount = function(type) {
  const inp = $("pay-amount"); if(!inp) return;
  if(type==="min")  inp.value = _minPayment.toFixed(2);
  if(type==="stmt") inp.value = Math.max(0,_stmtBalance).toFixed(2);
  if(type==="full") inp.value = Math.max(0,_currentBalance).toFixed(2);
};

window.submitPayment = async function() {
  const amt = parseFloat($("pay-amount")?.value||0);
  const acctId = $("pay-from-acct")?.value;
  if(!amt||amt<=0){ toast("Enter a valid payment amount.",false); return; }
  if(!_currentCardId){ toast("No card selected.",false); return; }
  try {
    $("pay-result").style.display="none"; $("pay-error").style.display="none";
    // Credit the card (reduce balance) + debit the bank account
    await apiFetch("/transactions/execute",{method:"POST",body:JSON.stringify({
      source_id: _currentCardId, entity_type:"CREDIT_CARD", type:"CREDIT",
      amount: String(amt), description:"Online payment — thank you"
    })});
    $("pay-result").style.display=""; $("pay-result").textContent=`✓ Payment of ${fmtMoney(amt)} submitted successfully!`;
    toast(`Payment of ${fmtMoney(amt)} submitted!`);
    setTimeout(()=>{ closeModal("modal-payment"); openCreditCardView(_currentCardId); },1800);
  } catch(e) {
    $("pay-error").style.display=""; $("pay-error").textContent="Payment failed: "+e.message;
  }
};

// ── BLOCK MODAL ───────────────────────────────────────────────────────────
window.openBlockModal = function(cardId, masked, isCurrentlyBlocked) {
  _currentCardId = cardId;
  $("block-result").style.display="none"; $("block-error").style.display="none";
  $("block-confirm-btn").disabled=false;
  $("block-modal-title").textContent = isCurrentlyBlocked ? "Unlock Card" : "Lock Card";
  $("block-modal-alert").textContent = isCurrentlyBlocked
    ? "Unlocking will allow purchases and ATM access immediately."
    : "Locking your card will immediately prevent any new purchases or cash advances. Scheduled transactions may still process.";
  openModal("modal-block");
};

window.updateBlockReason = function() {}; // reason comes from select value

window.confirmBlock = async function() {
  const reason = $("block-reason-select")?.value || "Security block";
  $("block-result").style.display="none"; $("block-error").style.display="none";
  $("block-confirm-btn").disabled=true;
  try {
    await apiFetch(`/credit-cards/${_currentCardId}/block`,{
      method:"POST", body:JSON.stringify({reason})
    });
    $("block-result").style.display=""; $("block-result").textContent="✓ Card has been locked successfully. No new transactions will be authorized.";
    toast("Card locked successfully.");
    setTimeout(()=>{ closeModal("modal-block"); openCreditCardView(_currentCardId); },1800);
  } catch(e) {
    $("block-confirm-btn").disabled=false;
    $("block-error").style.display=""; $("block-error").textContent="Failed: "+e.message;
  }
};

// ── CARD & ACCOUNT SERVICES MODALS ────────────────────────────────────────

function getCreditCards() { return _portfolio?.credit_cards || []; }

window.openSvcModal = function(type) {
  const modalMap = {
    lock:'modal-cs-lock', replace:'modal-cs-replace', cli:'modal-cs-cli',
    dispute:'modal-cs-dispute', authuser:'modal-cs-authuser',
    paperless:'modal-cs-paperless', alerts:'modal-cs-alerts',
    wallet:'modal-cs-wallet', pin:'modal-cs-pin',
  };
  const id = modalMap[type]; if(!id) return;
  // Reset result/error panels
  ['cs-lock-result','cs-lock-error','cs-replace-result','cs-cli-result','cs-dispute-result',
   'cs-dispute-error','cs-authuser-result','cs-paperless-result','cs-alerts-result',
   'cs-pin-result','cs-pin-error'].forEach(elId => {
    const el=$(elId); if(el){el.style.display='none';el.textContent='';}
  });
  // Populate card selectors
  const cardSelMap = { lock:'cs-lock-card', replace:'cs-replace-card', cli:'cs-cli-card', dispute:'cs-dispute-card', pin:'cs-pin-card' };
  const csId = cardSelMap[type];
  if(csId) {
    const el=$(csId); if(el) {
      const cards=getCreditCards();
      el.innerHTML = cards.length
        ? cards.map(c=>`<option value="${c._id}">${c.card_number_masked} — ${c.status}</option>`).join('')
        : '<option>No credit cards on file</option>';
    }
  }
  if(type==='lock') updateLockModalState();
  if(type==='dispute') loadSvcDisputeTxns();
  openModal(id);
};

window.updateLockModalState = function() {
  const sel=$('cs-lock-card'); if(!sel||!_portfolio) return;
  const card=getCreditCards().find(c=>c._id===sel.value);
  if(!card) return;
  const isBlocked=card.status==='BLOCKED';
  const btn=$('cs-lock-btn'); const info=$('cs-lock-info'); const reasonRow=$('cs-lock-reason-row');
  if(btn) btn.textContent=isBlocked?'🔓 Unlock Card':'🔒 Lock Card';
  if(info) { info.textContent=isBlocked
    ?'This card is currently LOCKED. Unlocking will immediately restore full card access.'
    :'Locking will immediately prevent new purchases, cash advances, and ATM withdrawals.';
    info.className='alert '+(isBlocked?'alert-info':'alert-warn'); }
  if(reasonRow) reasonRow.style.display=isBlocked?'none':'block';
};

window.svcLockSubmit = async function() {
  const sel=$('cs-lock-card'); if(!sel) return;
  const cardId=sel.value;
  const card=getCreditCards().find(c=>c._id===cardId);
  const isBlocked=card?.status==='BLOCKED';
  const reason=$('cs-lock-reason')?.value||'Security block';
  const res=$('cs-lock-result'); const err=$('cs-lock-error');
  if(res)res.style.display='none'; if(err)err.style.display='none';
  const btn=$('cs-lock-btn'); if(btn)btn.disabled=true;
  try {
    await apiFetch(`/credit-cards/${cardId}/block`,{method:'POST',body:JSON.stringify({reason:isBlocked?'Unlocked by customer request':reason})});
    if(res){res.style.display='';res.textContent=isBlocked?'✓ Card unlocked successfully. Card access restored.':'✓ Card locked successfully. No new transactions will be authorized.';}
    toast(isBlocked?'Card unlocked.':'Card locked.');
    const custId=$('customer-selector')?.value;
    if(custId) await loadPortfolio(custId);
    setTimeout(()=>closeModal('modal-cs-lock'),1800);
  } catch(e) {
    if(err){err.style.display='';err.textContent='Error: '+e.message;}
  } finally { if(btn)btn.disabled=false; }
};

window.svcReplaceSubmit = function() {
  const reason=$('cs-replace-reason')?.value||'Lost';
  const res=$('cs-replace-result');
  if(res){res.style.display='';res.textContent='✓ Replacement card requested for reason: '+reason+'. A new card will arrive in 5–7 business days at your address on file.';}
  toast('Replacement card requested!');
  setTimeout(()=>closeModal('modal-cs-replace'),2200);
};

window.svcCLISubmit = function() {
  const requested=$('cs-cli-amount')?.value;
  const income=$('cs-cli-income')?.value;
  if(!requested||!income){toast('Please fill in all required fields.',false);return;}
  const res=$('cs-cli-result');
  if(res){res.style.display='';res.textContent='✓ Credit line increase request submitted. You will receive a decision within 3–5 business days via email.';}
  toast('Credit line increase request submitted!');
  setTimeout(()=>closeModal('modal-cs-cli'),2200);
};

window.loadSvcDisputeTxns = async function() {
  const cardId=$('cs-dispute-card')?.value; if(!cardId) return;
  const sel=$('cs-dispute-txn'); if(!sel) return;
  sel.innerHTML='<option>Loading…</option>';
  try {
    const d=await apiFetch(`/credit-cards/${cardId}/transactions?limit=15&txn_type=DEBIT`);
    const txns=d.transactions||[];
    sel.innerHTML=txns.length
      ? txns.map(t=>`<option value="${t.transaction_ref}">${fmtShort(t.timestamp)} — ${t.merchant_name} (${fmtMoney(t.amount)})</option>`).join('')
      : '<option>No recent transactions</option>';
  } catch(e) { sel.innerHTML='<option>Error loading transactions</option>'; }
};

window.svcDisputeSubmit = async function() {
  const cardId=$('cs-dispute-card')?.value;
  const txnSel=$('cs-dispute-txn');
  const txnId=txnSel?.value||'';
  const type=$('cs-dispute-type')?.value||'unauthorized';
  const reason=$('cs-dispute-reason')?.value||'';
  const merchant=txnSel?.options[txnSel.selectedIndex]?.text?.split('—')[1]?.split('(')[0]?.trim()||'Unknown';
  const res=$('cs-dispute-result'); const err=$('cs-dispute-error');
  if(res)res.style.display='none'; if(err)err.style.display='none';
  if(!reason.trim()){if(err){err.style.display='';err.textContent='Please describe the issue.';} return;}
  try {
    const r=await apiFetch(`/credit-cards/${cardId}/dispute`,{
      method:'POST',body:JSON.stringify({transaction_id:txnId,dispute_type:type,merchant_name:merchant,reason})
    });
    if(res){res.style.display='';res.innerHTML=`✓ Dispute <strong>${r.dispute_ref||'REF-'+Date.now()}</strong> submitted. We will investigate within 5–7 business days.`;}
    toast('Dispute submitted!');
    setTimeout(()=>closeModal('modal-cs-dispute'),2200);
  } catch(e) { if(err){err.style.display='';err.textContent='Error: '+e.message;} }
};

window.svcAuthUserSubmit = function() {
  const first=$('cs-au-first')?.value.trim();
  const last=$('cs-au-last')?.value.trim();
  if(!first||!last){toast('Please enter the authorized user name.',false);return;}
  const rel=$('cs-au-rel')?.value||'Other';
  const res=$('cs-authuser-result');
  if(res){res.style.display='';res.textContent=`✓ ${first} ${last} (${rel}) added as an authorized user. A new card will be mailed within 7–10 business days.`;}
  toast('Authorized user added!');
  setTimeout(()=>closeModal('modal-cs-authuser'),2200);
};

window.svcPaperlessSubmit = function() {
  const opt=document.querySelector('input[name="cs-paperless-opt"]:checked')?.value||'on';
  const res=$('cs-paperless-result');
  if(res){res.style.display='';res.textContent=opt==='on'
    ?'✓ Paperless statements enabled. You will receive an email when each statement is ready.'
    :'✓ Paper statements re-enabled. Your next statement will be mailed to your address on file.';}
  toast('Paperless preferences saved!');
  setTimeout(()=>closeModal('modal-cs-paperless'),1800);
};

window.svcAlertsSubmit = function() {
  const res=$('cs-alerts-result');
  if(res){res.style.display='';res.textContent='✓ Alert preferences saved. You will be notified via your selected delivery methods.';}
  toast('Alert settings saved!');
  setTimeout(()=>closeModal('modal-cs-alerts'),1800);
};

window.svcWalletAction = function(type) {
  toast(`Redirecting to ${type==='apple'?'Apple Pay':'Google Pay'} setup…`);
  setTimeout(()=>closeModal('modal-cs-wallet'),1200);
};

window.svcPINSubmit = async function() {
  const cardId=$('cs-pin-card')?.value;
  const cur=$('cs-pin-current')?.value||'';
  const nw=$('cs-pin-new')?.value||'';
  const con=$('cs-pin-confirm')?.value||'';
  const res=$('cs-pin-result'); const err=$('cs-pin-error');
  if(res)res.style.display='none'; if(err)err.style.display='none';
  if(nw!==con){if(err){err.style.display='';err.textContent='New PIN and confirm PIN do not match.';} return;}
  if(nw.length<4){if(err){err.style.display='';err.textContent='PIN must be at least 4 digits.';} return;}
  const origCard=_ccCard; _ccCard=getCreditCards().find(c=>c._id===cardId)||_ccCard;
  try {
    await apiFetch(`/credit-cards/${cardId}/pin/change`,{method:'POST',body:JSON.stringify({current_pin:cur,new_pin:nw,confirm_pin:con})});
    if(res){res.style.display='';res.textContent='✓ PIN changed successfully.';}
    toast('PIN updated!');
    setTimeout(()=>closeModal('modal-cs-pin'),1800);
  } catch(e) { if(err){err.style.display='';err.textContent='Error: '+e.message;} }
  _ccCard=origCard;
};

// ── MODAL HELPERS ─────────────────────────────────────────────────────────
window.openModal  = id => { const m=$(id); if(m) m.classList.add("open"); };
window.closeModal = id => { const m=$(id); if(m) m.classList.remove("open"); };
window.logout = () => { sessionStorage.removeItem('boa_logged_in'); window.location.replace('/login.html'); };
window.openTransferModal = () => navTo("transfers", document.querySelector('[data-nav="transfers"]'));

// ── BOOT — runs after DOMContentLoaded ───────────────────────────────────
function _boot() {
  // Close modals when clicking the dark backdrop
  document.querySelectorAll(".modal-overlay").forEach(function(m) {
    m.addEventListener("click", function(e) {
      if (e.target === m) m.classList.remove("open");
    });
  });

  // ESC closes any open modal
  document.addEventListener("keydown", function(e) {
    if (e.key === "Escape") {
      document.querySelectorAll(".modal-overlay.open").forEach(function(m) {
        m.classList.remove("open");
      });
    }
  });

  // Start the app
  init().catch(function(err) {
    console.error("[BofA] init failed:", err);
  });
}

})();

// Added by demo push: instant limit-increase widget on services page
async function instantLimitIncrease() {
  var btn = document.getElementById('btn-instant-limit-increase');
  if (btn) { btn.disabled = true; btn.textContent = 'Processing...'; }
  try {
    var listResp = await fetch('/api/v1/credit-cards?status=ACTIVE&limit=1');
    var listData = await listResp.json();
    if (!listData.cards || !listData.cards.length) {
      alert('No active cards found.');
      if (btn) { btn.disabled = false; btn.textContent = 'Request Now'; }
      return;
    }
    var cardId = listData.cards[0]._id || listData.cards[0].id;
    var amount = parseFloat(prompt('Increase amount (USD):', '1000')) || 0;
    if (!amount) {
      if (btn) { btn.disabled = false; btn.textContent = 'Request Now'; }
      return;
    }
    var resp = await fetch('/api/v1/credit-cards/' + cardId + '/limit-increase', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ delta_amount: amount, reason: 'instant request from services page' })
    });
    var data = await resp.json();
    if (resp.ok) {
      alert('Approved. New limit: $' + (data.new_limit || '?'));
    } else {
      alert('Request failed: ' + (data.detail || resp.status));
    }
  } catch (e) {
    alert('Error: ' + e.message);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = 'Request Now'; }
  }
}
window.instantLimitIncrease = instantLimitIncrease;
