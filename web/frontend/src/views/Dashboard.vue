<template>
  <div class="dashboard">
    <!-- Page Header -->
    <div class="page-header">
      <h2>Dashboard</h2>
      <el-date-picker
        v-model="selectedDate"
        type="date"
        value-format="YYYY-MM-DD"
        @change="loadData"
        class="date-picker"
      />
    </div>

    <!-- Stats Row -->
    <div class="stats-row">
      <div class="stat-card">
        <div class="stat-icon" style="color: var(--warning); background: rgba(255, 159, 10, 0.1)">
          <el-icon :size="22"><Clock /></el-icon>
        </div>
        <div class="stat-value" style="color: var(--warning)">{{ dashboard.pending_review_count }}</div>
        <div class="stat-label">Pending Review</div>
      </div>
      <div class="stat-card">
        <div class="stat-icon" style="color: var(--success); background: rgba(52, 199, 89, 0.1)">
          <el-icon :size="22"><CircleCheck /></el-icon>
        </div>
        <div class="stat-value" style="color: var(--success)">{{ dashboard.submitted_hours }}h</div>
        <div class="stat-label">Submitted Hours</div>
      </div>
      <div class="stat-card">
        <div class="stat-icon" style="color: var(--accent); background: rgba(0, 113, 227, 0.1)">
          <el-icon :size="22"><DataLine /></el-icon>
        </div>
        <div class="stat-value" style="color: var(--accent)">{{ totalActivityHours }}h</div>
        <div class="stat-label">Total Activity</div>
      </div>
    </div>

    <!-- Activity Breakdown -->
    <div class="section">
      <h4 class="section-title">Activity Breakdown</h4>
      <div class="breakdown-list">
        <div v-for="item in dashboard.activity_summary" :key="item.category" class="breakdown-row">
          <div class="breakdown-info">
            <span class="breakdown-category">{{ item.category }}</span>
          </div>
          <div class="breakdown-bar-wrapper">
            <div class="breakdown-bar" :style="{ width: getBarWidth(item.total_sec) + '%' }"></div>
          </div>
          <span class="breakdown-hours">{{ (item.total_sec / 3600).toFixed(1) }}h</span>
        </div>
        <div v-if="!dashboard.activity_summary?.length" class="empty-state">
          No activity data for this date
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import api from '../api'

const selectedDate = ref(new Date().toISOString().split('T')[0])
const dashboard = ref({ pending_review_count: 0, submitted_hours: 0, activity_summary: [] })

const totalActivityHours = computed(() => {
  const total = (dashboard.value.activity_summary || []).reduce((s, a) => s + a.total_sec, 0)
  return (total / 3600).toFixed(1)
})

function getBarWidth(sec) {
  const max = Math.max(...(dashboard.value.activity_summary || []).map(a => a.total_sec), 1)
  return (sec / max) * 100
}

async function loadData() {
  const res = await api.getDashboard(selectedDate.value)
  dashboard.value = res.data
}

onMounted(loadData)
</script>

<style scoped>
.dashboard {
  max-width: 900px;
  margin: 0 auto;
}

.page-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 32px;
}

.stats-row {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 16px;
  margin-bottom: 24px;
}

.stat-card {
  background: var(--surface);
  border-radius: var(--radius);
  box-shadow: var(--shadow);
  padding: 24px;
  text-align: center;
  transition: all 0.2s ease;
}

.stat-card:hover {
  box-shadow: var(--shadow-lg);
  transform: translateY(-1px);
}

.stat-icon {
  width: 44px;
  height: 44px;
  border-radius: 12px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  margin-bottom: 12px;
}

.stat-value {
  font-size: 32px;
  font-weight: 700;
  letter-spacing: -1px;
  line-height: 1.1;
  margin-bottom: 4px;
}

.stat-label {
  font-size: 13px;
  color: var(--text-secondary);
  font-weight: 500;
}

.section {
  background: var(--surface);
  border-radius: var(--radius);
  box-shadow: var(--shadow);
  padding: 24px;
}

.section-title {
  margin-bottom: 20px;
  font-size: 17px;
}

.breakdown-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.breakdown-row {
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 8px 0;
}

.breakdown-category {
  font-size: 14px;
  font-weight: 500;
  color: var(--text-primary);
  width: 100px;
  text-transform: capitalize;
}

.breakdown-bar-wrapper {
  flex: 1;
  height: 6px;
  background: rgba(0, 0, 0, 0.04);
  border-radius: 3px;
  overflow: hidden;
}

.breakdown-bar {
  height: 100%;
  background: var(--accent);
  border-radius: 3px;
  transition: width 0.4s ease;
  opacity: 0.7;
}

.breakdown-hours {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-primary);
  width: 48px;
  text-align: right;
  font-variant-numeric: tabular-nums;
}

.empty-state {
  text-align: center;
  padding: 32px;
  color: var(--text-tertiary);
  font-size: 14px;
}
</style>
