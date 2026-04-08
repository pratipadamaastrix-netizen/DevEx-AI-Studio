/* ============================================================
   Devex AI Studios — Quote Wizard JS
   Extracted from quote_wizard.html Phase 2
   ============================================================ */

// ══ State ══
const state = {
  currentStep: 1,
  totalSteps: 6,
  projectType: '',
  goals: [],
  features: {},        // { name: 'must' | 'optional' | null }
  integrations: [],
  designStyle: '',
  budget: '',
  aiSuggestions: null,
};

// ══ Default feature set by project type ══
const BASE_FEATURES = {
  website:    ['Homepage','About Page','Services/Products Page','Contact Form','Mobile Responsive','SEO Basics'],
  web_app:    ['User Login / Auth','User Dashboard','Admin Panel','Search & Filter','Notifications','Role-Based Access'],
  ecommerce:  ['Product Catalogue','Shopping Cart','Checkout / Payments','Order Management','Customer Accounts','Discount Codes'],
  mobile_app: ['User Onboarding','Push Notifications','Offline Mode','Profile Management','In-App Messaging','App Store Submission'],
  portal:     ['Secure Login','Document Upload','Status Tracking','User Roles','Audit Trail','Email Notifications'],
  both:       ['User Auth','Web Dashboard','Mobile App','API Backend','Push Notifications','Admin Panel'],
  other:      ['User Authentication','Main Dashboard','Data Entry Forms','Reporting','Email Notifications','Admin Controls'],
};

// ══ Navigation ══
function goTo(step) {
  if (step > state.currentStep) {
    const err = validateStep(state.currentStep);
    if (err) { showError(err); return; }
  }
  hideError();

  const prevItem = document.querySelector(`[data-step="${state.currentStep}"]`);
  if (prevItem && step > state.currentStep) {
    prevItem.classList.remove('active');
    prevItem.classList.add('done');
  }
  if (step < state.currentStep) {
    const wasItem = document.querySelector(`[data-step="${state.currentStep}"]`);
    if (wasItem) { wasItem.classList.remove('active','done'); }
  }

  document.getElementById(`step${state.currentStep}`).classList.remove('active');
  state.currentStep = step;
  document.getElementById(`step${step}`).classList.add('active');

  const navItem = document.querySelector(`[data-step="${step}"]`);
  if (navItem) {
    document.querySelectorAll('.step-item').forEach(i => i.classList.remove('active'));
    navItem.classList.add('active');
    navItem.classList.remove('done');
  }

  updateProgress();

  if (step === 3) { buildFeatureList(); fetchAiSuggestions(); }
  if (step === 6) { buildReview(); }

  window.scrollTo(0, 0);
}

function validateStep(step) {
  if (step === 1) {
    if (!v('contact_name').trim()) return 'Full name is required.';
    if (!v('contact_email').trim()) return 'Email address is required.';
    if (!/\S+@\S+\.\S+/.test(v('contact_email'))) return 'Please enter a valid email address.';
  }
  if (step === 2) {
    if (!state.projectType) return 'Please select a project type.';
    if (!v('brief_description').trim()) return 'Please describe your project.';
  }
  return null;
}

function updateProgress() {
  const pct = Math.round(((state.currentStep - 1) / state.totalSteps) * 100);
  document.getElementById('progressFill').style.width = pct + '%';
  document.getElementById('progressLabel').textContent = `Step ${state.currentStep} of ${state.totalSteps}`;
  document.getElementById('progressPct').textContent = pct + '%';
}

// ══ Type selection ══
function selectType(el) {
  document.querySelectorAll('.type-card').forEach(c => c.classList.remove('selected'));
  el.classList.add('selected');
  state.projectType = el.dataset.value;
}

// ══ Chip toggles ══
function toggleChip(el) {
  el.classList.toggle('selected');
  const val = el.dataset.value;
  if (el.classList.contains('selected')) {
    if (!state.goals.includes(val)) state.goals.push(val);
  } else {
    state.goals = state.goals.filter(g => g !== val);
  }
}

function toggleChipGroup(el, group) {
  document.querySelectorAll(`[data-group="${group}"]`).forEach(c => c.classList.remove('selected'));
  el.classList.add('selected');
  if (group === 'budget') state.budget = el.dataset.value;
  if (group === 'design_style') state.designStyle = el.dataset.value;
}

function toggleChipMulti(el, group) {
  el.classList.toggle('selected');
  const val = el.dataset.value;
  if (el.classList.contains('selected')) {
    if (!state.integrations.includes(val)) state.integrations.push(val);
  } else {
    state.integrations = state.integrations.filter(i => i !== val);
  }
}

// ══ Feature list ══
function buildFeatureList() {
  const list = document.getElementById('featureList');
  const type = state.projectType || 'website';
  const features = BASE_FEATURES[type] || BASE_FEATURES.other;

  features.forEach(f => {
    if (!(f in state.features)) state.features[f] = null;
  });

  list.innerHTML = '';
  Object.keys(state.features).forEach(name => {
    list.appendChild(makeFeatureRow(name, state.features[name]));
  });

  document.getElementById('customFeatureInput').onkeydown = (e) => {
    if (e.key === 'Enter') {
      const val = e.target.value.trim();
      if (val && !(val in state.features)) {
        state.features[val] = 'optional';
        list.appendChild(makeFeatureRow(val, 'optional'));
        e.target.value = '';
      }
    }
  };
}

function makeFeatureRow(name, priority) {
  const row = document.createElement('div');
  row.className = `feature-row${priority ? ' ' + priority : ''}`;
  row.dataset.feature = name;
  const safeName = name.replace(/'/g, "\\'");
  row.innerHTML = `
    <div class="priority-dot"></div>
    <div class="feature-name">${name}</div>
    <div class="priority-toggle">
      <button class="p-btn ${priority==='must'?'must-active':''}" onclick="setFeaturePriority('${safeName}','must',this)">Must</button>
      <button class="p-btn ${priority==='optional'?'opt-active':''}" onclick="setFeaturePriority('${safeName}','optional',this)">Optional</button>
    </div>`;
  return row;
}

function setFeaturePriority(name, priority, btn) {
  state.features[name] = priority;
  const row = document.querySelector(`[data-feature="${name}"]`);
  if (!row) return;
  row.className = `feature-row ${priority}`;
  row.querySelectorAll('.p-btn').forEach(b => {
    b.className = 'p-btn';
    if (b === btn) b.className = `p-btn ${priority==='must'?'must-active':'opt-active'}`;
  });
}

// ══ AI mid-form suggestions ══
async function fetchAiSuggestions() {
  if (!state.projectType || state.aiSuggestions) return;
  try {
    const res = await fetch('/api/quote/suggest', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({
        project_type: state.projectType,
        goals: state.goals,
        features: Object.keys(state.features).filter(f => state.features[f]),
        brief_description: v('brief_description'),
      })
    });
    if (!res.ok) return;
    const data = await res.json();
    state.aiSuggestions = data;
    renderAiSuggestions(data);
  } catch(e) { /* non-critical — wizard works without AI suggestions */ }
}

function renderAiSuggestions(data) {
  const card = document.getElementById('aiSuggestCard');
  const pills = document.getElementById('aiSuggestPills');
  const suggestions = data.suggested_features || [];
  if (!suggestions.length) return;

  pills.innerHTML = suggestions
    .filter(f => !(f in state.features))
    .map(f => {
      const safe = f.replace(/'/g, "\\'");
      return `<div class="ai-pill" onclick="addAiFeature('${safe}',this)">+ ${f}</div>`;
    })
    .join('');

  if (pills.children.length > 0) card.style.display = 'block';
}

function addAiFeature(name, pill) {
  if (!(name in state.features)) {
    state.features[name] = 'optional';
    const list = document.getElementById('featureList');
    list.appendChild(makeFeatureRow(name, 'optional'));
  }
  pill.style.opacity = '.4';
  pill.style.pointerEvents = 'none';
}

// ══ Review builder ══
function buildReview() {
  const el = document.getElementById('reviewContent');
  const features = {
    must_have: Object.keys(state.features).filter(f => state.features[f] === 'must'),
    optional:  Object.keys(state.features).filter(f => state.features[f] === 'optional'),
  };

  el.innerHTML = `
    <div class="review-block">
      <h4>Contact</h4>
      ${rr('Name', v('contact_name'))}
      ${rr('Email', v('contact_email'))}
      ${rr('Phone', v('contact_phone') || '&mdash;')}
      ${rr('Company', v('company_name') || '&mdash;')}
    </div>
    <div class="review-block">
      <h4>Project</h4>
      ${rr('Name', v('project_name') || '&mdash;')}
      ${rr('Type', state.projectType.replace('_',' '))}
      ${rr('Goals', state.goals.join(', ') || '&mdash;')}
      ${rr('Description', v('brief_description'))}
    </div>
    <div class="review-block">
      <h4>Features</h4>
      ${rr('Must-Have', features.must_have.join(', ') || '&mdash; none marked yet')}
      ${rr('Optional', features.optional.join(', ') || '&mdash;')}
    </div>
    <div class="review-block">
      <h4>Technical &amp; Design</h4>
      ${rr('Stack', v('preferred_tech') || '&mdash;')}
      ${rr('Hosting', v('hosting') || '&mdash;')}
      ${rr('Design Style', state.designStyle || '&mdash;')}
      ${rr('Integrations', state.integrations.join(', ') || '&mdash;')}
    </div>
    <div class="review-block">
      <h4>Timeline &amp; Budget</h4>
      ${rr('Start Date', v('start_date') || '&mdash;')}
      ${rr('Launch Date', v('launch_date') || '&mdash;')}
      ${rr('Budget', state.budget.replace(/_/g,' ') || '&mdash;')}
      ${rr('Maintenance', v('maintenance_required') || '&mdash;')}
    </div>`;
}

function rr(k, v2) {
  return `<div class="review-row"><span class="rk">${k}</span><span class="rv">${v2}</span></div>`;
}

// ══ Submit ══
async function submitForm() {
  const btn = document.getElementById('submitBtn');
  const label = document.getElementById('submitLabel');
  btn.disabled = true;
  label.innerHTML = '<span class="spinner"></span> Analysing with AI...';

  const features = {
    must_have: Object.keys(state.features).filter(f => state.features[f] === 'must'),
    optional:  Object.keys(state.features).filter(f => state.features[f] === 'optional'),
  };

  const payload = {
    contact_name:       v('contact_name'),
    contact_email:      v('contact_email'),
    contact_phone:      v('contact_phone'),
    company_name:       v('company_name'),
    project_name:       v('project_name'),
    project_type:       state.projectType,
    brief_description:  v('brief_description'),
    goals:              state.goals,
    features,
    target_audience:    v('target_audience'),
    competitors:        v('competitors'),
    usp:                v('usp'),
    design: {
      style:             state.designStyle,
      has_branding:      v('has_branding'),
      content_provided:  v('content_provided'),
    },
    tech: {
      preferred_tech:   v('preferred_tech'),
      hosting:          v('hosting'),
      integrations:     state.integrations,
      seo_level:        v('seo_level'),
    },
    budget_range:        state.budget,
    start_date:          v('start_date'),
    launch_date:         v('launch_date'),
    maintenance: {
      type: v('maintenance_required'),
    },
    additional_notes:    v('additional_notes'),
    source:              'web',
  };

  try {
    const res = await fetch('/quote/submit', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify(payload),
    });
    const data = await res.json();

    if (!res.ok || !data.ok) {
      showError((data.errors || ['Submission failed. Please try again.']).join(' '));
      btn.disabled = false;
      label.textContent = 'Submit Requirements';
      return;
    }

    window.location.href = `/quote/confirmation/${data.ref}`;
  } catch(e) {
    showError('Network error. Please check your connection and try again.');
    btn.disabled = false;
    label.textContent = 'Submit Requirements';
  }
}

// ══ Helpers ══
function v(id) {
  const el = document.getElementById(id);
  return el ? el.value : '';
}

function showError(msg) {
  const b = document.getElementById('errorBanner');
  b.textContent = msg;
  b.style.display = 'block';
  b.scrollIntoView({behavior:'smooth', block:'nearest'});
}

function hideError() {
  document.getElementById('errorBanner').style.display = 'none';
}

// ══ Init ══
updateProgress();
