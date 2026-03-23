// app.js — NetThreads frontend logic

// ── Topic config (mirrors topics.py) ──────────────────────────────────────────
const TOPICS = [
  "sports","world_news","national_news","pesu_live",
  "music","gaming","hollywood","bollybuzz","stocks",
  "technology","memes","science"
];
const TOPIC_LABELS = {
  sports:"🏆 Sports", gaming:"🎮 Gaming",
  bollybuzz:"🎬 BollyBuzz", pesu_live:"🏫 PESU Live", music:"🎵 Music",
  world_news:"🌍 World News", national_news:"🇮🇳 National News", stocks:"📈 Stocks",
  hollywood:"🎥 Hollywood", technology:"💻 Technology",
  memes:"😂 Memes", science:"🔬 Science",
};
const TOPIC_COLORS = {
  sports:"#e8414e", gaming:"#7c6ff7",
  bollybuzz:"#f7a034", pesu_live:"#2dca8c", music:"#e85de8",
  world_news:"#4ecde8", national_news:"#e8c541", stocks:"#5de87a",
  hollywood:"#e85d9f", technology:"#4e9de8",
  memes:"#f7d034", science:"#34d4f7",
};
const getColor = t => TOPIC_COLORS[t] || "#888";
const getLabel = t => TOPIC_LABELS[t] || t;

// ── App state ─────────────────────────────────────────────────────────────────
let myUsername      = "Anonymous";
let mySubscriptions = new Set();
let allPosts        = [];
let allComments     = {};
let postVotes       = {};
let commentVotes    = {};
let pendingSubTopic = null;
let postCount       = 0;
let currentFilter   = "all";

// ── Socket.IO ─────────────────────────────────────────────────────────────────
const socket = io();

socket.on("connect", () => {
  document.getElementById("conn-dot").classList.add("connected");

  // check localStorage for a saved username from a previous visit
  const savedUsername = localStorage.getItem("netthreads_username");

  if (savedUsername) {
    // returning user — restore silently, no modal
    myUsername = savedUsername;
    document.getElementById("username-display").textContent = "@" + savedUsername;
    socket.emit("set_username", { username: savedUsername });
  } else {
    // first ever visit — send Anonymous for now, modal will prompt shortly
    socket.emit("set_username", { username: "Anonymous" });
  }
});

socket.on("disconnect", () => {
  document.getElementById("conn-dot").classList.remove("connected");
});

// ── load_posts: full post history from SQLite sent on every connect 
// this is what makes posts survive page refreshes
// all posts are shown regardless of subscription state so nothing looks empty

socket.on("load_posts", (data) => {
  // MERGE incoming posts with existing ones — deduplication by post_id
  // This is needed because load_posts is now called once per subscribed topic
  const existingIds = new Set(allPosts.map(p => p.post_id));
  const newPosts = data.posts
    .filter(p => !existingIds.has(p.post_id))
    .map(p => ({ ...p, fromMe: p.username === myUsername }));

  // merge then re-sort newest first
  allPosts = [...allPosts, ...newPosts].sort((a, b) => b.ts - a.ts);

  postCount = allPosts.length;
  document.getElementById("stat-posts").textContent = postCount;

  renderPosts();
});

// ── new_post: real-time incoming post ─────────────────────────────────────────
socket.on("new_post", (data) => {
  // deduplication: skip if already loaded via load_posts
  if (data.post_id && allPosts.some(p => p.post_id === data.post_id)) return;

  allPosts.unshift({ ...data, fromMe: data.username === myUsername });
  postCount++;
  document.getElementById("stat-posts").textContent = postCount;
  renderPosts();
  showNotifBubble(data);
});

socket.on("post_deleted", (data) => {
  allPosts = allPosts.filter(p => p.post_id !== data.post_id);
  delete allComments[data.post_id];
  renderPosts();
});

// ── my_subscriptions: sent by server after set_username to restore saved subs ──
// this fires when the server finds saved subscriptions for this username in SQLite
socket.on("my_subscriptions", (d) => {
  d.topics.forEach(t => mySubscriptions.add(t));
  syncUI();
  // re-render so the filter can apply correctly now that subs are loaded
  renderPosts();
});

socket.on("subscribed",   (d) => { mySubscriptions.add(d.topic);    syncUI(); renderPosts(); });
socket.on("unsubscribed", (d) => {
  mySubscriptions.delete(d.topic);
  syncUI();
  renderPosts();
});

// ── Comment socket handlers ───────────────────────────────────────────────────
socket.on("new_comment", (data) => {
  const { post_id, username, message, ts, comment_id } = data;
  if (!allComments[post_id]) allComments[post_id] = [];
  allComments[post_id].push({ username, message, ts, comment_id });

  const badge = document.getElementById(`ccount-${post_id}`);
  if (badge) badge.textContent = allComments[post_id].length;

  const list = document.getElementById(`clist-${post_id}`);
  if (list) {
    const post  = allPosts.find(p => p.post_id === post_id);
    const color = post ? getColor(post.topic) : "#888";
    const placeholder = list.querySelector(".no-comments");
    if (placeholder) placeholder.remove();
    list.insertAdjacentHTML("beforeend", renderCommentHTML({ username, message, ts, comment_id }, color));
  }
});

// ── Vote socket handlers ──────────────────────────────────────────────────────
socket.on("vote_updated", (data) => {
  const { target_type, target_id, up, down } = data;
  if (target_type === "post") {
    if (!postVotes[target_id]) postVotes[target_id] = { up: 0, down: 0, mine: null };
    postVotes[target_id].up   = up;
    postVotes[target_id].down = down;
    updateVoteUI(`pvote-${target_id}`, up, down, postVotes[target_id].mine);
  } else {
    if (!commentVotes[target_id]) commentVotes[target_id] = { up: 0, down: 0, mine: null };
    commentVotes[target_id].up   = up;
    commentVotes[target_id].down = down;
    updateVoteUI(`cvote-${target_id}`, up, down, commentVotes[target_id].mine);
  }
});

// ── Render ────────────────────────────────────────────────────────────────────
function syncUI() {
  document.getElementById("stat-subs").textContent = mySubscriptions.size;
  renderTopicStrip();
  renderSidebar();
}

function renderTopicStrip() {
  document.getElementById("topic-strip").innerHTML = TOPICS.map(t => {
    const sub = mySubscriptions.has(t);
    const c   = getColor(t);
    return `<button class="topic-pill ${sub ? "subscribed" : ""}" style="color:${c};" onclick="handleTopicClick('${t}')">
      <span style="width:8px;height:8px;border-radius:50%;background:${c};display:inline-block;flex-shrink:0;"></span>
      ${getLabel(t)}
    </button>`;
  }).join("");
}

function renderSidebar() {
  const list = document.getElementById("my-topics-list");
  if (mySubscriptions.size === 0) {
    list.innerHTML = `<div class="empty-subs">Subscribe to topics<br/>to see them here</div>`;
    return;
  }
  list.innerHTML = [...mySubscriptions].map(t => {
    const c = getColor(t);
    return `<button class="side-topic-pill">
      <span class="topic-dot" style="background:${c};"></span>
      ${getLabel(t)}
      <button class="unsub-x" onclick="event.stopPropagation();handleTopicClick('${t}')" title="Unsubscribe">×</button>
    </button>`;
  }).join("");
}

function renderPosts() {
  const container = document.getElementById("posts-container");
  const empty     = document.getElementById("empty-feed");

  let filtered = allPosts;

  if (currentFilter === "mine") {
    // "Posted by me" — show only this user's posts regardless of subscriptions
    filtered = allPosts.filter(p => p.username === myUsername);
  } else {
    // "All subscribed" — always filter by subscribed topics only
    // empty feed is correct and expected when not subscribed to anything
    filtered = allPosts.filter(p => mySubscriptions.has(p.topic));
  }

  container.querySelectorAll(".post-card").forEach(el => el.remove());
  empty.style.display = filtered.length === 0 ? "block" : "none";

  filtered.forEach(p => {
    const c    = getColor(p.topic);
    const bg   = c + "33";
    const card = document.createElement("div");
    card.className        = "post-card";
    card.dataset.postId   = p.post_id;
    card.style.borderLeft = `3px solid ${c}`;
    const postComments = allComments[p.post_id] || [];
    const pv = postVotes[p.post_id] || { up: 0, down: 0, mine: null };
    card.innerHTML = `
      <div class="post-header">
        <div class="post-avatar" style="background:${bg};color:${c};">${getInitials(p.username)}</div>
        <div class="post-meta">
          <div class="post-username">${esc(p.username)}</div>
          <div class="post-time">${timeAgo(p.ts)}</div>
        </div>
        <div class="post-topic-badge" style="background:${bg};color:${c};">${getLabel(p.topic)}</div>
        ${p.username === myUsername ? `
          <button class="post-delete-btn" onclick="deletePost('${p.post_id}')" title="Delete post">×</button>
        ` : ""}
      </div>
      <div class="post-body">${esc(p.message)}</div>
      <div class="post-footer">
        <div class="vote-row" id="pvote-${p.post_id}">
          <button class="vote-btn upvote ${pv.mine==='up'?'active':''}"
            onclick="castVote('post','${p.post_id}','up')">▲ <span>${pv.up}</span></button>
          <span class="vote-score ${pv.up-pv.down>0?'positive':pv.up-pv.down<0?'negative':''}">${pv.up - pv.down}</span>
          <button class="vote-btn downvote ${pv.mine==='down'?'active':''}"
            onclick="castVote('post','${p.post_id}','down')">▼ <span>${pv.down}</span></button>
        </div>
        <button class="comment-toggle-btn" onclick="toggleComments('${p.post_id}')">
          💬 Comments <span class="comment-count-badge" id="ccount-${p.post_id}">${postComments.length}</span>
        </button>
      </div>
      <div class="comment-section" id="csec-${p.post_id}">
        <div class="comment-list" id="clist-${p.post_id}">
          ${postComments.length === 0
            ? `<div class="no-comments">No comments yet. Be the first!</div>`
            : postComments.map(cm => renderCommentHTML(cm, c)).join("")}
        </div>
        <div class="comment-input-row">
          <input class="comment-input" id="cinput-${p.post_id}"
            placeholder="Write a comment…" maxlength="300"
            onkeydown="if(event.key==='Enter')submitComment('${p.post_id}')"/>
          <button class="comment-send-btn" onclick="submitComment('${p.post_id}')">➤</button>
        </div>
      </div>
    `;
    container.appendChild(card);
  });
}

// ── Helpers ───────────────────────────────────────────────────────────────────
const esc = s => String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
const getInitials = n => n.split(/[\s_]/).map(w => w[0] || "").join("").toUpperCase().slice(0,2) || "AN";
const timeAgo = ts => {
  const s = Math.floor((Date.now() - ts) / 1000);
  if (s < 5)     return "just now";
  if (s < 60)    return s + "s ago";
  if (s < 3600)  return Math.floor(s / 60) + "m ago";
  if (s < 86400) return Math.floor(s / 3600) + "h ago";
  return Math.floor(s / 86400) + "d ago";
};

// ── Comment helpers ───────────────────────────────────────────────────────────
function renderCommentHTML(cm, topicColor) {
  const bg  = (topicColor || "#888") + "33";
  const cid = cm.comment_id || "";
  const cv  = commentVotes[cid] || { up: 0, down: 0, mine: null };
  const scoreClass = cv.up - cv.down > 0 ? "positive" : cv.up - cv.down < 0 ? "negative" : "";
  return `
    <div class="comment-item" data-comment-id="${cid}">
      <div class="comment-avatar" style="background:${bg};color:${topicColor};">
        ${getInitials(cm.username)}
      </div>
      <div class="comment-bubble">
        <div class="comment-meta">
          <span class="comment-username">${esc(cm.username)}</span>
          <span class="comment-time">${timeAgo(cm.ts)}</span>
        </div>
        <div class="comment-text">${esc(cm.message)}</div>
        <div class="comment-vote-row" id="cvote-${cid}">
          <button class="comment-vote-btn upvote ${cv.mine==='up'?'active':''}"
            onclick="castVote('comment','${cid}','up')">▲ <span>${cv.up}</span></button>
          <span class="comment-vote-score ${scoreClass}">${cv.up - cv.down}</span>
          <button class="comment-vote-btn downvote ${cv.mine==='down'?'active':''}"
            onclick="castVote('comment','${cid}','down')">▼ <span>${cv.down}</span></button>
        </div>
      </div>
    </div>`;
}

// ── Vote functions ────────────────────────────────────────────────────────────
function castVote(target_type, target_id, direction) {
  const store = target_type === "post" ? postVotes : commentVotes;
  if (!store[target_id]) store[target_id] = { up: 0, down: 0, mine: null };
  const v = store[target_id];
  const newDir = v.mine === direction ? null : direction;
  v.mine = newDir;
  socket.emit("cast_vote", { target_type, target_id, direction: newDir });
  const prefix = target_type === "post" ? "pvote" : "cvote";
  updateVoteUI(`${prefix}-${target_id}`, v.up, v.down, newDir);
}

function updateVoteUI(containerId, up, down, mine) {
  const container = document.getElementById(containerId);
  if (!container) return;
  const upBtn     = container.querySelector(".upvote");
  const downBtn   = container.querySelector(".downvote");
  const scoreEl   = container.querySelector(".vote-score, .comment-vote-score");
  const upCount   = container.querySelector(".upvote span");
  const downCount = container.querySelector(".downvote span");

  if (upCount)    upCount.textContent   = up;
  if (downCount)  downCount.textContent = down;

  const net = up - down;
  if (scoreEl) {
    scoreEl.textContent = net;
    scoreEl.className   = scoreEl.className.replace(/positive|negative/g, "").trim();
    if (net > 0) scoreEl.classList.add("positive");
    else if (net < 0) scoreEl.classList.add("negative");
  }
  if (upBtn)   upBtn.classList.toggle("active",   mine === "up");
  if (downBtn) downBtn.classList.toggle("active", mine === "down");
}

// ── Comment actions ───────────────────────────────────────────────────────────
function toggleComments(post_id) {
  const sec = document.getElementById(`csec-${post_id}`);
  if (!sec) return;
  const isOpen = sec.classList.toggle("open");
  if (isOpen) {
    const input = document.getElementById(`cinput-${post_id}`);
    if (input) setTimeout(() => input.focus(), 50);
  }
}

function submitComment(post_id) {
  const input = document.getElementById(`cinput-${post_id}`);
  if (!input) return;
  const message = input.value.trim();
  if (!message) return;
  socket.emit("post_comment", { post_id, message });
  input.value = "";
}

// ── Subscribe / Unsubscribe modal ─────────────────────────────────────────────
function handleTopicClick(topic) {
  const c  = getColor(topic);
  const bg = c + "22";

  const badge = document.getElementById("sub-modal-badge");
  badge.textContent      = "#" + getLabel(topic);
  badge.style.color      = c;
  badge.style.background = bg;
  badge.style.border     = `1.5px solid ${c}`;

  const oldBtn = document.getElementById("sub-modal-confirm-btn");
  const newBtn = oldBtn.cloneNode(true);
  oldBtn.parentNode.replaceChild(newBtn, oldBtn);

  if (mySubscriptions.has(topic)) {
    document.getElementById("sub-modal-icon").textContent    = "🔕";
    document.getElementById("sub-modal-heading").textContent = "Unsubscribe from this topic?";
    document.getElementById("sub-modal-desc").textContent    =
      "You will stop receiving posts from this topic in your feed.";
    newBtn.textContent      = "Unsubscribe";
    newBtn.className        = "btn btn-danger";
    newBtn.style.flex       = "1";
    newBtn.style.background = "";
    newBtn.addEventListener("click", () => { socket.emit("unsubscribe", { topic }); closeSubModal(); });
  } else {
    document.getElementById("sub-modal-icon").textContent    = "🔔";
    document.getElementById("sub-modal-heading").textContent = "Subscribe to this topic?";
    document.getElementById("sub-modal-desc").textContent    =
      "You'll receive all new posts from this topic in your feed in real time. You can unsubscribe any time.";
    newBtn.textContent      = "Subscribe";
    newBtn.className        = "btn btn-primary";
    newBtn.style.flex       = "1";
    newBtn.style.background = c;
    newBtn.addEventListener("click", () => { socket.emit("subscribe", { topic }); closeSubModal(); });
  }

  pendingSubTopic = topic;
  document.getElementById("sub-modal").classList.remove("hidden");
}

function closeSubModal() {
  document.getElementById("sub-modal").classList.add("hidden");
  pendingSubTopic = null;
}

// ── Delete confirm modal ──────────────────────────────────────────────────────
function deletePost(post_id) {
  document.getElementById("delete-modal").classList.remove("hidden");

  const oldBtn = document.getElementById("confirm-delete-btn");
  const newBtn = oldBtn.cloneNode(true);
  oldBtn.parentNode.replaceChild(newBtn, oldBtn);

  newBtn.addEventListener("click", () => {
    closeDeleteModal();
    const card = document.querySelector(`[data-post-id="${post_id}"]`);
    if (card) {
      card.classList.add("deleting");
      setTimeout(() => { allPosts = allPosts.filter(p => p.post_id !== post_id); renderPosts(); }, 240);
    }
    socket.emit("delete_post", { post_id });
  });
}

function closeDeleteModal() {
  document.getElementById("delete-modal").classList.add("hidden");
}

// ── Filter ────────────────────────────────────────────────────────────────────
function setFilter(f, btn) {
  currentFilter = f;
  document.querySelectorAll(".filter-btn").forEach(b => b.classList.remove("active"));
  btn.classList.add("active");
  renderPosts();
}

// ── Username modal ────────────────────────────────────────────────────────────
function openUsernameModal() {
  document.getElementById("username-input").value = myUsername === "Anonymous" ? "" : myUsername;
  document.getElementById("username-modal").classList.remove("hidden");
  setTimeout(() => document.getElementById("username-input").focus(), 100);
}
function closeUsernameModal() { document.getElementById("username-modal").classList.add("hidden"); }
function saveUsername() {
  const v = document.getElementById("username-input").value.trim();
  if (!v) return;
  myUsername = v;
  document.getElementById("username-display").textContent = "@" + v;
  // sending set_username also triggers the server to restore saved subscriptions
  socket.emit("set_username", { username: v });
  // save to localStorage so username persists across page refreshes
  localStorage.setItem("netthreads_username", v);
  closeUsernameModal();
}

// ── Create post modal ─────────────────────────────────────────────────────────
function openPostModal() {
  const topics = mySubscriptions.size > 0 ? [...mySubscriptions] : TOPICS;
  document.getElementById("post-topic-select").innerHTML =
    topics.map(t => `<option value="${t}">${getLabel(t)}</option>`).join("");
  document.getElementById("post-message").value = "";
  document.getElementById("post-modal").classList.remove("hidden");
  setTimeout(() => document.getElementById("post-message").focus(), 100);
}
function closePostModal() { document.getElementById("post-modal").classList.add("hidden"); }
function submitPost() {
  const topic   = document.getElementById("post-topic-select").value;
  const message = document.getElementById("post-message").value.trim();
  if (!message) return;
  const post_id = Date.now() + "_" + Math.random().toString(36).slice(2);
  socket.emit("post", { topic, message, post_id });
  closePostModal();
}

// ── Notification bubble ───────────────────────────────────────────────────────
let notifTimer = null;
function showNotifBubble(data) {
  if (data.username === myUsername) return;
  const c = getColor(data.topic);
  document.querySelector(".notif-bubble")?.remove();
  if (notifTimer) clearTimeout(notifTimer);
  const el = document.createElement("div");
  el.className        = "notif-bubble";
  el.style.borderLeft = `3px solid ${c}`;
  el.innerHTML = `
    <div class="notif-topic" style="color:${c};">#${getLabel(data.topic)}</div>
    <div class="notif-msg">${esc(data.message.slice(0, 80))}${data.message.length > 80 ? "…" : ""}</div>
    <div class="notif-user">from ${esc(data.username)}</div>`;
  document.body.appendChild(el);
  notifTimer = setTimeout(() => el.remove(), 4500);
}

// ── Keyboard shortcuts ────────────────────────────────────────────────────────
document.addEventListener("keydown", e => {
  if (e.key === "Escape") {
    closePostModal(); closeUsernameModal(); closeSubModal(); closeDeleteModal();
  }
  if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
    if (!document.getElementById("post-modal").classList.contains("hidden"))     submitPost();
    if (!document.getElementById("username-modal").classList.contains("hidden")) saveUsername();
  }
});
document.querySelectorAll(".modal-overlay").forEach(o =>
  o.addEventListener("click", e => {
    if (e.target === o) {
      closePostModal(); closeUsernameModal(); closeSubModal(); closeDeleteModal();
    }
  })
);

// ── Init ──────────────────────────────────────────────────────────────────────
renderTopicStrip();
renderSidebar();

// only show the username modal if no saved username exists
if (!localStorage.getItem("netthreads_username")) {
  setTimeout(() => openUsernameModal(), 800);
}
// refresh timestamps every 30 seconds
setInterval(() => {
  document.querySelectorAll(".post-time").forEach((el, i) => {
    if (allPosts[i]) el.textContent = timeAgo(allPosts[i].ts);
  });
}, 30000);