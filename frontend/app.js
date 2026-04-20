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
const debugText   = document.getElementById('debug-text');
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

function cleanForSpeech(text) {
  return text
    .replace(/\*\*(.+?)\*\*/g, '$1')
    .replace(/\*(.+?)\*/g, '$1')
    .replace(/#+\s*/g, '')
    .replace(/^\s*[-•]\s+/gm, '')
    .replace(/\n{2,}/g, '. ')
    .replace(/\n/g, ' ')
    .trim();
}

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
  if (/show.*(log|report)|my logs|view log|open log/.test(t)) { await showLogs(); return; }
  if (t.includes('alertness') || t.includes('drowsy') || t.includes('fatigue') ||
      (t.includes('run') && t.includes('test')) || t.includes('alert test')) {
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
    const clean = cleanForSpeech(data.text);
    addMessage('assistant', data.text);
    setAgent(data.agent);
    await speak(clean);
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
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  for (let i = 0; i < 3; i++) {
    await speak('Ready.');

    // Pre-warm recognition before the random delay so it's running when "Now!" finishes
    let resultResolve;
    const resultPromise = new Promise(resolve => { resultResolve = resolve; });
    const rec = new SR();
    rec.lang = 'en-US';
    const warmPromise = new Promise(resolve => { rec.onstart = resolve; });
    rec.onresult = (e) => resultResolve(e.results[0][0].transcript);
    rec.onerror = () => resultResolve('');
    rec.onend = () => resultResolve('');
    try { rec.start(); } catch (_) { resultResolve(''); }
    await warmPromise; // wait until mic is actually open

    await sleep(randomBetween(2000, 4500));
    await speak('Now!');
    const rStart = Date.now(); // timer starts after "Now!" finishes, mic already running
    setTimeout(() => { try { rec.stop(); } catch (_) {} resultResolve(''); }, 5000);
    const rResp = await resultPromise;
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
  setState('processing');
  try {
    const [hos, weekly, alertHistory] = await Promise.all([
      fetch('/hos/summary').then(r => r.json()),
      fetch('/hos/weekly').then(r => r.json()),
      fetch('/alertness/history?limit=1').then(r => r.json()),
    ]);

    const latest = alertHistory[0];
    const alertPart = latest
      ? `Your last alertness check scored ${Math.round(latest.overall_score * 100)} percent and was rated ${latest.level}.`
      : 'No alertness checks on record.';

    const summary = `Today you have driven ${hos.driving_hours} hours with ${hos.driving_remaining} hours remaining. You have been on duty ${hos.on_duty_hours} hours with ${hos.on_duty_remaining} hours left. This week you have used ${weekly.weekly_on_duty_hours} of your 70 hour limit, with ${weekly.hours_remaining} hours remaining. ${alertPart}`;

    addMessage('assistant', summary);
    await speak(summary);
  } catch (_) {
    await speak('Sorry, I could not load your logs right now.');
  }
  startListening();
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

// ── Always-on listening (tap to activate, then stays on) ───────────────────

let listeningActive = false;

function stopListening() {
  if (mainRecognition) { try { mainRecognition.stop(); } catch (_) {} mainRecognition = null; }
}

function startListening() {
  stopListening();
  setState('idle');
  const btn = document.getElementById('talk-btn');
  if (listeningActive) {
    if (btn) { btn.textContent = '🔴 Listening'; btn.classList.add('listening'); btn.disabled = false; }
    beginContinuousListen();
  } else {
    if (btn) { btn.textContent = '🎤 Tap to Start'; btn.classList.remove('listening'); btn.disabled = false; }
  }
  if (debugText) debugText.textContent = '';
}

function beginContinuousListen() {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SR) { if (debugText) debugText.textContent = '❌ Use Chrome for voice support.'; return; }

  mainRecognition = new SR();
  mainRecognition.lang = 'en-US';
  mainRecognition.interimResults = true;

  mainRecognition.onresult = (e) => {
    const last = e.results[e.results.length - 1];
    if (debugText) debugText.textContent = last[0].transcript;
    if (last.isFinal) {
      const text = last[0].transcript.trim();
      if (text) {
        stopListening();
        handleCommand(text); // handleCommand calls startListening() when done, which restarts
      }
    }
  };

  mainRecognition.onerror = (e) => {
    if (e.error !== 'no-speech') {
      if (debugText) debugText.textContent = '❌ ' + e.error;
    }
  };

  // Auto-restart when recognition ends (browser cuts off after silence)
  mainRecognition.onend = () => {
    if (listeningActive && (appState === 'idle' || appState === 'awake')) {
      setTimeout(beginContinuousListen, 200);
    }
  };

  setState('awake');
  const btn = document.getElementById('talk-btn');
  if (btn) btn.textContent = '🔴 Listening';
  try { mainRecognition.start(); } catch (_) {
    setTimeout(beginContinuousListen, 500);
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
    splashView.classList.add('hidden');
    mainViewEl.classList.remove('hidden');
    startListening();
    document.getElementById('talk-btn').addEventListener('click', () => {
      if (listeningActive) {
        listeningActive = false;
        stopListening();
        setState('idle');
        const btn = document.getElementById('talk-btn');
        btn.textContent = '🎤 Tap to Start';
        btn.classList.remove('listening');
        if (debugText) debugText.textContent = '';
      } else {
        listeningActive = true;
        beginContinuousListen();
      }
    });
  });
}

// ── Boot ───────────────────────────────────────────────────────────────────

if ('serviceWorker' in navigator) {
  navigator.serviceWorker.register('/sw.js').catch(() => {});
}

// Hide main view until splash is dismissed
document.getElementById('main-view').classList.add('hidden');
initSplash();
