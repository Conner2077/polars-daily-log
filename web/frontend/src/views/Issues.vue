<template>
  <div class="issues-page">
    <div class="page-header">
      <h2>Jira Issues</h2>
      <el-button type="primary" round @click="dialogVisible = true">
        Add Issue
      </el-button>
    </div>

    <div class="issues-card">
      <el-table :data="issues">
        <el-table-column prop="issue_key" label="Key" width="150">
          <template #default="{ row }">
            <span class="issue-key">{{ row.issue_key }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="summary" label="Summary">
          <template #default="{ row }">
            <span class="issue-summary">{{ row.summary }}</span>
          </template>
        </el-table-column>
        <el-table-column label="Active" width="100">
          <template #default="{ row }">
            <el-switch v-model="row.is_active" @change="toggleActive(row)" />
          </template>
        </el-table-column>
        <el-table-column label="" width="80" align="center">
          <template #default="{ row }">
            <el-button type="danger" size="small" text @click="deleteIssue(row.issue_key)">
              <el-icon><Delete /></el-icon>
            </el-button>
          </template>
        </el-table-column>
      </el-table>

      <div v-if="issues.length === 0" class="empty-state">
        No issues configured yet
      </div>
    </div>

    <!-- Add Issue Dialog -->
    <el-dialog v-model="dialogVisible" title="Add Jira Issue" width="500px">
      <el-form :model="newIssue" label-position="top" class="add-form">
        <el-form-item label="Issue Key" required>
          <div style="display: flex; gap: 8px">
            <el-input v-model="newIssue.issue_key" placeholder="e.g. PLS-4387" @blur="fetchIssueInfo" />
            <el-button round :loading="fetching" @click="fetchIssueInfo" :disabled="!newIssue.issue_key">
              {{ fetching ? '获取中...' : '自动获取' }}
            </el-button>
          </div>
        </el-form-item>
        <el-form-item label="Summary">
          <el-input v-model="newIssue.summary" placeholder="自动获取或手动输入" />
        </el-form-item>
        <el-form-item label="Description">
          <el-input
            v-model="newIssue.description"
            type="textarea"
            :rows="3"
            placeholder="自动获取或手动输入（帮助 LLM 匹配活动到此任务）"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button round @click="dialogVisible = false">Cancel</el-button>
        <el-button type="primary" round @click="addIssue">Add Issue</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import api from '../api'

const issues = ref([])
const dialogVisible = ref(false)
const newIssue = ref({ issue_key: '', summary: '', description: '' })
const fetching = ref(false)

async function loadIssues() { const res = await api.getIssues(); issues.value = res.data }

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
    ElMessage.warning(e.response?.data?.detail || '无法从 Jira 获取任务信息，请手动填写')
  } finally {
    fetching.value = false
  }
}

async function addIssue() {
  if (!newIssue.value.issue_key) { ElMessage.warning('Issue key is required'); return }
  try {
    await api.addIssue(newIssue.value)
    ElMessage.success('Issue added'); dialogVisible.value = false
    newIssue.value = { issue_key: '', summary: '', description: '' }
    await loadIssues()
  } catch (e) { ElMessage.error(e.response?.data?.detail || 'Failed to add issue') }
}

async function toggleActive(row) { await api.updateIssue(row.issue_key, { is_active: row.is_active }) }

async function deleteIssue(key) {
  await ElMessageBox.confirm(`Delete issue ${key}?`, 'Confirm')
  await api.deleteIssue(key); ElMessage.success('Deleted'); await loadIssues()
}

onMounted(loadIssues)
</script>

<style scoped>
.issues-page {
  max-width: 800px;
  margin: 0 auto;
}

.page-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 32px;
}

.issues-card {
  background: var(--surface);
  border-radius: var(--radius);
  box-shadow: var(--shadow);
  overflow: hidden;
  padding: 4px 0;
}

.issue-key {
  font-weight: 600;
  color: var(--accent);
  font-size: 14px;
}

.issue-summary {
  font-size: 14px;
  color: var(--text-primary);
}

.add-form {
  padding: 0 4px;
}

.empty-state {
  text-align: center;
  padding: 48px;
  color: var(--text-tertiary);
  font-size: 14px;
}
</style>
