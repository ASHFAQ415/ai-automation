// Luxury Executive Client Dashboard Logic
const API_BASE = window.location.origin.includes('localhost') || window.location.origin.includes('127.0.0.1')
  ? window.location.origin
  : 'http://localhost:8000';

let API_KEY = '';

async function initAuth() {
  try {
    const res = await fetch(`${API_BASE}/api/auth/token`);
    if (res.ok) {
      const data = await res.json();
      API_KEY = data.api_key || '';
    }
  } catch (e) {
    console.warn('Could not fetch auth token:', e);
  }
}
initAuth();

function getAuthHeaders(extraHeaders = {}) {
  const headers = { 'Content-Type': 'application/json', ...extraHeaders };
  if (API_KEY) {
    headers['X-Api-Key'] = API_KEY;
  }
  return headers;
}

let activePresetKey = null;
let currentRadarLead = null;

const PRESETS = {
  real_estate: {
    botName: 'Real Estate Concierge',
    clientName: 'Commercial Realty Prospect',
    company: 'Metropolitan Properties',
    budget: '₹75,000',
    timeline: 'Within 7 days',
    customerInquiry: 'We receive dozens of inquiries daily for 3BHK and 4BHK apartments. We need an automated bot to qualify buyer budgets, share brochures, and schedule site visits. Budget is around 75k.'
  },
  coaching: {
    botName: 'Admissions Concierge',
    clientName: 'EdTech Admissions Lead',
    company: 'Apex Education Group',
    budget: '₹45,000',
    timeline: 'Immediate',
    customerInquiry: 'Admissions for our entrance exam crash courses are starting. We need an automated lead intake to score student marks, calculate scholarship discounts, and send fee proposals. Budget is around 45k.'
  },
  clinic: {
    botName: 'Healthcare Booking Concierge',
    clientName: 'Healthcare Lead',
    company: 'CityCare Medical Centre',
    budget: '₹35,000',
    timeline: 'Immediate',
    customerInquiry: 'We are missing patient calls after 8 PM. We need an automated WhatsApp system to book dental and medical consults, send appointment reminders, and sync with doctor calendars. Budget is around 35k.'
  },
  ecommerce: {
    botName: 'E-Commerce Concierge',
    clientName: 'Retail Brand Manager',
    company: 'UrbanCraft Direct',
    budget: '₹60,000',
    timeline: 'Immediate',
    customerInquiry: 'We have high abandoned cart rates on Shopify. We want an automated WhatsApp recovery workflow that sends personalized discount vouchers to buyers who drop off at checkout. Budget around 60k.'
  }
};

// View Switcher
function switchView(viewName, btn) {
  document.querySelectorAll('.lux-tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.view-panel').forEach(p => p.classList.remove('active'));

  if (btn) btn.classList.add('active');
  const target = document.getElementById(`view-${viewName}`);
  if (target) target.classList.add('active');

  if (viewName === 'pipeline') loadApprovals();
  if (viewName === 'deliverables') loadDeliverables();
}

// Preset Loader: Dispatches real prompt to Qwen3 4B
async function loadPreset(key, btn) {
  activePresetKey = key;
  document.querySelectorAll('.preset-chip').forEach(c => c.classList.remove('active'));
  if (btn) btn.classList.add('active');

  const p = PRESETS[key];
  if (!p) return;

  // Update phone header
  document.getElementById('sim-bot-name').innerText = p.botName;

  // Render chat messages in phone
  const chat = document.getElementById('wa-messages');
  const now = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  chat.innerHTML = `
    <div class="wa-bubble incoming">
      <p>Hello! Welcome to our automated business concierge. How can we help you today?</p>
      <span class="wa-time">${now}</span>
    </div>
    <div class="wa-bubble outgoing">
      <p>${p.customerInquiry}</p>
      <span class="wa-time">${now}</span>
    </div>
    <div class="wa-bubble incoming" id="wa-analyzing-msg">
      <p>⚡ <em>Analyzing requirement with Qwen3 4B on local GPU...</em></p>
      <span class="wa-time">${now}</span>
    </div>
  `;
  chat.scrollTop = chat.scrollHeight;

  // Set Radar Card into Loading State
  setRadarLoadingState({
    name: p.clientName,
    company: p.company,
    budget: p.budget,
    timeline: p.timeline
  });

  // Submit to real backend AI engine
  try {
    const res = await fetch(`${API_BASE}/api/leads`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        name: p.clientName,
        company: p.company,
        requirement: p.customerInquiry,
        budget: p.budget,
        timeline: p.timeline,
        source: 'whatsapp'
      })
    });

    if (res.ok) {
      const lead = await res.json();
      pollLeadUntilScored(lead.id);
    }
  } catch (err) {
    console.error('Submission error:', err);
  }
}

// Radar Loading State
function setRadarLoadingState(info) {
  document.getElementById('radar-name').innerText = info.name;
  document.getElementById('radar-company').innerText = info.company;
  document.getElementById('radar-budget').innerText = info.budget;
  document.getElementById('radar-timeline').innerText = info.timeline;

  const scorePill = document.getElementById('radar-score-pill');
  scorePill.className = 'score-pill-placeholder';
  scorePill.innerHTML = `⚡ QUALIFYING (QWEN3 4B GPU)...`;

  document.getElementById('radar-rationale').innerText = 'Local LLM (Qwen3 4B) is performing commercial intent analysis and requirement synthesis...';
  document.getElementById('radar-blueprint-title').innerText = '📦 Synthesizing Full-Stack Blueprint...';
  document.getElementById('radar-features').innerHTML = `
    <li>Evaluating commercial intent and budget viability</li>
    <li>Synthesizing full-stack deliverable architecture</li>
  `;

  const actBtn = document.getElementById('btn-radar-action');
  if (actBtn) actBtn.style.display = 'none';
}

// Poll lead until Qwen3 4B finishes scoring and planning
async function pollLeadUntilScored(leadId, attempts = 0) {
  if (attempts > 60) return; // 120s headroom

  try {
    const res = await fetch(`${API_BASE}/api/leads/${leadId}`);
    if (res.ok) {
      const lead = await res.json();
      if (lead.score && lead.score > 0) {
        applyRealScoringToRadar(lead);
        loadStats();
        return;
      }
    }
  } catch (err) {
    console.error('Polling error:', err);
  }

  setTimeout(() => pollLeadUntilScored(leadId, attempts + 1), 2000);
}

// Force-generate plan for leads that were not automatically drafted
async function forceGeneratePlan(leadId) {
  const actBtn = document.getElementById('btn-radar-action');
  if (actBtn) actBtn.innerText = '⚡ Drafting Architecture & Plan...';
  try {
    const res = await fetch(`${API_BASE}/api/leads/${leadId}/generate-plan`, { method: 'POST' });
    if (res.ok) {
      const lead = await res.json();
      applyRealScoringToRadar(lead);
      switchView('pipeline', document.querySelectorAll('.lux-tab')[1]);
    }
  } catch (err) {
    console.error('Plan generation failed:', err);
  }
}

// Apply genuine Qwen3 4B scoring and plan to Radar Card
function applyRealScoringToRadar(lead) {
  currentRadarLead = lead;

  document.getElementById('radar-name').innerText = lead.name;
  document.getElementById('radar-company').innerText = lead.company || 'Enterprise';
  document.getElementById('radar-budget').innerText = lead.budget || 'Flexible';
  document.getElementById('radar-timeline').innerText = lead.timeline || 'Immediate';

  // Score Pill
  const scorePill = document.getElementById('radar-score-pill');
  scorePill.className = 'score-pill';
  if (lead.temperature === 'HOT') {
    scorePill.innerHTML = `🔥 ${lead.score}/100 HOT DEAL`;
  } else if (lead.temperature === 'WARM') {
    scorePill.innerHTML = `⚡ ${lead.score}/100 WARM LEAD`;
  } else {
    scorePill.innerHTML = `❄️ ${lead.score}/100 COLD DEAL`;
  }

  // Real Rationale
  let rationaleText = 'High-intent lead processed by autonomous engine.';
  if (lead.ai_analysis) {
    try {
      const analysis = typeof lead.ai_analysis === 'string' ? JSON.parse(lead.ai_analysis) : lead.ai_analysis;
      if (analysis.reason) rationaleText = analysis.reason;
    } catch (e) {}
  }
  document.getElementById('radar-rationale').innerText = rationaleText;

  // Real Blueprint
  const actBtn = document.getElementById('btn-radar-action');
  if (lead.plan) {
    document.getElementById('radar-blueprint-title').innerText = `📦 ${lead.plan.title}`;
    let features = [];
    try {
      features = typeof lead.plan.core_features === 'string' ? JSON.parse(lead.plan.core_features) : lead.plan.core_features || [];
    } catch (e) {}
    if (features.length) {
      document.getElementById('radar-features').innerHTML = features.map(f => `<li>${f}</li>`).join('');
    }
    if (actBtn) {
      actBtn.style.display = 'block';
      actBtn.innerText = '📋 View Implementation Plan & Approve Build ➔';
      actBtn.onclick = () => switchView('pipeline', document.querySelectorAll('.lux-tab')[1]);
    }
  } else {
    document.getElementById('radar-blueprint-title').innerText = '📦 Ready to Build Architecture';
    document.getElementById('radar-features').innerHTML = `
      <li>Detected Needs: Generic Utility / App Request</li>
      <li>Click below to draft plan & start autonomous build</li>
    `;
    if (actBtn) {
      actBtn.style.display = 'block';
      actBtn.innerText = '🚀 Generate Implementation Plan & Build Now ➔';
      actBtn.onclick = () => forceGeneratePlan(lead.id);
    }
  }

  // Update phone chat with bot response
  const analyzingMsg = document.getElementById('wa-analyzing-msg');
  if (analyzingMsg) {
    const now = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    analyzingMsg.innerHTML = `
      <p>Requirement evaluated (${lead.score}/100 ${lead.temperature}). Implementation plan is ready for review!</p>
      <span class="wa-time">${now}</span>
    `;
  }

  if (actBtn) {
    actBtn.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }
}


// Send custom message in phone
async function sendPhoneMessage(e) {
  e.preventDefault();
  const input = document.getElementById('sim-message-input');
  const text = input.value.trim();
  if (!text) return;
  input.value = '';

  const chat = document.getElementById('wa-messages');
  const now = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  chat.innerHTML += `
    <div class="wa-bubble outgoing">
      <p>${text}</p>
      <span class="wa-time">${now}</span>
    </div>
    <div class="wa-bubble incoming" id="wa-analyzing-msg">
      <p>⚡ <em>Analyzing requirement with Qwen3 4B on local GPU...</em></p>
      <span class="wa-time">${now}</span>
    </div>
  `;
  chat.scrollTop = chat.scrollHeight;

  setRadarLoadingState({
    name: 'Direct Inbound Prospect',
    company: 'Custom Enterprise Lead',
    budget: 'Evaluated by AI',
    timeline: 'Evaluated by AI'
  });

  // Submit to real API
  try {
    const res = await fetch(`${API_BASE}/api/leads`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        name: 'Direct Inbound Prospect',
        company: 'Custom Enterprise Lead',
        requirement: text,
        budget: 'Evaluated by AI',
        timeline: 'Immediate',
        source: 'whatsapp'
      })
    });

    if (res.ok) {
      const lead = await res.json();
      pollLeadUntilScored(lead.id);
    }
  } catch (err) {
    console.error('Submission error:', err);
  }
}

// Proceed to Approval view from Radar
function proceedToApprovalFromRadar() {
  switchView('pipeline', document.querySelectorAll('.lux-tab')[1]);
}

// Approvals & Pipeline
async function loadApprovals() {
  const container = document.getElementById('approvals-list');
  try {
    const res = await fetch(`${API_BASE}/api/leads`);
    const leads = await res.json();
    const pending = leads.filter(l => l.plan && (l.status === 'PLAN_PENDING' || l.status === 'PLAN_APPROVED'));

    if (!pending.length) {
      container.innerHTML = `<div class="empty-state">No pending deals in approval queue. All qualified deals are built or approved!</div>`;
      return;
    }

    container.innerHTML = pending.map(l => {
      const plan = l.plan;
      let features = [];
      let tech = [];
      let steps = [];
      try { features = typeof plan.core_features === 'string' ? JSON.parse(plan.core_features) : plan.core_features || []; } catch (e) {}
      try { tech = typeof plan.tech_stack === 'string' ? JSON.parse(plan.tech_stack) : plan.tech_stack || []; } catch (e) {}
      try { steps = typeof plan.implementation_steps === 'string' ? JSON.parse(plan.implementation_steps) : plan.implementation_steps || []; } catch (e) {}

      return `
        <div class="pipeline-card">
          <div class="pipe-top">
            <div class="pipe-title">
              <h3>${plan.title}</h3>
              <div class="pipe-meta-sub">Client: <strong>${l.name}</strong> (${l.company || 'Enterprise'}) · Budget: <span class="text-cyan">${l.budget}</span></div>
            </div>
            <span class="score-pill">🔥 ${l.score}/100 HOT DEAL</span>
          </div>

          <div class="pipe-body">
            <div>
              <h5 style="font-size: 11px; color: var(--text-dim); text-transform: uppercase; margin-bottom: 8px;">TARGET DELIVERABLE & CORE CAPABILITIES</h5>
              <div style="font-size: 15px; font-weight: 700; color: var(--neon-cyan); margin-bottom: 10px;">${plan.target_deliverable}</div>
              <ul style="list-style: none; font-size: 13px; color: #cbd5e1; line-height: 1.7;">
                ${features.map(f => `<li style="position: relative; padding-left: 18px;"><span style="position: absolute; left: 0; color: var(--neon-emerald); font-weight: 800;">✓</span> ${f}</li>`).join('')}
              </ul>
            </div>
            <div>
              <h5 style="font-size: 11px; color: var(--text-dim); text-transform: uppercase; margin-bottom: 8px;">TECHNOLOGY STACK</h5>
              <div style="display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 14px;">
                ${tech.map(t => `<span class="feature-tag-pill">${t}</span>`).join('')}
              </div>
              <h5 style="font-size: 11px; color: var(--text-dim); text-transform: uppercase; margin-bottom: 6px;">ARCHITECTURE STRATEGY</h5>
              <p style="font-size: 13px; color: var(--text-muted); line-height: 1.5;">${plan.architecture_overview || 'High-performance local-first architecture with autonomous failover.'}</p>
            </div>
          </div>

          <div class="roadmap-box">
            <h5 style="font-size: 11px; color: var(--text-dim); text-transform: uppercase; margin-bottom: 10px;">CONSTRUCTION ROADMAP (${steps.length} Autonomous Steps)</h5>
            ${steps.map(s => `
              <div class="roadmap-step">
                <span class="step-idx">${s.step || '•'}</span>
                <span><strong>${s.title}:</strong> ${s.description}</span>
                <span style="margin-left: auto; font-family: 'JetBrains Mono', monospace; font-size: 11px; color: var(--neon-cyan);">${(s.files || []).join(', ')}</span>
              </div>
            `).join('')}
          </div>

          <div class="pipe-actions">
            ${l.status === 'PLAN_PENDING' ? `
              <button class="btn-act-approve" onclick="approvePlan(${l.id})">✓ Approve Implementation Plan</button>
              <button class="btn-act-reject" onclick="rejectPlan(${l.id})">✕ Reject</button>
            ` : `
              <button class="btn-act-build" onclick="triggerBuild(${l.id})">🚀 Execute Autonomous Build Now</button>
              <span style="align-self: center; font-size: 13px; color: var(--neon-emerald); font-weight: 700;">✓ Plan Approved · Ready to construct client codebase</span>
            `}
          </div>
        </div>
      `;
    }).join('');
  } catch (err) {
    container.innerHTML = `<div class="empty-state">Error loading pipeline: ${err.message}</div>`;
  }
}

async function approvePlan(leadId) {
  try {
    const res = await fetch(`${API_BASE}/api/approvals/${leadId}/plan`, {
      method: 'POST',
      headers: getAuthHeaders(),
      body: JSON.stringify({ decision: 'APPROVED' })
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      alert(`Approval error: ${err.detail || 'Failed to approve plan'}`);
    }
  } catch (e) {
    alert(`Network error: ${e.message}`);
  }
  loadApprovals();
  loadStats();
}

async function rejectPlan(leadId) {
  try {
    const res = await fetch(`${API_BASE}/api/approvals/${leadId}/plan`, {
      method: 'POST',
      headers: getAuthHeaders(),
      body: JSON.stringify({ decision: 'REJECTED' })
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      alert(`Reject error: ${err.detail || 'Failed to reject plan'}`);
    }
  } catch (e) {
    alert(`Network error: ${e.message}`);
  }
  loadApprovals();
  loadStats();
}

async function triggerBuild(leadId) {
  try {
    const res = await fetch(`${API_BASE}/api/approvals/${leadId}/build`, {
      method: 'POST',
      headers: getAuthHeaders(),
      body: JSON.stringify({})
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      alert(`Build trigger error: ${err.detail || 'Failed to trigger build'}`);
    } else {
      switchView('deliverables', document.querySelectorAll('.lux-tab')[2]);
      loadDeliverables();
    }
  } catch (e) {
    alert(`Network error: ${e.message}`);
  }
}

// Deliverables: Render genuine built project details
async function loadDeliverables() {
  const container = document.getElementById('deliverables-list');
  try {
    const res = await fetch(`${API_BASE}/api/projects`);
    const projects = await res.json();

    if (!projects.length) {
      container.innerHTML = `<div class="empty-state">No turnkey deliverables built yet. Authorize an implementation plan in Deal Pipeline to construct software!</div>`;
      return;
    }

    container.innerHTML = projects.map(p => {
      let files = [];
      try { files = typeof p.generated_files === 'string' ? JSON.parse(p.generated_files) : p.generated_files || []; } catch (e) {}

      return `
        <div class="d-card">
          <div class="d-top">
            <div class="d-title">
              <h3>${p.project_name}</h3>
              <div class="d-client">Deliverable Location: <span style="font-family: 'JetBrains Mono'; font-size: 11.5px; color: var(--neon-cyan);">${p.project_path}</span></div>
            </div>
            <span class="d-status-badge">
              ${p.status === 'COMPLETED' ? '✓ READY TO DELIVER' : `BUILDING (${p.progress_percent}%)`}
            </span>
          </div>

          <!-- Progress Bar for Active Builds -->
          ${p.status !== 'COMPLETED' ? `
            <div class="build-progress-bar-wrap">
              <div class="build-progress-bar-fill" style="width: ${p.progress_percent}%;"></div>
            </div>
            <div style="font-size: 11.5px; color: var(--neon-cyan); font-family: 'JetBrains Mono'; margin-bottom: 12px;">
              ⚡ Autonomous Builder is executing on RTX 3050 GPU · ${p.progress_percent}% Complete
            </div>
          ` : ''}

          <!-- Live Build Terminal / Console -->
          <div class="build-terminal">
            <div class="terminal-header">
              <span class="terminal-dot red"></span>
              <span class="terminal-dot yellow"></span>
              <span class="terminal-dot green"></span>
              <span class="terminal-title">AUTONOMOUS BUILD LOGS & COMPILER STREAM</span>
              <span style="margin-left: auto; font-family: 'JetBrains Mono'; font-size: 10.5px; color: var(--text-dim);">${p.status}</span>
            </div>
            <pre class="terminal-body"><code>${p.build_logs || 'Initializing build environment on GPU...'}</code></pre>
          </div>

          <!-- Generated Artifacts Chips -->
          <div class="d-files-box">
            <div class="d-files-header">Turnkey Code Artifacts (${files.length} Complete Files)</div>
            <div class="d-file-chips">
              ${files.length ? files.map(f => `<button class="d-chip" onclick="openFilePreview(${p.id}, '${f}')">📄 ${f}</button>`).join('') : '<span style="font-size: 12px; color: var(--text-dim);">Files will populate as each step completes...</span>'}
            </div>
          </div>

          <!-- Action Buttons -->
          <div class="d-actions">
            ${p.status === 'COMPLETED' ? `
              <button class="btn-zip" onclick="downloadProjectZip(${p.id})">
                📦 Download Turnkey ZIP Package
              </button>
              <button class="btn-preview" onclick="openFilePreview(${p.id}, 'index.html')">
                ⚡ Inspect UI
              </button>
              <button class="btn-preview" onclick="openFilePreview(${p.id}, 'main.py')">
                💻 Inspect Backend
              </button>
              <button class="btn-preview" style="color: var(--neon-coral); border-color: rgba(255, 71, 87, 0.4);" onclick="deleteProject(${p.id})">
                🗑 Delete Project
              </button>
            ` : p.status === 'CANCELLED' ? `
              <span style="font-size: 13px; color: var(--neon-coral); font-weight: 700; align-self: center;">✕ Build Cancelled</span>
              <button class="btn-preview" style="color: var(--neon-coral); border-color: rgba(255, 71, 87, 0.4);" onclick="deleteProject(${p.id})">
                🗑 Remove Project
              </button>
            ` : `
              <button class="btn-zip" disabled style="opacity: 0.6; cursor: wait;">
                ⏳ Building Codebase (${p.progress_percent}%)...
              </button>
              <button class="btn-preview" style="color: var(--neon-coral); border-color: rgba(255, 71, 87, 0.5); font-weight: 700;" onclick="cancelBuild(${p.id})">
                ✕ Cancel Build
              </button>
            `}
          </div>
        </div>
      `;
    }).join('');

    // If any build is in progress, automatically poll logs every 2.5s
    const hasActiveBuild = projects.some(p => p.status !== 'COMPLETED' && p.status !== 'CANCELLED');
    if (hasActiveBuild) {
      setTimeout(loadDeliverables, 2500);
    }
  } catch (err) {
    container.innerHTML = `<div class="empty-state">Error loading deliverables: ${err.message}</div>`;
  }
}

async function cancelBuild(buildId) {
  if (!confirm('Are you sure you want to cancel this autonomous build?')) return;
  try {
    const res = await fetch(`${API_BASE}/api/projects/${buildId}/cancel`, {
      method: 'POST',
      headers: getAuthHeaders()
    });
    if (res.ok) {
      loadDeliverables();
      loadStats();
    } else {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      alert('Failed to cancel build: ' + (err.detail || res.statusText));
    }
  } catch (err) {
    alert('Failed to cancel build: ' + err.message);
  }
}

async function deleteProject(buildId) {
  if (!confirm('Are you sure you want to permanently delete this project and all its files from disk?')) return;
  try {
    const res = await fetch(`${API_BASE}/api/projects/${buildId}`, {
      method: 'DELETE',
      headers: getAuthHeaders()
    });
    if (res.ok) {
      loadDeliverables();
      loadStats();
    } else {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      alert('Failed to delete project: ' + (err.detail || res.statusText));
    }
  } catch (err) {
    alert('Failed to delete project: ' + err.message);
  }
}

function downloadProjectZip(buildId) {
  window.open(`${API_BASE}/api/projects/${buildId}/download-zip`, '_blank');
}


// Code Preview Modal
async function openFilePreview(buildId, fileName) {
  const modal = document.getElementById('file-modal');
  document.getElementById('modal-filename').innerText = fileName;
  document.getElementById('modal-code').innerText = 'Loading source code from project directory...';
  modal.classList.add('active');

  try {
    const res = await fetch(`${API_BASE}/api/projects/${buildId}/file?file_name=${encodeURIComponent(fileName)}`);
    const data = await res.json();
    document.getElementById('modal-filesize').innerText = `${data.size} bytes`;
    document.getElementById('modal-code').innerText = data.content;
  } catch (err) {
    document.getElementById('modal-code').innerText = 'Failed to load file content.';
  }
}

function closeFileModal() {
  document.getElementById('file-modal').classList.remove('active');
}

// KPI Stats
async function loadStats() {
  try {
    const res = await fetch(`${API_BASE}/api/stats`);
    if (!res.ok) return;
    const s = await res.json();
    document.getElementById('stat-total').innerText = s.total_leads;
    document.getElementById('stat-hot').innerText = s.hot_deals;
    document.getElementById('stat-pending').innerText = s.pending_approvals;
    document.getElementById('stat-built').innerText = s.built_projects;
    document.getElementById('stat-pipeline').innerText = s.estimated_pipeline_value;

    document.getElementById('nav-pending-count').innerText = s.pending_approvals;
    document.getElementById('nav-built-count').innerText = s.built_projects;
  } catch (err) {}
}

// WebSocket Connection
function initWS() {
  const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  const host = window.location.host || 'localhost:8000';
  const ws = new WebSocket(`${proto}//${host}/ws/notifications`);

  ws.onmessage = (e) => {
    try {
      const msg = JSON.parse(e.data);
      if (msg.event === 'LEAD_SCORED' && msg.data && msg.data.lead_id) {
        fetch(`${API_BASE}/api/leads/${msg.data.lead_id}`)
          .then(r => r.json())
          .then(lead => applyRealScoringToRadar(lead));
      }
    } catch (err) {}
    loadStats();
    loadApprovals();
    loadDeliverables();
  };


  ws.onclose = () => {
    setTimeout(initWS, 3000);
  };
}

// Initialize on DOM Ready
document.addEventListener('DOMContentLoaded', () => {
  loadStats();
  initWS();
});
