<template>
  <div class="app-layout">
    <!-- Top Navigation -->
    <nav class="top-nav">
      <div class="nav-inner">
        <router-link to="/" class="nav-logo">
          <img src="/logo.png" alt="Polars" class="logo-img" />
          <span class="logo-text">Polars Daily Log</span>
        </router-link>

        <div class="nav-links">
          <router-link
            v-for="link in navLinks"
            :key="link.path"
            :to="link.path"
            class="nav-link"
            :class="{ active: isActive(link.path) }"
          >
            {{ link.label }}
          </router-link>
        </div>

        <div class="nav-right">
          <button class="feedback-btn" @click="feedbackOpen = true" title="反馈">
            <span class="feedback-icon">💡</span>
          </button>
          <router-link v-if="jiraUser" to="/settings" class="jira-status connected">
            <span class="jira-dot"></span>
            {{ jiraUser }}
          </router-link>
          <router-link v-else to="/settings" class="jira-status disconnected">
            <span class="jira-dot"></span>
            Jira 未登录
          </router-link>
        </div>
      </div>
    </nav>

    <!-- Feedback Dialog -->
    <el-dialog
      v-model="feedbackOpen"
      title="给我们反馈"
      width="480px"
      :close-on-click-modal="false"
    >
      <div class="feedback-types">
        <button
          v-for="t in feedbackTypes" :key="t.value"
          class="type-chip"
          :class="{ active: feedbackType === t.value }"
          @click="feedbackType = t.value"
          type="button"
        >{{ t.label }}</button>
      </div>
      <el-input
        v-model="feedbackContent"
        type="textarea"
        :rows="5"
        :maxlength="2000"
        show-word-limit
        :placeholder="placeholder"
      />
      <template #footer>
        <el-button round @click="feedbackOpen = false">取消</el-button>
        <el-button
          type="primary" round
          :loading="feedbackSubmitting"
          :disabled="!feedbackContent.trim()"
          @click="submitFeedback"
        >提交</el-button>
      </template>
    </el-dialog>

    <!-- Main Content -->
    <main class="main-content">
      <router-view />
    </main>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import api from './api'

const route = useRoute()
const jiraUser = ref(null)

const feedbackOpen = ref(false)
const feedbackType = ref('suggestion')
const feedbackContent = ref('')
const feedbackSubmitting = ref(false)

const feedbackTypes = [
  { value: 'bug',        label: '🐛 Bug' },
  { value: 'suggestion', label: '💡 建议' },
  { value: 'other',      label: '💬 其他' },
]

const placeholderMap = {
  bug: '遇到了什么异常？什么操作复现的？',
  suggestion: '希望它怎么变得更好？',
  other: '想说什么都可以～',
}
const placeholder = computed(() => placeholderMap[feedbackType.value] || '')

async function submitFeedback() {
  const content = feedbackContent.value.trim()
  if (!content) return
  feedbackSubmitting.value = true
  try {
    await api.submitFeedback(
      feedbackType.value,
      content,
      route.fullPath || window.location.pathname,
      navigator.userAgent || '',
    )
    ElMessage.success('感谢反馈，已收到！')
    feedbackOpen.value = false
    feedbackContent.value = ''
    feedbackType.value = 'suggestion'
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '提交失败，请稍后再试')
  } finally {
    feedbackSubmitting.value = false
  }
}

async function checkJiraStatus() {
  try {
    const res = await api.getJiraStatus()
    jiraUser.value = res.data.logged_in ? res.data.username : null
  } catch (e) { /* ignore */ }
}

onMounted(() => {
  checkJiraStatus()
  setInterval(checkJiraStatus, 5 * 60 * 1000) // check every 5 min
})

const navLinks = [
  { path: '/', label: 'Dashboard' },
  { path: '/activities', label: 'Activities' },
  { path: '/my-logs', label: 'My Logs' },
  { path: '/issues', label: 'Issues' },
  { path: '/settings', label: 'Settings' },
]

function isActive(path) {
  if (path === '/') return route.path === '/'
  return route.path.startsWith(path)
}
</script>

<style scoped>
.app-layout {
  min-height: 100vh;
  background: var(--bg);
}

.top-nav {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  z-index: 1000;
  background: rgba(255, 255, 255, 0.72);
  backdrop-filter: saturate(180%) blur(20px);
  -webkit-backdrop-filter: saturate(180%) blur(20px);
  border-bottom: 1px solid var(--border);
  height: 52px;
}

.nav-inner {
  max-width: 1200px;
  margin: 0 auto;
  height: 100%;
  display: flex;
  align-items: center;
  padding: 0 24px;
}

.nav-logo {
  display: flex;
  align-items: center;
  gap: 8px;
  text-decoration: none;
  color: var(--text-primary);
  flex-shrink: 0;
}

.logo-img {
  width: 28px;
  height: 28px;
  object-fit: contain;
}

.logo-text {
  font-size: 17px;
  font-weight: 600;
  letter-spacing: -0.3px;
}

.nav-links {
  display: flex;
  align-items: center;
  gap: 4px;
  margin: 0 auto;
}

.nav-link {
  text-decoration: none;
  color: var(--text-secondary);
  font-size: 14px;
  font-weight: 500;
  padding: 6px 16px;
  border-radius: 980px;
  transition: all 0.2s ease;
  position: relative;
}

.nav-link:hover {
  color: var(--text-primary);
  background: rgba(0, 0, 0, 0.04);
}

.nav-link.active {
  color: var(--text-primary);
  background: rgba(0, 0, 0, 0.06);
}

.nav-right {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: 8px;
}

.feedback-btn {
  background: transparent;
  border: none;
  cursor: pointer;
  width: 28px;
  height: 28px;
  border-radius: 980px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 15px;
  line-height: 1;
  transition: background 0.2s;
}
.feedback-btn:hover {
  background: rgba(0, 0, 0, 0.06);
}
.feedback-icon {
  filter: grayscale(0.2);
}

.feedback-types {
  display: flex;
  gap: 8px;
  margin-bottom: 12px;
}
.type-chip {
  flex: 1;
  padding: 6px 12px;
  font-size: 13px;
  font-weight: 500;
  color: var(--text-secondary, #86868b);
  background: rgba(0, 0, 0, 0.04);
  border: 1px solid transparent;
  border-radius: 8px;
  cursor: pointer;
  transition: all 0.15s;
}
.type-chip:hover {
  background: rgba(0, 0, 0, 0.06);
}
.type-chip.active {
  color: var(--accent, #0071e3);
  background: rgba(0, 113, 227, 0.08);
  border-color: rgba(0, 113, 227, 0.25);
}

.jira-status {
  display: flex;
  align-items: center;
  gap: 6px;
  text-decoration: none;
  font-size: 12px;
  font-weight: 500;
  padding: 4px 12px;
  border-radius: 980px;
  transition: all 0.2s;
}

.jira-status.connected {
  color: var(--success, #34c759);
  background: rgba(52, 199, 89, 0.08);
}

.jira-status.disconnected {
  color: var(--text-tertiary, #aeaeb2);
  background: rgba(0, 0, 0, 0.03);
}

.jira-status:hover {
  opacity: 0.8;
}

.jira-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: currentColor;
}

.main-content {
  max-width: 1200px;
  margin: 0 auto;
  padding: 84px 24px 48px;
}
</style>
