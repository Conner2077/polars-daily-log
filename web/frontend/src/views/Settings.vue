<template>
  <div class="settings-page">
    <div class="page-header">
      <h2>Settings</h2>
    </div>

    <!-- Tab Navigation -->
    <div class="tab-nav">
      <button
        v-for="tab in tabs"
        :key="tab.name"
        :class="['tab-btn', { active: activeTab === tab.name }]"
        @click="activeTab = tab.name"
      >
        {{ tab.label }}
      </button>
    </div>

    <!-- Monitor Tab -->
    <div v-show="activeTab === 'monitor'" class="tab-content">
      <div class="settings-card">
        <h4 class="card-title">Monitor Configuration</h4>
        <el-form label-position="top" class="settings-form">
          <el-form-item label="Sampling Interval (seconds)">
            <el-input-number v-model="settings.monitor_interval_sec" :min="10" :max="300" />
          </el-form-item>
          <el-form-item label="OCR Enabled">
            <el-switch v-model="settings.monitor_ocr_enabled" />
          </el-form-item>
          <el-form-item label="OCR Engine">
            <el-select v-model="settings.monitor_ocr_engine" style="width: 100%">
              <el-option label="Auto" value="auto" />
              <el-option label="Vision (macOS)" value="vision" />
              <el-option label="WinOCR (Windows)" value="winocr" />
              <el-option label="Tesseract" value="tesseract" />
            </el-select>
          </el-form-item>
          <el-form-item label="Screenshot Retention (days)">
            <el-input-number v-model="settings.monitor_screenshot_retention_days" :min="1" :max="90" />
          </el-form-item>
        </el-form>
      </div>
    </div>

    <!-- Git Repos Tab -->
    <div v-show="activeTab === 'git'" class="tab-content">
      <div class="settings-card">
        <h4 class="card-title">Git Repositories</h4>
        <p class="card-description">Configure git repositories to track commits from.</p>

        <el-table :data="gitRepos" style="margin-bottom: 20px">
          <el-table-column prop="path" label="Path" />
          <el-table-column prop="author_email" label="Author Email" />
          <el-table-column label="" width="80" align="center">
            <template #default="{ row }">
              <el-button text type="danger" @click="removeRepo(row.id)">
                <el-icon><Delete /></el-icon>
              </el-button>
            </template>
          </el-table-column>
        </el-table>

        <div class="add-repo-form">
          <el-input v-model="newRepo.path" placeholder="/path/to/repo" class="repo-input" />
          <el-input v-model="newRepo.author_email" placeholder="email@example.com" class="repo-input" />
          <el-button type="primary" round @click="addRepo">Add</el-button>
        </div>
      </div>
    </div>

    <!-- Jira Tab -->
    <div v-show="activeTab === 'jira'" class="tab-content">
      <div class="settings-card">
        <h4 class="card-title">Jira 连接</h4>
        <el-form label-position="top" class="settings-form">
          <el-form-item label="Server URL">
            <el-input v-model="settings.jira_server_url" placeholder="https://jira.example.com" />
          </el-form-item>
          <el-form-item label="认证方式">
            <el-select v-model="settings.jira_auth_mode" style="width: 100%">
              <el-option label="SSO 自动登录（推荐）" value="cookie" />
              <el-option label="Bearer Token（PAT）" value="bearer" />
            </el-select>
          </el-form-item>
          <el-form-item v-if="settings.jira_auth_mode === 'bearer'" label="Personal Access Token">
            <el-input v-model="settings.jira_pat" type="password" show-password />
          </el-form-item>
        </el-form>
      </div>

      <div v-if="settings.jira_auth_mode === 'cookie'" class="settings-card" style="margin-top: 16px">
        <h4 class="card-title">SSO 登录</h4>
        <p style="font-size: 13px; color: var(--text-secondary, #86868b); margin-bottom: 16px">
          输入帆软账号自动登录 Jira，获取并保存 Cookie。Cookie 过期后重新登录即可。
        </p>
        <el-form label-position="top" class="settings-form">
          <el-form-item label="手机号">
            <el-input v-model="jiraLogin.mobile" placeholder="18800000000" />
          </el-form-item>
          <el-form-item label="密码">
            <el-input v-model="jiraLogin.password" type="password" show-password placeholder="帆软账号密码" />
          </el-form-item>
          <el-form-item>
            <el-button type="primary" round :loading="jiraLogging" @click="doJiraLogin">
              {{ jiraLogging ? '登录中...' : '登录并获取 Cookie' }}
            </el-button>
            <span v-if="jiraLoginResult" :style="{ marginLeft: '12px', fontSize: '13px', color: jiraLoginResult.success ? 'var(--success, #34c759)' : 'var(--danger, #ff3b30)' }">
              {{ jiraLoginResult.success ? '&#10003;' : '&#10007;' }} {{ jiraLoginResult.message }}
            </span>
          </el-form-item>
        </el-form>
      </div>
    </div>

    <!-- LLM Tab -->
    <div v-show="activeTab === 'llm'" class="tab-content">
      <div class="settings-card">
        <h4 class="card-title">LLM Configuration</h4>
        <el-form label-position="top" class="settings-form">
          <el-form-item label="Engine">
            <el-select v-model="settings.llm_engine" style="width: 100%">
              <el-option label="Kimi (Moonshot)" value="kimi" />
              <el-option label="OpenAI" value="openai" />
              <el-option label="Ollama" value="ollama" />
              <el-option label="Claude" value="claude" />
            </el-select>
          </el-form-item>
          <el-form-item label="API Key">
            <el-input v-model="settings.llm_api_key" type="password" show-password />
          </el-form-item>
          <el-form-item label="Model">
            <el-input v-model="settings.llm_model" />
          </el-form-item>
          <el-form-item label="Base URL">
            <el-input v-model="settings.llm_base_url" />
          </el-form-item>
          <el-form-item>
            <el-button round :loading="checkingKey" @click="checkLLMKey">
              {{ checkingKey ? 'Checking...' : 'Test Connection' }}
            </el-button>
            <span v-if="keyCheckResult" :style="{ marginLeft: '12px', fontSize: '13px', color: keyCheckResult.valid ? 'var(--success, #34c759)' : 'var(--danger, #ff3b30)' }">
              {{ keyCheckResult.valid ? '&#10003;' : '&#10007;' }} {{ keyCheckResult.message }}
            </span>
          </el-form-item>
        </el-form>
      </div>
    </div>

    <!-- Prompts Tab -->
    <div v-show="activeTab === 'prompts'" class="tab-content">
      <div class="settings-card">
        <el-alert type="info" :closable="false" style="margin-bottom: 24px">
          Prompt 模板控制 LLM 生成工作日志的行为。留空则使用系统默认模板。修改后点击底部「保存」按钮生效。
        </el-alert>

        <div class="prompt-section">
          <h4 class="card-title">每日日志生成 Prompt</h4>
          <p class="card-description">
            <strong>用途：</strong>每天定时触发（默认 18:00）时，系统将当天的活动记录、Git 提交和 Jira 任务列表填入此模板，
            发送给 LLM，为每个相关 Jira 任务生成一条工作日志草稿。没有 Jira 任务时，会生成一条综合日志。
          </p>
          <p class="card-hint">
            可用变量：<code>{date}</code> 日期、<code>{jira_issues}</code> 活跃任务列表、<code>{git_commits}</code> 当天提交记录、<code>{activities}</code> 活动采集记录
          </p>
          <el-input v-model="settings.summarize_prompt" type="textarea" :rows="12" :placeholder="defaultPrompts.summarize_prompt" />
        </div>

        <div class="prompt-section">
          <h4 class="card-title">自动审批 Prompt</h4>
          <p class="card-description">
            <strong>用途：</strong>在设定的自动审批时间（默认 21:30），如果仍有未审批的每日日志草稿，
            系统会将每条草稿发送给 LLM 评估质量。合格则自动通过并提交到 Jira，不合格则保持待审批状态等待人工处理。
          </p>
          <p class="card-hint">
            可用变量：<code>{date}</code> 日期、<code>{issue_key}</code> 任务编号、<code>{issue_summary}</code> 任务标题、<code>{time_spent_hours}</code> 工时、<code>{summary}</code> 日志内容、<code>{git_commits}</code> 关联提交
          </p>
          <el-input v-model="settings.auto_approve_prompt" type="textarea" :rows="12" :placeholder="defaultPrompts.auto_approve_prompt" />
        </div>

        <div class="prompt-section">
          <h4 class="card-title">周报/月报生成 Prompt</h4>
          <p class="card-description">
            <strong>用途：</strong>手动点击「总结这周」或「总结当月」时，系统读取该周期内所有每日日志，
            拼接后发送给 LLM 生成周报或月报。输出为纯文本总结，不提交到 Jira，仅供归档查阅。
          </p>
          <p class="card-hint">
            可用变量：<code>{period_start}</code> 开始日期、<code>{period_end}</code> 结束日期、<code>{period_type}</code> 报告类型（周报/月报）、<code>{daily_logs}</code> 每日日志内容
          </p>
          <el-input v-model="settings.period_summary_prompt" type="textarea" :rows="12" :placeholder="defaultPrompts.period_summary_prompt" />
        </div>
      </div>
    </div>

    <!-- Scheduler Tab -->
    <div v-show="activeTab === 'scheduler'" class="tab-content">
      <div class="settings-card">
        <h4 class="card-title">定时任务</h4>
        <el-form label-position="top" class="settings-form">
          <el-form-item label="启用每日自动生成">
            <el-switch v-model="settings.scheduler_enabled" />
          </el-form-item>
          <el-form-item label="每日日志生成时间">
            <el-time-picker v-model="settings.scheduler_trigger_time" format="HH:mm" value-format="HH:mm" />
          </el-form-item>
          <el-form-item label="启用自动审批">
            <el-switch v-model="settings.auto_approve_enabled" />
          </el-form-item>
          <el-form-item label="自动审批并提交时间">
            <el-time-picker v-model="settings.auto_approve_trigger_time" format="HH:mm" value-format="HH:mm" />
          </el-form-item>
        </el-form>
      </div>
    </div>

    <!-- Save Button -->
    <div class="save-bar">
      <el-button type="primary" round size="large" @click="saveAll">
        Save All Settings
      </el-button>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import api from '../api'

const activeTab = ref('monitor')
const tabs = [
  { name: 'monitor', label: '活动采集' },
  { name: 'git', label: 'Git 仓库' },
  { name: 'jira', label: 'Jira 连接' },
  { name: 'llm', label: 'LLM 引擎' },
  { name: 'prompts', label: 'Prompt 模板' },
  { name: 'scheduler', label: '定时任务' },
]

const checkingKey = ref(false)
const keyCheckResult = ref(null)
const jiraLogin = ref({ mobile: '', password: '' })
const jiraLogging = ref(false)
const jiraLoginResult = ref(null)

const settings = ref({
  monitor_interval_sec: 30, monitor_ocr_enabled: true, monitor_ocr_engine: 'auto',
  monitor_screenshot_retention_days: 7, jira_server_url: '', jira_pat: '', jira_auth_mode: 'cookie', jira_cookie: '',
  llm_engine: 'kimi', llm_api_key: '', llm_model: '', llm_base_url: '',
  summarize_prompt: '', auto_approve_prompt: '', period_summary_prompt: '',
  scheduler_enabled: true, scheduler_trigger_time: '18:00',
  auto_approve_enabled: true, auto_approve_trigger_time: '21:30',
})
const gitRepos = ref([])
const newRepo = ref({ path: '', author_email: '' })

const defaultPrompts = ref({
  summarize_prompt: '',
  auto_approve_prompt: '',
  period_summary_prompt: '',
})

async function loadSettings() {
  const res = await api.getSettings()
  for (const item of res.data) {
    if (item.key in settings.value) {
      const val = item.value
      if (val === 'true') settings.value[item.key] = true
      else if (val === 'false') settings.value[item.key] = false
      else if (!isNaN(Number(val)) && val !== '') settings.value[item.key] = Number(val)
      else settings.value[item.key] = val
    }
  }
}

async function loadGitRepos() {
  try { const res = await api.getGitRepos(); gitRepos.value = res.data } catch (e) { /* ignore */ }
}

async function addRepo() {
  if (!newRepo.value.path) return
  await api.addGitRepo(newRepo.value)
  newRepo.value = { path: '', author_email: '' }
  await loadGitRepos()
}

async function removeRepo(id) {
  await api.deleteGitRepo(id)
  await loadGitRepos()
}

async function doJiraLogin() {
  if (!jiraLogin.value.mobile || !jiraLogin.value.password) {
    jiraLoginResult.value = { success: false, message: '请输入手机号和密码' }
    return
  }
  jiraLogging.value = true
  jiraLoginResult.value = null
  try {
    const res = await api.jiraLogin(
      jiraLogin.value.mobile,
      jiraLogin.value.password,
      settings.value.jira_server_url || 'https://work.fineres.com/'
    )
    jiraLoginResult.value = res.data
    if (res.data.success) {
      jiraLogin.value.password = ''
      await loadSettings()
    }
  } catch (e) {
    jiraLoginResult.value = { success: false, message: e.response?.data?.detail || 'Login failed' }
  } finally {
    jiraLogging.value = false
  }
}

async function checkLLMKey() {
  if (!settings.value.llm_api_key && settings.value.llm_engine !== 'ollama') {
    keyCheckResult.value = { valid: false, message: 'Please enter an API Key first' }
    return
  }
  checkingKey.value = true
  keyCheckResult.value = null
  try {
    const res = await api.checkLLMKey(
      settings.value.llm_engine,
      settings.value.llm_api_key,
      settings.value.llm_model,
      settings.value.llm_base_url,
    )
    keyCheckResult.value = res.data
  } catch (e) {
    keyCheckResult.value = { valid: false, message: e.response?.data?.detail || 'Check failed' }
  } finally {
    checkingKey.value = false
  }
}

async function loadDefaultPrompts() {
  try {
    const res = await api.getDefaultPrompts()
    defaultPrompts.value = res.data
  } catch (e) { /* ignore */ }
}

async function saveAll() {
  for (const [key, value] of Object.entries(settings.value)) {
    await api.putSetting(key, String(value))
  }
  ElMessage.success('Settings saved')
}

onMounted(() => { loadSettings(); loadGitRepos(); loadDefaultPrompts() })
</script>

<style scoped>
.settings-page {
  max-width: 800px;
  margin: 0 auto;
}

.page-header {
  margin-bottom: 24px;
}

.tab-nav {
  display: flex;
  gap: 6px;
  margin-bottom: 24px;
  flex-wrap: wrap;
}

.tab-btn {
  padding: 8px 18px;
  border: none;
  background: transparent;
  border-radius: 980px;
  font-size: 14px;
  font-weight: 500;
  color: var(--text-secondary);
  cursor: pointer;
  transition: all 0.2s ease;
  font-family: var(--font);
}

.tab-btn:hover {
  background: rgba(0, 0, 0, 0.04);
  color: var(--text-primary);
}

.tab-btn.active {
  background: var(--text-primary);
  color: #fff;
}

.tab-content {
  animation: fadeIn 0.2s ease;
}

@keyframes fadeIn {
  from { opacity: 0; transform: translateY(4px); }
  to { opacity: 1; transform: translateY(0); }
}

.settings-card {
  background: var(--surface);
  border-radius: var(--radius);
  box-shadow: var(--shadow);
  padding: 28px;
}

.card-title {
  font-size: 17px;
  font-weight: 600;
  margin-bottom: 4px;
  color: var(--text-primary);
}

.card-description {
  font-size: 14px;
  color: var(--text-secondary);
  margin-bottom: 20px;
  line-height: 1.5;
}

.card-hint {
  font-size: 13px;
  color: var(--text-tertiary);
  margin-bottom: 12px;
}

.card-hint code {
  background: rgba(0, 0, 0, 0.05);
  padding: 2px 6px;
  border-radius: 4px;
  font-size: 12px;
  font-family: "SF Mono", Menlo, Monaco, monospace;
}

.settings-form {
  max-width: 500px;
  margin-top: 20px;
}

.settings-form .el-form-item {
  margin-bottom: 24px;
}

.prompt-section {
  margin-bottom: 32px;
}

.prompt-section:last-child {
  margin-bottom: 0;
}

.add-repo-form {
  display: flex;
  gap: 10px;
  align-items: flex-start;
}

.repo-input {
  flex: 1;
}

.save-bar {
  margin-top: 32px;
  padding-top: 24px;
  border-top: 1px solid var(--border);
}
</style>
