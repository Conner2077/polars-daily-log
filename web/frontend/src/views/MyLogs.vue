<template>
  <div>
    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 24px">
      <h2 class="page-title">My Logs</h2>
      <div style="display: flex; gap: 8px; align-items: center">
        <el-button type="primary" round :disabled="generating" @click="generate('daily')">总结当天</el-button>
        <el-button round :disabled="generating" @click="generate('weekly')">总结这周</el-button>
        <el-button round :disabled="generating" @click="generate('monthly')">总结当月</el-button>
        <el-popover trigger="click" :width="300">
          <template #reference>
            <el-button round :disabled="generating">自定义</el-button>
          </template>
          <div>
            <p style="margin-bottom: 8px; font-size: 13px; color: var(--text-secondary)">选择日期范围</p>
            <el-date-picker
              v-model="customRange"
              type="daterange"
              value-format="YYYY-MM-DD"
              start-placeholder="开始日期"
              end-placeholder="结束日期"
              style="width: 100%; margin-bottom: 12px"
            />
            <el-button type="primary" size="small" round :disabled="!customRange || customRange.length < 2 || generating" @click="generateCustom" style="width: 100%">
              生成总结
            </el-button>
          </div>
        </el-popover>
      </div>
    </div>

    <!-- Generating overlay -->
    <div v-if="generating" class="generating-overlay">
      <div class="generating-card">
        <div class="generating-spinner"></div>
        <div class="generating-text">{{ generatingText }}</div>
        <div class="generating-dots">
          <span v-for="i in 3" :key="i" class="dot" :style="{ animationDelay: `${(i - 1) * 0.3}s` }"></span>
        </div>
      </div>
    </div>

    <!-- Filter tabs -->
    <div style="display: flex; gap: 8px; margin-bottom: 20px">
      <el-button
        v-for="t in tagFilters" :key="t.value"
        :type="activeTag === t.value ? 'primary' : ''"
        :plain="activeTag !== t.value"
        round size="small"
        @click="filterByTag(t.value)"
      >
        {{ t.label }}
      </el-button>
      <div style="flex: 1" />
      <el-date-picker
        v-if="activeTag === '' || activeTag === 'daily'"
        v-model="selectedDate"
        type="date"
        value-format="YYYY-MM-DD"
        placeholder="筛选日期"
        @change="loadDrafts"
        clearable
        size="small"
      />
    </div>

    <div v-if="drafts.length === 0" style="text-align: center; padding: 60px; color: var(--text-tertiary, #aeaeb2)">
      暂无日志记录
    </div>

    <!-- Log cards -->
    <div v-for="draft in drafts" :key="draft.id" class="log-card">
      <!-- Card header -->
      <div class="log-header">
        <div style="display: flex; align-items: center; gap: 8px">
          <span :class="['tag-pill', `tag-${draft.tag || 'daily'}`]">{{ tagLabel(draft.tag) }}</span>
          <span class="log-period">
            {{ draft.period_start && draft.period_end && draft.period_start !== draft.period_end
              ? `${draft.period_start} ~ ${draft.period_end}`
              : draft.date }}
          </span>
          <span v-if="isDailyTag(draft)" :class="['status-pill', `status-${draft.status}`]">
            {{ statusLabel(draft.status) }}
          </span>
          <span v-if="draft.user_edited" style="font-size: 11px; color: var(--text-tertiary, #aeaeb2)">(已编辑)</span>
        </div>
        <div style="display: flex; align-items: center; gap: 12px">
          <div class="log-hours">
            {{ (draft.time_spent_sec / 3600).toFixed(1) }}h
          </div>
        </div>
      </div>

      <!-- Daily log: 全部活动 (raw, all-inclusive) -->
      <div v-if="isDailyTag(draft) && draft.full_summary" class="issue-section">
        <div class="issue-header">
          <div style="display: flex; align-items: center; gap: 8px">
            <span class="section-label">📋 全部活动</span>
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
          <div style="display: flex; gap: 8px; margin-top: 8px">
            <el-button type="primary" round size="small" @click="saveFullEdit(draft.id)">保存</el-button>
            <el-button round size="small" @click="editingFullId = null">取消</el-button>
          </div>
        </div>
        <div v-else class="issue-body">
          <p style="white-space: pre-wrap">{{ draft.full_summary }}</p>
        </div>
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
            <div style="display: flex; align-items: center; gap: 8px">
              <span class="log-issue">{{ issue.issue_key }}</span>
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
              <span v-if="issue.jira_worklog_id" class="submitted-badge">已提交</span>
            </div>
          </div>

          <!-- Edit mode for this issue -->
          <div v-if="editingIssue === `${draft.id}-${idx}`" class="issue-edit">
            <div style="margin-bottom: 8px">
              <label class="edit-label">Issue Key</label>
              <el-input v-model="issueEditForm.issue_key" placeholder="e.g. PLS-4387" size="small" />
            </div>
            <div style="margin-bottom: 8px">
              <label class="edit-label">工时 (小时)</label>
              <el-input-number v-model="issueEditForm.time_spent_hours" :min="0" :step="0.5" :precision="1" size="small" />
            </div>
            <div style="margin-bottom: 8px">
              <label class="edit-label">摘要</label>
              <el-input v-model="issueEditForm.summary" type="textarea" :rows="3" />
            </div>
            <div style="display: flex; gap: 8px">
              <el-button type="primary" round size="small" @click="saveIssueEdit(draft.id, idx)">保存</el-button>
              <el-button round size="small" @click="editingIssue = null">取消</el-button>
            </div>
          </div>
          <div v-else class="issue-body">
            <p style="white-space: pre-wrap">{{ issue.summary }}</p>
          </div>
        </div>
      </template>

      <!-- Non-daily or fallback: plain text summary -->
      <template v-else>
        <div class="log-body">
          <p style="white-space: pre-wrap">{{ draft.summary }}</p>
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
    </div>

    <el-dialog v-model="auditVisible" title="审计记录" width="600px">
      <el-timeline>
        <el-timeline-item v-for="log in auditLogs" :key="log.id" :timestamp="log.created_at">
          <strong>{{ log.action }}</strong>
          <pre v-if="log.after_snapshot" style="font-size: 12px; max-height: 200px; overflow: auto">{{ log.after_snapshot }}</pre>
        </el-timeline-item>
      </el-timeline>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import api from '../api'

const selectedDate = ref(new Date().toISOString().split('T')[0])
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

const tagFilters = [
  { label: '全部', value: '' },
  { label: '每日', value: 'daily' },
  { label: '每周', value: 'weekly' },
  { label: '每月', value: 'monthly' },
  { label: '自定义', value: 'custom' },
]

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
      daily: ['正在采集 Git 提交记录...', '正在分析活动数据...', '正在调用 AI 生成日志...', '正在保存草稿...'],
      weekly: ['正在读取本周每日日志...', '正在调用 AI 生成周报...', '正在保存...'],
      monthly: ['正在读取本月每日日志...', '正在调用 AI 生成月报...', '正在保存...'],
      custom: ['正在读取指定周期日志...', '正在调用 AI 生成总结...', '正在保存...'],
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

onMounted(loadDrafts)
</script>

<style scoped>
.page-title {
  font-size: 28px;
  font-weight: 700;
  letter-spacing: -0.5px;
}
.log-card {
  background: var(--surface, #fff);
  border-radius: 16px;
  box-shadow: 0 1px 3px rgba(0,0,0,0.08);
  margin-bottom: 16px;
  overflow: hidden;
  transition: box-shadow 0.2s;
}
.log-card:hover {
  box-shadow: 0 4px 12px rgba(0,0,0,0.1);
}
.log-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 16px 20px;
  border-bottom: 1px solid var(--border, rgba(0,0,0,0.06));
}
.log-body {
  padding: 16px 20px;
  font-size: 14px;
  line-height: 1.7;
  color: var(--text-primary, #1d1d1f);
}
.log-actions {
  padding: 12px 20px;
  border-top: 1px solid var(--border, rgba(0,0,0,0.06));
  display: flex;
  gap: 8px;
}
.log-hours {
  font-size: 20px;
  font-weight: 700;
  color: var(--text-primary, #1d1d1f);
}
.log-period {
  font-size: 14px;
  color: var(--text-secondary, #86868b);
}
.log-issue {
  font-size: 13px;
  font-weight: 600;
  color: var(--accent, #0071e3);
}

/* Issue sections within a daily card */
.issue-section {
  border-bottom: 1px solid var(--border, rgba(0,0,0,0.04));
}
.issue-section:last-child {
  border-bottom: none;
}
.section-label {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-primary, #1d1d1f);
}
.section-hint {
  font-size: 12px;
  color: var(--text-tertiary, #aeaeb2);
}
.issue-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 10px 20px 0;
}
.issue-hours {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-secondary, #86868b);
}
.issue-body {
  padding: 4px 20px 12px;
  font-size: 14px;
  line-height: 1.7;
  color: var(--text-primary, #1d1d1f);
}
.issue-edit {
  padding: 8px 20px 12px;
}
.edit-label {
  font-size: 13px;
  color: var(--text-secondary, #86868b);
  margin-bottom: 4px;
  display: block;
}
.issue-actions {
  display: flex;
  gap: 6px;
  align-items: center;
  justify-content: flex-end;
  min-width: 180px;
  flex-shrink: 0;
}
.skip-hint {
  font-size: 11px;
  color: var(--text-tertiary, #aeaeb2);
}
.submitted-badge {
  font-size: 11px;
  padding: 1px 8px;
  border-radius: 980px;
  background: #e8f5e9;
  color: #2e7d32;
  font-weight: 500;
}

.tag-pill {
  font-size: 11px;
  font-weight: 600;
  padding: 2px 10px;
  border-radius: 980px;
  text-transform: uppercase;
  letter-spacing: 0.5px;
}
.tag-daily { background: #e3f2fd; color: #1565c0; }
.tag-weekly { background: #e8f5e9; color: #2e7d32; }
.tag-monthly { background: #f3e5f5; color: #7b1fa2; }
.tag-custom { background: #f5f5f5; color: #616161; }
.status-pill {
  font-size: 11px;
  padding: 2px 8px;
  border-radius: 980px;
}
.status-pending_review { background: #fff3e0; color: #e65100; }
.status-approved, .status-auto_approved { background: #e8f5e9; color: #2e7d32; }
.status-submitted { background: #e3f2fd; color: #1565c0; }
.status-rejected, .status-auto_rejected { background: #ffebee; color: #c62828; }
.status-archived { background: #f5f5f5; color: #9e9e9e; }

/* Generating overlay */
.generating-overlay {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background: rgba(245, 245, 247, 0.85);
  backdrop-filter: blur(8px);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
  animation: fadeIn 0.2s ease;
}
.generating-card {
  background: var(--surface, #fff);
  border-radius: 24px;
  box-shadow: 0 8px 32px rgba(0,0,0,0.12);
  padding: 48px 60px;
  text-align: center;
  animation: slideUp 0.3s ease;
}
.generating-spinner {
  width: 40px;
  height: 40px;
  border: 3px solid var(--border, rgba(0,0,0,0.08));
  border-top-color: var(--accent, #0071e3);
  border-radius: 50%;
  margin: 0 auto 20px;
  animation: spin 0.8s linear infinite;
}
.generating-text {
  font-size: 16px;
  font-weight: 500;
  color: var(--text-primary, #1d1d1f);
  margin-bottom: 16px;
  min-width: 200px;
  transition: opacity 0.3s;
}
.generating-dots {
  display: flex;
  justify-content: center;
  gap: 6px;
}
.dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--accent, #0071e3);
  animation: bounce 1.2s ease-in-out infinite;
}
@keyframes fadeIn {
  from { opacity: 0; }
  to { opacity: 1; }
}
@keyframes slideUp {
  from { opacity: 0; transform: translateY(20px); }
  to { opacity: 1; transform: translateY(0); }
}
@keyframes spin {
  to { transform: rotate(360deg); }
}
@keyframes bounce {
  0%, 60%, 100% { transform: translateY(0); opacity: 0.4; }
  30% { transform: translateY(-8px); opacity: 1; }
}
</style>
