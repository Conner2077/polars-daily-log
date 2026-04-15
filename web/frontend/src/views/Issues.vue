<template>
  <div class="issues-page">
    <!-- Page Header -->
    <div class="page-header">
      <div class="page-header-left">
        <h2 class="page-title">活跃 Issue 列表</h2>
        <div class="page-subtitle">{{ subtitleText }}</div>
      </div>
      <el-button type="primary" round @click="dialogVisible = true">
        添加 Issue
      </el-button>
    </div>

    <!-- Issues table card -->
    <div class="card issues-card">
      <el-table
        v-if="issues.length"
        :data="issues"
        style="width: 100%"
        :row-style="{ height: '52px' }"
      >
        <el-table-column prop="issue_key" label="Key" width="120">
          <template #default="{ row }">
            <span class="issue-key">{{ row.issue_key }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="summary" label="任务名称" min-width="200">
          <template #default="{ row }">
            <span class="issue-summary">{{ row.summary }}</span>
          </template>
        </el-table-column>
        <el-table-column
          prop="description"
          label="描述"
          min-width="250"
          show-overflow-tooltip
        >
          <template #default="{ row }">
            <span class="issue-description">{{ row.description }}</span>
          </template>
        </el-table-column>
        <el-table-column label="Active" width="80" align="center">
          <template #default="{ row }">
            <el-switch v-model="row.is_active" @change="toggleActive(row)" />
          </template>
        </el-table-column>
        <el-table-column label="" width="60" align="center">
          <template #default="{ row }">
            <el-popconfirm
              :title="`删除 ${row.issue_key}？`"
              confirm-button-text="删除"
              cancel-button-text="取消"
              :width="220"
              @confirm="confirmDelete(row.issue_key)"
            >
              <template #reference>
                <el-button text class="row-delete-btn">
                  <el-icon><Delete /></el-icon>
                </el-button>
              </template>
            </el-popconfirm>
          </template>
        </el-table-column>
      </el-table>

      <el-empty
        v-else
        description="暂无 Issue，点击右上角添加"
      />
    </div>

    <!-- Add Issue Dialog -->
    <el-dialog v-model="dialogVisible" title="添加 Issue" width="500px">
      <el-form :model="newIssue" label-position="top" class="add-form">
        <el-form-item label="Issue Key" required>
          <div class="key-row">
            <el-input
              v-model="newIssue.issue_key"
              placeholder="e.g. PLS-4387"
              @blur="fetchIssueInfo"
            />
            <el-button
              round
              :loading="fetching"
              :disabled="!newIssue.issue_key"
              @click="fetchIssueInfo"
            >
              {{ fetching ? '获取中...' : '自动获取' }}
            </el-button>
          </div>
        </el-form-item>
        <el-form-item label="任务名称">
          <el-input v-model="newIssue.summary" placeholder="自动获取或手动输入" />
        </el-form-item>
        <el-form-item label="任务描述">
          <el-input
            v-model="newIssue.description"
            type="textarea"
            :rows="3"
            placeholder="自动获取或手动输入（帮助 LLM 匹配活动到此任务）"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button round @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" round @click="addIssue">添加</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { Delete } from '@element-plus/icons-vue'
import api from '../api'

const issues = ref([])
const dialogVisible = ref(false)
const newIssue = ref({ issue_key: '', summary: '', description: '' })
const fetching = ref(false)

const subtitleText = computed(() => {
  const total = issues.value.length
  const active = issues.value.filter((i) => i.is_active).length
  const archived = total - active
  return `${active} 个 active issue · ${archived} 个归档`
})

async function loadIssues() {
  const res = await api.getIssues()
  issues.value = res.data
}

async function fetchIssueInfo() {
  const key = newIssue.value.issue_key.trim().toUpperCase()
  if (!key) return
  newIssue.value.issue_key = key
  fetching.value = true
  try {
    const res = await api.fetchJiraIssue(key)
    newIssue.value.summary = res.data.summary || ''
    newIssue.value.description = res.data.description || ''
    ElMessage.success(`已获取: ${res.data.summary}`)
  } catch (e) {
    ElMessage.warning(
      e.response?.data?.detail || '无法从 Jira 获取任务信息，请手动填写'
    )
  } finally {
    fetching.value = false
  }
}

async function addIssue() {
  if (!newIssue.value.issue_key) {
    ElMessage.warning('Issue key is required')
    return
  }
  try {
    await api.addIssue(newIssue.value)
    ElMessage.success('Issue added')
    dialogVisible.value = false
    newIssue.value = { issue_key: '', summary: '', description: '' }
    await loadIssues()
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || 'Failed to add issue')
  }
}

async function toggleActive(row) {
  await api.updateIssue(row.issue_key, { is_active: row.is_active })
}

async function confirmDelete(key) {
  await api.deleteIssue(key)
  ElMessage.success('已删除')
  await loadIssues()
}

onMounted(loadIssues)
</script>

<style scoped>
.issues-page {
  width: 100%;
}

/* ───── Page header ───── */
.page-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 24px;
  gap: 16px;
}

.page-header-left {
  display: flex;
  flex-direction: column;
  gap: 4px;
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

/* ───── Card chrome ───── */
.card {
  background: var(--bg);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  overflow: hidden;
}

.issues-card {
  padding: 0;
}

/* ───── Table cell content ───── */
.issue-key {
  font-family: var(--font-mono);
  font-size: 12.5px;
  color: var(--ink);
  letter-spacing: 0.02em;
}

.issue-summary {
  font-size: 13.5px;
  font-weight: 500;
  color: var(--ink);
}

.issue-description {
  font-size: 13px;
  color: var(--ink-muted);
}

.row-delete-btn {
  color: var(--ink-dim) !important;
}

.row-delete-btn:hover {
  color: var(--danger) !important;
}

/* ───── Dialog form ───── */
.add-form {
  padding: 0 4px;
}

.key-row {
  display: flex;
  gap: 8px;
  width: 100%;
}

.key-row .el-input {
  flex: 1;
}
</style>
