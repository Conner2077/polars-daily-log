<template>
  <div class="my-logs">
    <!-- Page header -->
    <div class="page-header">
      <div class="page-header-left">
        <h2 class="page-title">MyLog</h2>
        <div class="page-subtitle">{{ headerSubtitle }}</div>
      </div>
      <div class="page-header-right">
        <el-button round :disabled="generating" @click="generate('daily')">总结当天</el-button>
        <el-button round :disabled="generating" @click="generate('weekly')">总结这周</el-button>
        <el-button round :disabled="generating" @click="generate('monthly')">总结当月</el-button>
        <el-popover trigger="click" :width="300">
          <template #reference>
            <el-button round :disabled="generating">自定义</el-button>
          </template>
          <div>
            <p class="popover-hint">选择日期范围</p>
            <el-date-picker
              v-model="customRange"
              type="daterange"
              value-format="YYYY-MM-DD"
              start-placeholder="开始日期"
              end-placeholder="结束日期"
              class="full-width mb-12"
            />
            <el-button
              type="primary"
              size="small"
              round
              :disabled="!customRange || customRange.length < 2 || generating"
              @click="generateCustom"
              class="full-width"
            >
              生成总结
            </el-button>
          </div>
        </el-popover>
      </div>
    </div>

    <!-- Filter tabs: 今日 | 历史 (hover to inline-expand sub-options) -->
    <div class="toolbar">
      <div class="tag-filters">
        <!-- 今日 = daily draft for today -->
        <button
          :class="['primary-tab', { active: isToday }]"
          @click="selectToday"
        >
          今日
        </button>

        <!-- 历史 + inline expansion -->
        <div
          class="history-wrap"
          :class="{ open: historyOpen }"
          @mouseenter="openHistory"
          @mouseleave="scheduleCloseHistory"
        >
          <button
            :class="['primary-tab', { active: !isToday }]"
            @click="toggleHistoryOpen"
          >
            {{ historyTabLabel }}
          </button>
          <div class="history-inline">
            <button
              v-for="(t, i) in historyFilters"
              :key="t.value"
              :class="['history-chip', { active: activeTag === t.value && !isToday }]"
              :style="{ transitionDelay: historyOpen ? `${i * 45}ms` : '0ms' }"
              @click="selectHistory(t.value)"
            >
              {{ t.label }}
            </button>
          </div>
        </div>
      </div>
      <el-date-picker
        v-if="!isToday && (activeTag === '' || activeTag === 'daily' || activeTag === 'custom')"
        v-model="selectedDate"
        type="date"
        value-format="YYYY-MM-DD"
        placeholder="筛选日期"
        @change="loadDrafts"
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
      v-if="drafts.length === 0"
      description="该日期暂无 MyLog"
      class="empty-card"
    />

    <!-- Log cards -->
    <div
      v-for="draft in drafts"
      :key="draft.id"
      :class="['log-card', { collapsed: !isToday && !expandedIds.has(draft.id) }]"
    >
      <!-- Card header (clickable to toggle in "过去" mode) -->
      <div
        class="log-header"
        :class="{ clickable: !isToday }"
        @click="!isToday && toggleExpand(draft.id)"
      >
        <div class="log-header-left">
          <span v-if="!isToday" class="expand-arrow" :class="{ open: expandedIds.has(draft.id) }">›</span>
          <span class="log-period">
            {{ draft.period_start && draft.period_end && draft.period_start !== draft.period_end
              ? `${draft.period_start} ~ ${draft.period_end}`
              : draft.date }}
          </span>
          <span class="log-hours">{{ (draft.time_spent_sec / 3600).toFixed(1) }}h</span>
          <span v-if="draft.user_edited" class="edited-hint">已编辑</span>
        </div>
        <div class="log-header-right">
          <el-tag size="small" round>{{ tagLabel(draft.tag) }}</el-tag>
          <el-tag
            v-if="isDailyTag(draft)"
            :type="statusTagType(draft.status)"
            size="small"
            round
          >{{ statusLabel(draft.status) }}</el-tag>
        </div>
      </div>

      <!-- Collapsed preview (only in "过去" mode when not expanded) -->
      <div v-if="!isToday && !expandedIds.has(draft.id)" class="log-preview">
        {{ draftPreview(draft) }}
      </div>

      <!-- Card body — hidden when collapsed in "过去" mode -->
      <div v-show="isToday || expandedIds.has(draft.id)" class="log-body">

      <!-- Daily log: 全部活动 (raw, all-inclusive) -->
      <div v-if="isDailyTag(draft) && draft.full_summary" class="issue-section">
        <div class="issue-header">
          <div class="issue-header-left">
            <span class="section-label">全部活动</span>
            <span class="section-hint">原汁原味，包含所有活动</span>
          </div>
          <div class="issue-actions">
            <el-button
              v-if="editingFullId !== draft.id"
              round size="small"
              @click="startFullEdit(draft)"
            >编辑</el-button>
          </div>
        </div>
        <div v-if="editingFullId === draft.id" class="issue-edit">
          <el-input v-model="fullEditText" type="textarea" :rows="10" />
          <div class="edit-actions">
            <el-button type="primary" round size="small" @click="saveFullEdit(draft.id)">保存</el-button>
            <el-button round size="small" @click="editingFullId = null">取消</el-button>
          </div>
        </div>
        <div v-else class="issue-body markdown-body" v-html="renderMarkdown(draft.full_summary)"></div>
      </div>
      <div v-else-if="isDailyTag(draft) && !draft.full_summary && parseIssues(draft.summary)" class="issue-section">
        <div class="issue-header">
          <span class="section-hint">旧版日志无「全部活动」原始记录。重新生成可获得。</span>
        </div>
      </div>

      <!-- Daily log: show per-issue sections -->
      <template v-if="isDailyTag(draft) && parseIssues(draft.summary)">
        <div v-for="(issue, idx) in parseIssues(draft.summary)" :key="idx" class="issue-section">
          <div class="issue-header">
            <div class="issue-header-left">
              <a
                v-if="!isSkippedIssue(issue.issue_key) && jiraIssueUrl(issue.issue_key)"
                :class="['log-issue', 'log-issue-link', issueKeyClass(draft, issue)]"
                :href="jiraIssueUrl(issue.issue_key)"
                target="_blank" rel="noopener"
              >{{ issue.issue_key }}</a>
              <span v-else :class="['log-issue', issueKeyClass(draft, issue)]">{{ issue.issue_key }}</span>
              <span
                v-if="issueTitle(issue.issue_key)"
                class="issue-title"
                :title="issueTitle(issue.issue_key)"
              >{{ issueTitle(issue.issue_key) }}</span>
              <span v-if="isSkippedIssue(issue.issue_key)" class="skip-hint">（需改为真实 Issue Key 才可提交）</span>
              <span class="issue-hours">{{ issue.time_spent_hours }}h</span>
            </div>
            <div class="issue-actions">
              <template v-if="draft.status === 'pending_review'">
                <el-button
                  v-if="!isSkippedIssue(issue.issue_key)"
                  type="primary" round size="small"
                  @click="approveAndSubmitIssue(draft.id, idx)"
                  :loading="submittingIssue === `${draft.id}-${idx}`"
                >通过并提交</el-button>
                <el-button
                  v-if="editingIssue !== `${draft.id}-${idx}`"
                  round size="small"
                  @click="startIssueEdit(draft.id, idx, issue)"
                >编辑</el-button>
              </template>
              <template v-if="(draft.status === 'approved' || draft.status === 'auto_approved') && !issue.jira_worklog_id">
                <el-button
                  v-if="!isSkippedIssue(issue.issue_key)"
                  type="primary" round size="small"
                  :loading="submittingIssue === `${draft.id}-${idx}`"
                  @click="submitSingleIssue(draft.id, idx)"
                >提交到 Jira</el-button>
              </template>
              <el-tag v-if="issue.jira_worklog_id" type="success" size="small" round>已提交</el-tag>
            </div>
          </div>

          <!-- Edit mode for this issue -->
          <div v-if="editingIssue === `${draft.id}-${idx}`" class="issue-edit">
            <div class="edit-field">
              <label class="edit-label">Issue Key</label>
              <el-input v-model="issueEditForm.issue_key" placeholder="e.g. PLS-4387" size="small" />
            </div>
            <div class="edit-field">
              <label class="edit-label">工时 (小时)</label>
              <el-input-number v-model="issueEditForm.time_spent_hours" :min="0" :step="0.5" :precision="1" size="small" />
            </div>
            <div class="edit-field">
              <label class="edit-label">摘要</label>
              <el-input v-model="issueEditForm.summary" type="textarea" :rows="3" />
            </div>
            <div class="edit-actions">
              <el-button type="primary" round size="small" @click="saveIssueEdit(draft.id, idx)">保存</el-button>
              <el-button round size="small" @click="editingIssue = null">取消</el-button>
            </div>
          </div>
          <div v-else class="issue-body markdown-body" v-html="renderMarkdown(issue.summary)"></div>
        </div>
      </template>

      <!-- Non-daily or fallback: markdown summary -->
      <template v-else-if="!isDailyTag(draft) || !draft.full_summary">
        <div class="issue-section">
          <div class="issue-body markdown-body" v-html="renderMarkdown(draft.summary)"></div>
        </div>
      </template>

      <!-- Card-level action buttons -->
      <div v-if="isDailyTag(draft) && draft.status === 'pending_review'" class="log-actions">
        <el-button type="primary" round size="small" @click="approve(draft.id)">一键通过</el-button>
        <el-popconfirm
          title="归档后进回收站，可在 Settings 恢复。"
          confirm-button-text="归档"
          cancel-button-text="取消"
          :width="260"
          @confirm="archiveDraft(draft.id)"
        >
          <template #reference>
            <el-button round size="small" class="danger-btn">归档</el-button>
          </template>
        </el-popconfirm>
      </div>
      <div v-else-if="isDailyTag(draft) && (draft.status === 'approved' || draft.status === 'auto_approved')" class="log-actions">
        <el-button type="primary" round size="small" @click="submitAll(draft.id)">全部提交到 Jira</el-button>
      </div>
      <div v-else-if="draft.status === 'submitted'" class="log-actions">
        <el-button round size="small" @click="showAudit(draft.id)">查看审计记录</el-button>
      </div>
      </div><!-- /.log-body -->
    </div>

    <el-dialog v-model="auditVisible" title="审计记录" width="600px">
      <el-timeline>
        <el-timeline-item v-for="log in auditLogs" :key="log.id" :timestamp="log.created_at">
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

// LOCAL date — avoid UTC shift past midnight
function todayLocalISO() {
  const d = new Date()
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}
const selectedDate = ref(todayLocalISO())
const drafts = ref([])
const editingIssue = ref(null)
const issueEditForm = ref({ issue_key: '', time_spent_hours: 0, summary: '' })
const editingFullId = ref(null)
const fullEditText = ref('')
const auditVisible = ref(false)
const auditLogs = ref([])
const activeTag = ref('')
const customRange = ref(null)
const generating = ref(false)
const generatingText = ref('')
const submittingIssue = ref(null)
const jiraServerUrl = ref('')
const issueTitleMap = ref({})   // { 'PLS-4387': '2026 Q1 任务记录', ... }

function jiraIssueUrl(key) {
  if (!jiraServerUrl.value || !key) return null
  return `${jiraServerUrl.value.replace(/\/$/, '')}/browse/${key}`
}

function issueTitle(key) {
  return issueTitleMap.value[key] || ''
}

async function loadJiraContext() {
  try {
    const [settingsRes, issuesRes] = await Promise.all([api.getSettings(), api.getIssues()])
    const urlRow = settingsRes.data.find(s => s.key === 'jira_server_url')
    jiraServerUrl.value = urlRow?.value || ''
    issueTitleMap.value = Object.fromEntries(
      issuesRes.data.map(i => [i.issue_key, i.summary || ''])
    )
  } catch { /* silent — link + title are enhancements, not critical */ }
}

const tagFilters = [
  { label: '全部', value: '' },
  { label: '每日', value: 'daily' },
  { label: '每周', value: 'weekly' },
  { label: '每月', value: 'monthly' },
  { label: '自定义', value: 'custom' },
]

// "历史" dropdown options (all options except "today")
const historyFilters = [
  { label: '全部', value: '' },
  { label: '每日', value: 'daily' },
  { label: '每周', value: 'weekly' },
  { label: '每月', value: 'monthly' },
  { label: '自定义', value: 'custom' },
]

// "今日" is special: it's the daily draft for today. We track it with
// a dedicated flag because the tag filter alone ('daily' + today's date)
// can't distinguish "today's daily" from "all daily history".
const isToday = ref(true)
const expandedIds = ref(new Set())
const historyOpen = ref(false)
let closeHistoryTimer = null

// Label for the primary tab — shows selected sub-filter when
// user has picked one, else just "过去".
const historyTabLabel = computed(() => {
  if (isToday.value) return '过去'
  const match = historyFilters.find(t => t.value === activeTag.value)
  return match ? match.label : '过去'
})

function selectToday() {
  isToday.value = true
  historyOpen.value = false
  // '' + today's date → shows only today's drafts via getWorklogs(date)
  activeTag.value = ''
  selectedDate.value = todayLocalISO()
  loadDrafts()
}

function selectHistory(value) {
  isToday.value = false
  historyOpen.value = false
  // Clear date filter when switching to all/weekly/monthly (inappropriate),
  // keep it for daily/custom
  if (value !== 'daily' && value !== 'custom') {
    selectedDate.value = null
  }
  filterByTag(value)
}

function openHistory() {
  cancelCloseHistory()
  historyOpen.value = true
}

function scheduleCloseHistory() {
  cancelCloseHistory()
  closeHistoryTimer = setTimeout(() => { historyOpen.value = false }, 180)
}

function cancelCloseHistory() {
  if (closeHistoryTimer) {
    clearTimeout(closeHistoryTimer)
    closeHistoryTimer = null
  }
}

function toggleHistoryOpen() {
  historyOpen.value = !historyOpen.value
  if (historyOpen.value) isToday.value = false
}

function toggleExpand(id) {
  const s = new Set(expandedIds.value)
  if (s.has(id)) s.delete(id)
  else s.add(id)
  expandedIds.value = s
}

function draftPreview(draft) {
  // Build a short one-line summary from the draft content
  const parts = []
  // Try issue keys + summaries
  const issues = parseIssues(draft.summary)
  if (issues && issues.length) {
    for (const iss of issues.slice(0, 3)) {
      const label = iss.issue_key === 'OTHER' ? '其他' : iss.issue_key
      const text = (iss.summary || '').replace(/[\n\r]+/g, ' ').trim()
      parts.push(text ? `${label}: ${text}` : label)
    }
    if (issues.length > 3) parts.push(`+${issues.length - 3} 项`)
  } else if (draft.full_summary) {
    parts.push(draft.full_summary.replace(/[\n\r#*]+/g, ' ').trim())
  } else if (draft.summary && typeof draft.summary === 'string') {
    parts.push(draft.summary.replace(/[\n\r#*]+/g, ' ').trim())
  }
  const joined = parts.join(' · ')
  return joined.length > 120 ? joined.slice(0, 120) + '…' : joined || '暂无内容'
}

function tagLabel(tag) {
  const map = { daily: '每日', weekly: '每周', monthly: '每月', custom: '自定义' }
  return map[tag] || '每日'
}

function statusLabel(status) {
  const map = {
    pending_review: '待审批', approved: '已通过', auto_approved: '自动通过',
    submitted: '已提交', rejected: '已驳回', auto_rejected: '自动驳回', archived: '已归档'
  }
  return map[status] || status
}

function statusTagType(status) {
  switch (status) {
    case 'pending_review':
      return 'warning'
    case 'approved':
    case 'auto_approved':
      return 'success'
    case 'submitted':
      return 'info'
    case 'rejected':
    case 'auto_rejected':
      return 'danger'
    case 'archived':
    default:
      return 'info'
  }
}

function issueKeyClass(draft, issue) {
  if (issue.jira_worklog_id) return 'log-issue--submitted'
  if (draft.status === 'pending_review') return 'log-issue--pending'
  return ''
}

function isDailyTag(draft) {
  return draft.tag === 'daily' || !draft.tag
}

function isSkippedIssue(key) {
  return ['OTHER', 'ALL', 'DAILY'].includes(key)
}

function parseIssues(summary) {
  try {
    const parsed = JSON.parse(summary)
    if (Array.isArray(parsed) && parsed.length > 0) return parsed
  } catch {}
  return null
}

const headerSubtitle = computed(() => {
  const parts = []
  if (selectedDate.value && (activeTag.value === '' || activeTag.value === 'daily')) {
    try {
      const d = new Date(selectedDate.value + 'T00:00:00')
      const weekdays = ['周日', '周一', '周二', '周三', '周四', '周五', '周六']
      parts.push(`${selectedDate.value} · ${weekdays[d.getDay()]}`)
    } catch {
      parts.push(selectedDate.value)
    }
  }
  const pending = drafts.value.filter(d => d.status === 'pending_review').length
  const submitted = drafts.value.filter(d => d.status === 'submitted').length
  if (pending > 0) parts.push(`${pending} 条待审批`)
  if (submitted > 0) parts.push(`${submitted} 条已提交`)
  if (drafts.value.length === 0) parts.push('暂无记录')
  return parts.join(' · ')
})

async function loadDrafts() {
  if (activeTag.value) {
    const res = await api.getWorklogsByTag(activeTag.value)
    drafts.value = res.data
  } else {
    const res = await api.getWorklogs(selectedDate.value)
    drafts.value = res.data
  }
}

async function filterByTag(tag) {
  activeTag.value = tag
  await loadDrafts()
}

async function generate(type, startDate = null, endDate = null) {
  try {
    generatingText.value = '检查是否已有记录...'
    const check = await api.checkPeriodExists(type, startDate, endDate)
    if (check.data.exists) {
      const period = check.data.period_start === check.data.period_end
        ? check.data.period_start
        : `${check.data.period_start} ~ ${check.data.period_end}`
      await ElMessageBox.confirm(
        `${tagLabel(type)}总结（${period}）已存在，是否覆盖？`,
        '确认覆盖',
        { confirmButtonText: '覆盖', cancelButtonText: '取消', type: 'warning' }
      )
    }

    generating.value = true
    const steps = {
      daily: ['正在采集 Git 提交记录...', '正在分析活动数据...', '正在调用 AI 生成日志...', 'AI 正在总结...', 'AI 正在炼化...'],
      weekly: ['正在读取本周每日日志...', '正在调用 AI 生成周报...', 'AI 正在总结...', 'AI 正在炼化...'],
      monthly: ['正在读取本月每日日志...', '正在调用 AI 生成月报...', 'AI 正在总结...', 'AI 正在炼化...'],
      custom: ['正在读取指定周期日志...', '正在调用 AI 生成总结...', 'AI 正在总结...', 'AI 正在炼化...'],
    }
    const typeSteps = steps[type] || steps.daily
    let stepIndex = 0
    generatingText.value = typeSteps[0]
    const timer = setInterval(() => {
      stepIndex++
      if (stepIndex < typeSteps.length) {
        generatingText.value = typeSteps[stepIndex]
      }
    }, 2000)

    try {
      await api.generateSummary(type, startDate, endDate, true)
      clearInterval(timer)
      generatingText.value = '生成完成!'
      await new Promise(r => setTimeout(r, 500))
      ElMessage.success(`${tagLabel(type)}总结已生成`)
      activeTag.value = type
      await loadDrafts()
    } finally {
      clearInterval(timer)
    }
  } catch (e) {
    if (e === 'cancel' || e?.toString?.().includes('cancel')) return
    ElMessage.error(e.response?.data?.detail || '生成失败')
  } finally {
    generating.value = false
    generatingText.value = ''
  }
}

async function generateCustom() {
  if (!customRange.value || customRange.value.length < 2) return
  await generate('custom', customRange.value[0], customRange.value[1])
}

function startIssueEdit(draftId, idx, issue) {
  editingIssue.value = `${draftId}-${idx}`
  issueEditForm.value = {
    issue_key: issue.issue_key,
    time_spent_hours: issue.time_spent_hours,
    summary: issue.summary,
  }
}

async function saveIssueEdit(draftId, idx) {
  await api.updateDraftIssue(draftId, idx, issueEditForm.value)
  editingIssue.value = null
  ElMessage.success('已更新')
  await loadDrafts()
}

async function approve(id) {
  await api.approveDraft(id)
  ElMessage.success('已通过')
  await loadDrafts()
}

async function archiveDraft(id) {
  // Archive = soft-delete via reject endpoint + set status=archived via update
  // For now we reuse rejectDraft to mark as rejected (treated same as archived in UI)
  // TODO: add a dedicated archive endpoint; for now reject semantics cover it.
  await api.rejectDraft(id)
  ElMessage.warning('已归档')
  await loadDrafts()
}

function startFullEdit(draft) {
  editingFullId.value = draft.id
  fullEditText.value = draft.full_summary || ''
}

async function saveFullEdit(draftId) {
  await api.updateDraft(draftId, { full_summary: fullEditText.value })
  editingFullId.value = null
  ElMessage.success('已更新')
  await loadDrafts()
}

async function submitAll(id) {
  try {
    await api.submitDraft(id)
    ElMessage.success('已全部提交到 Jira')
    await loadDrafts()
  } catch (e) {
    ElMessage.error('提交失败: ' + (e.response?.data?.detail || e.message))
  }
}

async function approveAndSubmitIssue(draftId, idx) {
  submittingIssue.value = `${draftId}-${idx}`
  try {
    // Approve the whole record first (if still pending)
    const draft = drafts.value.find(d => d.id === draftId)
    if (draft && draft.status === 'pending_review') {
      await api.approveDraft(draftId)
    }
    // Then submit this single issue
    const res = await api.submitIssue(draftId, idx)
    ElMessage.success(`${res.data.issue_key} 已通过并提交到 Jira`)
    await loadDrafts()
  } catch (e) {
    ElMessage.error('提交失败: ' + (e.response?.data?.detail || e.message))
  } finally {
    submittingIssue.value = null
  }
}

async function submitSingleIssue(draftId, idx) {
  submittingIssue.value = `${draftId}-${idx}`
  try {
    const res = await api.submitIssue(draftId, idx)
    ElMessage.success(`${res.data.issue_key} 已提交到 Jira`)
    await loadDrafts()
  } catch (e) {
    ElMessage.error('提交失败: ' + (e.response?.data?.detail || e.message))
  } finally {
    submittingIssue.value = null
  }
}

async function showAudit(id) {
  const res = await api.getAuditTrail(id)
  auditLogs.value = res.data
  auditVisible.value = true
}

onMounted(() => { loadJiraContext(); loadDrafts() })
</script>

<style scoped>
.my-logs {
  width: 100%;
}

/* ───── Page header ───── */
.page-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 16px;
  margin-bottom: 24px;
}

.page-header-left {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
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

.page-header-right {
  display: flex;
  gap: 8px;
  align-items: center;
  flex-shrink: 0;
}

.popover-hint {
  margin: 0 0 8px;
  font-size: 13px;
  color: var(--ink-muted);
}

.full-width { width: 100%; }
.mb-12 { margin-bottom: 12px; }

/* ───── Toolbar: filter tabs + date picker ───── */
.toolbar {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 20px;
}

.tag-filters {
  display: flex;
  gap: 10px;
  flex: 1;
  align-items: center;
}

/* Primary tab — 今日 / 历史 pill */
.primary-tab {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  height: 34px;
  padding: 0 18px;
  box-sizing: border-box;
  line-height: 1;
  border-radius: 999px;
  background: transparent;
  border: 1px solid var(--line);
  color: var(--ink-muted);
  font-size: 14px;
  font-weight: 500;
  font-family: inherit;
  cursor: pointer;
  transition: background 0.15s ease, color 0.15s ease, border-color 0.15s ease;
}

.primary-tab:hover {
  color: var(--ink);
  border-color: var(--ink-muted);
}

.primary-tab.active {
  background: var(--ink);
  color: #fff;
  border-color: var(--ink);
}

/* 历史 wrap: inline expansion — chips slide out horizontally on hover */
.history-wrap {
  display: inline-flex;
  align-items: center;
  gap: 8px;
}

.history-inline {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  overflow: hidden;
  /* Collapsed: zero width + no padding; chips themselves are also hidden */
  max-width: 0;
  opacity: 0;
  transition: max-width 0.32s ease, opacity 0.2s ease;
}

.history-wrap.open .history-inline {
  max-width: 520px;   /* enough to fit all 5 chips */
  opacity: 1;
}

.history-chip {
  height: 30px;
  padding: 0 14px;
  border-radius: 999px;
  background: var(--bg);
  border: 1px solid var(--line);
  color: var(--ink-soft);
  font-size: 13px;
  font-family: inherit;
  font-weight: 500;
  cursor: pointer;
  white-space: nowrap;
  /* Staggered per-chip slide-in */
  opacity: 0;
  transform: translateX(-8px);
  transition: background 0.15s ease, color 0.15s ease, border-color 0.15s ease,
              opacity 0.25s ease, transform 0.25s ease;
}

.history-wrap.open .history-chip {
  opacity: 1;
  transform: translateX(0);
}

.history-chip:hover {
  border-color: var(--ink-muted);
  color: var(--ink);
}

.history-chip.active {
  background: var(--ink);
  color: #fff;
  border-color: var(--ink);
}

/* ───── Empty state ───── */
.empty-card {
  background: var(--bg);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  padding: 40px 24px;
}

/* ───── Log card chrome ───── */
.log-card {
  background: var(--bg);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  padding: 24px;
  margin-bottom: 16px;
  transition: box-shadow 0.2s ease;
}

.log-card.collapsed {
  padding: 16px 24px;
  cursor: pointer;
}

.log-card.collapsed:hover {
  box-shadow: 0 2px 12px -4px rgba(0, 0, 0, 0.08);
}

.log-header.clickable {
  cursor: pointer;
  user-select: none;
}

.expand-arrow {
  display: inline-block;
  font-size: 16px;
  color: var(--ink-dim);
  transition: transform 0.2s ease;
  margin-right: 4px;
  width: 12px;
  text-align: center;
}

.expand-arrow.open {
  transform: rotate(90deg);
  color: var(--ink);
}

.log-preview {
  margin-top: 10px;
  font-size: 13px;
  color: var(--ink-muted);
  line-height: 1.55;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  word-break: break-word;
}

.log-body {
  margin-top: 16px;
}

.log-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  margin-bottom: 16px;
}

.log-header-left {
  display: flex;
  align-items: baseline;
  gap: 12px;
  min-width: 0;
  flex: 1;
}

.log-header-right {
  display: flex;
  gap: 6px;
  align-items: center;
  flex-shrink: 0;
}

.log-period {
  font-size: 14px;
  font-weight: 600;
  color: var(--ink);
}

.log-hours {
  font-size: 14px;
  font-weight: 700;
  color: var(--ink);
  font-variant-numeric: tabular-nums;
}

.edited-hint {
  font-size: 11px;
  color: var(--ink-dim);
}

/* ───── Issue sections within a card ───── */
.issue-section {
  padding: 14px 0;
  border-top: 1px solid var(--line-soft);
}

.issue-section:first-of-type {
  border-top: none;
  padding-top: 0;
}

.issue-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  margin-bottom: 6px;
}

.issue-header-left {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
  flex: 1 1 auto;
  flex-wrap: wrap;
}

.section-label {
  font-size: 14px;
  font-weight: 600;
  color: var(--ink);
}

.section-hint {
  font-size: 12px;
  color: var(--ink-muted);
}

/* Issue key — mono, small, state-colored */
.log-issue {
  font-family: var(--font-mono);
  font-size: 11px;
  font-weight: 500;
  color: var(--ink-muted);
  letter-spacing: 0.02em;
  flex-shrink: 0;
  line-height: 1.5;
}

.log-issue--pending {
  color: var(--warning);
}

.log-issue--submitted {
  color: var(--ink-dim);
}

.log-issue-link {
  text-decoration: none;
  transition: opacity 0.15s ease;
}

.log-issue-link:hover {
  opacity: 0.7;
}

.issue-title {
  font-size: 14px;
  font-weight: 500;
  color: var(--ink);
  max-width: 420px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  flex: 0 1 auto;
  min-width: 0;
}

.issue-hours {
  font-size: 12px;
  color: var(--ink-muted);
  font-variant-numeric: tabular-nums;
  flex-shrink: 0;
  margin-left: auto;
}

.issue-actions {
  display: flex;
  gap: 6px;
  align-items: center;
  justify-content: flex-end;
  flex-shrink: 0;
}

.skip-hint {
  font-size: 11px;
  color: var(--ink-dim);
}

/* ───── Issue body / markdown ───── */
.issue-body {
  font-size: 14px;
  line-height: 1.7;
  color: var(--ink-soft);
  padding-left: 4px;
}

/* Markdown rendering — all typography inherits from theme */
.markdown-body :deep(h1) {
  font-size: 18px;
  font-weight: 700;
  color: var(--ink);
  margin: 12px 0 8px;
  line-height: 1.4;
}

.markdown-body :deep(h2) {
  font-size: 16px;
  font-weight: 700;
  color: var(--ink);
  margin: 12px 0 6px;
  line-height: 1.4;
}

.markdown-body :deep(h3) {
  font-size: 15px;
  font-weight: 600;
  color: var(--ink);
  margin: 10px 0 6px;
  line-height: 1.4;
}

.markdown-body :deep(p) {
  font-size: 14px;
  line-height: 1.7;
  color: var(--ink-soft);
  margin: 6px 0;
}

.markdown-body :deep(ul),
.markdown-body :deep(ol) {
  font-size: 14px;
  line-height: 1.7;
  color: var(--ink-soft);
  margin: 6px 0;
  padding-left: 20px;
}

.markdown-body :deep(li) {
  margin-bottom: 4px;
}

.markdown-body :deep(code) {
  font-family: var(--font-mono);
  font-size: 12.5px;
  background: var(--bg-soft);
  color: var(--ink);
  padding: 1px 5px;
  border-radius: 4px;
}

.markdown-body :deep(pre) {
  background: var(--bg-code);
  color: #e5e5e5;
  padding: 12px 16px;
  border-radius: var(--radius-sm);
  font-family: var(--font-mono);
  font-size: 12.5px;
  line-height: 1.65;
  overflow-x: auto;
  margin: 8px 0;
}

.markdown-body :deep(pre code) {
  background: transparent;
  color: inherit;
  padding: 0;
  font-size: 12.5px;
}

.markdown-body :deep(blockquote) {
  border-left: 2px solid var(--line);
  padding-left: 12px;
  color: var(--ink-muted);
  margin: 8px 0;
}

.markdown-body :deep(a) {
  color: var(--ink);
  text-decoration: underline;
  text-underline-offset: 3px;
  text-decoration-color: var(--line);
  transition: text-decoration-color 0.15s ease;
}

.markdown-body :deep(a:hover) {
  text-decoration-color: var(--ink);
}

.markdown-body :deep(strong) {
  font-weight: 600;
  color: var(--ink);
}

.markdown-body :deep(hr) {
  border: none;
  border-top: 1px solid var(--line-soft);
  margin: 12px 0;
}

/* ───── Issue edit mode ───── */
.issue-edit {
  padding: 4px 0;
}

.edit-field {
  margin-bottom: 8px;
}

.edit-label {
  font-size: 13px;
  color: var(--ink-muted);
  margin-bottom: 4px;
  display: block;
}

.edit-actions {
  display: flex;
  gap: 8px;
  margin-top: 8px;
}

/* ───── Card-level actions ───── */
.log-actions {
  display: flex;
  gap: 8px;
  margin-top: 16px;
  padding-top: 16px;
  border-top: 1px solid var(--line-soft);
}

/* ───── Audit dialog ───── */
.audit-snapshot {
  font-family: var(--font-mono);
  font-size: 12px;
  background: var(--bg-soft);
  color: var(--ink);
  padding: 8px 12px;
  border-radius: var(--radius-sm);
  max-height: 200px;
  overflow: auto;
  margin-top: 6px;
}

/* ───── Generating overlay ───── */
.generating-overlay {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background: rgba(255, 255, 255, 0.7);
  backdrop-filter: blur(2px);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
  animation: fadeIn 0.2s ease;
}

.generating-card {
  background: var(--bg);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  padding: 32px 48px;
  text-align: center;
}

.generating-spinner {
  width: 32px;
  height: 32px;
  border: 2px solid var(--line);
  border-top-color: var(--ink);
  border-radius: 50%;
  margin: 0 auto 16px;
  animation: spin 0.8s linear infinite;
}

.generating-text {
  font-size: 14px;
  font-weight: 500;
  color: var(--ink-muted);
  margin-bottom: 12px;
  min-width: 200px;
  transition: opacity 0.3s;
}

.generating-dots {
  display: flex;
  justify-content: center;
  gap: 5px;
}

.dot {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: var(--ink-muted);
  animation: bounce 1.2s ease-in-out infinite;
}

@keyframes fadeIn {
  from { opacity: 0; }
  to { opacity: 1; }
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

@keyframes bounce {
  0%, 60%, 100% { transform: translateY(0); opacity: 0.4; }
  30% { transform: translateY(-6px); opacity: 1; }
}
</style>
