<template>
  <div class="app-shell" :class="{ 'sidebar-open': mobileOpen }">
    <!-- Mobile top-bar (visible < 900px). Desktop-first: simple hamburger toggle. -->
    <header class="mobile-bar">
      <button class="hamburger" @click="mobileOpen = !mobileOpen" aria-label="菜单">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round">
          <line x1="4" y1="7" x2="20" y2="7" />
          <line x1="4" y1="12" x2="20" y2="12" />
          <line x1="4" y1="17" x2="20" y2="17" />
        </svg>
      </button>
      <span class="mobile-brand">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" aria-hidden="true">
          <circle cx="12" cy="12" r="10" stroke="var(--ink)" stroke-width="1.4" opacity="0.3" />
          <circle cx="12" cy="12" r="5" fill="var(--ink)" />
        </svg>
        Polars Daily Log
      </span>
    </header>

    <!-- Sidebar -->
    <aside class="sidebar" @click.self="mobileOpen = false">
      <div class="sidebar-inner">
        <!-- Brand -->
        <router-link to="/" class="brand" @click="mobileOpen = false">
          <svg width="22" height="22" viewBox="0 0 24 24" fill="none" aria-hidden="true">
            <circle cx="12" cy="12" r="10" stroke="var(--ink)" stroke-width="1.4" opacity="0.3" />
            <circle cx="12" cy="12" r="5" fill="var(--ink)" />
          </svg>
          <span class="brand-name">Polars Daily Log</span>
        </router-link>

        <!-- NAVIGATION -->
        <div class="section-label">NAVIGATION</div>
        <nav class="nav">
          <router-link
            v-for="link in navLinks"
            :key="link.path"
            :to="link.path"
            class="nav-item"
            :class="{ active: isActive(link.path) }"
            @click="mobileOpen = false"
          >
            <span class="nav-label">{{ link.label }}</span>
            <span
              v-if="link.badge != null && link.badge > 0"
              class="nav-badge"
              :class="{ actionable: link.actionable }"
            >{{ link.badge }}</span>
          </router-link>

          <button class="nav-item nav-item-btn" @click="feedbackOpen = true; mobileOpen = false">
            <span class="nav-label">反馈</span>
          </button>
        </nav>

        <!-- DEVICES -->
        <template v-if="devicesAvailable">
          <div class="section-label devices-label">DEVICES</div>
          <div class="devices">
            <div
              v-for="d in visibleDevices"
              :key="d.machine_id || d.name"
              class="device-row"
            >
              <span class="device-dot" :class="{ online: d.online }"></span>
              <span class="device-name" :class="{ dim: !d.online }">{{ d.name }}</span>
              <span v-if="d.primary" class="device-meta">主</span>
              <span v-else-if="!d.online && d.last_seen_text" class="device-meta">{{ d.last_seen_text }}</span>
            </div>
            <div v-if="devices.length > 5" class="device-more">+{{ devices.length - 5 }} more</div>
          </div>
        </template>

        <!-- Bottom user block -->
        <div class="user-block">
          <div class="user-divider"></div>
          <div class="user-row">
            <div class="user-avatar">{{ userInitial }}</div>
            <div class="user-meta">
              <div class="user-name">{{ userName }}</div>
              <div class="user-handle">
                <span v-if="jiraUser" class="jira-dot connected" title="Jira 已登录"></span>
                <span v-else class="jira-dot disconnected" title="Jira 未登录"></span>
                {{ jiraUser || userHandle }}
              </div>
            </div>
          </div>
        </div>
      </div>
    </aside>

    <!-- Overlay for mobile drawer -->
    <div
      v-if="mobileOpen"
      class="mobile-overlay"
      @click="mobileOpen = false"
    ></div>

    <!-- Main content -->
    <main class="main">
      <router-view />
    </main>

    <!-- Feedback Dialog -->
    <el-dialog
      v-model="feedbackOpen"
      title="给我们反馈"
      width="480px"
      :close-on-click-modal="false"
    >
      <div class="feedback-types">
        <button
          v-for="t in feedbackTypes" :key="t.value"
          class="type-chip"
          :class="{ active: feedbackType === t.value }"
          @click="feedbackType = t.value"
          type="button"
        >{{ t.label }}</button>
      </div>
      <el-input
        v-model="feedbackContent"
        type="textarea"
        :rows="5"
        :maxlength="2000"
        show-word-limit
        :placeholder="placeholder"
      />
      <template #footer>
        <el-button round @click="feedbackOpen = false">取消</el-button>
        <el-button
          type="primary" round
          :loading="feedbackSubmitting"
          :disabled="!feedbackContent.trim()"
          @click="submitFeedback"
        >提交</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import api from './api'

const route = useRoute()

// ─── Jira status ────────────────────────────────────────────
const jiraUser = ref(null)

async function checkJiraStatus() {
  try {
    const res = await api.getJiraStatus()
    jiraUser.value = res.data.logged_in ? res.data.username : null
  } catch (e) { /* ignore */ }
}

// ─── User profile ───────────────────────────────────────────
const userName = ref('Conner')
const userHandle = ref('connery')
const userInitial = computed(() => (userName.value || 'U').charAt(0).toUpperCase())

async function loadUser() {
  try {
    const res = await api.getSettings()
    const settings = res.data || {}
    // settings may be a flat dict or nested; try a few common shapes
    const name = settings.user_name || settings.username || settings.display_name
    const handle = settings.user_handle || settings.jira_username || settings.handle
    if (name) userName.value = name
    if (handle) userHandle.value = handle
  } catch (e) { /* fallback to defaults */ }
}

// ─── Sidebar counts (from dashboard API) ────────────────────
const pendingReviewCount = ref(0)
const activityCount = ref(0)

function todayISO() {
  const d = new Date()
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${y}-${m}-${day}`
}

async function loadDashboardCounts() {
  try {
    const res = await api.getDashboard(todayISO())
    const data = res.data || {}
    pendingReviewCount.value = data.pending_review_count || 0
    // Best-effort activity count: sum activity_summary if available
    if (Array.isArray(data.activity_summary)) {
      activityCount.value = data.activity_summary.reduce((s, row) => s + (row.count || row.total || 0), 0)
    } else if (typeof data.activity_count === 'number') {
      activityCount.value = data.activity_count
    }
  } catch (e) { /* ignore */ }
}

// ─── Devices ────────────────────────────────────────────────
const devices = ref([])
const devicesAvailable = ref(false)
const visibleDevices = computed(() => devices.value.slice(0, 5))

function formatLastSeen(ts) {
  if (!ts) return ''
  const then = typeof ts === 'number' ? ts : new Date(ts).getTime()
  if (!then || Number.isNaN(then)) return ''
  const diffMs = Date.now() - then
  const mins = Math.floor(diffMs / 60000)
  if (mins < 1) return 'now'
  if (mins < 60) return `${mins}m`
  const hrs = Math.floor(mins / 60)
  if (hrs < 24) return `${hrs}h`
  const days = Math.floor(hrs / 24)
  return `${days}d`
}

async function loadDevices() {
  try {
    const res = await fetch('/api/machines/status')
    if (!res.ok) {
      // 404 or other: endpoint not available yet — silently hide section
      devicesAvailable.value = false
      return
    }
    const body = await res.json()
    const rows = Array.isArray(body) ? body : (body.machines || body.devices || [])
    devices.value = rows.map(r => ({
      machine_id: r.machine_id || r.id,
      name: r.name || r.hostname || r.machine_id || 'Unknown',
      online: !!r.online,
      primary: !!r.primary,
      last_seen_text: r.online ? '' : formatLastSeen(r.last_seen || r.last_seen_ts || r.last_seen_at),
    }))
    devicesAvailable.value = devices.value.length > 0
  } catch (e) {
    devicesAvailable.value = false
  }
}

// ─── Nav links with badges ──────────────────────────────────
const navLinks = computed(() => [
  { path: '/', label: '概览' },
  {
    path: '/activities',
    label: '活动记录',
    badge: activityCount.value || null,
    actionable: false,
  },
  {
    path: '/my-logs',
    label: 'Worklog 草稿',
    badge: pendingReviewCount.value || null,
    actionable: pendingReviewCount.value > 0,
  },
  { path: '/issues', label: 'Issues' },
  { path: '/settings', label: '设置' },
])

function isActive(path) {
  if (path === '/') return route.path === '/'
  return route.path.startsWith(path)
}

// ─── Feedback dialog ────────────────────────────────────────
const feedbackOpen = ref(false)
const feedbackType = ref('suggestion')
const feedbackContent = ref('')
const feedbackSubmitting = ref(false)

const feedbackTypes = [
  { value: 'bug',        label: '🐛 Bug' },
  { value: 'suggestion', label: '💡 建议' },
  { value: 'other',      label: '💬 其他' },
]

const placeholderMap = {
  bug: '遇到了什么异常？什么操作复现的？',
  suggestion: '希望它怎么变得更好？',
  other: '想说什么都可以～',
}
const placeholder = computed(() => placeholderMap[feedbackType.value] || '')

async function submitFeedback() {
  const content = feedbackContent.value.trim()
  if (!content) return
  feedbackSubmitting.value = true
  try {
    await api.submitFeedback(
      feedbackType.value,
      content,
      route.fullPath || window.location.pathname,
      navigator.userAgent || '',
    )
    ElMessage.success('感谢反馈，已收到！')
    feedbackOpen.value = false
    feedbackContent.value = ''
    feedbackType.value = 'suggestion'
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '提交失败，请稍后再试')
  } finally {
    feedbackSubmitting.value = false
  }
}

// ─── Mobile drawer state ────────────────────────────────────
const mobileOpen = ref(false)

// ─── Polling ────────────────────────────────────────────────
let pollHandle = null
let jiraHandle = null

onMounted(() => {
  checkJiraStatus()
  loadUser()
  loadDashboardCounts()
  loadDevices()
  // refresh sidebar data every 30s
  pollHandle = setInterval(() => {
    loadDashboardCounts()
    loadDevices()
  }, 30 * 1000)
  // jira status every 5 min (unchanged from previous behavior)
  jiraHandle = setInterval(checkJiraStatus, 5 * 60 * 1000)
})

onBeforeUnmount(() => {
  if (pollHandle) clearInterval(pollHandle)
  if (jiraHandle) clearInterval(jiraHandle)
})
</script>

<style scoped>
/* ────────────── Shell ────────────── */
.app-shell {
  min-height: 100vh;
  background: var(--bg);
  font-family: var(--font);
}

/* ────────────── Sidebar ────────────── */
.sidebar {
  position: fixed;
  top: 0;
  left: 0;
  width: 220px;
  height: 100vh;
  background: var(--bg-soft);
  border-right: 1px solid var(--line);
  z-index: 100;
  overflow: hidden;
}

.sidebar-inner {
  display: flex;
  flex-direction: column;
  height: 100%;
  padding: 20px;
  overflow-y: auto;
}

/* Brand */
.brand {
  display: flex;
  align-items: center;
  gap: 10px;
  text-decoration: none;
  color: var(--ink);
  height: 28px;
  margin-bottom: 28px;
  flex-shrink: 0;
}

.brand-name {
  font-size: 15px;
  font-weight: 600;
  color: var(--ink);
  letter-spacing: -0.01em;
}

/* Section labels */
.section-label {
  font-size: 11px;
  font-weight: 500;
  color: var(--ink-dim);
  letter-spacing: 0.1em;
  text-transform: uppercase;
  margin-bottom: 8px;
  padding: 0 4px;
}

.devices-label {
  margin-top: 28px;
}

/* Nav list */
.nav {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.nav-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 10px 16px;
  border-radius: 8px;
  text-decoration: none;
  color: var(--ink-soft);
  font-size: 13px;
  font-weight: 400;
  transition: background 0.15s ease, color 0.15s ease;
  background: transparent;
  border: none;
  cursor: pointer;
  text-align: left;
  font-family: inherit;
  width: 100%;
}

.nav-item:hover {
  background: rgba(0, 0, 0, 0.04);
  color: var(--ink);
}

.nav-item.active {
  background: var(--ink);
  color: #ffffff;
  font-weight: 500;
}

.nav-item.active:hover {
  background: #000000;
  color: #ffffff;
}

.nav-label {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.nav-badge {
  font-size: 10px;
  font-weight: 500;
  line-height: 1;
  padding: 3px 7px;
  border-radius: 999px;
  background: rgba(0, 0, 0, 0.06);
  color: var(--ink-muted);
  flex-shrink: 0;
}

.nav-badge.actionable {
  background: rgba(16, 163, 127, 0.15);
  color: var(--success);
}

.nav-item.active .nav-badge {
  background: rgba(255, 255, 255, 0.18);
  color: #ffffff;
}

.nav-item.active .nav-badge.actionable {
  background: rgba(16, 163, 127, 0.32);
  color: #ffffff;
}

/* Devices */
.devices {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.device-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 6px 8px;
  font-size: 13px;
  color: var(--ink);
}

.device-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--ink-dim);
  flex-shrink: 0;
}

.device-dot.online {
  background: var(--success);
}

.device-name {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.device-name.dim {
  color: var(--ink-muted);
}

.device-meta {
  font-family: var(--font-mono);
  font-size: 10px;
  color: var(--ink-dim);
  flex-shrink: 0;
}

.device-more {
  padding: 4px 8px;
  font-size: 11px;
  color: var(--ink-dim);
}

/* Bottom user block */
.user-block {
  margin-top: auto;
  padding-top: 16px;
  flex-shrink: 0;
}

.user-divider {
  height: 1px;
  background: var(--line);
  margin-bottom: 14px;
}

.user-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 0 4px;
}

.user-avatar {
  width: 28px;
  height: 28px;
  border-radius: 50%;
  background: var(--ink);
  color: #ffffff;
  font-size: 12px;
  font-weight: 600;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.user-meta {
  min-width: 0;
  flex: 1;
}

.user-name {
  font-size: 13px;
  font-weight: 500;
  color: var(--ink);
  line-height: 1.2;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.user-handle {
  font-size: 11px;
  color: var(--ink-dim);
  line-height: 1.4;
  display: inline-flex;
  align-items: center;
  gap: 5px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.jira-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  flex-shrink: 0;
}

.jira-dot.connected {
  background: var(--success);
}

.jira-dot.disconnected {
  background: var(--ink-dim);
}

/* ────────────── Main content ────────────── */
.main {
  margin-left: 220px;
  min-height: 100vh;
  padding: 32px 40px 48px;
  box-sizing: border-box;
}

/* ────────────── Feedback dialog ────────────── */
.feedback-types {
  display: flex;
  gap: 8px;
  margin-bottom: 12px;
}

.type-chip {
  flex: 1;
  padding: 6px 12px;
  font-size: 13px;
  font-weight: 500;
  color: var(--ink-muted);
  background: rgba(0, 0, 0, 0.04);
  border: 1px solid transparent;
  border-radius: 8px;
  cursor: pointer;
  transition: all 0.15s;
  font-family: inherit;
}

.type-chip:hover {
  background: rgba(0, 0, 0, 0.06);
}

.type-chip.active {
  color: var(--ink);
  background: rgba(0, 0, 0, 0.08);
  border-color: var(--line);
}

/* ────────────── Mobile top-bar (< 900px) ────────────── */
.mobile-bar {
  display: none;
}

.mobile-overlay {
  display: none;
}

@media (max-width: 900px) {
  .sidebar {
    transform: translateX(-100%);
    transition: transform 0.22s ease;
    box-shadow: var(--shadow-lg);
  }

  .app-shell.sidebar-open .sidebar {
    transform: translateX(0);
  }

  .main {
    margin-left: 0;
    padding: 64px 20px 40px;
  }

  .mobile-bar {
    position: fixed;
    top: 0;
    left: 0;
    right: 0;
    height: 52px;
    background: rgba(255, 255, 255, 0.9);
    backdrop-filter: saturate(180%) blur(20px);
    -webkit-backdrop-filter: saturate(180%) blur(20px);
    border-bottom: 1px solid var(--line);
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 0 16px;
    z-index: 90;
  }

  .hamburger {
    background: transparent;
    border: none;
    color: var(--ink);
    width: 32px;
    height: 32px;
    border-radius: 8px;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    cursor: pointer;
  }

  .hamburger:hover {
    background: rgba(0, 0, 0, 0.04);
  }

  .mobile-brand {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    font-size: 14px;
    font-weight: 600;
    color: var(--ink);
  }

  .app-shell.sidebar-open .mobile-overlay {
    display: block;
    position: fixed;
    top: 0;
    left: 0;
    right: 0;
    bottom: 0;
    background: rgba(0, 0, 0, 0.3);
    z-index: 95;
  }
}
</style>
