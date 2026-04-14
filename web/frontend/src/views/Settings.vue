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
      <el-alert type="warning" :closable="true" style="margin-bottom: 16px" v-if="isLocalhost">
        <template #title>
          <strong>检测到通过 localhost 访问</strong>
        </template>
        如果登录失败（只拿到 1 个 cookie），可能是本地代理（如 Clash）拦截了 localhost 请求。
        请改用 <a :href="'http://127.0.0.1:' + location.port + location.hash" style="color: var(--accent, #0071e3); font-weight: 600">http://127.0.0.1:{{ location.port }}</a> 访问，可绕过代理。
      </el-alert>
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
        <el-form label-position="top" class="settings-form" autocomplete="on">
          <el-form-item label="手机号">
            <el-input v-model="jiraLogin.mobile" placeholder="18800000000" name="username" autocomplete="username" />
          </el-form-item>
          <el-form-item label="密码">
            <el-input v-model="jiraLogin.password" type="password" show-password placeholder="帆软账号密码" name="password" autocomplete="current-password" />
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
        <h4 class="card-title">LLM 配置</h4>

        <!-- Quick fill buttons -->
        <div class="quick-fill-row">
          <span class="quick-fill-label">💡 快速填充：</span>
          <el-button
            v-for="p in presets" :key="p.name"
            size="small" round
            @click="applyPreset(p)"
          >{{ p.name }}</el-button>
        </div>

        <el-form label-position="top" class="settings-form">
          <el-form-item label="API 协议">
            <el-select v-model="settings.llm_engine" style="width: 100%">
              <el-option label="OpenAI 兼容（OpenAI / Kimi / DeepSeek / 智谱 / …）" value="openai_compat" />
              <el-option label="Anthropic（Claude）" value="anthropic" />
              <el-option label="Ollama 本地" value="ollama" />
            </el-select>
          </el-form-item>
          <el-form-item label="API Key">
            <el-input v-model="settings.llm_api_key" type="password" show-password placeholder="留空使用系统内置 Kimi Key" />
          </el-form-item>
          <el-form-item label="Model">
            <el-input v-model="settings.llm_model" :placeholder="modelPlaceholder" />
          </el-form-item>
          <el-form-item label="Base URL">
            <el-input v-model="settings.llm_base_url" :placeholder="basePlaceholder" />
            <div class="form-hint">
              填<strong>根地址</strong>，不要带 <code>/chat/completions</code> 等接口路径。留空用默认：
              <code>{{ basePlaceholder }}</code>
            </div>
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
          <div class="prompt-toolbar">
            <el-button size="small" link @click="resetPrompt('summarize_prompt')">恢复默认</el-button>
          </div>
          <el-input v-model="settings.summarize_prompt" type="textarea" :rows="12" />
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
          <div class="prompt-toolbar">
            <el-button size="small" link @click="resetPrompt('auto_approve_prompt')">恢复默认</el-button>
          </div>
          <el-input v-model="settings.auto_approve_prompt" type="textarea" :rows="12" />
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
          <div class="prompt-toolbar">
            <el-button size="small" link @click="resetPrompt('period_summary_prompt')">恢复默认</el-button>
          </div>
          <el-input v-model="settings.period_summary_prompt" type="textarea" :rows="12" />
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

    <!-- Collectors Tab -->
    <div v-show="activeTab === 'collectors'" class="tab-content">
      <div class="settings-card">
        <h4 class="card-title">数据采集节点</h4>
        <p class="card-description">
          列出所有已注册的 collector。本机自带的 collector 使用 machine_id = <code>local</code>。
          远程 collector 通过 <code>python -m auto_daily_log_collector</code> 启动。
        </p>

        <div v-if="collectors.length === 0" style="text-align: center; padding: 32px; color: var(--text-tertiary)">
          还没有采集节点，首次启动 collector 会自动出现在这里
        </div>
        <el-table v-else :data="collectors" style="width: 100%">
          <el-table-column label="名称" min-width="140">
            <template #default="{ row }">
              <span style="margin-right: 6px">{{ platformIcon(row.platform) }}</span>
              <strong>{{ row.name }}</strong>
              <span v-if="row.hostname" style="color: var(--text-tertiary); font-size: 12px; margin-left: 6px">
                ({{ row.hostname }})
              </span>
            </template>
          </el-table-column>
          <el-table-column label="平台" min-width="160">
            <template #default="{ row }">
              <span style="font-size: 12px; color: var(--text-secondary)">
                {{ row.platform_detail || row.platform || '—' }}
              </span>
            </template>
          </el-table-column>
          <el-table-column label="能力" min-width="180">
            <template #default="{ row }">
              <el-tag
                v-for="cap in row.capabilities" :key="cap"
                size="small"
                style="margin-right: 4px; margin-bottom: 2px"
              >{{ cap }}</el-tag>
              <span v-if="!row.capabilities || row.capabilities.length === 0" style="color: var(--text-tertiary); font-size: 12px">—</span>
            </template>
          </el-table-column>
          <el-table-column label="状态" width="100">
            <template #default="{ row }">
              <span v-if="row.is_paused" class="collector-status paused">已暂停</span>
              <span v-else-if="isOnline(row)" class="collector-status online">在线</span>
              <span v-else class="collector-status offline">离线</span>
            </template>
          </el-table-column>
          <el-table-column label="最后心跳" min-width="160">
            <template #default="{ row }">
              <span style="font-size: 12px; color: var(--text-secondary)">
                {{ row.last_seen || '—' }}
              </span>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="260">
            <template #default="{ row }">
              <el-button
                v-if="row.is_paused"
                size="small" round
                @click="resumeCol(row)"
              >继续</el-button>
              <el-button
                v-else
                size="small" round
                @click="pauseCol(row)"
              >暂停</el-button>
              <el-popconfirm
                :title="`移除 ${row.name}？不会删除历史数据`"
                confirm-button-text="移除"
                cancel-button-text="取消"
                :width="260"
                @confirm="removeCol(row)"
              >
                <template #reference>
                  <el-button size="small" round class="danger-btn">移除</el-button>
                </template>
              </el-popconfirm>
            </template>
          </el-table-column>
        </el-table>

        <div style="margin-top: 16px; display: flex; justify-content: flex-end">
          <el-button size="small" round @click="loadCollectors">
            <el-icon><Refresh /></el-icon>&nbsp;刷新
          </el-button>
        </div>
      </div>
    </div>

    <!-- Recycle Bin Tab -->
    <div v-show="activeTab === 'recycle'" class="tab-content">
      <div class="settings-card">
        <h4 class="card-title">回收站</h4>
        <p class="card-description">
          被删除的活动记录会保留在回收站中，超过 {{ settings.recycle_retention_days || 30 }} 天后自动永久删除。
        </p>
        <el-form label-position="top" class="settings-form" style="margin-bottom: 20px">
          <el-form-item label="活动记录保留天数（超过自动移入回收站）">
            <el-input-number v-model="settings.activity_retention_days" :min="1" :max="365" />
          </el-form-item>
          <el-form-item label="回收站保留天数（超过自动永久删除）">
            <el-input-number v-model="settings.recycle_retention_days" :min="1" :max="365" />
          </el-form-item>
        </el-form>

        <div v-if="recycledItems.length === 0" style="text-align: center; padding: 32px; color: var(--text-tertiary)">
          回收站为空
        </div>
        <el-table v-else :data="recycledItems" style="width: 100%">
          <el-table-column prop="date" label="日期" width="140" />
          <el-table-column prop="count" label="记录数" width="100" />
          <el-table-column prop="deleted_at" label="删除时间" width="180">
            <template #default="{ row }">
              {{ row.deleted_at?.substring(0, 16) }}
            </template>
          </el-table-column>
          <el-table-column label="操作" width="200">
            <template #default="{ row }">
              <el-button size="small" round @click="restoreDate(row.date)">恢复</el-button>
              <el-popconfirm
                title="永久删除后不可恢复，确认继续？"
                confirm-button-text="永久删除"
                cancel-button-text="取消"
                :width="280"
                @confirm="purgeDate(row.date)"
              >
                <template #reference>
                  <el-button size="small" round class="danger-btn">永久删除</el-button>
                </template>
              </el-popconfirm>
            </template>
          </el-table-column>
        </el-table>

        <div v-if="recycledItems.length > 0" style="margin-top: 16px; display: flex; justify-content: flex-end">
          <el-popconfirm
            title="清空回收站后所有记录将永久删除，确认继续？"
            confirm-button-text="清空"
            cancel-button-text="取消"
            :width="300"
            @confirm="purgeAll"
          >
            <template #reference>
              <el-button round class="danger-btn-strong">清空回收站</el-button>
            </template>
          </el-popconfirm>
        </div>
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
import { ref, computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { Refresh } from '@element-plus/icons-vue'
import api from '../api'

const activeTab = ref('monitor')
const tabs = [
  { name: 'monitor', label: '活动采集' },
  { name: 'git', label: 'Git 仓库' },
  { name: 'jira', label: 'Jira 连接' },
  { name: 'llm', label: 'LLM 引擎' },
  { name: 'prompts', label: 'Prompt 模板' },
  { name: 'scheduler', label: '定时任务' },
  { name: 'collectors', label: '数据采集节点' },
  { name: 'recycle', label: '回收站' },
]

const isLocalhost = window.location.hostname === 'localhost'

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
  activity_retention_days: 7, recycle_retention_days: 30,
})
const recycledItems = ref([])
const collectors = ref([])

// Preset shortcuts — pure UI, not persisted. Clicking a preset fills
// protocol + URL + model; user then only needs to paste their API Key.
const presets = [
  { name: 'Kimi',        engine: 'openai_compat', base_url: 'https://api.moonshot.cn/v1',   model: 'moonshot-v1-8k' },
  { name: 'OpenAI',      engine: 'openai_compat', base_url: 'https://api.openai.com/v1',    model: 'gpt-4o' },
  { name: 'DeepSeek',    engine: 'openai_compat', base_url: 'https://api.deepseek.com',     model: 'deepseek-chat' },
  { name: 'Claude',      engine: 'anthropic',     base_url: 'https://api.anthropic.com',    model: 'claude-sonnet-4-20250514' },
  { name: 'Ollama 本地', engine: 'ollama',        base_url: 'http://localhost:11434',       model: 'llama3' },
]

function applyPreset(p) {
  settings.value.llm_engine = p.engine
  settings.value.llm_base_url = p.base_url
  settings.value.llm_model = p.model
  ElMessage.success(`已填入 ${p.name} 预设，请补充 API Key`)
}

// Map protocol → default URL/model for placeholder hints
const PROTOCOL_DEFAULTS = {
  openai_compat: { url: 'https://api.moonshot.cn/v1', model: 'moonshot-v1-8k' },
  anthropic:     { url: 'https://api.anthropic.com',  model: 'claude-sonnet-4-20250514' },
  ollama:        { url: 'http://localhost:11434',     model: 'llama3' },
  // Legacy values still seen from old DB
  kimi:          { url: 'https://api.moonshot.cn/v1', model: 'moonshot-v1-8k' },
  openai:        { url: 'https://api.openai.com/v1',  model: 'gpt-4o' },
  claude:        { url: 'https://api.anthropic.com',  model: 'claude-sonnet-4-20250514' },
}
const basePlaceholder = computed(() => (PROTOCOL_DEFAULTS[settings.value.llm_engine] || {}).url || '')
const modelPlaceholder = computed(() => (PROTOCOL_DEFAULTS[settings.value.llm_engine] || {}).model || '')

function platformIcon(p) {
  if (!p) return '💻'
  if (p === 'macos') return '🖥'
  if (p === 'windows') return '🪟'
  if (p.startsWith('linux')) return '🐧'
  return '💻'
}

function isOnline(c) {
  if (!c.last_seen) return false
  const last = new Date(c.last_seen.replace(' ', 'T') + 'Z').getTime()
  return Date.now() - last < 3 * 60 * 1000
}

async function loadCollectors() {
  try {
    const r = await api.getCollectors()
    collectors.value = r.data
  } catch { /* ignore */ }
}

async function pauseCol(row) {
  await api.pauseCollector(row.machine_id)
  ElMessage.success(`${row.name} 已暂停`)
  await loadCollectors()
}

async function resumeCol(row) {
  await api.resumeCollector(row.machine_id)
  ElMessage.success(`${row.name} 已继续`)
  await loadCollectors()
}

async function removeCol(row) {
  await api.deleteCollector(row.id)
  ElMessage.success(`${row.name} 已移除`)
  await loadCollectors()
}
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
    const res = await api.jiraLoginGet(
      jiraLogin.value.mobile,
      jiraLogin.value.password,
      settings.value.jira_server_url || 'https://work.fineres.com/'
    )
    const d = res.data
    jiraLoginResult.value = {
      success: d.success,
      message: d.success ? `当前登录为：${d.username || '未知用户'}` : (d.message || '登录失败')
    }
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
    // If the stored setting is empty (meaning "use default"), prefill the
    // textarea with the default so users can edit inline.
    for (const key of ['summarize_prompt', 'auto_approve_prompt', 'period_summary_prompt']) {
      if (!settings.value[key] || settings.value[key].trim() === '') {
        settings.value[key] = defaultPrompts.value[key] || ''
      }
    }
  } catch (e) { /* ignore */ }
}

function isDefaultPrompt(key) {
  const cur = (settings.value[key] || '').trim()
  const def = (defaultPrompts.value[key] || '').trim()
  return cur === def
}

function resetPrompt(key) {
  settings.value[key] = defaultPrompts.value[key] || ''
}

async function saveAll() {
  const PROMPT_KEYS = new Set(['summarize_prompt', 'auto_approve_prompt', 'period_summary_prompt'])
  for (const [key, value] of Object.entries(settings.value)) {
    let out = value
    // Prompts: if user didn't change the default, save as empty string so
    // that future default-template updates propagate automatically.
    if (PROMPT_KEYS.has(key) && isDefaultPrompt(key)) {
      out = ''
    }
    await api.putSetting(key, String(out))
  }
  ElMessage.success('Settings saved')
}

async function loadRecycled() {
  try { const res = await api.getRecycledActivities(); recycledItems.value = res.data } catch {}
}

async function restoreDate(date) {
  await api.restoreActivities(date)
  ElMessage.success(`${date} 的记录已恢复`)
  await loadRecycled()
}

async function purgeDate(date) {
  await api.purgeActivities(date)
  ElMessage.success(`${date} 的记录已永久删除`)
  await loadRecycled()
}

async function purgeAll() {
  await api.purgeAllActivities()
  ElMessage.success('回收站已清空')
  recycledItems.value = []
}

onMounted(async () => {
  // settings must load BEFORE defaults: defaults prefill only when setting is empty
  await loadSettings()
  await loadDefaultPrompts()
  loadGitRepos()
  loadRecycled()
  loadCollectors()
})
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

.quick-fill-row {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
  margin: 12px 0 20px;
  padding: 12px 16px;
  background: rgba(0, 113, 227, 0.04);
  border-radius: 12px;
}
.quick-fill-label {
  font-size: 13px;
  color: var(--text-secondary, #86868b);
  margin-right: 4px;
}

.prompt-toolbar {
  display: flex;
  justify-content: flex-end;
  margin-bottom: 8px;
}

.form-hint {
  font-size: 12px;
  color: var(--text-tertiary, #aeaeb2);
  margin-top: 6px;
  line-height: 1.5;
}
.form-hint code {
  background: rgba(0, 0, 0, 0.05);
  padding: 1px 6px;
  border-radius: 4px;
  font-size: 11px;
  font-family: "SF Mono", Menlo, Monaco, monospace;
}
.form-hint strong {
  color: var(--text-primary, #1d1d1f);
}

.collector-status {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  font-weight: 500;
  padding: 2px 10px;
  border-radius: 980px;
}
.collector-status::before {
  content: '';
  width: 6px;
  height: 6px;
  border-radius: 50%;
}
.collector-status.online { background: #e8f5e9; color: #2e7d32; }
.collector-status.online::before { background: #34c759; }
.collector-status.offline { background: #f5f5f7; color: #86868b; }
.collector-status.offline::before { background: #aeaeb2; }
.collector-status.paused { background: #fff3e0; color: #e65100; }
.collector-status.paused::before { background: #ff9f0a; }
</style>
