<template>
  <div class="activities-page">
    <!-- Page header -->
    <div class="page-header">
      <div class="page-header-left">
        <h2 class="page-title">活动记录</h2>
        <div class="page-subtitle">{{ subtitleText }}</div>
      </div>
      <div class="page-header-right">
        <el-input
          v-model="searchQuery"
          placeholder="搜索日志和提交记录..."
          @keyup.enter="doSearch"
          clearable
          @clear="clearSearch"
          class="search-input"
        >
          <template #prefix>
            <el-icon><Search /></el-icon>
          </template>
        </el-input>
        <el-select
          v-model="searchType"
          placeholder="全部类型"
          class="search-type"
          clearable
        >
          <el-option label="全部" value="" />
          <el-option label="工作日志" value="worklog" />
          <el-option label="Git 提交" value="git_commit" />
        </el-select>
        <el-button type="primary" @click="doSearch" :loading="searching">搜索</el-button>
      </div>
    </div>

    <!-- Machine filter (hidden when only one collector) -->
    <div v-if="hasMultipleMachines" class="machine-filter-row">
      <MachineSelector v-model="selectedMachine" @change="onMachineChange" ref="machineSel" />
    </div>

    <!-- Search results (conditional) -->
    <div v-if="searchResults.length > 0 || (searchQuery && searched)" class="card search-card">
      <div class="card-head">
        <div class="card-title">搜索结果</div>
        <span class="card-subtitle">{{ searchResults.length }} 条</span>
      </div>
      <div v-if="searchResults.length > 0" class="search-results">
        <div v-for="(item, i) in searchResults" :key="i" class="search-result-row">
          <div class="search-result-left">
            <el-tag size="small" :type="sourceTagType(item.source_type)">
              {{ item.source_type === 'worklog' ? '日志' : 'Git' }}
            </el-tag>
            <span class="search-result-text">{{ item.text_content }}</span>
          </div>
          <span class="search-relevance mono">
            {{ item.distance !== undefined ? (1 - item.distance).toFixed(2) : '—' }}
          </span>
        </div>
      </div>
      <div v-else class="empty-state">未找到相关内容</div>
    </div>

    <!-- Split panel: dates list + detail -->
    <div class="split-panel">
      <!-- Left: date list -->
      <aside class="dates-col">
        <div class="dates-head">
          <span class="dates-title">日期</span>
          <el-button link @click="loadDates" class="refresh-btn">
            <el-icon><Refresh /></el-icon>
          </el-button>
        </div>
        <div v-if="loadingDates" class="dates-skeleton">
          <el-skeleton :rows="5" animated />
        </div>
        <el-empty
          v-else-if="dates.length === 0"
          description="暂无活动记录"
          :image-size="64"
        />
        <div v-else class="dates-list">
          <button
            v-for="d in dates"
            :key="d.date"
            :class="['date-item', { active: d.date === selectedDate }]"
            @click="selectDate(d.date)"
          >
            <div class="date-label mono">{{ d.date }}</div>
            <div class="date-meta">
              {{ d.count }} 条 · {{ (d.total_sec / 3600).toFixed(1) }}h
            </div>
          </button>
        </div>
      </aside>

      <!-- Right: detail -->
      <section class="detail-col">
        <div v-if="selectedDate" class="detail-head">
          <div class="detail-head-info">
            <span class="detail-date mono">{{ selectedDate }}</span>
            <span class="detail-stats">{{ activities.length }} 条 · {{ totalHours }}h</span>
          </div>
          <div class="detail-actions">
            <el-button
              size="small"
              @click="viewMode = viewMode === 'table' ? 'timeline' : 'table'"
            >
              {{ viewMode === 'table' ? '时间轴视图' : '表格视图' }}
            </el-button>
            <el-button
              v-if="failedCount > 0"
              size="small"
              :loading="retryingFailed"
              @click="retryFailedForDate"
            >
              重新识别失败项（{{ failedCount }}）
            </el-button>
            <el-popconfirm
              title="移入回收站？可在 Settings 中恢复"
              confirm-button-text="移入回收站"
              cancel-button-text="取消"
              :width="280"
              @confirm="deleteAllForDate"
            >
              <template #reference>
                <el-button size="small" class="danger-btn">移入回收站</el-button>
              </template>
            </el-popconfirm>
          </div>
        </div>

        <!-- Loading skeleton -->
        <div v-if="loadingActivities" class="detail-body">
          <el-skeleton :rows="6" animated />
        </div>

        <!-- Table view -->
        <div
          v-else-if="selectedDate && viewMode === 'table' && activities.length > 0"
          class="detail-body table-body"
        >
          <el-table
            :data="activities"
            style="width: 100%"
            max-height="640"
            :row-style="{ height: '44px' }"
            :cell-style="{ verticalAlign: 'middle' }"
          >
            <el-table-column label="时间" width="84">
              <template #default="{ row }">
                <span class="mono cell-time">{{ formatTime(row.timestamp) }}</span>
              </template>
            </el-table-column>
            <el-table-column label="分类" width="108">
              <template #default="{ row }">
                <el-tag :type="categoryType(row.category)" size="small">{{ row.category }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="app_name" label="应用" width="140" show-overflow-tooltip />
            <el-table-column prop="window_title" label="窗口" show-overflow-tooltip />
            <el-table-column label="LLM 摘要" min-width="220" show-overflow-tooltip>
              <template #default="{ row }">
                <span v-if="row.llm_summary && row.llm_summary !== '(failed)'" class="cell-summary">
                  {{ row.llm_summary }}
                </span>
                <span v-else-if="row.llm_summary === '(failed)'" class="cell-summary-failed">
                  识别失败
                </span>
                <span v-else class="cell-summary-empty">—</span>
              </template>
            </el-table-column>
            <el-table-column label="时长" width="80" align="right">
              <template #default="{ row }">
                <span class="mono cell-duration">{{ formatDuration(row.duration_sec) }}</span>
              </template>
            </el-table-column>
            <el-table-column label="截图" width="72" align="center">
              <template #default="{ row }">
                <img
                  v-if="getScreenshotPath(row)"
                  :src="screenshotUrl(getScreenshotPath(row))"
                  class="thumb"
                  @click="showPreview(row)"
                />
              </template>
            </el-table-column>
            <el-table-column label="" width="48" align="center">
              <template #default="{ row }">
                <el-popconfirm title="移入回收站？" :width="220" @confirm="deleteOne(row.id)">
                  <template #reference>
                    <el-button link class="delete-btn">
                      <el-icon><Delete /></el-icon>
                    </el-button>
                  </template>
                </el-popconfirm>
              </template>
            </el-table-column>
          </el-table>
        </div>

        <!-- Timeline view -->
        <div
          v-else-if="selectedDate && viewMode === 'timeline' && activities.length > 0"
          class="detail-body timeline-body"
        >
          <el-timeline>
            <el-timeline-item
              v-for="row in activities"
              :key="row.id"
              :timestamp="formatTime(row.timestamp)"
              placement="top"
            >
              <div class="tl-card">
                <div class="tl-card-main">
                  <div class="tl-card-left">
                    <el-tag :type="categoryType(row.category)" size="small">{{ row.category }}</el-tag>
                    <strong class="tl-app">{{ row.app_name }}</strong>
                    <span class="tl-title">{{ row.window_title }}</span>
                  </div>
                  <div class="tl-card-right">
                    <span class="mono tl-duration">{{ formatDuration(row.duration_sec) }}</span>
                    <img
                      v-if="getScreenshotPath(row)"
                      :src="screenshotUrl(getScreenshotPath(row))"
                      class="thumb"
                      @click="showPreview(row)"
                    />
                    <el-popconfirm title="移入回收站？" :width="220" @confirm="deleteOne(row.id)">
                      <template #reference>
                        <el-button link class="delete-btn">
                          <el-icon><Delete /></el-icon>
                        </el-button>
                      </template>
                    </el-popconfirm>
                  </div>
                </div>
                <div
                  v-if="row.llm_summary && row.llm_summary !== '(failed)'"
                  class="tl-summary"
                >
                  {{ row.llm_summary }}
                </div>
                <div v-else-if="row.llm_summary === '(failed)'" class="tl-summary-failed">
                  识别失败
                </div>
                <div v-if="row.url" class="tl-url mono">{{ row.url }}</div>
              </div>
            </el-timeline-item>
          </el-timeline>
        </div>

        <!-- Empty: date selected but no records -->
        <div
          v-else-if="selectedDate && activities.length === 0"
          class="detail-body"
        >
          <el-empty description="该日期没有活动记录" :image-size="80" />
        </div>

        <!-- Empty: no date selected -->
        <div v-else class="detail-body detail-empty">
          <el-empty description="选择左侧日期以查看活动" :image-size="96" />
        </div>
      </section>
    </div>

    <!-- Screenshot preview dialog -->
    <el-dialog
      v-model="previewVisible"
      title="活动详情"
      width="760px"
      destroy-on-close
    >
      <div class="preview-body">
        <div v-if="previewImage" class="preview-img-wrap">
          <img
            :src="screenshotUrl(previewImage)"
            class="preview-img"
          />
        </div>
        <div v-if="previewLlmSummary" class="preview-meta">
          <div class="preview-meta-label">LLM 摘要</div>
          <div class="preview-meta-value">{{ previewLlmSummary }}</div>
        </div>
        <div v-if="previewOcrText" class="preview-ocr-block">
          <div class="preview-meta-label">OCR 文本</div>
          <pre class="preview-ocr">{{ previewOcrText }}</pre>
        </div>
      </div>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Search, Refresh, Delete } from '@element-plus/icons-vue'
import api from '../api'
import MachineSelector from '../components/MachineSelector.vue'

const route = useRoute()
const dates = ref([])
const selectedDate = ref(null)
// Initialize from ?machine=xxx (e.g. sidebar DEVICES click)
const selectedMachine = ref(route.query.machine || null)
const activities = ref([])
const viewMode = ref('table')
const machineSel = ref(null)
const hasMultipleMachines = ref(false)
const loadingDates = ref(false)
const loadingActivities = ref(false)

// Search refs
const searchQuery = ref('')
const searchType = ref('')
const searchResults = ref([])
const searching = ref(false)
const searched = ref(false)

// Preview refs
const previewVisible = ref(false)
const previewImage = ref(null)
const previewOcrText = ref('')
const previewLlmSummary = ref('')

const totalHours = computed(() => {
  const sec = activities.value.reduce((s, a) => s + (a.duration_sec || 0), 0)
  return (sec / 3600).toFixed(1)
})

const failedCount = computed(() =>
  activities.value.filter(a => a.llm_summary === '(failed)').length
)

const retryingFailed = ref(false)

const subtitleText = computed(() => {
  if (!selectedDate.value) {
    return dates.value.length > 0
      ? `共 ${dates.value.length} 天记录`
      : '暂无活动记录'
  }
  const weekdayMap = ['周日', '周一', '周二', '周三', '周四', '周五', '周六']
  let weekday = ''
  try {
    const d = new Date(selectedDate.value + 'T00:00:00')
    weekday = weekdayMap[d.getDay()]
  } catch (_) { /* ignore */ }
  const count = activities.value.length
  const parts = [selectedDate.value]
  if (weekday) parts.push(weekday)
  parts.push(`${count} 条 · ${totalHours.value}h`)
  return parts.join(' · ')
})

async function loadDates() {
  loadingDates.value = true
  try {
    const res = await api.getActivityDates(selectedMachine.value)
    dates.value = res.data
    if (dates.value.length > 0 && !selectedDate.value) {
      selectDate(dates.value[0].date)
    } else if (dates.value.length === 0) {
      selectedDate.value = null
      activities.value = []
    } else if (selectedDate.value && !dates.value.find(d => d.date === selectedDate.value)) {
      selectDate(dates.value[0].date)
    }
  } finally {
    loadingDates.value = false
  }
}

async function selectDate(d) {
  selectedDate.value = d
  loadingActivities.value = true
  try {
    const res = await api.getActivities(d, selectedMachine.value)
    activities.value = res.data
  } finally {
    loadingActivities.value = false
  }
}

async function onMachineChange() {
  selectedDate.value = null
  await loadDates()
}

async function probeMachines() {
  try {
    const r = await api.getCollectors()
    hasMultipleMachines.value = r.data.length > 1
  } catch { /* ignore */ }
}

async function deleteOne(id) {
  await api.deleteActivity(id)
  ElMessage.success('已移入回收站')
  await selectDate(selectedDate.value)
  await loadDates()
}

async function deleteAllForDate() {
  await api.deleteActivitiesByDate(selectedDate.value)
  ElMessage.success(`${selectedDate.value} 的记录已移入回收站`)
  selectedDate.value = null
  activities.value = []
  await loadDates()
}

async function retryFailedForDate() {
  if (!selectedDate.value) return
  retryingFailed.value = true
  try {
    const res = await api.retryFailedActivities(selectedDate.value)
    const n = res.data?.count ?? 0
    if (n > 0) {
      ElMessage.success(`已重新入队 ${n} 条，几秒后刷新查看结果`)
    } else {
      ElMessage.info('没有需要重试的失败项')
    }
    await selectDate(selectedDate.value)
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '重新识别失败')
  } finally {
    retryingFailed.value = false
  }
}

function formatTime(ts) {
  if (!ts) return ''
  return ts.substring(11, 19)
}

function formatDuration(sec) {
  if (!sec) return '0s'
  if (sec < 60) return `${sec}s`
  if (sec < 3600) return `${Math.round(sec / 60)}m`
  return `${(sec / 3600).toFixed(1)}h`
}

function categoryType(cat) {
  const map = {
    coding: 'success', meeting: 'danger', communication: 'warning',
    design: 'info', writing: 'info', research: 'info', reading: 'info',
    browsing: 'info', other: 'info', idle: 'info',
  }
  return map[cat] || 'info'
}

function getOcrText(row) {
  if (!row.signals) return null
  try {
    const signals = typeof row.signals === 'string' ? JSON.parse(row.signals) : row.signals
    return signals.ocr_text || null
  } catch { return null }
}

function getScreenshotPath(row) {
  if (!row.signals) return null
  try {
    const signals = typeof row.signals === 'string' ? JSON.parse(row.signals) : row.signals
    return signals.screenshot_path || null
  } catch { return null }
}

function screenshotUrl(path) {
  return `/api/activities/screenshot?path=${encodeURIComponent(path)}`
}

function showPreview(row) {
  previewImage.value = getScreenshotPath(row)
  previewOcrText.value = getOcrText(row) || ''
  const llm = row.llm_summary
  previewLlmSummary.value = (llm && llm !== '(failed)') ? llm : ''
  previewVisible.value = true
}

function sourceTagType(type) {
  return { activity: 'success', git_commit: 'warning', worklog: 'info' }[type] || ''
}

function clearSearch() {
  searchResults.value = []
  searched.value = false
}

async function doSearch() {
  if (!searchQuery.value.trim()) return
  searching.value = true
  searched.value = false
  try {
    const res = await api.search(searchQuery.value, 20, searchType.value || null)
    searchResults.value = res.data
  } catch (e) {
    ElMessage.warning(e.response?.data?.detail || 'Search unavailable')
    searchResults.value = []
  } finally {
    searching.value = false
    searched.value = true
  }
}

onMounted(() => { loadDates(); probeMachines() })

// React to sidebar clicks while already on /activities
watch(() => route.query.machine, (v) => {
  const next = v || null
  if (next !== selectedMachine.value) {
    selectedMachine.value = next
    selectedDate.value = null
    loadDates()
  }
})
</script>

<style scoped>
.activities-page {
  width: 100%;
}

/* ───── Page header ───── */
.page-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 20px;
  gap: 16px;
  flex-wrap: wrap;
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
  flex-wrap: wrap;
}

.search-input {
  width: 280px;
}

.search-type {
  width: 120px;
}

.machine-filter-row {
  margin-bottom: 16px;
}

/* ───── Card chrome (aligned with Dashboard) ───── */
.card {
  background: var(--bg);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  padding: 24px;
}

.card-head {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  margin-bottom: 16px;
  gap: 12px;
}

.card-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--ink);
}

.card-subtitle {
  font-size: 12px;
  color: var(--ink-muted);
}

/* ───── Search card ───── */
.search-card {
  margin-bottom: 16px;
}

.search-results {
  display: flex;
  flex-direction: column;
}

.search-result-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 12px 0;
  border-top: 1px solid var(--line-soft);
}

.search-result-row:first-child {
  border-top: none;
  padding-top: 4px;
}

.search-result-left {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
  flex: 1;
}

.search-result-text {
  font-size: 13px;
  color: var(--ink);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  min-width: 0;
}

.search-relevance {
  font-size: 12px;
  color: var(--ink-muted);
  flex-shrink: 0;
}

/* ───── Split panel layout ───── */
.split-panel {
  display: grid;
  grid-template-columns: 260px 1fr;
  gap: 0;
  align-items: stretch;
  border: 1px solid var(--line);
  border-radius: var(--radius);
  background: var(--bg);
  overflow: hidden;
  min-height: 520px;
}

.dates-col {
  border-right: 1px solid var(--line);
  min-width: 0;
  display: flex;
  flex-direction: column;
}

.detail-col {
  min-width: 0;
  display: flex;
  flex-direction: column;
}

/* ───── Dates list ───── */
.dates-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 16px 20px;
  border-bottom: 1px solid var(--line);
}

.dates-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--ink);
}

.refresh-btn {
  color: var(--ink-muted);
}

.dates-skeleton {
  padding: 16px 20px;
}

.dates-list {
  padding: 8px;
  overflow-y: auto;
  flex: 1;
  max-height: 600px;
}

.date-item {
  width: 100%;
  text-align: left;
  padding: 10px 12px;
  cursor: pointer;
  border-radius: var(--radius-sm);
  margin-bottom: 2px;
  transition: background 0.15s ease;
  background: transparent;
  border: none;
  font-family: inherit;
  display: block;
}

.date-item:hover {
  background: var(--bg-soft);
}

.date-item.active {
  background: var(--bg-soft);
}

.date-item.active .date-label {
  color: var(--ink);
  font-weight: 600;
}

.date-label {
  font-size: 13px;
  color: var(--ink);
  margin-bottom: 2px;
  letter-spacing: 0.01em;
}

.date-meta {
  font-size: 11px;
  color: var(--ink-muted);
}

/* ───── Detail column ───── */
.detail-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 16px 20px;
  border-bottom: 1px solid var(--line);
  gap: 12px;
  flex-wrap: wrap;
}

.detail-head-info {
  display: flex;
  align-items: baseline;
  gap: 12px;
  min-width: 0;
}

.detail-date {
  font-size: 14px;
  font-weight: 600;
  color: var(--ink);
}

.detail-stats {
  font-size: 12px;
  color: var(--ink-muted);
}

.detail-actions {
  display: flex;
  gap: 8px;
}

.detail-body {
  flex: 1;
  min-width: 0;
}

.table-body {
  padding: 0;
}

.timeline-body {
  padding: 20px 24px;
}

.detail-empty {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 400px;
}

/* ───── Table cells ───── */
.mono {
  font-family: var(--font-mono);
  font-variant-numeric: tabular-nums;
}

.cell-time {
  font-size: 12px;
  color: var(--ink-muted);
}

.cell-duration {
  font-size: 12px;
  color: var(--ink-soft);
}

.cell-summary {
  font-size: 13px;
  color: var(--ink);
}

.cell-summary-failed {
  font-size: 12px;
  color: var(--ink-dim);
  font-style: italic;
}

.cell-summary-empty {
  color: var(--ink-dim);
}

.thumb {
  width: 48px;
  height: 32px;
  object-fit: cover;
  border-radius: var(--radius-sm);
  border: 1px solid var(--line);
  cursor: pointer;
  transition: border-color 0.15s ease;
  display: block;
}

.thumb:hover {
  border-color: var(--ink-muted);
}

.delete-btn {
  color: var(--ink-muted) !important;
}

.delete-btn:hover {
  color: var(--ink) !important;
}

/* ───── Timeline cards ───── */
.tl-card {
  background: var(--bg);
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  padding: 12px 16px;
}

.tl-card-main {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
}

.tl-card-left {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  flex: 1;
}

.tl-app {
  font-size: 13px;
  font-weight: 600;
  color: var(--ink);
  flex-shrink: 0;
}

.tl-title {
  color: var(--ink-soft);
  font-size: 13px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  min-width: 0;
}

.tl-card-right {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-shrink: 0;
}

.tl-duration {
  font-size: 12px;
  color: var(--ink-muted);
}

.tl-summary {
  margin-top: 8px;
  font-size: 13px;
  color: var(--ink-soft);
  line-height: 1.5;
}

.tl-summary-failed {
  margin-top: 8px;
  font-size: 12px;
  color: var(--ink-dim);
  font-style: italic;
}

.tl-url {
  margin-top: 6px;
  font-size: 11px;
  color: var(--ink-muted);
  word-break: break-all;
}

/* ───── Empty state ───── */
.empty-state {
  text-align: center;
  padding: 32px 16px;
  color: var(--ink-muted);
  font-size: 13px;
}

/* ───── Preview dialog ───── */
.preview-body {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.preview-img-wrap {
  display: flex;
  justify-content: center;
}

.preview-img {
  display: block;
  max-width: 680px;
  max-height: 480px;
  object-fit: contain;
  border-radius: var(--radius);
  border: 1px solid var(--line);
}

.preview-meta {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.preview-meta-label {
  font-size: 11px;
  color: var(--ink-muted);
  text-transform: uppercase;
  letter-spacing: 0.05em;
  font-weight: 500;
}

.preview-meta-value {
  font-size: 14px;
  color: var(--ink);
  line-height: 1.5;
}

.preview-ocr-block {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.preview-ocr {
  background: var(--bg-code);
  color: #e5e5e5;
  font-family: var(--font-mono);
  font-size: 12px;
  padding: 16px;
  max-height: 300px;
  overflow-y: auto;
  border-radius: var(--radius-sm);
  white-space: pre-wrap;
  line-height: 1.5;
  margin: 0;
}

/* ───── Responsive ───── */
@media (max-width: 900px) {
  .split-panel {
    grid-template-columns: 1fr;
  }
  .dates-col {
    border-right: none;
    border-bottom: 1px solid var(--line);
  }
  .dates-list {
    max-height: 240px;
  }
  .search-input {
    width: 100%;
    flex: 1 1 240px;
  }
}
</style>
