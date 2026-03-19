// app.js — NetThreads frontend logic
// connects to the Flask-SocketIO server, handles all UI interactions


// ─────────────────────────────────────────────────────────────────────────────
// TOPIC CONFIG
// keep this in sync with topics.py on the server
// ─────────────────────────────────────────────────────────────────────────────

const TOPICS = [
  "sports", "world_news", "national_news", "pesu_live",
  "music", "gaming", "hollywood", "bollybuzz",
  "stocks", "technology", "memes", "science"
];

// one display-friendly label per topic
const TOPIC_LABELS = {
  sports:        "Sports",
  gaming:        "Gaming",
  technology:    "Technology",
  bollybuzz:     "BollyBuzz",
  pesu_live:     "PESU Live",
  music:         "Music",
  world_news:    "World News",
  national_news: "National News",
  stocks:        "Stocks",
  hollywood:     "Hollywood",
  memes:         "Memes",
  science:       "Science",
};

// one accent colour per topic — matches CSS variables in style.css
const TOPIC_COLORS = {
  sports:        "#e8414e",
  gaming:        "#7c6ff7",
  technology:    "#3a9fff",
  bollybuzz:     "#f7a034",
  pesu_live:     "#2dca8c",
  music:         "#e85de8",
  world_news:    "#4ecde8",
  national_news: "#e8c541",
  stocks:        "#5de87a",
  hollywood:     "#e85d9f",
  memes:         "#ff8c42",
  science:       "#42c7ff",
};

// helpers to avoid repetition
function getColor(topic)  { return TOPIC_COLORS[topic]  || "#888"; }
function getLabel(topic)  { return TOPIC_LABELS[topic]  || topic;  }


// ─────────────────────────────────────────────────────────────────────────────
// APP STATE
// ─────────────────────────────────────────────────────────────────────────────

let myUsername      = "Anonymous";   // current user's username
let mySubscriptions = new Set();     // set of topics this client is subscribed to
let allPosts        = [];            // array of post objects (newest first)
let pendingSubTopic = null;          // topic waiting for subscribe confirmation
let postCount       = 0;            // total posts received (for stats card)
let currentFilter   = "all";        // "all" | "mine"


// ─────────────────────────────────────────────────────────────────────────────
// SOCKET.IO — connect to the Flask-SocketIO server
// ─────────────────────────────────────────────────────────────────────────────

// io() auto-connects to the host that served this page (localhost:8000)
const socket = io();

// ── connection lifecycle ──────────────────────────────────────────────────────

socket.on("connect", () => {
  // turn the header dot green
  document.getElementById("conn-dot").classList.add("connected");

  // register username with server
  socket.emit("set_username", { username: myUsername });

  // ask server for any subscriptions this session already had (e.g. page refresh)
  socket.emit("get_subscriptions");
});

socket.on("disconnect", () => {
  // turn the header dot grey
  document.getElementById("conn-dot").classList.remove("connected");
});

// ── incoming events from server ───────────────────────────────────────────────

// a new post was broadcast to a topic we're subscribed to
socket.on("new_post", (data) => {
  // add to local posts array with a timestamp
  const post = {
    ...data,
    ts:     Date.now(),
    fromMe: data.username === myUsername,
  };
  allPosts.unshift(post);

  // update stats counter
  postCount++;
  document.getElementById("stat-posts").textContent = postCount;

  renderPosts();

  // show the popping notification bubble (like notifs in the sketch)
  showNotifBubble(data);
});

// server confirmed we are now subscribed to a topic
socket.on("subscribed", (data) => {
  mySubscriptions.add(data.topic);
  updateSubCount();
  renderTopicStrip();
  renderSidebar();
});

// server confirmed we are now unsubscribed from a topic
socket.on("unsubscribed", (data) => {
  mySubscriptions.delete(data.topic);

  // remove posts from this topic from the local feed
  allPosts = allPosts.filter(p => p.topic !== data.topic);

  updateSubCount();
  renderTopicStrip();
  renderSidebar();
  renderPosts();
});

// server returned our existing subscriptions (called after connect)
socket.on("my_subscriptions", (data) => {
  data.topics.forEach(t => mySubscriptions.add(t));
  updateSubCount();
  renderTopicStrip();
  renderSidebar();
});


// ─────────────────────────────────────────────────────────────────────────────
// RENDER FUNCTIONS
// ─────────────────────────────────────────────────────────────────────────────

// render the horizontal scrollable topic pills at the top of the feed
function renderTopicStrip() {
  const strip = document.getElementById("topic-strip");

  strip.innerHTML = TOPICS.map(t => {
    const subscribed = mySubscriptions.has(t);
    const color      = getColor(t);

    return `
      <button
        class="topic-pill ${subscribed ? "subscribed" : ""}"
        style="color: ${color};"
        onclick="handleTopicClick('${t}')"
      >
        <!-- small coloured dot inside the pill -->
        <span style="
          width: 7px; height: 7px; border-radius: 50%;
          background: ${color}; display: inline-block; flex-shrink: 0;
        "></span>
        ${getLabel(t)}
      </button>
    `;
  }).join("");
}


// render the "My Threads" list in the sidebar
function renderSidebar() {
  const list = document.getElementById("my-topics-list");

  // show placeholder when nothing is subscribed
  if (mySubscriptions.size === 0) {
    list.innerHTML = `<div class="empty-subs">Subscribe to topics<br/>to see them here</div>`;
    return;
  }

  list.innerHTML = [...mySubscriptions].map(t => {
    const color = getColor(t);
    return `
      <button class="side-topic-pill">
        <span class="topic-dot" style="background: ${color};"></span>
        ${getLabel(t)}
        <!-- × button to unsubscribe from this topic -->
        <button class="unsub-x"
                onclick="event.stopPropagation(); unsubscribe('${t}')"
                title="Unsubscribe">×</button>
      </button>
    `;
  }).join("");
}


// render the post cards in the feed
function renderPosts() {
  const container = document.getElementById("posts-container");
  const emptyFeed = document.getElementById("empty-feed");

  // apply current filter
  let filtered = allPosts;
  if (currentFilter === "mine") {
    filtered = allPosts.filter(p => p.username === myUsername);
  }

  if (filtered.length === 0) {
    // show the empty-state illustration
    emptyFeed.style.display = "block";
    container.querySelectorAll(".post-card").forEach(el => el.remove());
    return;
  }

  emptyFeed.style.display = "none";

  // remove existing cards and rebuild
  container.querySelectorAll(".post-card").forEach(el => el.remove());

  filtered.forEach(p => {
    const color   = getColor(p.topic);
    const bgLight = color + "33";      // colour at ~20% opacity for avatar bg
    const initials = getInitials(p.username);

    const card = document.createElement("div");
    card.className = "post-card";

    // left coloured stripe to visually identify the topic
    card.style.borderLeft = `3px solid ${color}`;

    card.innerHTML = `
      <div class="post-header">
        <!-- round avatar with user initials -->
        <div class="post-avatar" style="background: ${bgLight}; color: ${color};">
          ${initials}
        </div>
        <div class="post-meta">
          <div class="post-username">${escHtml(p.username)}</div>
          <div class="post-time">${timeAgo(p.ts)}</div>
        </div>
        <!-- topic badge top-right of card -->
        <div class="post-topic-badge" style="background: ${bgLight}; color: ${color};">
          ${getLabel(p.topic)}
        </div>
      </div>
      <div class="post-body">${escHtml(p.message)}</div>
    `;

    container.appendChild(card);
  });
}


// ─────────────────────────────────────────────────────────────────────────────
// UTILITY HELPERS
// ─────────────────────────────────────────────────────────────────────────────

// escape HTML to prevent XSS in user-generated content
function escHtml(str) {
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

// get up-to-2 initials from a username (e.g. "benny_d" → "BD")
function getInitials(name) {
  return name
    .split(/[\s_]/)
    .map(w => w[0] || "")
    .join("")
    .toUpperCase()
    .slice(0, 2) || "AN";
}

// human-readable relative time (e.g. "3m ago")
function timeAgo(ts) {
  const s = Math.floor((Date.now() - ts) / 1000);
  if (s < 5)    return "just now";
  if (s < 60)   return s + "s ago";
  if (s < 3600) return Math.floor(s / 60) + "m ago";
  return Math.floor(s / 3600) + "h ago";
}

// keep the subscription count stat card in sync
function updateSubCount() {
  document.getElementById("stat-subs").textContent = mySubscriptions.size;
}


// ─────────────────────────────────────────────────────────────────────────────
// SUBSCRIBE / UNSUBSCRIBE
// ─────────────────────────────────────────────────────────────────────────────

// called when user clicks a topic pill
function handleTopicClick(topic) {
  if (mySubscriptions.has(topic)) {
    // already subscribed — clicking again does nothing (unsub via sidebar ×)
    return;
  }

  // show the confirmation bar at the bottom of the screen
  pendingSubTopic = topic;
  const color = getColor(topic);
  document.getElementById("sub-confirm-text").innerHTML =
    `Subscribe to <strong style="color: ${color};">#${getLabel(topic)}</strong>?`;

  const bar = document.getElementById("sub-confirm");
  bar.classList.remove("hidden");

  // auto-dismiss after 6 seconds if user ignores it
  setTimeout(() => bar.classList.add("hidden"), 6000);
}

// user clicked the Subscribe button in the confirm bar
function confirmSubscribe() {
  if (!pendingSubTopic) return;
  socket.emit("subscribe", { topic: pendingSubTopic });
  hideSubConfirm();
}

// dismiss the confirm bar without subscribing
function hideSubConfirm() {
  document.getElementById("sub-confirm").classList.add("hidden");
  pendingSubTopic = null;
}

// send unsubscribe event to server
function unsubscribe(topic) {
  socket.emit("unsubscribe", { topic });
}


// ─────────────────────────────────────────────────────────────────────────────
// FILTER (all subscribed posts vs only posts from this user)
// ─────────────────────────────────────────────────────────────────────────────

function setFilter(f, btn) {
  currentFilter = f;

  // update active state on filter buttons
  document.querySelectorAll(".filter-btn").forEach(b => b.classList.remove("active"));
  btn.classList.add("active");

  renderPosts();
}


// ─────────────────────────────────────────────────────────────────────────────
// USERNAME MODAL
// ─────────────────────────────────────────────────────────────────────────────

function openUsernameModal() {
  // pre-fill with current username (unless it's still the default)
  document.getElementById("username-input").value =
    myUsername === "Anonymous" ? "" : myUsername;

  document.getElementById("username-modal").classList.remove("hidden");
  setTimeout(() => document.getElementById("username-input").focus(), 100);
}

function closeUsernameModal() {
  document.getElementById("username-modal").classList.add("hidden");
}

function saveUsername() {
  const val = document.getElementById("username-input").value.trim();
  if (!val) return;

  myUsername = val;

  // update the header chip
  document.getElementById("username-display").textContent = "@" + val;

  // tell the server our new username
  socket.emit("set_username", { username: val });

  closeUsernameModal();
}


// ─────────────────────────────────────────────────────────────────────────────
// CREATE POST MODAL
// ─────────────────────────────────────────────────────────────────────────────

function openPostModal() {
  // populate the topic dropdown with subscribed topics (or all if none subscribed)
  const sel    = document.getElementById("post-topic-select");
  const topics = mySubscriptions.size > 0 ? [...mySubscriptions] : TOPICS;

  sel.innerHTML = topics
    .map(t => `<option value="${t}">${getLabel(t)}</option>`)
    .join("");

  document.getElementById("post-message").value = "";
  document.getElementById("post-modal").classList.remove("hidden");
  setTimeout(() => document.getElementById("post-message").focus(), 100);
}

function closePostModal() {
  document.getElementById("post-modal").classList.add("hidden");
}

// send post event to server
function submitPost() {
  const topic   = document.getElementById("post-topic-select").value;
  const message = document.getElementById("post-message").value.trim();

  if (!message) return;

  // server broadcasts this to all subscribers of the topic
  socket.emit("post", { topic, message });

  closePostModal();
}


// ─────────────────────────────────────────────────────────────────────────────
// REAL-TIME NOTIFICATION BUBBLE  (the "popping bubble" from the sketch)
// appears top-right when someone else posts to a subscribed topic
// ─────────────────────────────────────────────────────────────────────────────

let notifTimer = null;

function showNotifBubble(data) {
  // don't show a bubble for our own posts
  if (data.username === myUsername) return;

  const color = getColor(data.topic);

  // remove any existing bubble first
  const existing = document.querySelector(".notif-bubble");
  if (existing) existing.remove();
  if (notifTimer) clearTimeout(notifTimer);

  // create the bubble element
  const el = document.createElement("div");
  el.className  = "notif-bubble";
  el.style.borderLeft = `3px solid ${color}`;  // topic colour stripe

  el.innerHTML = `
    <div class="notif-topic" style="color: ${color};">#${getLabel(data.topic)}</div>
    <div class="notif-msg">
      ${escHtml(data.message.slice(0, 80))}${data.message.length > 80 ? "…" : ""}
    </div>
    <div class="notif-user">from ${escHtml(data.username)}</div>
  `;

  document.body.appendChild(el);

  // auto-dismiss after 4.5 seconds
  notifTimer = setTimeout(() => el.remove(), 4500);
}


// ─────────────────────────────────────────────────────────────────────────────
// KEYBOARD SHORTCUTS
// ─────────────────────────────────────────────────────────────────────────────

document.addEventListener("keydown", (e) => {
  // Escape closes any open modal or confirm bar
  if (e.key === "Escape") {
    closePostModal();
    closeUsernameModal();
    hideSubConfirm();
  }

  // Ctrl/Cmd + Enter submits the active modal
  if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
    if (!document.getElementById("post-modal").classList.contains("hidden"))     submitPost();
    if (!document.getElementById("username-modal").classList.contains("hidden")) saveUsername();
  }
});

// clicking the dark overlay behind a modal also closes it
document.querySelectorAll(".modal-overlay").forEach(overlay => {
  overlay.addEventListener("click", e => {
    if (e.target === overlay) {
      closePostModal();
      closeUsernameModal();
    }
  });
});


// INIT — to run once on page load


// draw the topic pills immediately (subscriptions will update them once socket connects)
renderTopicStrip();
renderSidebar();

// prompt for username on first visit
setTimeout(() => {
  if (myUsername === "Anonymous") openUsernameModal();
}, 800);

// refresh "X ago" timestamps on all visible post cards every 30 seconds
setInterval(() => {
  const timeEls = document.querySelectorAll(".post-time");
  timeEls.forEach((el, i) => {
    if (allPosts[i]) el.textContent = timeAgo(allPosts[i].ts);
  });
}, 30000);