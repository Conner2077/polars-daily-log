<template>
  <div class="dashboard">
    <!-- Page Header -->
    <div class="page-header">
      <div class="page-header-left">
        <h2 class="page-title">今日概览</h2>
        <div class="page-subtitle">{{ selectedDate }} · {{ weekdayLabel }}</div>
      </div>
      <el-date-picker
        v-model="selectedDate"
        type="date"
        value-format="YYYY-MM-DD"
        :clearable="false"
        @change="loadData"
        class="date-picker"
      />
    </div>

    <!-- Row 1: Stat cards -->
    <div class="stats-row">
      <div class="card stat-card">
        <div class="stat-label">工作时长</div>
        <div class="stat-value-line">
          <span class="stat-value">{{ formatHours(extended.work_hours) }}</span>
          <span class="stat-unit">小时</span>
        </div>
        <div class="stat-sub" :class="workHoursDeltaClass">{{ workHoursDeltaText }}</div>
      </div>

      <div class="card stat-card">
        <div class="stat-label">活动记录</div>
        <div class="stat-value-line">
          <span class="stat-value">{{ extended.activity_count ?? 0 }}</span>
          <span class="stat-unit">条</span>
        </div>
        <div class="stat-sub">{{ llmSummaryCountText }}</div>
      </div>

      <div class="card stat-card">
        <div class="stat-label">MyLog</div>
        <div class="stat-value-line">
          <span class="stat-value">{{ extended.pending_drafts_count ?? 0 }}</span>
          <span class="stat-unit">待审批</span>
        </div>
        <div class="stat-sub" :class="draftsSubClass">{{ draftsSubText }}</div>
      </div>

      <div class="card stat-card">
        <div class="stat-label">已推 Jira</div>
        <div class="stat-value-line">
          <span class="stat-value">{{ formatHours(extended.submitted_jira_hours) }}</span>
          <span class="stat-unit">小时</span>
        </div>
        <div class="stat-sub">{{ jiraSubText }}</div>
      </div>
    </div>

    <!-- Row 2: Timeline + Drafts split -->
    <div class="split-row">
      <div class="card split-left">
        <div class="card-head">
          <div>
            <div class="card-title">活动时间轴</div>
            <div class="card-subtitle">{{ timelineSubtitle }} · 按 15 分钟聚合</div>
          </div>
        </div>
        <div class="timeline-legend">
          <span class="legend-item"><span class="legend-swatch legend-active"></span>活动</span>
          <span class="legend-item"><span class="legend-swatch legend-idle"></span>空闲</span>
        </div>
        <div class="timeline-body">
          <TimelineChart :hours="12" :bucket-minutes="15" />
        </div>
      </div>

      <div class="card split-right">
        <div class="card-head">
          <div class="card-title">待审批 MyLog</div>
          <router-link to="/my-logs" class="card-link">全部审批 →</router-link>
        </div>
        <div class="drafts-list" v-if="drafts.length">
          <div
            v-for="(d, idx) in drafts"
            :key="d.issue_key + idx"
            class="draft-row"
            :class="{ 'draft-row--first': idx === 0 }"
          >
            <div class="draft-info">
              <div class="draft-key">{{ d.issue_key }}</div>
              <div class="draft-title">{{ d.title }}</div>
              <div class="draft-meta">
                <span>{{ d.time_range }}</span>
                <span class="dot">·</span>
                <span>{{ formatHours(d.hours) }}h</span>
              </div>
            </div>
            <button
              class="approve-btn"
              :class="idx === drafts.length - 1 ? 'approve-btn--primary' : 'approve-btn--soft'"
              @click="goToDrafts"
            >
              批准
            </button>
          </div>
        </div>
        <div v-else class="empty-state empty-state--drafts">暂无待审批草稿</div>
      </div>
    </div>

    <!-- Row 3: Recent activities table -->
    <div class="card activities-card">
      <div class="card-head">
        <div class="card-title">最近活动</div>
        <router-link to="/activities" class="card-link">查看全部 →</router-link>
      </div>
      <div class="activities-table" v-if="recentActivities.length">
        <div class="activities-header">
          <div class="col col-time">时间</div>
          <div class="col col-app">应用</div>
          <div class="col col-window">窗口 / URL</div>
          <div class="col col-summary">LLM 摘要</div>
          <div class="col col-machine">机器</div>
        </div>
        <div
          v-for="(row, idx) in recentActivities"
          :key="idx"
          class="activities-row"
        >
          <div class="col col-time mono">{{ row.timestamp }}</div>
          <div class="col col-app truncate">{{ row.app_name }}</div>
          <div class="col col-window truncate" :title="row.window_title">{{ row.window_title }}</div>
          <div class="col col-summary truncate" :title="row.llm_summary">{{ row.llm_summary || '—' }}</div>
          <div class="col col-machine mono">{{ row.machine_name }}</div>
        </div>
      </div>
      <div v-else class="empty-state">暂无活动</div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import axios from 'axios'
import api from '../api'
import TimelineChart from '../components/charts/TimelineChart.vue'

const router = useRouter()
const http = axios.create({ baseURL: '/api' })

const selectedDate = ref(new Date().toISOString().split('T')[0])
const dashboard = ref({ pending_review_count: 0, submitted_hours: 0, activity_summary: [] })
const extended = ref({
  work_hours: null,
  activity_count: null,
  pending_drafts_count: null,
  submitted_jira_count: null,
  submitted_jira_hours: null,
  work_hours_delta: null,
  llm_summary_count: null,
  latest_jira_time: null,
})
const drafts = ref([])
const recentActivities = ref([])

const weekdayLabel = computed(() => {
  try {
    const d = new Date(selectedDate.value + 'T00:00:00')
    const map = ['周日', '周一', '周二', '周三', '周四', '周五', '周六']
    return map[d.getDay()]
  } catch (_) {
    return ''
  }
})

const timelineSubtitle = computed(() => {
  const now = new Date()
  const start = new Date(now.getTime() - 12 * 60 * 60 * 1000)
  const fmt = (d) => `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
  return `${fmt(start)} — ${fmt(now)}`
})

function formatHours(v) {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return '0.0'
  return Number(v).toFixed(1)
}

const workHoursDeltaText = computed(() => {
  const d = extended.value.work_hours_delta
  if (d === null || d === undefined) return ''
  const sign = d > 0 ? '+' : ''
  return `${sign}${Number(d).toFixed(1)}h vs 昨日`
})

const workHoursDeltaClass = computed(() => {
  const d = extended.value.work_hours_delta
  if (d === null || d === undefined) return 'stat-sub--muted'
  if (d > 0) return 'stat-sub--success'
  return 'stat-sub--muted'
})

const llmSummaryCountText = computed(() => {
  const n = extended.value.llm_summary_count
  if (n === null || n === undefined) return '—'
  return `${n} 已 LLM 摘要`
})

const draftsSubText = computed(() => {
  const n = extended.value.pending_drafts_count ?? 0
  return n > 0 ? '需要你决定' : '全部已审批'
})

const draftsSubClass = computed(() => {
  const n = extended.value.pending_drafts_count ?? 0
  return n > 0 ? 'stat-sub--warning' : 'stat-sub--muted'
})

const jiraSubText = computed(() => {
  const n = extended.value.submitted_jira_count
  const latest = extended.value.latest_jira_time
  if (!n) return '今日未推'
  if (latest) return `${n} issues · 最新 ${latest}`
  return `${n} issues`
})

function goToDrafts() {
  router.push('/my-logs')
}

async function fetchDashboardBase() {
  try {
    const res = await api.getDashboard(selectedDate.value)
    dashboard.value = res.data || { pending_review_count: 0, submitted_hours: 0, activity_summary: [] }
  } catch (_) {
    dashboard.value = { pending_review_count: 0, submitted_hours: 0, activity_summary: [] }
  }
}

async function fetchExtended() {
  try {
    const res = await http.get('/dashboard/extended', { params: { date: selectedDate.value } })
    const d = res.data || {}
    extended.value = {
      work_hours: d.work_hours ?? null,
      activity_count: d.activity_count ?? null,
      pending_drafts_count: d.pending_drafts_count ?? null,
      submitted_jira_count: d.submitted_jira_count ?? null,
      submitted_jira_hours: d.submitted_jira_hours ?? null,
      work_hours_delta: d.work_hours_delta ?? null,
      llm_summary_count: d.llm_summary_count ?? null,
      latest_jira_time: d.latest_jira_time ?? null,
    }
  } catch (_) {
    // Fallback: derive what we can from the base dashboard response
    const totalSec = (dashboard.value.activity_summary || []).reduce((s, a) => s + (a.total_sec || 0), 0)
    extended.value = {
      work_hours: Number((totalSec / 3600).toFixed(1)),
      activity_count: null,
      pending_drafts_count: dashboard.value.pending_review_count ?? 0,
      submitted_jira_count: null,
      submitted_jira_hours: dashboard.value.submitted_hours ?? 0,
      work_hours_delta: null,
      llm_summary_count: null,
      latest_jira_time: null,
    }
  }
}

async function fetchDrafts() {
  try {
    const res = await http.get('/worklogs/drafts/preview', { params: { limit: 3 } })
    const data = Array.isArray(res.data) ? res.data : []
    drafts.value = data.slice(0, 3).map((d) => ({
      issue_key: d.issue_key || '',
      title: d.title || '',
      hours: d.hours ?? 0,
      time_range: d.time_range || '',
    }))
  } catch (_) {
    drafts.value = []
  }
}

async function fetchRecentActivities() {
  try {
    const res = await http.get('/activities/recent', { params: { limit: 5 } })
    const data = Array.isArray(res.data) ? res.data : []
    recentActivities.value = data.slice(0, 5).map((r) => ({
      timestamp: r.timestamp || '',
      app_name: r.app_name || '',
      window_title: r.window_title || '',
      llm_summary: r.llm_summary || '',
      machine_name: r.machine_name || '',
    }))
  } catch (_) {
    recentActivities.value = []
  }
}

async function loadData() {
  await Promise.all([
    fetchDashboardBase().catch(() => null),
    fetchDrafts().catch(() => null),
    fetchRecentActivities().catch(() => null),
  ])
  // Extended depends on dashboard fallback, so run after
  await fetchExtended().catch(() => null)
}

onMounted(loadData)
</script>

<style scoped>
.dashboard {
  width: 100%;
}

/* ───── Page header ───── */
.page-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 28px;
  gap: 16px;
}

.page-header-left {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.page-title {
  font-size: 22px;
  font-weight: 700;
  letter-spacing: -0.5px;
  color: var(--ink);
  margin: 0;
  line-height: 1.2;
}

.page-subtitle {
  font-size: 13px;
  color: var(--ink-muted);
}

.date-picker {
  width: 180px;
}

/* ───── Card chrome ───── */
.card {
  background: var(--bg);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  padding: 24px;
}

.card-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 16px;
  gap: 12px;
}

.card-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--ink);
  line-height: 1.4;
}

.card-subtitle {
  font-size: 11px;
  color: var(--ink-muted);
  margin-top: 2px;
}

.card-link {
  font-size: 12px;
  color: var(--ink);
  font-weight: 500;
  text-decoration: none;
  line-height: 20px;
}

.card-link:hover {
  opacity: 0.7;
}

/* ───── Row 1: Stat cards ───── */
.stats-row {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 12px;
  margin-bottom: 24px;
}

.stat-card {
  display: flex;
  flex-direction: column;
  gap: 10px;
  min-width: 0;
  padding: 18px 24px;   /* tighter than default card 24px — reduces card height ~15px */
}

.stat-label {
  font-size: 12px;
  color: var(--ink-muted);
}

.stat-value-line {
  display: flex;
  align-items: baseline;
  gap: 8px;
  line-height: 1;
}

.stat-value {
  font-size: 36px;
  font-weight: 700;
  letter-spacing: -1px;
  color: var(--ink);
  font-variant-numeric: tabular-nums;
}

.stat-unit {
  font-size: 14px;
  color: var(--ink-muted);
}

.stat-sub {
  font-size: 11px;
  color: var(--ink-muted);
  min-height: 14px;
}

.stat-sub--success { color: var(--success); }
.stat-sub--warning { color: var(--warning); }
.stat-sub--muted   { color: var(--ink-muted); }

/* ───── Row 2: split ───── */
.split-row {
  display: grid;
  grid-template-columns: 1.2fr 1fr;
  gap: 20px;
  margin-bottom: 24px;
}

/* min-width: 0 is REQUIRED on grid children to prevent long content (e.g.
   long Chinese strings without word-breaks) from forcing the column to
   grow beyond its fr share. Without this, 1.5fr/1fr collapses to content-
   based widths and the narrower column shrinks to near-zero. */
.split-left,
.split-right {
  min-width: 0;
}

.timeline-legend {
  display: flex;
  gap: 20px;
  padding: 0 0 8px;
}

.legend-item {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--ink-muted);
}

.legend-swatch {
  display: inline-block;
  width: 10px;
  height: 10px;
  border-radius: 2px;
}

.legend-active {
  background: var(--ink);
}

.legend-idle {
  background: var(--ink);
  opacity: 0.25;
}

.timeline-body {
  height: 200px;  /* Fixed height so preserveAspectRatio="none" works correctly */
}

/* ───── Drafts list ───── */
.drafts-list {
  display: flex;
  flex-direction: column;
}

.draft-row {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  padding: 14px 0;
  border-top: 1px solid var(--line-soft);
}

.approve-btn {
  flex-shrink: 0;  /* keep the 批准 button visible even under long titles */
}

.draft-row--first {
  border-top: none;
  padding-top: 4px;
}

.draft-info {
  min-width: 0;
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.draft-key {
  font-family: var(--font-mono);
  font-size: 11px;
  font-weight: 500;
  color: var(--warning);
  letter-spacing: 0.02em;
}

.draft-title {
  font-size: 14px;
  font-weight: 500;
  color: var(--ink);
  /* 2-line clamp: long drafts still show the gist without taking over
     the whole right column. Single-line ellipsis hid too much. */
  display: -webkit-box;
  -webkit-line-clamp: 2;
  line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  word-break: break-word;
  line-height: 1.45;
}

.draft-meta {
  font-size: 11px;
  color: var(--ink-muted);
  display: flex;
  gap: 6px;
  align-items: center;
}

.draft-meta .dot { opacity: 0.6; }

.approve-btn {
  border: none;
  border-radius: var(--radius-pill);
  padding: 5px 14px;
  font-size: 11px;
  cursor: pointer;
  font-family: inherit;
  line-height: 1.4;
  transition: opacity 0.15s ease;
}

.approve-btn:hover { opacity: 0.85; }

.approve-btn--soft {
  background: var(--bg-soft);
  color: var(--ink);
}

.approve-btn--primary {
  background: var(--ink);
  color: #ffffff;
}

.empty-state {
  text-align: center;
  padding: 40px 16px;
  color: var(--ink-muted);
  font-size: 13px;
}

.empty-state--drafts {
  padding: 60px 16px;
}

/* ───── Activities table ───── */
.activities-card {
  margin-bottom: 0;
}

.activities-table {
  display: flex;
  flex-direction: column;
}

.activities-header,
.activities-row {
  display: grid;
  grid-template-columns: 56px 120px 1fr 1.6fr 80px;
  gap: 10px 16px;
  align-items: center;
  padding: 5px 4px;
}

.activities-header {
  border-bottom: 1px solid var(--line);
  padding-top: 8px;
  padding-bottom: 10px;
  font-size: 11px;
  color: var(--ink-muted);
  font-weight: 500;
  letter-spacing: 0.02em;
}

.activities-row {
  border-bottom: 1px solid var(--line-soft);
  font-size: 13px;
  color: var(--ink);
  transition: background 0.15s ease;
  line-height: 1.3;
}

.activities-row:last-child {
  border-bottom: none;
}

.activities-row:hover {
  background: var(--bg-soft);
}

.col {
  min-width: 0;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.col-machine {
  text-align: right;
  color: var(--ink-muted);
  font-family: var(--font-mono);
  font-size: 11px;
}

.col-summary {
  color: var(--ink-muted);
}

.col-window {
  color: var(--ink-soft);
}

.col-app {
  font-weight: 500;
}

.mono {
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--ink-muted);
}

/* ───── Responsive: sidebar layout gives ~1200px max ───── */
@media (max-width: 1100px) {
  .stats-row {
    grid-template-columns: repeat(2, 1fr);
  }
  .split-row {
    grid-template-columns: 1fr;
  }
}
</style>
