<template>
  <div class="chat-view">
    <!-- Page header -->
    <div class="page-header">
      <div class="page-header-left">
        <h2 class="page-title">Chat</h2>
        <div class="page-subtitle">基于你最近 7 天的工作日志和活动摘要答题</div>
      </div>
      <div class="page-header-right">
        <el-button
          v-if="history.length > 0 && !busy"
          round
          size="small"
          @click="exportTranscript"
        >导出</el-button>
        <el-button
          v-if="history.length > 0"
          round
          size="small"
          :disabled="busy"
          @click="resetChat"
        >新建对话</el-button>
      </div>
    </div>

    <!-- Chat container -->
    <div class="chat-shell">
      <!-- Message log -->
      <div ref="logEl" class="log">
        <!-- Intro / suggestions -->
        <div v-if="history.length === 0" class="intro">
          <div class="intro-hero">
            <div class="intro-eyebrow">对话助手</div>
            <h3 class="intro-title">问问你自己都做了什么。</h3>
            <p class="intro-sub">
              我读你本地数据库里的 <code>worklog_drafts.full_summary</code> 和
              <code>activities.llm_summary</code>（最近 7 天），基于真实记录答题。
            </p>
          </div>
          <div class="suggestions">
            <button
              v-for="s in suggestions" :key="s"
              class="suggestion"
              type="button"
              :disabled="busy"
              @click="ask(s)"
            >{{ s }}</button>
          </div>
        </div>

        <!-- Messages -->
        <div
          v-for="(m, idx) in history"
          :key="idx"
          class="msg"
          :class="m.role"
        >
          <div class="bubble" :class="{ error: m.error }">
            <div v-if="m.role === 'ai'" class="rendered" v-html="renderMd(m.text)"></div>
            <template v-else>{{ m.text }}</template>
          </div>
          <a
            v-if="m.error && idx === history.length - 1 && !busy"
            class="retry-link"
            href="#"
            @click.prevent="retry(idx)"
          >重试</a>

          <!-- Worklog extract action (latest AI bubble only, no errors, session set) -->
          <div
            v-if="
              m.role === 'ai' &&
              !m.error &&
              idx === lastAiIdx &&
              sessionId &&
              !busy &&
              !draftsPreview
            "
            class="extract-action"
          >
            <el-button
              round
              size="small"
              :loading="extracting"
              @click="onExtractWorklog"
            >整理为工时草稿</el-button>
          </div>

          <!-- Inline drafts preview + submit bar -->
          <div
            v-if="
              m.role === 'ai' &&
              idx === lastAiIdx &&
              draftsPreview
            "
            class="drafts-panel"
          >
            <div class="drafts-panel-title">生成的工时草稿（可编辑后提交）</div>
            <div v-if="draftsPreview.length === 0" class="drafts-empty">
              LLM 没有提取到任何工时条目。
            </div>
            <div
              v-for="(d, dIdx) in draftsPreview"
              :key="dIdx"
              class="draft-row"
              :class="{ failed: d._failed }"
            >
              <div class="draft-row-line1">
                <el-input
                  v-model="d.issue_key"
                  size="small"
                  style="width: 120px"
                  placeholder="issue_key"
                />
                <el-input-number
                  v-model="d.time_spent_hours"
                  size="small"
                  :min="0"
                  :step="0.5"
                  controls-position="right"
                  style="width: 110px"
                />
                <span class="draft-row-unit">h</span>
                <span v-if="d._failed" class="draft-row-err">{{ d._failed }}</span>
              </div>
              <el-input
                v-model="d.summary"
                size="small"
                type="textarea"
                :rows="2"
                placeholder="summary"
                style="margin-top: 6px"
              />
            </div>
            <div class="drafts-panel-footer">
              <el-button
                size="small"
                round
                @click="cancelDraftsPreview"
              >取消</el-button>
              <el-button
                size="small"
                round
                type="primary"
                :loading="pushing"
                @click="onPushToJira"
              >提交到 Jira</el-button>
            </div>
          </div>
        </div>

        <!-- Typing indicator -->
        <div v-if="busy && !streaming" class="msg ai">
          <div class="typing">
            <span></span><span></span><span></span>
          </div>
        </div>
      </div>

      <!-- Input bar -->
      <div class="inputbar">
        <textarea
          ref="boxEl"
          v-model="draft"
          class="box"
          rows="1"
          :placeholder="boxPlaceholder"
          :disabled="busy"
          @keydown="onKey"
          @input="autosize"
        />
        <el-button
          v-if="!busy"
          type="primary"
          round
          :disabled="!draft.trim()"
          @click="sendFromBox"
        >发送</el-button>
        <el-button
          v-else
          type="default"
          round
          @click="stop"
        >停止</el-button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, nextTick, computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus'

const history = ref([])    // [{role:'user'|'ai', text, error?}]
const draft = ref('')
const busy = ref(false)
const streaming = ref(false)
const logEl = ref(null)
const boxEl = ref(null)

const sessionId = ref(null)
// Live AbortController for the in-flight fetch — mutated on ask()/stop().
let controller = null

// Phase 3 — worklog extraction UI state. Lives on the latest AI bubble.
const extracting = ref(false)
const pushing = ref(false)
const draftsPreview = ref(null)  // null = hidden; array = inline panel visible

const lastAiIdx = computed(() => {
  for (let i = history.value.length - 1; i >= 0; i--) {
    if (history.value[i].role === 'ai') return i
  }
  return -1
})

function todayIso() {
  const d = new Date()
  const mm = String(d.getMonth() + 1).padStart(2, '0')
  const dd = String(d.getDate()).padStart(2, '0')
  return `${d.getFullYear()}-${mm}-${dd}`
}

async function onExtractWorklog() {
  if (!sessionId.value || extracting.value) return
  extracting.value = true
  try {
    const resp = await fetch(
      `/api/chat/sessions/${encodeURIComponent(sessionId.value)}/extract_worklog`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ target_date: todayIso() }),
      },
    )
    if (!resp.ok) {
      let msg = `HTTP ${resp.status}`
      try {
        const body = await resp.json()
        if (body && body.detail) {
          msg = typeof body.detail === 'string'
            ? body.detail
            : (body.detail.detail || JSON.stringify(body.detail))
        }
      } catch (_) {}
      ElMessage.error('抽取失败: ' + msg)
      return
    }
    const rows = await resp.json()
    draftsPreview.value = rows.map(r => ({
      issue_key: r.issue_key,
      time_spent_hours: r.time_spent_hours,
      summary: r.summary,
      _failed: '',
    }))
  } catch (err) {
    ElMessage.error('抽取失败: ' + (err && err.message ? err.message : err))
  } finally {
    extracting.value = false
  }
}

function cancelDraftsPreview() {
  draftsPreview.value = null
}

async function onPushToJira() {
  if (!sessionId.value || pushing.value || !draftsPreview.value) return
  pushing.value = true
  try {
    const rowsToSend = draftsPreview.value.map(d => ({
      issue_key: d.issue_key,
      time_spent_hours: Number(d.time_spent_hours) || 0,
      summary: d.summary,
    }))
    const resp = await fetch(
      `/api/chat/sessions/${encodeURIComponent(sessionId.value)}/push_to_jira`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          target_date: todayIso(),
          drafts: rowsToSend,
        }),
      },
    )
    if (!resp.ok) {
      let msg = `HTTP ${resp.status}`
      try {
        const body = await resp.json()
        if (body && body.detail) msg = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail)
      } catch (_) {}
      ElMessage.error('提交失败: ' + msg)
      return
    }
    const body = await resp.json()
    const submitted = Array.isArray(body.submitted) ? body.submitted : []
    const failed = Array.isArray(body.failed) ? body.failed : []
    ElMessage.success(`已提交 ${submitted.length} 条，失败 ${failed.length} 条`)

    if (failed.length === 0) {
      draftsPreview.value = null
    } else {
      // Keep the panel open and mark failed rows red so the user can retry.
      const failMap = new Map(failed.map(f => [f.issue_key, f.error || '提交失败']))
      draftsPreview.value = draftsPreview.value.map(d => ({
        ...d,
        _failed: failMap.get(d.issue_key) || '',
      }))
    }
  } catch (err) {
    ElMessage.error('提交失败: ' + (err && err.message ? err.message : err))
  } finally {
    pushing.value = false
  }
}

// Suggestions come from the backend (computed against the user's actual
// data). If the endpoint is unreachable we fall back to a static list
// so the chat intro stays usable on a fresh / empty DB.
const FALLBACK_SUGGESTIONS = [
  '最近一周干了啥？',
  '今天的主要工作有哪些？',
  '最近在哪个 Jira 任务上花时间最多？',
  '总结一下这周的开发主线。',
]
const suggestions = ref([...FALLBACK_SUGGESTIONS])

async function loadSuggestions() {
  try {
    const res = await fetch('/api/chat/suggestions')
    if (!res.ok) return
    const body = await res.json()
    if (Array.isArray(body.suggestions) && body.suggestions.length > 0) {
      suggestions.value = body.suggestions
    }
  } catch (_) {
    // keep fallback
  }
}

// Title of the active session (used for export filename + header).
const sessionTitle = ref('')

async function loadSessionTitle(id) {
  if (!id) { sessionTitle.value = ''; return }
  try {
    const res = await fetch(`/api/chat/sessions/${encodeURIComponent(id)}`)
    if (!res.ok) { sessionTitle.value = ''; return }
    const body = await res.json()
    sessionTitle.value = (body && body.title) || ''
  } catch (_) {
    sessionTitle.value = ''
  }
}

function exportTranscript() {
  if (history.value.length === 0) return
  const title = sessionTitle.value || '对话'
  const parts = [`# Chat — ${title}`, '']
  for (const m of history.value) {
    if (m.error) continue
    const heading = m.role === 'user' ? '## 用户' : '## 助手'
    parts.push(heading, '', m.text, '')
  }
  const content = parts.join('\n')

  const now = new Date()
  const pad = (n) => String(n).padStart(2, '0')
  const stamp = `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}-${pad(now.getHours())}${pad(now.getMinutes())}`
  const filename = `chat-${stamp}.md`

  const blob = new Blob([content], { type: 'text/markdown;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
}

const boxPlaceholder = computed(() =>
  busy.value ? '生成中...' : '问点什么... (Enter 发送, Shift+Enter 换行)'
)

function scrollToBottom() {
  nextTick(() => {
    if (logEl.value) logEl.value.scrollTop = logEl.value.scrollHeight
  })
}

function autosize() {
  nextTick(() => {
    if (!boxEl.value) return
    boxEl.value.style.height = 'auto'
    boxEl.value.style.height = Math.min(boxEl.value.scrollHeight, 140) + 'px'
  })
}

function onKey(e) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    sendFromBox()
  }
}

function sendFromBox() {
  const text = draft.value.trim()
  if (!text) return
  draft.value = ''
  autosize()
  ask(text)
}

function resetChat() {
  if (busy.value) return
  sessionId.value = null
  sessionTitle.value = ''
  history.value = []
  draft.value = ''
  draftsPreview.value = null
  autosize()
}

function stop() {
  if (controller) {
    try { controller.abort() } catch (_) { /* noop */ }
  }
}

function retry(errorIdx) {
  // Walk back from the error bubble to find the last user message. Drop
  // the error bubble before resending so it doesn't leak into the next
  // prompt's history.
  let lastUserText = ''
  for (let i = errorIdx - 1; i >= 0; i--) {
    if (history.value[i].role === 'user') {
      lastUserText = history.value[i].text
      break
    }
  }
  if (!lastUserText) return
  // Drop the error bubble AND the most recent user message (ask() will
  // re-push it so history/messages stay consistent with the request).
  history.value.splice(errorIdx, 1)
  for (let i = history.value.length - 1; i >= 0; i--) {
    if (history.value[i].role === 'user' && history.value[i].text === lastUserText) {
      history.value.splice(i, 1)
      break
    }
  }
  ask(lastUserText)
}

// Lightweight markdown renderer — matches DEFAULT_CHAT_PROMPT's output surface.
// Handles ``` fenced code blocks, ### headers, **bold**, `code`, "- " bullets,
// line breaks.
//
// Citation linkification: dates (YYYY-MM-DD) → #/my-logs?date=..., issue
// keys (ABC-123) → #/issues?key=.... Done on the raw text BEFORE backtick
// code substitution so that `2026-04-16` inside inline code stays literal.
// Content inside fenced code blocks is NEVER linkified — it's code, not
// narrative.
//
// Mid-stream behavior: when the opening ``` has arrived but the closer
// hasn't, we render what we have so far as an open <pre><code> block.
// When the closer arrives on a later chunk, the whole block flips to the
// closed state (identical DOM minus the `open` class).
function renderMd(md) {
  if (!md) return ''
  const esc = (s) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
  const DATE_RE = /\b(\d{4}-\d{2}-\d{2})\b/g
  const ISSUE_RE = /\b([A-Z][A-Z0-9]+-\d+)\b/g
  const linkify = (s) =>
    s.replace(DATE_RE, '<a class="citation" href="#/my-logs?date=$1">$1</a>')
     .replace(ISSUE_RE, '<a class="citation" href="#/issues?key=$1">$1</a>')
  const inline = (s) => {
    const escaped = esc(s)
    // Split by backtick-delimited spans so we don't linkify citations that
    // appear inside inline code (e.g. `2026-04-16` as a code sample).
    const parts = escaped.split(/(`[^`]+`)/)
    const rebuilt = parts.map((part) => {
      if (part.startsWith('`') && part.endsWith('`') && part.length >= 2) {
        return '<code>' + part.slice(1, -1) + '</code>'
      }
      return linkify(part)
    }).join('')
    return rebuilt.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
  }

  // First pass: peel fenced blocks out of the line stream so the
  // narrative pass never sees their content (no linkify / no inline).
  const allLines = md.split(/\r?\n/)
  // Tokens: { type: 'line', text } | { type: 'code', lang, body, closed }
  const tokens = []
  let i = 0
  while (i < allLines.length) {
    const line = allLines[i]
    const openMatch = /^```(\S*)\s*$/.exec(line.trimEnd())
    if (openMatch) {
      const lang = openMatch[1] || ''
      const bodyLines = []
      let closed = false
      let j = i + 1
      while (j < allLines.length) {
        if (/^```\s*$/.test(allLines[j].trimEnd())) {
          closed = true
          break
        }
        bodyLines.push(allLines[j])
        j++
      }
      tokens.push({ type: 'code', lang, body: bodyLines.join('\n'), closed })
      // Skip past the closer when present; when unclosed we've consumed
      // the rest of the input so the outer loop will stop naturally.
      i = closed ? j + 1 : j
      continue
    }
    tokens.push({ type: 'line', text: line })
    i++
  }

  let out = ''
  let inList = false
  const flushList = () => { if (inList) { out += '</ul>'; inList = false } }
  for (const tok of tokens) {
    if (tok.type === 'code') {
      flushList()
      const cls = tok.lang ? ' class="language-' + esc(tok.lang) + '"' : ''
      const openAttr = tok.closed ? '' : ' data-open="1"'
      out += '<pre' + openAttr + '><code' + cls + '>' + esc(tok.body) + '</code></pre>'
      continue
    }
    const line = tok.text.trimEnd()
    if (/^###\s+/.test(line)) {
      flushList()
      out += '<h4>' + inline(line.replace(/^###\s+/, '')) + '</h4>'
    } else if (/^-\s+/.test(line)) {
      if (!inList) { out += '<ul>'; inList = true }
      out += '<li>' + inline(line.replace(/^-\s+/, '')) + '</li>'
    } else if (line === '') {
      flushList()
    } else {
      flushList()
      out += inline(line) + '<br/>'
    }
  }
  if (inList) out += '</ul>'
  return out
}

async function ask(text) {
  if (busy.value || !text.trim()) return
  busy.value = true
  streaming.value = false
  controller = new AbortController()
  // A new turn invalidates any stale drafts preview attached to the
  // previous AI bubble — the preview only makes sense for "the current
  // thread up to right now".
  draftsPreview.value = null

  history.value.push({ role: 'user', text })
  scrollToBottom()

  // Index into history.value for the AI bubble being streamed. We write
  // through the reactive proxy (history.value[aiIdx].text = ...) rather
  // than holding a raw-object reference — the latter bypasses Vue's
  // reactivity and leaves the bubble stuck on whatever rendered first.
  let aiIdx = -1
  let assembled = ''

  try {
    const body = {
      messages: history.value.map(m => ({ role: m.role, text: m.text })),
    }
    if (sessionId.value) body.session_id = sessionId.value

    const resp = await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      signal: controller.signal,
    })
    if (!resp.ok) throw new Error('HTTP ' + resp.status)

    const reader = resp.body.getReader()
    const decoder = new TextDecoder()
    let buf = ''

    while (true) {
      const { value, done } = await reader.read()
      if (done) break
      buf += decoder.decode(value, { stream: true })
      let idx
      while ((idx = buf.indexOf('\n\n')) !== -1) {
        const chunk = buf.slice(0, idx)
        buf = buf.slice(idx + 2)
        const dataLine = chunk.split('\n').find(l => l.startsWith('data: '))
        if (!dataLine) continue
        const payload = dataLine.slice(6).trim()
        if (payload === '[DONE]') continue
        try {
          const evt = JSON.parse(payload)
          if (evt.session_id !== undefined) {
            // Control event from server — pick up the id so subsequent
            // POSTs in this conversation carry it. No localStorage needed;
            // on next page load we fetch the latest session from the DB.
            sessionId.value = evt.session_id
            loadSessionTitle(evt.session_id)
          } else if (evt.text !== undefined) {
            if (aiIdx < 0) {
              aiIdx = history.value.length
              history.value.push({ role: 'ai', text: '' })
              streaming.value = true
            }
            assembled += evt.text
            history.value[aiIdx].text = assembled
            scrollToBottom()
          } else if (evt.error) {
            history.value.push({ role: 'ai', text: '错误: ' + evt.error, error: true })
            scrollToBottom()
            break
          }
        } catch (_) {
          // ignore malformed event
        }
      }
    }
  } catch (err) {
    if (err && err.name === 'AbortError') {
      // User clicked 停止 — swallow silently. Any partial AI bubble
      // already streamed stays put as an honest record of what arrived.
    } else {
      history.value.push({ role: 'ai', text: '请求失败: ' + err.message, error: true })
      ElMessage.error('Chat 请求失败')
      scrollToBottom()
    }
  } finally {
    busy.value = false
    streaming.value = false
    controller = null
    boxEl.value && boxEl.value.focus()
  }
}

async function restoreSession() {
  // Load the most recent session from the server. No localStorage needed —
  // the DB is the single source of truth per AGENTS.md's data principles.
  try {
    const listResp = await fetch('/api/chat/sessions')
    if (!listResp.ok) return
    const sessions = await listResp.json()
    if (!Array.isArray(sessions) || sessions.length === 0) return

    const latest = sessions[0]  // sorted by updated_at DESC on the server
    const msgsResp = await fetch(`/api/chat/sessions/${encodeURIComponent(latest.id)}/messages`)
    if (!msgsResp.ok) return
    const rows = await msgsResp.json()
    if (!rows.length) return

    sessionId.value = latest.id
    history.value = rows.map(r => ({ role: r.role, text: r.text }))
    scrollToBottom()
  } catch (_) {
    // Network error on boot — skip restoration, user starts fresh.
  }
}

onMounted(async () => {
  autosize()
  // Fire-and-forget: the intro chips render from the fallback until the
  // request resolves, so a slow backend never blocks focusing the input.
  loadSuggestions()
  await restoreSession()
  if (sessionId.value) loadSessionTitle(sessionId.value)
  boxEl.value && boxEl.value.focus()
})
</script>

<style scoped>
.chat-view {
  display: flex;
  flex-direction: column;
  /* Fill available main area height (main has padding 32px top/48px bottom) */
  height: calc(100vh - 80px);
  min-height: 520px;
}

/* Reuse page-header pattern from other views */
.page-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-end;
  gap: 16px;
  margin-bottom: 20px;
  flex-shrink: 0;
}
.page-title {
  margin: 0 0 4px;
  font-size: 22px;
  font-weight: 700;
  letter-spacing: -0.02em;
  color: var(--ink);
}
.page-subtitle {
  font-size: 13px;
  color: var(--ink-muted);
}

/* Chat shell: message log + input bar */
.chat-shell {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  overflow: hidden;
  box-shadow: var(--shadow);
}

.log {
  flex: 1;
  overflow-y: auto;
  padding: 24px 28px;
  display: flex;
  flex-direction: column;
  gap: 14px;
  scroll-behavior: smooth;
}

/* Intro / suggestions */
.intro {
  margin: auto;
  max-width: 560px;
  text-align: center;
  padding: 32px 16px;
}
.intro-eyebrow {
  font-size: 11px;
  font-weight: 500;
  letter-spacing: 0.1em;
  text-transform: uppercase;
  color: var(--ink-dim);
  margin-bottom: 12px;
}
.intro-title {
  margin: 0 0 12px;
  font-size: 26px;
  font-weight: 700;
  letter-spacing: -0.02em;
  color: var(--ink);
}
.intro-sub {
  margin: 0 0 28px;
  font-size: 14px;
  line-height: 1.6;
  color: var(--ink-muted);
}
.intro-sub code {
  background: rgba(0, 0, 0, 0.05);
  padding: 1px 6px;
  border-radius: 4px;
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--ink-soft);
}
.suggestions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  justify-content: center;
}
.suggestion {
  padding: 8px 14px;
  font-size: 13px;
  font-family: inherit;
  color: var(--ink-soft);
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--radius-pill);
  cursor: pointer;
  transition: var(--transition);
}
.suggestion:hover:not(:disabled) {
  border-color: var(--ink);
  color: var(--ink);
  background: var(--surface-hover);
}
.suggestion:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

/* Messages */
.msg {
  display: flex;
  flex-direction: column;
  max-width: 100%;
  animation: slideIn 0.22s ease-out;
}
@keyframes slideIn {
  from { opacity: 0; transform: translateY(6px); }
  to   { opacity: 1; transform: translateY(0); }
}
.msg.user { align-self: flex-end; max-width: 82%; align-items: flex-end; }
.msg.ai   { align-self: flex-start; max-width: 92%; align-items: flex-start; }

.bubble {
  padding: 11px 16px;
  border-radius: var(--radius);
  line-height: 1.65;
  font-size: 14px;
  word-wrap: break-word;
  white-space: pre-wrap;
}
.msg.user .bubble {
  background: var(--ink);
  color: #ffffff;
  border-bottom-right-radius: 4px;
}
.msg.ai .bubble {
  background: var(--surface-hover);
  color: var(--ink-soft);
  border: 1px solid var(--line);
  border-bottom-left-radius: 4px;
}
.bubble.error {
  border-color: var(--danger) !important;
  background: rgba(209, 69, 59, 0.06) !important;
  color: var(--danger) !important;
}

.retry-link {
  margin-top: 6px;
  font-size: 12px;
  color: var(--ink-muted);
  text-decoration: none;
  cursor: pointer;
}
.retry-link:hover {
  color: var(--ink);
  text-decoration: underline;
}

.bubble .rendered { white-space: normal; }
.bubble :deep(h4) {
  font-size: 14px;
  font-weight: 700;
  margin: 12px 0 6px;
  color: var(--ink);
  letter-spacing: -0.01em;
}
.bubble :deep(h4:first-child) { margin-top: 0; }
.bubble :deep(strong) { color: var(--ink); font-weight: 600; }
.bubble :deep(code) {
  background: rgba(0, 0, 0, 0.06);
  padding: 1px 6px;
  border-radius: 4px;
  font-family: var(--font-mono);
  font-size: 13px;
  color: var(--ink);
}
.bubble :deep(ul) {
  margin: 6px 0;
  padding-left: 20px;
}
.bubble :deep(li) { margin: 3px 0; }

/* Typing indicator */
.typing {
  display: inline-flex;
  gap: 4px;
  padding: 14px 16px;
  background: var(--surface-hover);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  border-bottom-left-radius: 4px;
}
.typing span {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--ink-dim);
  animation: blink 1.2s infinite;
}
.typing span:nth-child(2) { animation-delay: 0.2s; }
.typing span:nth-child(3) { animation-delay: 0.4s; }
@keyframes blink {
  0%, 60%, 100% { opacity: 0.3; }
  30% { opacity: 1; }
}

/* Input bar */
.inputbar {
  flex-shrink: 0;
  border-top: 1px solid var(--line);
  padding: 14px 16px;
  background: var(--surface);
  display: flex;
  gap: 10px;
  align-items: flex-end;
}
.box {
  flex: 1;
  resize: none;
  min-height: 42px;
  max-height: 140px;
  background: var(--surface-hover);
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  color: var(--ink);
  padding: 10px 14px;
  font-size: 14px;
  font-family: inherit;
  line-height: 1.55;
  outline: none;
  transition: border-color 0.15s;
}
.box:focus { border-color: var(--ink); }
.box:disabled { opacity: 0.6; cursor: not-allowed; }

/* Phase 3 — citation links inside AI bubbles */
.bubble :deep(a.citation) {
  color: var(--ink);
  text-decoration: none;
  border-bottom: 1px dotted var(--line);
}
.bubble :deep(a.citation:hover) {
  text-decoration: underline;
  border-bottom-color: transparent;
}

/* Phase 3 — extract-to-draft action + inline preview panel */
.extract-action {
  margin-top: 8px;
}
.drafts-panel {
  margin-top: 10px;
  width: 100%;
  max-width: 640px;
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  background: var(--surface-hover);
  padding: 12px 14px;
  font-size: 13px;
  color: var(--ink-soft);
}
.drafts-panel-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--ink);
  margin-bottom: 10px;
}
.drafts-empty {
  font-size: 13px;
  color: var(--ink-muted);
  padding: 6px 0;
}
.draft-row {
  padding: 10px;
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  background: var(--surface);
  margin-bottom: 8px;
}
.draft-row.failed {
  border-color: var(--danger);
}
.draft-row-line1 {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.draft-row-unit {
  font-size: 12px;
  color: var(--ink-muted);
}
.draft-row-err {
  font-size: 12px;
  color: var(--danger);
  margin-left: auto;
  max-width: 100%;
}
.drafts-panel-footer {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 4px;
}

@media (max-width: 900px) {
  .chat-view { height: calc(100vh - 96px); }
  .log { padding: 16px 18px; }
  .inputbar { padding: 12px; }
}
</style>
