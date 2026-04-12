<template>
  <div>
    <h2 style="margin-bottom: 20px">Activities</h2>

    <el-row :gutter="20">
      <!-- Left: date list -->
      <el-col :span="6">
        <el-card>
          <template #header>
            <div style="display: flex; justify-content: space-between; align-items: center">
              <span>Dates</span>
              <el-button size="small" text type="primary" @click="loadDates">
                <el-icon><Refresh /></el-icon>
              </el-button>
            </div>
          </template>
          <div v-if="dates.length === 0" style="color: #909399; text-align: center; padding: 20px">
            Nothing recorded yet
          </div>
          <div
            v-for="d in dates" :key="d.date"
            @click="selectDate(d.date)"
            :class="['date-item', { active: d.date === selectedDate }]"
          >
            <div style="font-weight: bold">{{ d.date }}</div>
            <div style="font-size: 12px; color: #909399">
              {{ d.count }} records / {{ (d.total_sec / 3600).toFixed(1) }}h
            </div>
          </div>
        </el-card>
      </el-col>

      <!-- Right: activity detail -->
      <el-col :span="18">
        <el-card v-if="selectedDate">
          <template #header>
            <div style="display: flex; justify-content: space-between; align-items: center">
              <span>{{ selectedDate }} ({{ activities.length }} records, {{ totalHours }}h)</span>
              <div>
                <el-button size="small" @click="viewMode = viewMode === 'table' ? 'timeline' : 'table'">
                  {{ viewMode === 'table' ? 'Timeline' : 'Table' }}
                </el-button>
                <el-popconfirm
                  title="Delete all records for this date?"
                  confirm-button-text="Delete"
                  cancel-button-text="Cancel"
                  @confirm="deleteAllForDate"
                >
                  <template #reference>
                    <el-button size="small" type="danger">Delete All</el-button>
                  </template>
                </el-popconfirm>
              </div>
            </div>
          </template>

          <!-- Table view -->
          <el-table v-if="viewMode === 'table'" :data="activities" stripe style="width: 100%" max-height="600">
            <el-table-column label="Time" width="90">
              <template #default="{ row }">
                {{ formatTime(row.timestamp) }}
              </template>
            </el-table-column>
            <el-table-column label="Category" width="120">
              <template #default="{ row }">
                <el-tag :type="categoryType(row.category)" size="small">{{ row.category }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="app_name" label="App" width="150" show-overflow-tooltip />
            <el-table-column prop="window_title" label="Title" show-overflow-tooltip />
            <el-table-column label="Duration" width="80">
              <template #default="{ row }">
                {{ formatDuration(row.duration_sec) }}
              </template>
            </el-table-column>
            <el-table-column label="OCR" width="60">
              <template #default="{ row }">
                <el-button v-if="getOcrText(row)" size="small" text @click="showOcr(row)">
                  <el-icon><View /></el-icon>
                </el-button>
              </template>
            </el-table-column>
            <el-table-column label="" width="60">
              <template #default="{ row }">
                <el-popconfirm title="Delete?" @confirm="deleteOne(row.id)">
                  <template #reference>
                    <el-button size="small" text type="danger">
                      <el-icon><Delete /></el-icon>
                    </el-button>
                  </template>
                </el-popconfirm>
              </template>
            </el-table-column>
          </el-table>

          <!-- Timeline view -->
          <el-timeline v-else>
            <el-timeline-item
              v-for="row in activities" :key="row.id"
              :timestamp="formatTime(row.timestamp)"
              placement="top"
            >
              <el-card shadow="never" style="padding: 8px">
                <div style="display: flex; justify-content: space-between; align-items: center">
                  <div>
                    <el-tag :type="categoryType(row.category)" size="small" style="margin-right: 8px">{{ row.category }}</el-tag>
                    <strong>{{ row.app_name }}</strong>
                    <span style="color: #606266; margin-left: 8px">{{ row.window_title }}</span>
                  </div>
                  <div style="display: flex; align-items: center; gap: 8px">
                    <span style="color: #909399; font-size: 12px">{{ formatDuration(row.duration_sec) }}</span>
                    <el-button v-if="getOcrText(row)" size="small" text @click="showOcr(row)">
                      <el-icon><View /></el-icon>
                    </el-button>
                    <el-popconfirm title="Delete?" @confirm="deleteOne(row.id)">
                      <template #reference>
                        <el-button size="small" text type="danger">
                          <el-icon><Delete /></el-icon>
                        </el-button>
                      </template>
                    </el-popconfirm>
                  </div>
                </div>
                <div v-if="row.url" style="font-size: 12px; color: #909399; margin-top: 4px; word-break: break-all">
                  {{ row.url }}
                </div>
              </el-card>
            </el-timeline-item>
          </el-timeline>

          <div v-if="activities.length === 0" style="text-align: center; padding: 40px; color: #909399">
            No records
          </div>
        </el-card>

        <el-card v-else style="text-align: center; padding: 60px; color: #909399">
          Select a date on the left
        </el-card>
      </el-col>
    </el-row>

    <!-- OCR dialog -->
    <el-dialog v-model="ocrVisible" title="OCR Content" width="600px">
      <pre style="white-space: pre-wrap; font-size: 13px; max-height: 400px; overflow: auto">{{ ocrContent }}</pre>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import api from '../api'

const dates = ref([])
const selectedDate = ref(null)
const activities = ref([])
const viewMode = ref('table')
const ocrVisible = ref(false)
const ocrContent = ref('')

const totalHours = computed(() => {
  const sec = activities.value.reduce((s, a) => s + (a.duration_sec || 0), 0)
  return (sec / 3600).toFixed(1)
})

async function loadDates() {
  const res = await api.getActivityDates()
  dates.value = res.data
  if (dates.value.length > 0 && !selectedDate.value) {
    selectDate(dates.value[0].date)
  }
}

async function selectDate(d) {
  selectedDate.value = d
  const res = await api.getActivities(d)
  activities.value = res.data
}

async function deleteOne(id) {
  await api.deleteActivity(id)
  ElMessage.success('Deleted')
  await selectDate(selectedDate.value)
  await loadDates()
}

async function deleteAllForDate() {
  await api.deleteActivitiesByDate(selectedDate.value)
  ElMessage.success(`All records for ${selectedDate.value} deleted`)
  selectedDate.value = null
  activities.value = []
  await loadDates()
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
    design: '', writing: 'info', research: '', reading: 'info',
    browsing: '', other: 'info',
  }
  return map[cat] || ''
}

function getOcrText(row) {
  if (!row.signals) return null
  try {
    const signals = typeof row.signals === 'string' ? JSON.parse(row.signals) : row.signals
    return signals.ocr_text || null
  } catch { return null }
}

function showOcr(row) {
  ocrContent.value = getOcrText(row) || 'No OCR content'
  ocrVisible.value = true
}

onMounted(loadDates)
</script>

<style scoped>
.date-item {
  padding: 10px 12px;
  cursor: pointer;
  border-radius: 4px;
  margin-bottom: 4px;
  transition: background 0.2s;
}
.date-item:hover {
  background: #f5f7fa;
}
.date-item.active {
  background: #ecf5ff;
  border-left: 3px solid #409EFF;
}
</style>
