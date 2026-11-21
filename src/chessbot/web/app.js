// The phone decides when the board is still and sends one frame then; the
// laptop decides everything about chess. Frames go up as binary JPEG over the
// WebSocket, events come back as JSON.

const PIXEL_DELTA = 22;  // 0-255 change for one thumbnail pixel to count as changed
const MOTION = 0.02;     // share of thumbnail pixels that must change: a hand covers far more
const STILL_MS = 300;    // how long it must stay still before a frame is sent
const SAMPLE_MS = 80;    // how often the thumbnail is compared
const SEND_WIDTH = 768;  // width of the frame Jev sees

const $ = (id) => document.getElementById(id);
const cam = $("cam");
const thumb = Object.assign(document.createElement("canvas"), { width: 32, height: 24 });
const tctx = thumb.getContext("2d", { willReadFrequently: true });
const big = document.createElement("canvas");

const CORNER_NAMES = ["near-left", "near-right", "far-right", "far-left"];
const overlay = $("overlay");
const octx = overlay.getContext("2d");

let ws, color = "white", prev = null, moving = false, lastMotion = 0, sentAt = 0, wakeLock = null;
let corners = [], tapping = false, sampler = null;

// ---- connection ----------------------------------------------------------

function connect() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  ws = new WebSocket(`${proto}://${location.host}/ws`);
  ws.binaryType = "arraybuffer";
  ws.onopen = () => {
    $("conn").textContent = "laptop"; $("conn").className = "ok";
    send({ type: "hello" });
    if (corners.length === 4) send({ type: "corners", points: corners });
  };
  ws.onclose = () => { $("conn").textContent = "reconnecting"; $("conn").className = ""; setTimeout(connect, 800); };
  ws.onmessage = (e) => onEvent(JSON.parse(e.data));
}

function send(msg) {
  if (ws && ws.readyState === WebSocket.OPEN) ws.send(typeof msg === "string" || msg instanceof Blob ? msg : JSON.stringify(msg));
}

// ---- events from the laptop ----------------------------------------------

function show(text, sub, cls = "") {
  $("move").className = cls;
  $("move").innerHTML = "";
  $("move").append(text, Object.assign(document.createElement("small"), { textContent: sub }));
}

function say(text) {
  speechSynthesis.cancel();
  const u = new SpeechSynthesisUtterance(text);
  u.rate = 1.15;
  speechSynthesis.speak(u);
}

function onEvent(ev) {
  if (ev.type === "best_move") {
    const t = ev.timing;
    const round = sentAt ? Math.round(performance.now() - sentAt) : null;
    show(ev.text, `read ${t.read_ms}ms · stockfish ${t.engine_ms}ms${round ? ` · total ${round}ms` : ""}`);
    say(ev.speech);
  } else if (ev.type === "opponent_move") {
    log(`opponent: ${ev.text}`);
  } else if (ev.type === "reaim") {
    show("RE-AIM", ev.reason, "reaim");
    if (navigator.vibrate) navigator.vibrate(120);
  } else if (ev.type === "capture") {
    setTimeout(sendFrame, 150); // let a just-moved hand clear the shot
  } else if (ev.type === "log") {
    log(ev.line);
  } else if (ev.type === "state") {
    if (ev.over) show("GAME OVER", "tap New for another", "idle");
    else if (!ev.my_turn && !ev.suggested) show("THEIR MOVE", "watching the board…", "idle");
  }
}

function log(line) {
  const div = document.createElement("div");
  div.textContent = line;
  $("log").append(div);
  while ($("log").children.length > 5) $("log").firstChild.remove();
}

// ---- stillness detection ---------------------------------------------------

function sample() {
  if (cam.readyState < 2) return;
  tctx.drawImage(cam, 0, 0, thumb.width, thumb.height);
  const px = tctx.getImageData(0, 0, thumb.width, thumb.height).data;
  const gray = new Uint8Array(px.length / 4);
  for (let i = 0; i < gray.length; i++) gray[i] = (px[i * 4] * 3 + px[i * 4 + 1] * 6 + px[i * 4 + 2]) / 10;
  if (prev) {
    let changed = 0;
    for (let i = 0; i < gray.length; i++) if (Math.abs(gray[i] - prev[i]) > PIXEL_DELTA) changed++;
    const diff = changed / gray.length;
    const now = performance.now();
    $("motion").textContent = diff > MOTION ? "motion" : moving ? "settling" : "still";
    if (diff > MOTION) { moving = true; lastMotion = now; }
    else if (moving && now - lastMotion > STILL_MS) { moving = false; sendFrame(); }
  }
  prev = gray;
}

function sendFrame() {
  const w = cam.videoWidth, h = cam.videoHeight;
  if (!w) return;
  big.width = SEND_WIDTH;
  big.height = Math.round((h / w) * SEND_WIDTH);
  big.getContext("2d").drawImage(cam, 0, 0, big.width, big.height);
  big.toBlob((blob) => { if (blob) { sentAt = performance.now(); send(blob); } }, "image/jpeg", 0.7);
}

// ---- board corners ----------------------------------------------------------
// Taps are stored as 0-1 fractions of the camera frame, which the video shows
// whole (object-fit: contain), letterboxed inside its box. Coordinates below
// are relative to that box, which the overlay canvas covers exactly.

function frameBox() {
  const { width: ew, height: eh } = overlay.getBoundingClientRect();
  const vw = cam.videoWidth || 4, vh = cam.videoHeight || 3;
  const scale = Math.min(ew / vw, eh / vh);
  const w = vw * scale, h = vh * scale;
  return { x: (ew - w) / 2, y: (eh - h) / 2, w, h };
}

function drawCorners() {
  const dpr = devicePixelRatio || 1;
  const { width, height } = overlay.getBoundingClientRect();
  overlay.width = width * dpr;
  overlay.height = height * dpr;
  octx.setTransform(dpr, 0, 0, dpr, 0, 0);
  octx.clearRect(0, 0, width, height);
  if (!corners.length) return;
  const box = frameBox();
  const pts = corners.map(([x, y]) => [box.x + x * box.w, box.y + y * box.h]);
  octx.strokeStyle = "#3ef0ff";
  octx.lineWidth = 3;
  octx.beginPath();
  pts.forEach(([x, y], i) => (i ? octx.lineTo(x, y) : octx.moveTo(x, y)));
  if (pts.length === 4) octx.closePath();
  octx.stroke();
  octx.fillStyle = "#3ef0ff";
  for (const [x, y] of pts) { octx.beginPath(); octx.arc(x, y, tapping ? 7 : 4, 0, Math.PI * 2); octx.fill(); }
}

function startTapping() {
  corners = [];
  tapping = true;
  guide();
  requestAnimationFrame(drawCorners); // after the view grows to full screen
}

function guide() {
  document.body.classList.toggle("tapping", tapping);
  $("guide").hidden = !tapping;
  if (tapping) $("guide").innerHTML = `Tap the board's corner nearest your <b>${CORNER_NAMES[corners.length].toUpperCase()}</b> (${corners.length + 1} of 4)`;
}

overlay.addEventListener("pointerdown", (e) => {
  if (!tapping) return;
  const box = frameBox();
  const rect = overlay.getBoundingClientRect();
  corners.push([(e.clientX - rect.left - box.x) / box.w, (e.clientY - rect.top - box.y) / box.h].map((v) => Math.min(1, Math.max(0, v))));
  if (corners.length === 4) {
    tapping = false;
    send({ type: "corners", points: corners });
  }
  guide();
  requestAnimationFrame(drawCorners);
});
addEventListener("resize", drawCorners);
new ResizeObserver(drawCorners).observe(overlay);
new ResizeObserver(([entry]) => {
  if (entry.contentRect.height) document.documentElement.style.setProperty("--controls", `${$("bottom").offsetHeight}px`);
}).observe($("bottom"));

// ---- start ----------------------------------------------------------------

async function keepAwake() {
  try { wakeLock = await navigator.wakeLock?.request("screen"); } catch {}
}

$("color").addEventListener("click", (e) => {
  const b = e.target.closest("button");
  if (!b) return;
  color = b.dataset.color;
  for (const x of $("color").children) x.setAttribute("aria-pressed", String(x === b));
});

$("go").addEventListener("click", async () => {
  say(" "); // iOS only lets a page speak after a tap; this unlocks it
  try {
    cam.srcObject = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: "environment", width: { ideal: 1280 }, height: { ideal: 960 } }, audio: false,
    });
  } catch (err) {
    alert(`Camera blocked: ${err.message}. Open this page over https (the Tailscale URL).`);
    return;
  }
  $("start").hidden = true;
  keepAwake();
  send({ type: "new_game", color });
  if (corners.length === 4) send({ type: "corners", points: corners }); // same spot, new game
  else if (cam.readyState >= 1) startTapping();
  else cam.addEventListener("loadedmetadata", startTapping, { once: true });
  if (!sampler) sampler = setInterval(sample, SAMPLE_MS);
});

$("undo").onclick = () => send({ type: "undo" });
$("read").onclick = () => sendFrame();
$("recorner").onclick = () => startTapping();
$("newgame").onclick = () => { $("start").hidden = false; };
$("setform").onsubmit = (e) => {
  e.preventDefault();
  const v = $("setmove").value.trim();
  if (v) send({ type: "set_move", move: v });
  $("setmove").value = "";
  $("setmove").blur();
};
document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible") keepAwake(); });

connect();
