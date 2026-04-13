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

    <!-- Main Content -->
    <main class="main-content">
      <router-view />
    </main>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import api from './api'

const route = useRoute()
const jiraUser = ref(null)

async function checkJiraStatus() {
  try {
    const res = await api.getSetting('jira_cookie')
    if (res.data && res.data.value) {
      // Cookie exists, try to get username
      const res2 = await api.getSetting('jira_username')
      if (res2.data && res2.data.value) {
        jiraUser.value = res2.data.value
      } else {
        jiraUser.value = 'Connected'
      }
    }
  } catch (e) { /* ignore */ }
}

onMounted(checkJiraStatus)

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
