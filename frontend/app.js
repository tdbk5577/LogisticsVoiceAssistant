'use strict';

// ── Constants ──────────────────────────────────────────────────────────────

const WAKE_WORD = 'elmeeda';
const SESSION_ID = 'demo-' + Date.now();

const WORD_LISTS = [
  ['apple', 'river', 'truck', 'mountain', 'blanket'],
  ['coffee', 'engine', 'sunset', 'hammer', 'carpet'],
  ['pencil', 'window', 'forest', 'pillow', 'orange'],
  ['bottle', 'farmer', 'thunder', 'saddle', 'mirror'],
];
const MATH_QUESTIONS = [
  ['What is 8 plus 6?', 14],   ['What is 15 minus 7?', 8],
  ['What is 4 times 3?', 12],  ['What is 9 plus 5?', 14],
  ['What is 18 minus 9?', 9],  ['What is 6 times 4?', 24],
  ['What is 13 plus 8?', 21],  ['What is 20 minus 6?', 14],
  ['What is 7 times 3?', 21],  ['What is 17 minus 8?', 9],
];
const WORD_TO_NUM = {
  zero:0,one:1,two:2,three:3,four:4,five:5,six:6,seven:7,eight:8,nine:9,
  ten:10,eleven:11,twelve:12,thirteen:13,fourteen:14,fifteen:15,
  sixteen:16,seventeen:17,eighteen:18,nineteen:19,twenty:20,
  'twenty one':21,'twenty-one':21,'twenty four':24,'twenty-four':24,
};

// ── State ──────────────────────────────────────────────────────────────────

let appState = 'idle'; // idle | awake | processing | speaking | logs | alertness
let mainRecognition = null;
let awakeTimer = null;

// ── DOM refs ───────────────────────────────────────────────────────────────

const statusText  = document.getElementById('status-text');
const agentBadge  = document.getElementById('agent-badge');
const transcript  = document.getElementById('transcript');
const mainView    = document.getElementById('main-view');
const logsView    = document.getElementById('logs-view');
const logsContent = document.getElementById('logs-content');

// ── State management ───────────────────────────────────────────────────────

function setState(s, statusMsg) {
  appState = s;
  document.body.className = 'state-' + s;
  const labels = {
    idle:        'Say "Hey Elmeeda"',
    awake:       'Listening...',
    processing:  'Thinking...',
    speaking:    'Speaking...',
    logs:        'Showing logs',
    alertness:   'Alertness test',
  };
  statusText.textContent = statusMsg || labels[s] || s;
}

function setAgent(agent) {
  if (!agent || agent === 'unknown') { agentBadge.className = 'hidden'; return; }
  agentBadge.className = 'agent-' + agent;
  agentBadge.textContent = agent.charAt(0).toUpperCase() + agent.slice(1) + ' Agent';
}

function addMessage(role, text) {
  const div = document.createElement('div');
  div.className = 'msg msg-' + role;
  div.textContent = text;
  transcript.appendChild(div);
  transcript.scrollTop = transcript.scrollHeight;
  // Keep last 6 messages
  while (transcript.children.length > 6) transcript.removeChild(transcript.firstChild);
}

// ── Audio helpers ──────────────────────────────────────────────────────────

function playBeep() {
  try {
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.connect(gain); gain.connect(ctx.destination);
    osc.frequency.value = 880;
    gain.gain.setValueAtTime(0.3, ctx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.25);
    osc.start(); osc.stop(ctx.currentTime + 0.25);
  } catch (_) {}
}

async function speak(text) {
  setState('speaking');
  try {
    const resp = await fetch('/tts', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text }),
    });
    if (!resp.ok) throw new Error('tts failed');
    const blob = await resp.blob();
    const url = URL.createObjectURL(blob);
    await new Promise((resolve, reject) => {
      const audio = new Audio(url);
      audio.onended = resolve;
      audio.onerror = reject;
      audio.play().catch(reject);
    });
    URL.revokeObjectURL(url);
  } catch (_) {
    // Fallback to browser TTS
    await new Promise((resolve) => {
      const utt = new SpeechSynthesisUtterance(text);
      utt.onend = resolve;
      utt.onerror = resolve;
      speechSynthesis.speak(utt);
    });
  }
}

// ── Single-shot listen (used during alertness test) ────────────────────────

function listenOnce(timeoutSec) {
  return new Promise((resolve) => {
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SR) { resolve(''); return; }
    const rec = new SR();
    rec.lang = 'en-US';
    let done = false;
    const finish = (val) => { if (!done) { done = true; resolve(val); } };
    rec.onresult = (e) => finish(e.results[0][0].transcript);
    rec.onerror = () => finish('');
    rec.onend = () => finish('');
    try { rec.start(); } catch (_) { finish(''); return; }
    setTimeout(() => { try { rec.stop(); } catch (_) {} finish(''); }, timeoutSec * 1000);
  });
}

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

function randomChoice(arr) { return arr[Math.floor(Math.random() * arr.length)]; }

function randomSample(arr, n) {
  const copy = [...arr]; const out = [];
  for (let i = 0; i < n; i++) {
    const idx = Math.floor(Math.random() * copy.length);
    out.push(copy.splice(idx, 1)[0]);
  }
  return out;
}

function parseNumber(text) {
  const t = text.trim().toLowerCase();
  if (t in WORD_TO_NUM) return WORD_TO_NUM[t];
  const n = parseInt(t, 10);
  return isNaN(n) ? null : n;
}

function randomBetween(lo, hi) { return lo + Math.random() * (hi - lo); }

// ── Command routing ────────────────────────────────────────────────────────

async function handleCommand(text) {
  const t = text.toLowerCase();
  if (t.includes('log') || t.includes('report')) { await showLogs(); return; }
  if (t.includes('alertness') || t.includes('alert test') || t.includes('drowsy') || t.includes('check')) {
    await runAlertnessTest(); return;
  }
  if (t.includes('go back') || t.includes('back') || t.includes('cancel')) { setState('idle'); return; }

  addMessage('user', text);
  setState('processing');

  try {
    const resp = await fetch('/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: SESSION_ID, text }),
    });
    const data = await resp.json();
    addMessage('assistant', data.text);
    setAgent(data.agent);
    await speak(data.text);
  } catch (err) {
    addMessage('assistant', 'Sorry, I had trouble connecting. Please try again.');
    await speak('Sorry, I had trouble connecting.');
  }

  setState('idle');
  startListening();
}

// ── Alertness test ─────────────────────────────────────────────────────────

async function runAlertnessTest() {
  setState('alertness');
  stopListening();

  await speak('Starting alertness test. Three quick checks: memory, math, and reaction. Please respond quickly and accurately.');
  await sleep(500);

  // Memory test
  const words = randomChoice(WORD_LISTS);
  await speak('Memory test. Listen to these five words.');
  await sleep(300);
  await speak(words.join('. '));
  await sleep(3000);
  await speak('Now repeat all five words.');
  const memResp = await listenOnce(15);
  const recalled = words.filter(w => memResp.toLowerCase().includes(w)).length;

  // Math test
  await speak('Math test. Answer each question as fast as you can.');
  await sleep(500);
  const questions = randomSample(MATH_QUESTIONS, 3);
  let mathCorrect = 0, mathTotal = 0;
  for (const [q, ans] of questions) {
    await speak(q);
    const start = Date.now();
    const resp = await listenOnce(10);
    mathTotal += (Date.now() - start) / 1000;
    if (parseNumber(resp) === ans) mathCorrect++;
  }
  const mathAvg = mathTotal / 3;

  // Reaction test
  await speak('Reaction test. Say anything the instant you hear me say Now.');
  await sleep(1000);
  const reactionTimes = [];
  for (let i = 0; i < 3; i++) {
    await speak('Ready.');
    await sleep(randomBetween(2000, 4500));
    await speak('Now!');
    const rStart = Date.now();
    const rResp = await listenOnce(5);
    reactionTimes.push(rResp ? (Date.now() - rStart) / 1000 : 6.0);
    await sleep(800);
  }
  const reactionAvg = reactionTimes.reduce((a, b) => a + b) / 3;

  // Score
  const memScore = recalled / 5;
  const mathAcc  = mathCorrect / 3;
  const mathFactor = Math.max(0, 1 - Math.max(0, mathAvg - 3) / 10);
  const reactScore = Math.max(0, 1 - Math.max(0, reactionAvg - 1) / 3);
  const overall = (memScore + mathAcc * mathFactor + reactScore) / 3;
  const level = overall >= 0.75 ? 'alert' : overall >= 0.5 ? 'warning' : 'danger';

  // Save result
  try {
    await fetch('/alertness', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        level,
        overall_score: Math.round(overall * 1000) / 1000,
        memory_recalled: recalled,
        math_correct: mathCorrect,
        math_avg_time: Math.round(mathAvg * 100) / 100,
        reaction_avg_time: Math.round(reactionAvg * 100) / 100,
      }),
    });
  } catch (_) {}

  // Get assessment from chat
  const summary = `Alertness test complete. Memory: ${recalled} of 5 words recalled. Math: ${mathCorrect} of 3 correct, average ${mathAvg.toFixed(1)} seconds. Reaction: average ${reactionAvg.toFixed(1)} seconds. Overall score: ${Math.round(overall * 100)} percent.`;
  try {
    const resp = await fetch('/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: SESSION_ID, text: summary }),
    });
    const data = await resp.json();
    addMessage('assistant', data.text);
    setAgent('alertness');
    await speak(data.text);
  } catch (_) {
    const fallback = `Your alertness score is ${Math.round(overall * 100)} percent. ${level === 'alert' ? "You're good to keep driving." : level === 'warning' ? 'Consider taking a short break.' : 'Please pull over and rest.'}`;
    addMessage('assistant', fallback);
    await speak(fallback);
  }

  setState('idle');
  startListening();
}

// ── Logs view ──────────────────────────────────────────────────────────────

async function showLogs() {
  setState('logs');
  logsView.classList.remove('hidden');
  mainView.classList.add('hidden');
  logsContent.innerHTML = '<p style="color:#666;text-align:center;padding:2rem">Loading...</p>';

  try {
    const [hos, weekly, alertHistory] = await Promise.all([
      fetch('/hos/summary').then(r => r.json()),
      fetch('/hos/weekly').then(r => r.json()),
      fetch('/alertness/history?limit=10').then(r => r.json()),
    ]);

    const driveClass = hos.driving_remaining < 2 ? 'bad' : hos.driving_remaining < 4 ? 'warn' : 'good';
    const dutyClass  = hos.on_duty_remaining < 2 ? 'bad' : hos.on_duty_remaining < 4 ? 'warn' : 'good';
    const weekClass  = weekly.hours_remaining < 10 ? 'bad' : weekly.hours_remaining < 20 ? 'warn' : 'good';

    const alertRows = alertHistory.length
      ? alertHistory.map(a => {
          const d = new Date(a.timestamp);
          const when = d.toLocaleDateString() + ' ' + d.toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'});
          return `<div class="alertness-row">
            <span class="level-badge level-${a.level}">${a.level}</span>
            <span class="alertness-detail">${when}</span>
            <span class="alertness-detail">Mem ${a.memory_recalled}/5 · Math ${a.math_correct}/3 · React ${a.reaction_avg_time?.toFixed(1)}s</span>
            <span class="alertness-score">${Math.round(a.overall_score * 100)}%</span>
          </div>`;
        }).join('')
      : '<p style="color:#555;padding:0.5rem">No alertness tests recorded yet.</p>';

    logsContent.innerHTML = `
      <div class="log-section">
        <h2>Today — Hours of Service (${hos.date})</h2>
        <div class="stat-grid">
          <div class="stat">
            <div class="stat-value">${hos.driving_hours}</div>
            <div class="stat-label">Hours Driven</div>
          </div>
          <div class="stat">
            <div class="stat-value ${driveClass}">${hos.driving_remaining}</div>
            <div class="stat-label">Drive Time Left</div>
          </div>
          <div class="stat">
            <div class="stat-value">${hos.on_duty_hours}</div>
            <div class="stat-label">On-Duty Hours</div>
          </div>
          <div class="stat">
            <div class="stat-value ${dutyClass}">${hos.on_duty_remaining}</div>
            <div class="stat-label">On-Duty Left</div>
          </div>
        </div>
      </div>

      <div class="log-section">
        <h2>8-Day Rolling Window</h2>
        <div class="stat-grid">
          <div class="stat">
            <div class="stat-value">${weekly.weekly_on_duty_hours}</div>
            <div class="stat-label">Hours Used</div>
          </div>
          <div class="stat">
            <div class="stat-value ${weekClass}">${weekly.hours_remaining}</div>
            <div class="stat-label">Hours Remaining</div>
          </div>
          <div class="stat">
            <div class="stat-value">70</div>
            <div class="stat-label">Weekly Limit</div>
          </div>
        </div>
      </div>

      <div class="log-section">
        <h2>Alertness History</h2>
        ${alertRows}
      </div>
    `;
  } catch (_) {
    logsContent.innerHTML = '<p style="color:var(--danger);padding:1rem">Failed to load logs. Check your connection.</p>';
  }

  await speak('Your logs are ready. Say go back when you are done.');
  startListeningForBack();
}

function closeLogs() {
  logsView.classList.add('hidden');
  mainView.classList.remove('hidden');
  setState('idle');
  startListening();
}
window.closeLogs = closeLogs;

function startListeningForBack() {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SR) return;
  const rec = new SR();
  rec.continuous = true;
  rec.lang = 'en-US';
  rec.onresult = (e) => {
    const t = e.results[e.results.length - 1][0].transcript.toLowerCase();
    if (t.includes('back') || t.includes('done') || t.includes('close')) {
      rec.stop();
      closeLogs();
    }
  };
  try { rec.start(); } catch (_) {}
}

// ── Main recognition loop ──────────────────────────────────────────────────

function stopListening() {
  if (mainRecognition) { try { mainRecognition.stop(); } catch (_) {} mainRecognition = null; }
  clearTimeout(awakeTimer);
}

function startListening() {
  stopListening();
  setState('idle');

  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SR) {
    statusText.textContent = 'Voice not supported in this browser. Use Chrome.';
    return;
  }

  let currentState = 'idle';
  let commandAccum = '';

  mainRecognition = new SR();
  mainRecognition.continuous = true;
  mainRecognition.interimResults = true;
  mainRecognition.lang = 'en-US';

  mainRecognition.onresult = (e) => {
    if (appState === 'processing' || appState === 'speaking' || appState === 'alertness' || appState === 'logs') return;

    const results = Array.from(e.results);
    const fullText = results.map(r => r[0].transcript).join(' ').toLowerCase();
    const lastResult = results[results.length - 1];
    const lastText = lastResult[0].transcript.toLowerCase().trim();
    const isFinal = lastResult.isFinal;

    if (currentState === 'idle') {
      if (fullText.includes(WAKE_WORD) || fullText.includes('elmida') || fullText.includes('almeeda')) {
        currentState = 'awake';
        setState('awake');
        playBeep();
        commandAccum = '';
        clearTimeout(awakeTimer);
        awakeTimer = setTimeout(() => {
          if (currentState === 'awake') { currentState = 'idle'; setState('idle'); }
        }, 8000);
      }
    } else if (currentState === 'awake') {
      if (isFinal) {
        // Strip wake word from the command if it's in the same utterance
        let cmd = lastText.replace(/hey\s+el[a-z]*/gi, '').trim();
        if (!cmd) return;
        clearTimeout(awakeTimer);
        currentState = 'done';
        stopListening();
        handleCommand(cmd);
      }
    }
  };

  mainRecognition.onerror = (e) => {
    if (e.error === 'not-allowed') {
      statusText.textContent = 'Microphone permission denied';
      return;
    }
    // Restart on transient errors
    setTimeout(startListening, 1000);
  };

  mainRecognition.onend = () => {
    if (appState === 'idle' || appState === 'awake') {
      setTimeout(startListening, 300);
    }
  };

  try {
    mainRecognition.start();
  } catch (_) {
    setTimeout(startListening, 1000);
  }
}

// ── Splash ─────────────────────────────────────────────────────────────────

function initSplash() {
  const splashView = document.getElementById('splash-view');
  const mainViewEl = document.getElementById('main-view');
  const browserWarn = document.getElementById('browser-warn');
  const splashHow  = document.getElementById('splash-how');
  const splashBtn  = document.getElementById('splash-btn');
  const splashTip  = document.getElementById('splash-tip');

  const isChromium = /Chrome/.test(navigator.userAgent);
  const isArc = /Arc\//.test(navigator.userAgent);

  if (!isChromium) {
    browserWarn.classList.remove('hidden');
  } else {
    splashHow.classList.remove('hidden');
    splashBtn.classList.remove('hidden');
    splashTip.classList.remove('hidden');
    if (isArc) {
      splashTip.textContent = 'Arc user? Allow mic in Arc Settings → Privacy if prompted.';
    }
  }

  splashBtn.addEventListener('click', () => {
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SR) {
      splashTip.textContent = 'Speech recognition not available. Please use Chrome.';
      splashTip.style.color = 'var(--danger)';
      splashTip.classList.remove('hidden');
      return;
    }

    splashBtn.textContent = 'Starting...';
    splashBtn.disabled = true;

    // Let SpeechRecognition handle mic permission directly — avoids Arc audio routing conflicts
    const test = new SR();
    test.onstart = () => {
      test.stop();
      splashView.classList.add('hidden');
      mainViewEl.classList.remove('hidden');
      startListening();
    };
    test.onerror = (e) => {
      splashBtn.textContent = 'Tap to Start';
      splashBtn.disabled = false;
      splashTip.textContent = e.error === 'not-allowed'
        ? (isArc ? 'Mic blocked. Allow in Arc Settings → Privacy → Microphone.' : 'Microphone access denied. Check browser permissions.')
        : 'Could not start voice recognition. Try reloading.';
      splashTip.style.color = 'var(--danger)';
      splashTip.classList.remove('hidden');
    };
    try { test.start(); } catch (_) {
      splashBtn.textContent = 'Tap to Start';
      splashBtn.disabled = false;
    }
  });
}

// ── Boot ───────────────────────────────────────────────────────────────────

if ('serviceWorker' in navigator) {
  navigator.serviceWorker.register('/sw.js').catch(() => {});
}

// Hide main view until splash is dismissed
document.getElementById('main-view').classList.add('hidden');
initSplash();
