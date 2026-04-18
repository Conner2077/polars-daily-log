<template>
  <div class="my-logs">
    <!-- Page header -->
    <div class="page-header">
      <div class="page-header-left">
        <h2 class="page-title">MyLog</h2>
        <div class="page-subtitle">{{ headerSubtitle }}</div>
      </div>
      <div class="page-header-right">
        <el-button
          v-for="scope in enabledScopes"
          :key="scope.name"
          round
          :disabled="generating"
          @click="generate(scope.name)"
        >{{ scope.display_name }}</el-button>
        <router-link to="/settings?tab=scopes" class="add-type-link" title="管理总结周期">
          <el-button round size="small">+</el-button>
        </router-link>
      </div>
    </div>

    <!-- Filter tabs: 今日 | 历史 -->
    <div class="toolbar">
      <div class="tag-filters">
        <button
          :class="['primary-tab', { active: isToday }]"
          @click="selectToday"
        >今日</button>

        <div
          class="history-wrap"
          :class="{ open: historyOpen }"
          @mouseenter="openHistory"
          @mouseleave="scheduleCloseHistory"
        >
          <button
            :class="['primary-tab', { active: !isToday }]"
            @click="toggleHistoryOpen"
          >{{ historyTabLabel }}</button>
          <div class="history-inline">
            <button
              v-for="(t, i) in historyFilters"
              :key="t.value"
              :class="['history-chip', { active: activeScope === t.value && !isToday }]"
              :style="{ transitionDelay: historyOpen ? `${i * 45}ms` : '0ms' }"
              @click="selectHistory(t.value)"
            >{{ t.label }}</button>
          </div>
        </div>
      </div>
      <el-date-picker
        v-if="!isToday && showDatePicker"
        v-model="selectedDate"
        type="date"
        value-format="YYYY-MM-DD"
        placeholder="筛选日期"
        @change="loadSummaries"
        clearable
        size="small"
      />
    </div>

    <!-- Generating overlay -->
    <div v-if="generating" class="generating-overlay">
      <div class="generating-card">
        <div class="generating-spinner"></div>
        <div class="generating-text">{{ generatingText }}</div>
        <div class="generating-dots">
          <span v-for="i in 3" :key="i" class="dot" :style="{ animationDelay: `${(i - 1) * 0.2}s` }"></span>
        </div>
      </div>
    </div>

    <!-- Empty state -->
    <el-empty
      v-if="groupedByDate.length === 0"
      description="该日期暂无 MyLog"
      class="empty-card"
    />

    <!-- Summary cards grouped by date -->
    <div
      v-for="group in groupedByDate"
      :key="group.date"
      class="date-group"
    >
      <div class="date-group-header" v-if="!isToday || groupedByDate.length > 1">
        <span class="date-label">{{ group.date }}</span>
        <span v-if="group.totalHours > 0" class="date-hours">{{ group.totalHours }}h</span>
      </div>

      <!-- Output cards within each date -->
      <div
        v-for="outputGroup in group.outputs"
        :key="outputGroup.outputId"
        :class="['log-card', { collapsed: !isToday && !expandedIds.has(outputGroup.key) }]"
      >
        <div
          class="log-header"
          :class="{ clickable: !isToday }"
          @click="!isToday && toggleExpand(outputGroup.key)"
        >
          <div class="log-header-left">
            <span v-if="!isToday" class="expand-arrow" :class="{ open: expandedIds.has(outputGroup.key) }">›</span>
            <span class="section-label">{{ outputGroup.displayName }}</span>
            <span v-if="outputGroup.totalHours > 0" class="log-hours">{{ outputGroup.totalHours }}h</span>
          </div>
          <div class="log-header-right">
            <el-tag v-if="outputGroup.scopeName" size="small" round>{{ scopeLabel(outputGroup.scopeName) }}</el-tag>
          </div>
        </div>

        <div v-show="isToday || expandedIds.has(outputGroup.key)">
          <!-- Single output: one content block -->
          <template v-if="outputGroup.mode === 'single'">
            <div class="issue-section">
              <div class="issue-header" v-if="outputGroup.summaries[0]">
                <div class="issue-header-left">
                  <span class="section-hint" v-if="outputGroup.summaries[0].published_at">
                    ✓ 已推送 {{ outputGroup.summaries[0].published_at }}
                  </span>
                </div>
                <div class="issue-actions">
                  <el-button
                    v-if="editingSummaryId !== outputGroup.summaries[0].id"
                    round size="small"
                    @click="startEdit(outputGroup.summaries[0])"
                  >编辑</el-button>
                  <el-button
                    v-if="outputGroup.publisher && !outputGroup.summaries[0].published_id"
                    type="primary" round size="small"
                    :loading="publishingId === outputGroup.summaries[0].id"
                    @click="publishSummary(outputGroup.summaries[0].id)"
                  >推送{{ publisherLabel(outputGroup.publisher) }}</el-button>
                </div>
              </div>
              <div v-if="editingSummaryId === outputGroup.summaries[0]?.id" class="issue-edit">
                <el-input v-model="editText" type="textarea" :rows="10" />
                <div class="edit-actions">
                  <el-button type="primary" round size="small" @click="saveEdit(outputGroup.summaries[0].id)">保存</el-button>
                  <el-button round size="small" @click="editingSummaryId = null">取消</el-button>
                </div>
              </div>
              <div v-else-if="outputGroup.summaries[0]" class="issue-body markdown-body" v-html="renderMarkdown(outputGroup.summaries[0].content)"></div>
            </div>
          </template>

          <!-- Per-issue output: one section per issue -->
          <template v-else>
            <div v-for="s in outputGroup.summaries" :key="s.id" class="issue-section">
              <div class="issue-header">
                <div class="issue-header-left">
                  <a
                    v-if="!isSkippedIssue(s.issue_key) && jiraIssueUrl(s.issue_key)"
                    class="log-issue log-issue-link"
                    :class="{ 'log-issue--submitted': s.published_id }"
                    :href="jiraIssueUrl(s.issue_key)"
                    target="_blank" rel="noopener"
                  >{{ s.issue_key }}</a>
                  <span v-else class="log-issue">{{ s.issue_key }}</span>
                  <span v-if="issueTitle(s.issue_key)" class="issue-title" :title="issueTitle(s.issue_key)">{{ issueTitle(s.issue_key) }}</span>
                  <span v-if="isSkippedIssue(s.issue_key)" class="skip-hint">（需改为真实 Issue Key 才可推送）</span>
                  <span class="issue-hours">{{ (s.time_spent_sec / 3600).toFixed(1) }}h</span>
                </div>
                <div class="issue-actions">
                  <el-button
                    v-if="editingSummaryId !== s.id"
                    round size="small"
                    @click="startEdit(s)"
                  >编辑</el-button>
                  <el-button
                    v-if="outputGroup.publisher && !s.published_id && !isSkippedIssue(s.issue_key)"
                    type="primary" round size="small"
                    :loading="publishingId === s.id"
                    @click="publishSummary(s.id)"
                  >推送{{ publisherLabel(outputGroup.publisher) }}</el-button>
                  <el-tag v-if="s.published_id" type="success" size="small" round>已推送</el-tag>
                </div>
              </div>

              <div v-if="editingSummaryId === s.id" class="issue-edit">
                <div class="edit-field">
                  <label class="edit-label">Issue Key</label>
                  <el-input v-model="editForm.issue_key" placeholder="e.g. PLS-4387" size="small" />
                </div>
                <div class="edit-field">
                  <label class="edit-label">工时 (秒)</label>
                  <el-input-number v-model="editForm.time_spent_sec" :min="0" :step="1800" size="small" />
                </div>
                <div class="edit-field">
                  <label class="edit-label">摘要</label>
                  <el-input v-model="editForm.content" type="textarea" :rows="3" />
                </div>
                <div class="edit-actions">
                  <el-button type="primary" round size="small" @click="saveEdit(s.id)">保存</el-button>
                  <el-button round size="small" @click="editingSummaryId = null">取消</el-button>
                </div>
              </div>
              <div v-else class="issue-body markdown-body" v-html="renderMarkdown(s.content)"></div>
            </div>
          </template>

          <!-- Card-level actions -->
          <div class="log-actions">
            <el-button
              v-if="outputGroup.publisher && outputGroup.mode === 'per_issue' && outputGroup.unpublishedCount > 0"
              type="primary" round size="small"
              :loading="publishingAll === outputGroup.key"
              @click="publishAllInGroup(outputGroup)"
            >全部推送{{ publisherLabel(outputGroup.publisher) }} ({{ outputGroup.unpublishedCount }})</el-button>

            <el-popconfirm
              title="删除该组所有总结？"
              confirm-button-text="删除"
              cancel-button-text="取消"
              :width="220"
              @confirm="deleteGroup(outputGroup)"
            >
              <template #reference>
                <el-button round size="small" class="danger-btn">删除</el-button>
              </template>
            </el-popconfirm>
          </div>
        </div>
      </div>
    </div>

    <!-- Audit dialog -->
    <el-dialog v-model="auditVisible" title="审计记录" width="600px">
      <el-timeline>
        <el-timeline-item v-for="log in auditLogs" :key="log.id" :timestamp="fmtTime(log.created_at)">
          <strong>{{ log.action }}</strong>
          <pre v-if="log.after_snapshot" class="audit-snapshot">{{ log.after_snapshot }}</pre>
        </el-timeline-item>
      </el-timeline>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { marked } from 'marked'
import api from '../api'

marked.setOptions({ breaks: true, gfm: true })

function renderMarkdown(text) {
  if (!text) return ''
  return marked.parse(text)
}

function todayLocalISO() {
  const d = new Date()
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

const selectedDate = ref(todayLocalISO())
const summaries = ref([])
const scopes = ref([])
const isToday = ref(true)
const activeScope = ref('')
const historyOpen = ref(false)
const generating = ref(false)
const generatingText = ref('')
const expandedIds = ref(new Set())
const editingSummaryId = ref(null)
const editText = ref('')
const editForm = ref({ issue_key: '', time_spent_sec: 0, content: '' })
const publishingId = ref(null)
const publishingAll = ref(null)
const auditVisible = ref(false)
const auditLogs = ref([])
const jiraServerUrl = ref('')
const issueTitleMap = ref({})
let closeHistoryTimer = null

// ── Computed ────────────────────────────────────────────────────────

const enabledScopes = computed(() => scopes.value.filter(s => s.enabled))

const historyFilters = computed(() => [
  { label: '全部', value: '' },
  ...enabledScopes.value.map(s => ({ label: s.display_name, value: s.name })),
])

const showDatePicker = computed(() => {
  if (!activeScope.value) return true
  const scope = scopes.value.find(s => s.name === activeScope.value)
  return scope?.scope_type === 'day'
})

const historyTabLabel = computed(() => {
  if (isToday.value) return '过去'
  const match = historyFilters.value.find(t => t.value === activeScope.value)
  return match ? match.label : '过去'
})

const headerSubtitle = computed(() => {
  const parts = []
  if (selectedDate.value && isToday.value) {
    try {
      const d = new Date(selectedDate.value + 'T00:00:00')
      const weekdays = ['周日', '周一', '周二', '周三', '周四', '周五', '周六']
      parts.push(`${selectedDate.value} · ${weekdays[d.getDay()]}`)
    } catch { parts.push(selectedDate.value) }
  }
  const total = summaries.value.length
  const published = summaries.value.filter(s => s.published_id).length
  if (total > 0) parts.push(`${total} 条总结`)
  if (published > 0) parts.push(`${published} 条已推送`)
  if (total === 0) parts.push('暂无记录')
  return parts.join(' · ')
})

// Group summaries by date → output
const groupedByDate = computed(() => {
  const dateMap = {}
  for (const s of summaries.value) {
    const date = s.date || 'unknown'
    if (!dateMap[date]) dateMap[date] = {}
    const outputId = s.output_id
    if (!dateMap[date][outputId]) {
      dateMap[date][outputId] = {
        outputId,
        displayName: s.output_display_name || '总结',
        mode: s.output_mode || 'single',
        scopeName: s.scope_name,
        publisher: s.output_publisher,
        key: `${date}-${outputId}`,
        summaries: [],
      }
    }
    dateMap[date][outputId].summaries.push(s)
  }

  return Object.entries(dateMap)
    .sort(([a], [b]) => b.localeCompare(a))
    .map(([date, outputs]) => {
      const outputGroups = Object.values(outputs).map(g => ({
        ...g,
        totalHours: parseFloat((g.summaries.reduce((sum, s) => sum + (s.time_spent_sec || 0), 0) / 3600).toFixed(1)),
        unpublishedCount: g.summaries.filter(s => !s.published_id && s.issue_key && !isSkippedIssue(s.issue_key)).length,
      }))
      const totalHours = parseFloat(outputGroups.reduce((sum, g) => sum + g.totalHours, 0).toFixed(1))
      return { date, outputs: outputGroups, totalHours }
    })
})

// ── Actions ─────────────────────────────────────────────────────────

function jiraIssueUrl(key) {
  if (!jiraServerUrl.value || !key) return null
  return `${jiraServerUrl.value.replace(/\/$/, '')}/browse/${key}`
}

function issueTitle(key) { return issueTitleMap.value[key] || '' }

function isSkippedIssue(key) { return ['ALL', 'DAILY'].includes(key) }

function scopeLabel(name) {
  const map = { daily: '每日', weekly: '每周', monthly: '每月', quarterly: '每季' }
  return map[name] || name
}

function publisherLabel(name) {
  if (!name) return ''
  const map = { jira: 'Jira', webhook: 'Webhook', feishu: '飞书' }
  return map[name] || name
}

async function loadScopes() {
  try {
    const r = await api.getScopes()
    scopes.value = r.data
  } catch {
    scopes.value = [
      { name: 'daily', display_name: '每日日志', scope_type: 'day', enabled: 1 },
      { name: 'weekly', display_name: '周报', scope_type: 'week', enabled: 1 },
      { name: 'monthly', display_name: '月报', scope_type: 'month', enabled: 1 },
      { name: 'quarterly', display_name: '季报', scope_type: 'quarter', enabled: 1 },
    ]
  }
}

async function loadJiraContext() {
  try {
    const [settingsRes, issuesRes] = await Promise.all([api.getSettings(), api.getIssues()])
    const urlRow = settingsRes.data.find(s => s.key === 'jira_server_url')
    jiraServerUrl.value = urlRow?.value || ''
    issueTitleMap.value = Object.fromEntries(
      issuesRes.data.map(i => [i.issue_key, i.summary || ''])
    )
  } catch { /* silent */ }
}

async function loadSummaries() {
  const params = {}
  if (activeScope.value) params.scope_name = activeScope.value
  if (selectedDate.value) params.date = selectedDate.value
  try {
    const r = await api.getSummaries(params)
    summaries.value = r.data
  } catch {
    summaries.value = []
  }
}

function selectToday() {
  isToday.value = true
  historyOpen.value = false
  activeScope.value = ''
  selectedDate.value = todayLocalISO()
  loadSummaries()
}

function selectHistory(value) {
  isToday.value = false
  historyOpen.value = false
  activeScope.value = value
  // Clear date for "全部" (value='') and non-day scopes so the API
  // returns all summaries instead of filtering to today's empty date.
  const scope = scopes.value.find(s => s.name === value)
  if (!value || (scope && scope.scope_type !== 'day')) {
    selectedDate.value = null
  }
  loadSummaries()
}

function openHistory() { cancelCloseHistory(); historyOpen.value = true }
function scheduleCloseHistory() { cancelCloseHistory(); closeHistoryTimer = setTimeout(() => { historyOpen.value = false }, 180) }
function cancelCloseHistory() { if (closeHistoryTimer) { clearTimeout(closeHistoryTimer); closeHistoryTimer = null } }
function toggleHistoryOpen() { historyOpen.value = !historyOpen.value; if (historyOpen.value) isToday.value = false }
function toggleExpand(key) {
  const s = new Set(expandedIds.value)
  if (s.has(key)) s.delete(key); else s.add(key)
  expandedIds.value = s
}

async function generate(scopeName) {
  try {
    // Check existing
    const target = todayLocalISO()
    generatingText.value = '检查是否已有记录...'

    // Try to check; if exists, confirm overwrite
    try {
      const existing = await api.getSummaries({ scope_name: scopeName, date: target })
      if (existing.data.length > 0) {
        await ElMessageBox.confirm(
          `${scopeLabel(scopeName)}总结（${target}）已存在，是否覆盖？`,
          '确认覆盖',
          { confirmButtonText: '覆盖', cancelButtonText: '取消', type: 'warning' }
        )
      }
    } catch { /* ignore check errors */ }

    generating.value = true
    const steps = ['正在采集数据...', '正在调用 AI 生成...', 'AI 正在总结...', 'AI 正在炼化...']
    let stepIndex = 0
    generatingText.value = steps[0]
    const timer = setInterval(() => {
      stepIndex++
      if (stepIndex < steps.length) generatingText.value = steps[stepIndex]
    }, 2000)

    try {
      await api.generateScopeSummary(scopeName, target, true)
      clearInterval(timer)
      generatingText.value = '生成完成!'
      await new Promise(r => setTimeout(r, 500))
      ElMessage.success(`${scopeLabel(scopeName)}总结已生成`)
      activeScope.value = scopeName
      isToday.value = scopeName === 'daily'
      selectedDate.value = target
      await loadSummaries()
    } finally { clearInterval(timer) }
  } catch (e) {
    if (e === 'cancel' || e?.toString?.().includes('cancel')) return
    ElMessage.error(e.response?.data?.detail || '生成失败')
  } finally {
    generating.value = false
    generatingText.value = ''
  }
}

function startEdit(summary) {
  editingSummaryId.value = summary.id
  editText.value = summary.content || ''
  editForm.value = {
    issue_key: summary.issue_key || '',
    time_spent_sec: summary.time_spent_sec || 0,
    content: summary.content || '',
  }
}

async function saveEdit(id) {
  const data = {}
  const s = summaries.value.find(s => s.id === id)
  if (s?.issue_key) {
    // per_issue: update all fields
    data.content = editForm.value.content
    data.time_spent_sec = editForm.value.time_spent_sec
    data.issue_key = editForm.value.issue_key
  } else {
    // single: content only
    data.content = editText.value
  }
  await api.updateSummary(id, data)
  editingSummaryId.value = null
  ElMessage.success('已更新')
  await loadSummaries()
}

async function publishSummary(id) {
  publishingId.value = id
  try {
    await api.publishSummary(id)
    ElMessage.success('推送成功')
    await loadSummaries()
  } catch (e) {
    ElMessage.error('推送失败: ' + (e.response?.data?.detail || e.message))
  } finally {
    publishingId.value = null
  }
}

async function publishAllInGroup(group) {
  publishingAll.value = group.key
  try {
    for (const s of group.summaries) {
      if (s.published_id || !s.issue_key || isSkippedIssue(s.issue_key)) continue
      try {
        await api.publishSummary(s.id)
      } catch { /* continue with others */ }
    }
    ElMessage.success('批量推送完成')
    await loadSummaries()
  } finally {
    publishingAll.value = null
  }
}

async function deleteGroup(group) {
  for (const s of group.summaries) {
    await api.deleteSummary(s.id)
  }
  ElMessage.success('已删除')
  await loadSummaries()
}

function fmtTime(s) {
  if (!s) return ''
  const iso = s.includes('T') ? s : s.replace(' ', 'T') + 'Z'
  const d = new Date(iso)
  if (isNaN(d.getTime())) return s
  const pad = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

onMounted(() => { loadScopes(); loadJiraContext(); loadSummaries() })
</script>

<style scoped>
.my-logs { width: 100%; }

/* ───── Page header ───── */
.page-header { display: flex; justify-content: space-between; align-items: flex-start; gap: 16px; margin-bottom: 24px; }
.page-header-left { display: flex; flex-direction: column; gap: 4px; min-width: 0; }
.page-title { font-size: 22px; font-weight: 700; letter-spacing: -0.5px; color: var(--ink); margin: 0; line-height: 1.2; }
.page-subtitle { font-size: 13px; color: var(--ink-muted); }
.page-header-right { display: flex; gap: 8px; align-items: center; flex-shrink: 0; }

/* ───── Toolbar ───── */
.toolbar { display: flex; align-items: center; gap: 12px; margin-bottom: 20px; }
.tag-filters { display: flex; gap: 10px; flex: 1; align-items: center; }

.primary-tab {
  display: inline-flex; align-items: center; justify-content: center; gap: 6px;
  height: 34px; padding: 0 18px; box-sizing: border-box; line-height: 1;
  border-radius: 999px; background: transparent; border: 1px solid var(--line);
  color: var(--ink-muted); font-size: 14px; font-weight: 500; font-family: inherit;
  cursor: pointer; transition: background 0.15s ease, color 0.15s ease, border-color 0.15s ease;
}
.primary-tab:hover { color: var(--ink); border-color: var(--ink-muted); }
.primary-tab.active { background: var(--ink); color: #fff; border-color: var(--ink); }

.history-wrap { display: inline-flex; align-items: center; gap: 8px; }
.history-inline {
  display: inline-flex; align-items: center; gap: 6px; overflow: hidden;
  max-width: 0; opacity: 0; transition: max-width 0.32s ease, opacity 0.2s ease;
}
.history-wrap.open .history-inline { max-width: 520px; opacity: 1; }

.history-chip {
  height: 30px; padding: 0 14px; border-radius: 999px; background: var(--bg);
  border: 1px solid var(--line); color: var(--ink-soft); font-size: 13px;
  font-family: inherit; font-weight: 500; cursor: pointer; white-space: nowrap;
  opacity: 0; transform: translateX(-8px);
  transition: background 0.15s ease, color 0.15s ease, border-color 0.15s ease,
              opacity 0.25s ease, transform 0.25s ease;
}
.history-wrap.open .history-chip { opacity: 1; transform: translateX(0); }
.history-chip:hover { border-color: var(--ink-muted); color: var(--ink); }
.history-chip.active { background: var(--ink); color: #fff; border-color: var(--ink); }

/* ───── Date groups ───── */
.date-group { margin-bottom: 24px; }
.date-group-header {
  display: flex; align-items: baseline; gap: 12px;
  margin-bottom: 12px; padding-bottom: 8px; border-bottom: 1px solid var(--line-soft);
}
.date-label { font-size: 15px; font-weight: 600; color: var(--ink); }
.date-hours { font-size: 14px; font-weight: 700; color: var(--ink); font-variant-numeric: tabular-nums; }

/* ───── Log card ───── */
.empty-card { background: var(--bg); border: 1px solid var(--line); border-radius: var(--radius); padding: 40px 24px; }
.log-card { background: var(--bg); border: 1px solid var(--line); border-radius: var(--radius); padding: 24px; margin-bottom: 16px; transition: box-shadow 0.2s ease; }
.log-card.collapsed { padding: 16px 24px; cursor: pointer; }
.log-card.collapsed:hover { box-shadow: 0 2px 12px -4px rgba(0, 0, 0, 0.08); }
.log-header { display: flex; justify-content: space-between; align-items: center; gap: 12px; margin-bottom: 16px; }
.log-header.clickable { cursor: pointer; user-select: none; }
.log-header-left { display: flex; align-items: baseline; gap: 12px; min-width: 0; flex: 1; }
.log-header-right { display: flex; gap: 6px; align-items: center; flex-shrink: 0; }
.log-hours { font-size: 14px; font-weight: 700; color: var(--ink); font-variant-numeric: tabular-nums; }

.expand-arrow { display: inline-block; font-size: 16px; color: var(--ink-dim); transition: transform 0.2s ease; margin-right: 4px; width: 12px; text-align: center; }
.expand-arrow.open { transform: rotate(90deg); color: var(--ink); }

/* ───── Issue sections ───── */
.issue-section { padding: 14px 0; border-top: 1px solid var(--line-soft); }
.issue-section:first-of-type { border-top: none; padding-top: 0; }
.issue-header { display: flex; justify-content: space-between; align-items: center; gap: 12px; margin-bottom: 6px; }
.issue-header-left { display: flex; align-items: center; gap: 10px; min-width: 0; flex: 1 1 auto; flex-wrap: wrap; }
.issue-actions { display: flex; gap: 6px; align-items: center; justify-content: flex-end; flex-shrink: 0; }

.section-label { font-size: 14px; font-weight: 600; color: var(--ink); }
.section-hint { font-size: 12px; color: var(--ink-muted); }

.log-issue { font-family: var(--font-mono); font-size: 11px; font-weight: 500; color: var(--ink-muted); letter-spacing: 0.02em; flex-shrink: 0; line-height: 1.5; }
.log-issue--submitted { color: var(--ink-dim); }
.log-issue-link { text-decoration: none; transition: opacity 0.15s ease; }
.log-issue-link:hover { opacity: 0.7; }
.issue-title { font-size: 14px; font-weight: 500; color: var(--ink); max-width: 420px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; flex: 0 1 auto; min-width: 0; }
.issue-hours { font-size: 12px; color: var(--ink-muted); font-variant-numeric: tabular-nums; flex-shrink: 0; margin-left: auto; }
.skip-hint { font-size: 11px; color: var(--ink-dim); }

.issue-body { font-size: 14px; line-height: 1.7; color: var(--ink-soft); padding-left: 4px; }

/* ───── Markdown ───── */
.markdown-body :deep(h1) { font-size: 18px; font-weight: 700; color: var(--ink); margin: 12px 0 8px; }
.markdown-body :deep(h2) { font-size: 16px; font-weight: 700; color: var(--ink); margin: 12px 0 6px; }
.markdown-body :deep(h3) { font-size: 15px; font-weight: 600; color: var(--ink); margin: 10px 0 6px; }
.markdown-body :deep(p) { font-size: 14px; line-height: 1.7; color: var(--ink-soft); margin: 6px 0; }
.markdown-body :deep(ul), .markdown-body :deep(ol) { font-size: 14px; line-height: 1.7; color: var(--ink-soft); margin: 6px 0; padding-left: 20px; }
.markdown-body :deep(li) { margin-bottom: 4px; }
.markdown-body :deep(code) { font-family: var(--font-mono); font-size: 12.5px; background: var(--bg-soft); color: var(--ink); padding: 1px 5px; border-radius: 4px; }
.markdown-body :deep(pre) { background: var(--bg-code); color: #e5e5e5; padding: 12px 16px; border-radius: var(--radius-sm); font-family: var(--font-mono); font-size: 12.5px; line-height: 1.65; overflow-x: auto; margin: 8px 0; }
.markdown-body :deep(pre code) { background: transparent; color: inherit; padding: 0; }
.markdown-body :deep(blockquote) { border-left: 2px solid var(--line); padding-left: 12px; color: var(--ink-muted); margin: 8px 0; }
.markdown-body :deep(a) { color: var(--ink); text-decoration: underline; text-underline-offset: 3px; text-decoration-color: var(--line); }
.markdown-body :deep(a:hover) { text-decoration-color: var(--ink); }
.markdown-body :deep(strong) { font-weight: 600; color: var(--ink); }
.markdown-body :deep(hr) { border: none; border-top: 1px solid var(--line-soft); margin: 12px 0; }

/* ───── Edit mode ───── */
.issue-edit { padding: 4px 0; }
.edit-field { margin-bottom: 8px; }
.edit-label { font-size: 13px; color: var(--ink-muted); margin-bottom: 4px; display: block; }
.edit-actions { display: flex; gap: 8px; margin-top: 8px; }

/* ───── Card actions ───── */
.log-actions { display: flex; gap: 8px; margin-top: 16px; padding-top: 16px; border-top: 1px solid var(--line-soft); }

/* ───── Audit ───── */
.audit-snapshot { font-family: var(--font-mono); font-size: 12px; background: var(--bg-soft); color: var(--ink); padding: 8px 12px; border-radius: var(--radius-sm); max-height: 200px; overflow: auto; margin-top: 6px; }

/* ───── Generating overlay ───── */
.generating-overlay { position: fixed; top: 0; left: 0; right: 0; bottom: 0; background: rgba(255,255,255,0.7); backdrop-filter: blur(2px); display: flex; align-items: center; justify-content: center; z-index: 1000; animation: fadeIn 0.2s ease; }
.generating-card { background: var(--bg); border: 1px solid var(--line); border-radius: var(--radius); padding: 32px 48px; text-align: center; }
.generating-spinner { width: 32px; height: 32px; border: 2px solid var(--line); border-top-color: var(--ink); border-radius: 50%; margin: 0 auto 16px; animation: spin 0.8s linear infinite; }
.generating-text { font-size: 14px; font-weight: 500; color: var(--ink-muted); margin-bottom: 12px; min-width: 200px; }
.generating-dots { display: flex; justify-content: center; gap: 5px; }
.dot { width: 5px; height: 5px; border-radius: 50%; background: var(--ink-muted); animation: bounce 1.2s ease-in-out infinite; }

@keyframes fadeIn { from { opacity: 0; } to { opacity: 1; } }
@keyframes spin { to { transform: rotate(360deg); } }
@keyframes bounce { 0%, 60%, 100% { transform: translateY(0); opacity: 0.4; } 30% { transform: translateY(-6px); opacity: 1; } }
</style>
